// Pure logic for the chat panel: what is sent to the model, how its answer is
// read, and how a proposal turns into Azure DevOps changes. Nothing here
// touches the Azure DevOps SDK or the network, so it runs under `node --test`.
//
// Field names and stored labels must stay in step with
// extensions/ado/scripts/python/ado.py (checked by tests/extensions/ado).

import type { Action } from "./model.ts";

export const F = {
  ask: "Custom.EtaliiAsk",
  specificationState: "Custom.EtaliiSpecificationState",
  specification: "Custom.EtaliiSpecification",
} as const;

export const ASK_LABELS = { human: "\u{1F464} Human", agent: "\u{1F916} Agent" } as const;
export const STATE_LABELS = {
  workedOn: "⚙ Worked on by agent",
  readyForReview: "\u{1F441} Ready for review by user",
} as const;

export const LINKS = {
  parent: "System.LinkTypes.Hierarchy-Reverse",
  child: "System.LinkTypes.Hierarchy-Forward",
  predecessor: "System.LinkTypes.Dependency-Reverse",
  related: "System.LinkTypes.Related",
} as const;

// -- Model settings -----------------------------------------------------------

export type ProviderId = "anthropic" | "openai-compatible";

export interface ModelSettings {
  provider: ProviderId;
  model: string;
  apiKey: string;
  /** Only for openai-compatible providers, e.g. https://api.openai.com/v1 */
  baseUrl: string;
}

export const DEFAULT_SETTINGS: ModelSettings = {
  provider: "anthropic",
  model: "claude-opus-5-5",
  apiKey: "",
  baseUrl: "",
};

export function normalizeSettings(stored: Partial<ModelSettings> | null | undefined): ModelSettings {
  const provider: ProviderId = stored?.provider === "openai-compatible" ? "openai-compatible" : "anthropic";
  return {
    provider,
    model: (stored?.model ?? "").trim() || (provider === "anthropic" ? DEFAULT_SETTINGS.model : ""),
    apiKey: (stored?.apiKey ?? "").trim(),
    baseUrl: (stored?.baseUrl ?? "").trim().replace(/\/+$/, ""),
  };
}

/** Why these settings cannot be used yet, or undefined when they can. */
export function settingsProblem(settings: ModelSettings): string | undefined {
  if (!settings.apiKey) {
    return "Enter an API key.";
  }
  if (!settings.model) {
    return "Enter a model name.";
  }
  if (settings.provider === "openai-compatible") {
    if (!settings.baseUrl) {
      return "Enter the base URL of the provider, for example https://api.openai.com/v1.";
    }
    if (!/^https:\/\//i.test(settings.baseUrl)) {
      return "The base URL must start with https://.";
    }
  }
  return undefined;
}

// -- Conversation context -----------------------------------------------------

export interface ItemContext {
  id: number;
  type: string;
  title: string;
  specification: string;
  specificationState: string;
}

export interface ChatContext {
  action: Action;
  item: ItemContext;
  parent?: ItemContext;
  children: ItemContext[];
  /** Work item type names per level, top level first and tasks last. */
  levels: string[][];
  /** Level index of the item. */
  level: number;
  /** Type that subdivide, decompose or plan creates. */
  childType?: string;
}

type LevelKind = "portfolio" | "story" | "task";

