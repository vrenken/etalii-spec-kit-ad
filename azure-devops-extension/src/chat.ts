// The chat panel: talks to the model about one work item, shows the proposal,
// and applies it to Azure DevOps when the user says so.

import * as SDK from "azure-devops-extension-sdk";
import { getClient, type IExtensionDataManager, type IExtensionDataService } from "azure-devops-extension-api";
import { WorkRestClient } from "azure-devops-extension-api/Work";
import { WorkItemTrackingRestClient, type WorkItem } from "azure-devops-extension-api/WorkItemTracking";

import {
  analysisComment,
  type ChatContext,
  childPatch,
  type ExistingLinks,
  F,
  type ItemContext,
  linkPatch,
  LINKS,
  type ModelSettings,
  normalizeSettings,
  openingMessage,
  parseAnswer,
  type Proposal,
  renderMarkdown,
  escapeHtml,
  settingsProblem,
  specificationPatches,
  systemPrompt,
} from "./chat-model";
import { type Conversation, startConversation } from "./llm";
import { type Action, actionsForLevel, childTypeFor, levelOf, levelsFromBacklogs, menuText, requestPatch, resolveAction } from "./model";

interface PanelConfiguration {
  /** The menu pick; replaced by the action that fits the item's level. */
  action: Action;
  id: number;
  project: string;
  levels: string[][];
  level: number;
  childType?: string;
  dialog?: { close: (result?: unknown) => void };
}

const SETTINGS_KEY = "model-settings";
const RELATIONS = 1; // WorkItemExpand.Relations
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

let configuration: PanelConfiguration;
let settings: ModelSettings;
let dataManager: IExtensionDataManager;
let conversation: Conversation | undefined;
let context: ChatContext;
let item: WorkItem;
let children: WorkItem[] = [];
let proposal: Proposal | undefined;
let busy = false;
let ready = false; // an action that fits the item has been settled

const client = () => getClient(WorkItemTrackingRestClient);

// -- Azure DevOps -------------------------------------------------------------

function relatedIds(workItem: WorkItem, rel: string): number[] {
  return (workItem.relations ?? [])
    .filter((relation) => relation.rel === rel)
    .map((relation) => Number(relation.url.split("/").pop()))
    .filter((id) => Number.isInteger(id) && id > 0);
}

function relatedUrls(workItem: WorkItem, rel: string): Set<string> {
  return new Set((workItem.relations ?? []).filter((relation) => relation.rel === rel).map((relation) => relation.url.toLowerCase()));
}

function toContext(workItem: WorkItem): ItemContext {
  const fields = workItem.fields;
  return {
    id: workItem.id,
    type: String(fields["System.WorkItemType"] ?? ""),
    title: String(fields["System.Title"] ?? ""),
    specification: String(fields[F.specification] ?? ""),
    specificationState: String(fields[F.specificationState] ?? ""),
  };
}

async function loadContext(): Promise<void> {
  const { id, project } = configuration;
  item = await client().getWorkItem(id, project, undefined, undefined, RELATIONS);
  const parentId = relatedIds(item, LINKS.parent)[0];
  const childIds = relatedIds(item, LINKS.child);
  const [parent, loadedChildren] = await Promise.all([
    parentId ? client().getWorkItem(parentId, project) : Promise.resolve(undefined),
    childIds.length ? client().getWorkItems(childIds, project, undefined, undefined, RELATIONS) : Promise.resolve([] as WorkItem[]),
  ]);
  children = loadedChildren;
  context = {
    action: configuration.action,
    item: toContext(item),
    parent: parent ? toContext(parent) : undefined,
    children: children.map(toContext),
    levels: configuration.levels,
    level: configuration.level,
    childType: configuration.childType,
  };
}

