// Adds an "Etalii Spec Kit" group to the work item menus on the backlog, the
// board and the work item form. Picking an action does not run an agent in
// the browser: it stores the request on the work item, where an agent session
// running `speckit.ado.requests` picks it up.

import * as SDK from "azure-devops-extension-sdk";
import {
  getClient,
  type IGlobalMessagesService,
  type IHostPageLayoutService,
  type IProjectPageService,
} from "azure-devops-extension-api";
import { WorkRestClient } from "azure-devops-extension-api/Work";
import { WorkItemTrackingRestClient } from "azure-devops-extension-api/WorkItemTracking";

import {
  type Action,
  childTypeFor,
  commonActions,
  levelOf,
  levelsFromBacklogs,
  menuText,
  requestPatch,
  selectedIds,
} from "./model";

// CommonServiceIds is a const enum, which has no runtime value to import.
const SERVICES = {
  project: "ms.vss-tfs-web.tfs-page-data-service",
  layout: "ms.vss-features.host-page-layout-service",
  messages: "ms.vss-tfs-web.tfs-global-messages-service",
} as const;

interface MenuItem {
  id: string;
  text: string;
  title?: string;
  disabled?: boolean;
  childItems?: MenuItem[];
  action?: () => void;
}

async function projectName(): Promise<string> {
  const service = await SDK.getService<IProjectPageService>(SERVICES.project);
  const project = await service.getProject();
  if (!project) {
    throw new Error("No project in context.");
  }
  return project.name;
}

async function hierarchy(project: string): Promise<string[][]> {
  const team = SDK.getTeamContext()?.name;
  const configuration = await getClient(WorkRestClient).getBacklogConfigurations({
    project,
    team,
    projectId: "",
    teamId: "",
  });
  return levelsFromBacklogs(configuration);
}

async function askForNotes(action: Action, count: number): Promise<string | undefined> {
  const layout = await SDK.getService<IHostPageLayoutService>(SERVICES.layout);
  const extension = SDK.getExtensionContext();
  return new Promise((resolve) => {
    layout.openCustomDialog<string | undefined>(`${extension.id}.request-dialog`, {
      title: `${menuText(action)}: ${count === 1 ? "1 item" : `${count} items`}`,
      configuration: { action },
      onClose: (result) => resolve(result),
    });
  });
}

async function queue(action: Action, ids: number[], project: string): Promise<void> {
  const notes = await askForNotes(action, ids.length);
  if (notes === undefined) {
    return; // cancelled
  }
  const client = getClient(WorkItemTrackingRestClient);
  const messages = await SDK.getService<IGlobalMessagesService>(SERVICES.messages);
  const failed: number[] = [];
  for (const id of ids) {
    try {
      await client.updateWorkItem(requestPatch(action, notes), id, project);
    } catch {
      failed.push(id);
    }
  }
  const queued = ids.length - failed.length;
  messages.addToast({
    duration: 6000,
    message: failed.length
      ? `Queued ${queued} of ${ids.length}. Could not update: ${failed.join(", ")}. Are the Etalii fields provisioned?`
      : `Queued for an agent: ${menuText(action).toLowerCase()} (${queued}).`,
  });
}

async function menuItems(context: unknown): Promise<MenuItem[]> {
  const ids = selectedIds(context);
  if (ids.length === 0) {
    return [];
  }
  const project = await projectName();
  const [levels, items] = await Promise.all([
    hierarchy(project),
    getClient(WorkItemTrackingRestClient).getWorkItems(ids, project, ["System.WorkItemType"]),
  ]);
  const itemLevels = items.map((item) => levelOf(String(item.fields["System.WorkItemType"] ?? ""), levels));
  const actions = commonActions(itemLevels, levels.length);
  if (actions.length === 0) {
    return [];
  }
  return [
    {
      id: "etalii-spec-kit",
      text: "Etalii Spec Kit",
      title: "Ask an agent to work on the specification",
      childItems: actions.map((action) => ({
        id: `etalii-spec-kit-${action}`,
        text: menuText(action, childTypeFor(action, itemLevels[0], levels)),
        action: () => void queue(action, ids, project),
      })),
    },
  ];
}

SDK.register("etalii-spec-kit-menu", {
  getMenuItems: (context: unknown) => menuItems(context).catch(() => []),
});
void SDK.init();
