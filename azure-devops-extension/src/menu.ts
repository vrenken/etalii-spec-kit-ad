// Handles the "Etalii Spec Kit" entries on the work item menus of the backlog,
// the board and the work item form. Each entry is a plain menu action declared
// in the manifest, so it shows up without any code running first; clicking one
// opens the chat dialog for the selected work item with that action.
//
// The entries are the same for every work item type, because a declared menu
// action cannot look at the item. The dialog checks whether the action fits
// the item's level and offers the ones that do when it does not.

import * as SDK from "azure-devops-extension-sdk";
import { getClient, type IHostPageLayoutService, type IProjectPageService } from "azure-devops-extension-api";
import { WorkRestClient } from "azure-devops-extension-api/Work";
import { WorkItemTrackingRestClient } from "azure-devops-extension-api/WorkItemTracking";

import { type Action, actionsForLevel, childTypeFor, levelOf, levelsFromBacklogs, MENU_ACTIONS, menuText, selectedIds } from "./model";

// CommonServiceIds is a const enum, which has no runtime value to import.
const SERVICES = {
  project: "ms.vss-tfs-web.tfs-page-data-service",
  layout: "ms.vss-features.host-page-layout-service",
} as const;

async function open(action: Action, context: unknown): Promise<void> {
  const layout = await SDK.getService<IHostPageLayoutService>(SERVICES.layout);
  const title = `Etalii Spec Kit: ${menuText(action)}`;
  try {
    const ids = selectedIds(context);
    if (ids.length !== 1) {
      layout.openMessageDialog(
        ids.length === 0 ? "No work item was selected." : "Select a single work item; the chat is about one item at a time.",
        { title, showCancel: false },
      );
      return;
    }
    const projects = await SDK.getService<IProjectPageService>(SERVICES.project);
    const project = await projects.getProject();
    if (!project) {
      throw new Error("No project in context.");
    }
    layout.openCustomDialog<void>(`${SDK.getExtensionContext().id}.chat-panel`, {
      title,
      configuration: { action, id: ids[0], project: project.name },
    });
  } catch (error) {
    layout.openMessageDialog(`Could not open the chat: ${error instanceof Error ? error.message : String(error)}`, {
      title,
      showCancel: false,
    });
  }
}

interface MenuItem {
  id: string;
  text: string;
  title?: string;
  childItems?: MenuItem[];
  action?: () => void;
}

/** The actions that fit the selected item, named after its child level. */
async function fittingItems(context: unknown): Promise<MenuItem[]> {
  const ids = selectedIds(context);
  if (ids.length !== 1) {
    return [];
  }
  const projects = await SDK.getService<IProjectPageService>(SERVICES.project);
  const project = (await projects.getProject())?.name;
  if (!project) {
    throw new Error("No project in context.");
  }
  const [backlogs, item] = await Promise.all([
    getClient(WorkRestClient).getBacklogConfigurations({ project, team: SDK.getTeamContext()?.name, projectId: "", teamId: "" }),
    getClient(WorkItemTrackingRestClient).getWorkItem(ids[0], project, ["System.WorkItemType"]),
  ]);
  const levels = levelsFromBacklogs(backlogs);
  const level = levelOf(String(item.fields["System.WorkItemType"] ?? ""), levels);
  return actionsForLevel(level, levels.length).map((action) => ({
    id: `etalii-spec-kit-sub-${action}`,
    text: menuText(action, childTypeFor(action, level, levels)),
    action: () => void open(action, context),
  }));
}

/**
 * A submenu whose entries depend on the item. The lookups must never leave
 * the menu empty or hanging: when they fail or take too long, every action is
 * offered under its generic name and the dialog sorts out which one fits.
 */
async function submenu(context: unknown): Promise<MenuItem[]> {
  const generic = (): MenuItem[] =>
    MENU_ACTIONS.map((action) => ({
      id: `etalii-spec-kit-sub-${action}`,
      text: menuText(action),
      action: () => void open(action, context),
    }));
  const timeout = new Promise<MenuItem[]>((resolve) => setTimeout(() => resolve(generic()), 2500));
  const children = await Promise.race([fittingItems(context).catch(generic), timeout]);
  if (children.length === 0) {
    return []; // a task, another type outside the hierarchy, or a multi-selection
  }
  return [{ id: "etalii-spec-kit", text: "Etalii Spec Kit", title: "Work on this item's specification with a model", childItems: children }];
}

SDK.register("etalii-spec-kit-submenu", { getMenuItems: (context: unknown) => submenu(context) });

for (const action of MENU_ACTIONS) {
  SDK.register(`etalii-spec-kit-${action}`, () => ({
    execute: (context: unknown) => void open(action, context),
  }));
}
void SDK.init();