const GUIDE: Record<LevelKind, string> = {
  portfolio: [
    "## Goal",
    "One paragraph: the outcome and who benefits.",
    "",
    "## Scope",
    "**In scope**",
    "- ...",
    "",
    "**Out of scope**",
    "- ...",
    "",
    "## Requirements",
    "- **R1**: A testable statement.",
    "",
    "## Success criteria",
    "- **S1**: A measurable, technology-agnostic outcome.",
    "",
    "## Assumptions and dependencies",
    "- ...",
    "",
    "## Open questions",
    "- [NEEDS CLARIFICATION: ...]",
  ].join("\n"),
  story: [
    "## Story",
    "As a <role> I want <capability> so that <benefit>.",
    "",
    "## Acceptance scenarios",
    "1. **Given** <state>, **when** <action>, **then** <outcome>.",
    "",
    "## Requirements",
    "- **R1**: A testable statement. Trace: parent R<n>.",
    "",
    "## Edge cases",
    "- ...",
    "",
    "## Open questions",
    "- [NEEDS CLARIFICATION: ...]",
  ].join("\n"),
  task: [
    "## Objective",
    "What this task delivers, in one or two sentences. Trace: story R<n>.",
    "",
    "## Where",
    "Files, modules or systems this task touches.",
    "",
    "## Steps",
    "1. ...",
    "",
    "## Definition of done",
    "- [ ] A check that can be run or observed.",
  ].join("\n"),
};

function kindOfLevel(level: number, depth: number): LevelKind {
  if (level >= depth - 1) {
    return "task";
  }
  return level === depth - 2 ? "story" : "portfolio";
}

export type ProposalKind = "specification" | "children" | "analysis";

export function proposalKind(action: Action): ProposalKind {
  if (action === "subdivide" || action === "decompose" || action === "plan") {
    return "children";
  }
  return action === "analyse" ? "analysis" : "specification";
}

const TASKS: Record<Action, string> = {
  describe: "Write the specification of this work item.",
  regenerate:
    "Write a fresh specification for this work item. Do not carry over wording from the current text, but keep every decision the user made in it and list those under a final section named Decisions kept.",
  refine:
    "Improve the current specification from the user's feedback with the smallest change that satisfies it. Keep the structure and the wording of sections the feedback does not touch. If the user has not yet said what should change, ask before proposing anything.",
  subdivide: "Break this work item into items of the next level down.",
  decompose: "Break this work item into stories.",
  plan: "Break this work item into tasks that a person or an agent can pick up one at a time.",
  analyse:
    "Check whether the children are consistent with this work item and with each other: coverage of every requirement, scope creep, contradictions, overlap, sequencing, and items that need a human decision but are marked for an agent.",
};

const OUTPUT: Record<ProposalKind, string> = {
  specification: '{"specification": "<the full markdown specification>"}',
  children:
    '{"children": [{"key": "a", "title": "...", "ask": "agent", "specification": "<markdown>", "depends_on": [], "related": []}]}',
  analysis:
    '{"findings": [{"id": 123, "severity": "high", "finding": "...", "action": "..."}]}',
};

/** Instructions for the model: the role, the rules and the answer format. */
export function systemPrompt(context: ChatContext): string {
  const kind = proposalKind(context.action);
  const depth = context.levels.length;
  const lines = [
    "You help a software team do spec-driven development inside Azure DevOps. Specifications are markdown stored on work items. You are talking with a team member in a chat panel; they will read your proposal, may ask for changes, and then apply it to Azure DevOps with a button.",
    "",
    `Hierarchy, top level first: ${context.levels.map((names) => names[0]).join(" > ")}.`,
    "",
    `Your task: ${TASKS[context.action]}`,
    "",
    "Rules:",
    "- Stay inside the scope of this work item and its parent. Do not invent requirements, people, dates or decisions.",
    "- Mark what you cannot determine as [NEEDS CLARIFICATION: question], at most three per item, and mention those in your message so the user can answer them.",
    "- Each requirement of a child traces to a requirement of its parent. Do not restate the parent; refer to it by work item id.",
    "- You cannot see the source code. When a plan needs facts about the code that you do not have, say so instead of guessing file names.",
  ];
  if (kind === "specification") {
    lines.push("", "Use these sections, in this order, and write None where a section has nothing in it:", "", GUIDE[kindOfLevel(context.level, depth)]);
  }
  if (kind === "children") {
    const childLevel = context.action === "plan" ? depth - 1 : context.level + 1;
    lines.push(
      "",
      `Create ${context.childType ?? "child"} items. Together they cover the parent without overlapping, and each is traceable to a statement in the parent specification.`,
      "- key: a short unique identifier within this proposal.",
      "- ask: agent when an agent can specify and deliver it, human when it needs a person (a decision, access, a conversation with a stakeholder).",
      "- depends_on: keys of the siblings that must be finished first. This becomes predecessor/successor links and is what a delivery plan is ordered by, so include every real dependency and no others.",
      "- related: keys of siblings that touch the same area without ordering.",
      "- Reuse the exact title of an existing child you want to keep; it is matched on title and not duplicated.",
      "",
      "Each child specification uses these sections:",
      "",
      GUIDE[kindOfLevel(childLevel, depth)],
    );
  }
  if (kind === "analysis") {
    lines.push("", "Report one finding per problem. severity is one of critical, high, medium, low. id is the work item the finding is about. An empty findings list means the children are consistent.");
  }
  lines.push(
    "",
    "Answer format: first a short message to the user in plain prose (what you propose and anything you need from them). Then, when you have a proposal, exactly one fenced code block tagged json containing:",
    "",
    OUTPUT[kind],
    "",
    "Whenever the user asks for a change, answer with the complete updated proposal in the same format, not a diff. When you only have a question, leave the json block out.",
  );
  return lines.join("\n");
}