async function applyProposal(current: Proposal): Promise<string> {
  const { id, project } = configuration;
  if (current.kind === "specification") {
    const [begin, write] = specificationPatches(current.specification);
    await client().updateWorkItem(begin, id, project);
    await client().updateWorkItem(write, id, project);
    return `Specification of ${context.item.type} ${id} updated. It is now ready for review.`;
  }
  if (current.kind === "analysis") {
    await client().updateWorkItem(analysisComment(current.findings), id, project);
    return `Analysis added to the discussion of ${context.item.type} ${id}.`;
  }

  const childType = configuration.childType;
  if (!childType) {
    throw new Error("The type of the items to create is unknown.");
  }
  const existing = new Map(
    children
      .filter((child) => String(child.fields["System.WorkItemType"] ?? "").toLowerCase() === childType.toLowerCase())
      .map((child) => [String(child.fields["System.Title"] ?? "").trim().toLowerCase(), child]),
  );
  const parent = {
    url: item.url,
    areaPath: item.fields["System.AreaPath"] as string | undefined,
    iterationPath: item.fields["System.IterationPath"] as string | undefined,
  };
  const items = new Map<string, WorkItem>();
  let created = 0;
  for (const child of current.children) {
    let workItem = existing.get(child.title.toLowerCase());
    if (!workItem) {
      workItem = await client().createWorkItem(childPatch(child, parent), project, childType);
      created += 1;
    }
    items.set(child.key, workItem);
  }
  const urls = new Map([...items].map(([key, workItem]) => [key, workItem.url]));
  let links = 0;
  for (const child of current.children) {
    const workItem = items.get(child.key)!;
    const present: ExistingLinks = {
      predecessors: relatedUrls(workItem, LINKS.predecessor),
      related: relatedUrls(workItem, LINKS.related),
    };
    const patch = linkPatch(child, urls, present);
    if (patch.length > 0) {
      await client().updateWorkItem(patch, workItem.id, project);
      links += patch.length;
    }
  }
  const reused = current.children.length - created;
  return `Created ${created} ${childType} item${created === 1 ? "" : "s"}${reused ? `, kept ${reused} existing` : ""}, and added ${links} link${links === 1 ? "" : "s"}. They are ready for review.`;
}

// -- Rendering ----------------------------------------------------------------

function addBubble(role: "user" | "assistant" | "notice" | "error", html: string): HTMLElement {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  bubble.innerHTML = html;
  $("transcript").appendChild(bubble);
  bubble.scrollIntoView({ block: "end" });
  return bubble;
}

