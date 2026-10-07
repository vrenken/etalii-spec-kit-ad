// Handles the "Etalii Spec Kit" entries on the work item menus of the backlog,
// the board and the work item form. Each entry is a plain menu action declared
// in the manifest, so it shows up without any code running first; clicking one
// opens the chat dialog for the selected work item with that action.
//
// The entries are the same for every work item type, because a declared menu
// action cannot look at the item. The dialog checks whether the action fits
// the item's level and offers the ones that do when it does not.

import * as SDK from "azure-devops-extension-sdk";
import type { IHostPageLayoutService, IProjectPageService } from "azure-devops-extension-api";

import { type Action, MENU_ACTIONS, menuText, selectedIds } from "./model";

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

for (const action of MENU_ACTIONS) {
  SDK.register(`etalii-spec-kit-${action}`, () => ({
    execute: (context: unknown) => void open(action, context),
  }));
}
void SDK.init();