function describeItem(label: string, item: ItemContext): string {
  return [
    `### ${label}: ${item.type} ${item.id} - ${item.title}`,
    `Specification state: ${item.specificationState || "not set"}`,
    "",
    item.specification.trim() || "(no specification yet)",
  ].join("\n");
}

/** The first user message: the work item, its surroundings and the notes. */
export function openingMessage(context: ChatContext, notes: string): string {
  const parts = [describeItem("Work item", context.item)];
  if (context.parent) {
    parts.push(describeItem("Parent", context.parent));
  }
  if (context.children.length > 0) {
    parts.push(...context.children.map((child) => describeItem("Existing child", child)));
  } else if (proposalKind(context.action) !== "specification") {
    parts.push("### Existing children\nNone.");
  }
  if (notes.trim()) {
    parts.push(`### Instructions from the user\n${notes.trim()}`);
  }
  return parts.join("\n\n");
}

// -- Reading the model's answer -----------------------------------------------

export interface PlannedChild {
  key: string;
  title: string;
  ask: "human" | "agent";
  specification: string;
  depends_on: string[];
  related: string[];
}

export interface Finding {
  id: number;
  severity: string;
  finding: string;
  action: string;
}

export type Proposal =
  | { kind: "specification"; specification: string }
  | { kind: "children"; children: PlannedChild[] }
  | { kind: "analysis"; findings: Finding[] };

export interface ParsedAnswer {
  /** The prose around the proposal, for the chat transcript. */
  message: string;
  proposal?: Proposal;
  /** Set when a json block was present but could not be used. */
  problem?: string;
}

const JSON_BLOCK = /```json\s*\n([\s\S]*?)\n```/g;

export function parseAnswer(text: string, action: Action): ParsedAnswer {
  const blocks = [...text.matchAll(JSON_BLOCK)];
  const message = text.replace(JSON_BLOCK, "").replace(/\n{3,}/g, "\n\n").trim();
  if (blocks.length === 0) {
    return { message };
  }
  let data: unknown;
  try {
    data = JSON.parse(blocks[blocks.length - 1][1]);
  } catch (error) {
    return { message, problem: `The proposal was not valid JSON (${(error as Error).message}). Ask the model to send it again.` };
  }
  const record = (data ?? {}) as Record<string, unknown>;
  const kind = proposalKind(action);
  if (kind === "specification") {
    if (typeof record.specification !== "string" || !record.specification.trim()) {
      return { message, problem: "The proposal has no specification text." };
    }
    return { message, proposal: { kind, specification: record.specification.trim() } };
  }
  if (kind === "children") {
    const result = readChildren(record.children);
    return typeof result === "string" ? { message, problem: result } : { message, proposal: { kind, children: result } };
  }
  if (!Array.isArray(record.findings)) {
    return { message, problem: "The proposal has no findings list." };
  }
  const findings = record.findings.map((entry) => {
    const finding = (entry ?? {}) as Record<string, unknown>;
    return {
      id: Number(finding.id) || 0,
      severity: String(finding.severity ?? "medium"),
      finding: String(finding.finding ?? ""),
      action: String(finding.action ?? ""),
    };
  });
  return { message, proposal: { kind, findings: findings.filter((finding) => finding.finding.trim()) } };
}