function renderProposal(): void {
  const target = $("proposal");
  if (!proposal) {
    target.hidden = true;
    return;
  }
  target.hidden = false;
  let html: string;
  if (proposal.kind === "specification") {
    html = `<h3>Proposed specification</h3><div class="card">${renderMarkdown(proposal.specification)}</div>`;
  } else if (proposal.kind === "children") {
    const titles = new Map(proposal.children.map((child) => [child.key, child.title]));
    html = `<h3>Proposed ${escapeHtml(configuration.childType ?? "items")} (${proposal.children.length})</h3>${proposal.children
      .map((child) => {
        const after = child.depends_on.map((key) => escapeHtml(titles.get(key) ?? key)).join(", ");
        return `<details class="card"><summary><span class="ask">${child.ask === "agent" ? "\u{1F916}" : "\u{1F464}"}</span> ${escapeHtml(child.title)}${
          after ? `<span class="after">after: ${after}</span>` : ""
        }</summary>${renderMarkdown(child.specification)}</details>`;
      })
      .join("")}`;
  } else {
    html = `<h3>Findings (${proposal.findings.length})</h3>${
      proposal.findings.length
        ? `<ul class="card">${proposal.findings
            .map((finding) => `<li><b>${escapeHtml(finding.severity)}</b>${finding.id ? ` #${finding.id}` : ""}: ${escapeHtml(finding.finding)}${finding.action ? `<br><i>${escapeHtml(finding.action)}</i>` : ""}</li>`)
            .join("")}</ul>`
        : '<div class="card">The children are consistent with this item.</div>'
    }`;
  }
  target.innerHTML = html;
}

function refreshControls(): void {
  const chosen = ready;
  ($("send") as HTMLButtonElement).disabled = busy || !chosen;
  ($("apply") as HTMLButtonElement).disabled = busy || !proposal;
  ($("queue") as HTMLButtonElement).disabled = busy || !chosen;
  $("apply").textContent = proposal?.kind === "analysis" ? "Add to discussion" : "Apply to Azure DevOps";
}

function setBusy(value: boolean): void {
  busy = value;
  refreshControls();
}

// -- Conversation -------------------------------------------------------------

async function ask(text: string, shown?: string): Promise<void> {
  if (!conversation) {
    return;
  }
  if (shown) {
    addBubble("user", renderMarkdown(shown));
  }
  setBusy(true);
  const bubble = addBubble("assistant", '<span class="typing">Thinking…</span>');
  let streamed = "";
  try {
    const answer = await conversation.send(text, (delta) => {
      streamed += delta;
      // While it streams, show the prose and hide the half-written proposal.
      bubble.innerHTML = renderMarkdown(streamed.split("```json")[0]) || '<span class="typing">Writing the proposal…</span>';
      bubble.scrollIntoView({ block: "end" });
    });
    const parsed = parseAnswer(answer, configuration.action);
    bubble.innerHTML = renderMarkdown(parsed.message) || "<p>Here is the proposal.</p>";
    if (parsed.problem) {
      addBubble("error", escapeHtml(parsed.problem));
    }
    if (parsed.proposal) {
      proposal = parsed.proposal;
      renderProposal();
    }
  } catch (error) {
    bubble.remove();
    addBubble("error", escapeHtml(error instanceof Error ? error.message : String(error)));
  } finally {
    setBusy(false);
  }
}

function begin(): void {
  if (!ready) {
    showSettings(false);
    return; // settings were saved before the action was settled
  }
  const problem = settingsProblem(settings);
  if (problem) {
    showSettings(true, `Model settings are needed before the chat can start. ${problem}`);
    return;
  }
  showSettings(false);
  $("transcript").innerHTML = "";
  proposal = undefined;
  renderProposal();
  refreshControls();
  conversation = startConversation(settings, systemPrompt(context));
  const notes = ($("notes") as HTMLTextAreaElement).value;
  void ask(openingMessage(context, notes), notes.trim() || undefined);
  ($("notes") as HTMLTextAreaElement).value = "";
}

// -- Settings -----------------------------------------------------------------

function showSettings(visible: boolean, message = ""): void {
  $("settings").hidden = !visible;
  $("settings-message").textContent = message;
  ($("provider") as HTMLSelectElement).value = settings.provider;
  ($("model") as HTMLInputElement).value = settings.model;
  ($("base-url") as HTMLInputElement).value = settings.baseUrl;
  ($("api-key") as HTMLInputElement).value = settings.apiKey;
  $("base-url-row").hidden = settings.provider === "anthropic";
}

function readSettingsForm(): ModelSettings {
  return normalizeSettings({
    provider: ($("provider") as HTMLSelectElement).value as ModelSettings["provider"],
    model: ($("model") as HTMLInputElement).value,
    baseUrl: ($("base-url") as HTMLInputElement).value,
    apiKey: ($("api-key") as HTMLInputElement).value,
  });
}

async function saveSettings(): Promise<void> {
  const candidate = readSettingsForm();
  const problem = settingsProblem(candidate);
  if (problem) {
    $("settings-message").textContent = problem;
    return;
  }
  settings = candidate;
  // Stored per user in Azure DevOps: other people cannot read this key.
  await dataManager.setValue(SETTINGS_KEY, settings, { scopeType: "User" });
  begin();
}

// -- Start --------------------------------------------------------------------

async function start(): Promise<void> {
  await SDK.init({ applyTheme: true });
  await SDK.ready();
  configuration = SDK.getConfiguration() as PanelConfiguration;
  // The frame cannot measure the host window, so size the dialog from the
  // screen: about 70% wide and, allowing for browser chrome, 70% high.
  SDK.resize(Math.round(window.screen.availWidth * 0.7), Math.round((window.screen.availHeight - 140) * 0.7));

  const dataService = await SDK.getService<IExtensionDataService>("ms.vss-features.extension-data-service");
  dataManager = await dataService.getExtensionDataManager(SDK.getExtensionContext().id, await SDK.getAccessToken());
  settings = normalizeSettings(await dataManager.getValue<ModelSettings>(SETTINGS_KEY, { scopeType: "User" }).catch(() => undefined));

  $("provider").addEventListener("change", () => {
    const provider = ($("provider") as HTMLSelectElement).value;
    $("base-url-row").hidden = provider === "anthropic";
    const model = $("model") as HTMLInputElement;
    model.value = provider === "anthropic" ? "claude-opus-5-5" : "";
  });
  $("save-settings").addEventListener("click", () => void saveSettings());
  $("open-settings").addEventListener("click", () => showSettings(Boolean($("settings").hidden)));
  $("send").addEventListener("click", () => {
    const input = $("notes") as HTMLTextAreaElement;
    const text = input.value.trim();
    if (text && !busy && conversation) {
      input.value = "";
      void ask(text, text);
    }
  });
  $("notes").addEventListener("keydown", (event) => {
    if ((event as KeyboardEvent).key === "Enter" && ((event as KeyboardEvent).ctrlKey || (event as KeyboardEvent).metaKey)) {
      $("send").click();
    }
  });
  $("apply").addEventListener("click", async () => {
    if (!proposal || busy) {
      return;
    }
    setBusy(true);
    try {
      addBubble("notice", escapeHtml(await applyProposal(proposal)));
      proposal = undefined;
      renderProposal();
      await loadContext(); // so a second apply sees what the first one created
    } catch (error) {
      addBubble("error", `Azure DevOps did not accept the change: ${escapeHtml(error instanceof Error ? error.message : String(error))}`);
    } finally {
      setBusy(false);
    }
  });
  $("queue").addEventListener("click", async () => {
    setBusy(true);
    try {
      const notes = ($("notes") as HTMLTextAreaElement).value;
      await client().updateWorkItem(requestPatch(configuration.action, notes), configuration.id, configuration.project);
      addBubble("notice", "Queued on the work item for an agent session with access to the code.");
    } catch (error) {
      addBubble("error", escapeHtml(error instanceof Error ? error.message : String(error)));
    } finally {
      setBusy(false);
    }
  });
  $("close").addEventListener("click", () => configuration.dialog?.close());

  refreshControls();
  try {
    await prepare();
  } catch (error) {
    addBubble("error", `Could not load the work item: ${escapeHtml(error instanceof Error ? error.message : String(error))}`);
  }
}

/**
 * Check the menu pick against the item's level. When it fits, the chat starts
 * straight away; when it does not, say so and offer the actions that do.
 */
async function prepare(): Promise<void> {
  const { id, project } = configuration;
  const [backlogs, workItem] = await Promise.all([
    getClient(WorkRestClient).getBacklogConfigurations({ project, team: SDK.getTeamContext()?.name, projectId: "", teamId: "" }),
    client().getWorkItem(id, project, ["System.WorkItemType", "System.Title"]),
  ]);
  const type = String(workItem.fields["System.WorkItemType"] ?? "");
  $("subject").innerHTML = `<b>${escapeHtml(type)} ${id}</b> ${escapeHtml(String(workItem.fields["System.Title"] ?? ""))}`;
  configuration.levels = levelsFromBacklogs(backlogs);
  configuration.level = levelOf(type, configuration.levels);
  const depth = configuration.levels.length;

  const choose = async (action: Action) => {
    $("picker").hidden = true;
    configuration.action = action;
    configuration.childType = childTypeFor(action, configuration.level, configuration.levels);
    await loadContext();
    ready = true;
    begin();
  };

  const picked = resolveAction(configuration.action, configuration.level, depth);
  if (picked) {
    await choose(picked);
    return;
  }
  const available = actionsForLevel(configuration.level, depth);
  const picker = $("picker");
  picker.hidden = false;
  const wanted = escapeHtml(menuText(configuration.action).toLowerCase());
  if (available.length === 0) {
    picker.innerHTML = `<p>"${wanted}" is not available for a ${escapeHtml(type)}. These actions work on the levels above tasks in your backlog.</p>`;
    return;
  }
  picker.innerHTML = `<p>"${wanted}" does not fit a ${escapeHtml(type)}. Choose one of these instead:</p><div class="choices"></div>`;
  for (const action of available) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = menuText(action, childTypeFor(action, configuration.level, configuration.levels));
    button.addEventListener("click", () => {
      choose(action).catch((error: unknown) => {
        addBubble("error", `Could not load the work item: ${escapeHtml(error instanceof Error ? error.message : String(error))}`);
      });
    });
    picker.querySelector(".choices")!.appendChild(button);
  }
}

void start();
