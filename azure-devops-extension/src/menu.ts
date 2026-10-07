// Adds an "Etalii Spec Kit" group to the work item menus on the backlog, the
// board and the work item form. Picking an action opens a chat panel in which
// a model proposes the result; the user applies it to Azure DevOps from there.

import * as SDK from "azure-devops-extension-sdk";
import { getClient, type IHostPageLayoutService, type IProjectPageService } from "azure-devops-extension-api";
import { WorkRestClient } from "azure-devops-extension-api/Work";
import { WorkItemTrackingRestClient } from "azure-devops-extension-api/WorkItemTracking";

import { type Action, actionsForLevel, childTypeFor, levelOf, levelsFromBacklogs, menuText, selectedIds } from "./model";

// CommonServiceIds is a const enum, which has no runtime value to import.
const SERVICES = {
  project: "ms.vss-tfs-web.tfs-page-data-service",
  layout: "ms.vss-features.host-page-layout-service",
} as const;
const PANEL_SIZE_LARGE = 2;

interface MenuItem {
  id: string;
  text: string;
  title?: string;
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

async function openChat(action: Action, id: number, project: string, levels: string[][], level: number): Promise<void> {
  const layout = await SDK.getService<IHostPageLayoutService>(SERVICES.layout);
  const childType = childTypeFor(action, level, levels);
  layout.openPanel<void>(`${SDK.getExtensionContext().id}.chat-panel`, {
    title: `Etalii Spec Kit: ${menuText(action, childType)}`,
    size: PANEL_SIZE_LARGE,
    configuration: { action, id, project, levels, level, childType },
  });
}

async function menuItems(context: unknown): Promise<MenuItem[]> {
  const ids = selectedIds(context);
  // The chat is about one work item, so a multi-selection gets no menu.
  if (ids.length !== 1) {
    return [];
  }
  const [id] = ids;
  const project = await projectName();
  const [levels, item] = await Promise.all([
    hierarchy(project),
    getClient(WorkItemTrackingRestClient).getWorkItem(id, project, ["System.WorkItemType"]),
  ]);
  const level = levelOf(String(item.fields["System.WorkItemType"] ?? ""), levels);
  const actions = actionsForLevel(level, levels.length);
  if (actions.length === 0) {
    return [];
  }
  return [
    {
      id: "etalii-spec-kit",
      text: "Etalii Spec Kit",
      title: "Work on the specification with a model",
      childItems: actions.map((action) => ({
        id: `etalii-spec-kit-${action}`,
        text: menuText(action, childTypeFor(action, level, levels)),
        action: () => void openChat(action, id, project, levels, level),
      })),
    },
  ];
}

SDK.register("etalii-spec-kit-menu", {
  getMenuItems: (context: unknown) => menuItems(context).catch(() => []),
});
void SDK.init();