function stringList(value: unknown): string[] | undefined {
  if (value === undefined || value === null) {
    return [];
  }
  return Array.isArray(value) && value.every((entry) => typeof entry === "string") ? (value as string[]) : undefined;
}

/** Validated children, or a sentence saying what is wrong with the plan. */
export function readChildren(value: unknown): PlannedChild[] | string {
  if (!Array.isArray(value) || value.length === 0) {
    return "The proposal has no children.";
  }
  const children: PlannedChild[] = [];
  const keys = new Set<string>();
  const titles = new Set<string>();
  for (const entry of value) {
    const child = (entry ?? {}) as Record<string, unknown>;
    const key = typeof child.key === "string" ? child.key.trim() : "";
    const title = typeof child.title === "string" ? child.title.trim() : "";
    if (!key) {
      return "A child has no key.";
    }
    if (!title) {
      return `Child ${key} has no title.`;
    }
    if (keys.has(key)) {
      return `The key ${key} is used twice.`;
    }
    if (titles.has(title.toLowerCase())) {
      return `Two children share the title "${title}".`;
    }
    if (typeof child.specification !== "string" || !child.specification.trim()) {
      return `Child ${key} has no specification.`;
    }
    const ask = child.ask === undefined ? "agent" : child.ask;
    if (ask !== "human" && ask !== "agent") {
      return `Child ${key} has ask "${String(child.ask)}"; it must be human or agent.`;
    }
    const dependsOn = stringList(child.depends_on);
    const related = stringList(child.related);
    if (!dependsOn || !related) {
      return `Child ${key} has a depends_on or related value that is not a list of keys.`;
    }
    keys.add(key);
    titles.add(title.toLowerCase());
    children.push({ key, title, ask, specification: child.specification.trim(), depends_on: dependsOn, related });
  }
  for (const child of children) {
    for (const target of [...child.depends_on, ...child.related]) {
      if (!keys.has(target)) {
        return `Child ${child.key} refers to ${target}, which is not in the proposal.`;
      }
      if (target === child.key) {
        return `Child ${child.key} refers to itself.`;
      }
    }
  }
  const cycle = findCycle(new Map(children.map((child) => [child.key, child.depends_on])));
  return cycle ? `The dependencies form a cycle: ${cycle.join(" -> ")}.` : children;
}

/** One dependency cycle as a path of keys, or undefined when there is none. */
export function findCycle(graph: Map<string, string[]>): string[] | undefined {
  const done = new Set<string>();
  const path: string[] = [];
  const visit = (node: string): string[] | undefined => {
    const at = path.indexOf(node);
    if (at >= 0) {
      return [...path.slice(at), node];
    }
    if (done.has(node)) {
      return undefined;
    }
    path.push(node);
    for (const next of graph.get(node) ?? []) {
      const cycle = visit(next);
      if (cycle) {
        return cycle;
      }
    }
    path.pop();
    done.add(node);
    return undefined;
  };
  for (const node of graph.keys()) {
    const cycle = visit(node);
    if (cycle) {
      return cycle;
    }
  }
  return undefined;
}

// -- Turning a proposal into Azure DevOps changes -----------------------------

export interface PatchOperation {
  op: "add";
  path: string;
  value: unknown;
}

const field = (name: string, value: string): PatchOperation => ({ op: "add", path: `/fields/${name}`, value });
const markdownFormat: PatchOperation = { op: "add", path: `/multilineFieldsFormat/${F.specification}`, value: "Markdown" };
const relation = (rel: string, url: string): PatchOperation => ({ op: "add", path: "/relations/-", value: { rel, url } });

/**
 * The two updates that replace a specification. The first marks the item as
 * being worked on, the second writes the text and hands it back for review, so
 * the history shows the same state trail as an agent session would leave.
 */
export function specificationPatches(markdown: string): [PatchOperation[], PatchOperation[]] {
  return [
    [field(F.specificationState, STATE_LABELS.workedOn)],
    [markdownFormat, field(F.specification, markdown), field(F.specificationState, STATE_LABELS.readyForReview)],
  ];
}

export function childPatch(child: PlannedChild, parent: { url: string; areaPath?: string; iterationPath?: string }): PatchOperation[] {
  const patch = [
    field("System.Title", child.title),
    field(F.ask, ASK_LABELS[child.ask]),
    field(F.specificationState, STATE_LABELS.readyForReview),
    markdownFormat,
    field(F.specification, child.specification),
    relation(LINKS.parent, parent.url),
  ];
  if (parent.areaPath) {
    patch.push(field("System.AreaPath", parent.areaPath));
  }
  if (parent.iterationPath) {
    patch.push(field("System.IterationPath", parent.iterationPath));
  }
  return patch;
}

export interface ExistingLinks {
  predecessors: Set<string>;
  related: Set<string>;
}

/** Link updates for one child, skipping links that already exist. */
export function linkPatch(child: PlannedChild, urls: Map<string, string>, existing: ExistingLinks): PatchOperation[] {
  const patch: PatchOperation[] = [];
  for (const key of child.depends_on) {
    const url = urls.get(key);
    if (url && !existing.predecessors.has(url.toLowerCase())) {
      patch.push(relation(LINKS.predecessor, url));
    }
  }
  for (const key of child.related) {
    const url = urls.get(key);
    if (url && !existing.related.has(url.toLowerCase())) {
      patch.push(relation(LINKS.related, url));
    }
  }
  return patch;
}

/** The analysis as an HTML discussion comment. */
export function analysisComment(findings: Finding[]): PatchOperation[] {
  const body =
    findings.length === 0
      ? "<p>Etalii Spec Kit analysis: the children are consistent with this item.</p>"
      : `<p>Etalii Spec Kit analysis:</p><ul>${findings
          .map(
            (finding) =>
              `<li><b>${escapeHtml(finding.severity)}</b>${finding.id ? ` (#${finding.id})` : ""}: ${escapeHtml(finding.finding)}${
                finding.action ? ` <i>Suggested: ${escapeHtml(finding.action)}</i>` : ""
              }</li>`,
          )
          .join("")}</ul>`;
  return [field("System.History", body)];
}

// -- Rendering ----------------------------------------------------------------

export function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/**
 * A small, safe markdown renderer for the chat: headings, lists, bold, italic
 * and inline code. Everything is escaped first, so model output can never
 * inject markup.
 */
export function renderMarkdown(markdown: string): string {
  const inline = (text: string) =>
    escapeHtml(text)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>");
  const html: string[] = [];
  let list: "ul" | "ol" | undefined;
  const closeList = () => {
    if (list) {
      html.push(`</${list}>`);
      list = undefined;
    }
  };
  for (const line of markdown.split("\n")) {
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+\.\s+(.*)$/.exec(line);
    if (heading) {
      closeList();
      html.push(`<h${heading[1].length + 2}>${inline(heading[2])}</h${heading[1].length + 2}>`);
    } else if (bullet || numbered) {
      const wanted = bullet ? "ul" : "ol";
      if (list !== wanted) {
        closeList();
        html.push(`<${wanted}>`);
        list = wanted;
      }
      html.push(`<li>${inline((bullet ?? numbered)![1])}</li>`);
    } else if (line.trim() === "") {
      closeList();
    } else {
      closeList();
      html.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  return html.join("");
}
