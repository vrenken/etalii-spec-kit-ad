// Adds one "Etalii Spec Kit" entry to the work item menus on the backlog, the
// board and the work item form. It opens the chat panel for the selected work
// item; the panel offers the actions that fit that item's level.
//
// The entry is a plain menu action declared in the manifest, so it shows up
// without any code running first. Everything that can fail happens after the
// click, where the failure can be shown.

import * as SDK from "azure-devops-extension-sdk";
import type { IHostPageLayoutService, IProjectPageService } from "azure-devops-extension-api";

import { selectedIds } from "./model";

// CommonServiceIds is a const enum, which has no runtime value to import.
const SERVICES = {
  project: "ms.vss-tfs-web.tfs-page-data-service",
  layout: "ms.vss-features.host-page-layout-service",
} as const;
const PANEL_SIZE_LARGE = 2;

async function open(context: unknown): Promise<void> {
  const layout = await SDK.getService<IHostPageLayoutService>(SERVICES.layout);
  try {
    const ids = selectedIds(context);
    if (ids.length !== 1) {
      layout.openMessageDialog(
        ids.length === 0 ? "No work item was selected." : "Select a single work item; the chat is about one item at a time.",
        { title: "Etalii Spec Kit", showCancel: false },
      );
      return;
    }
    const projects = await SDK.getService<IProjectPageService>(SERVICES.project);
    const project = await projects.getProject();
    if (!project) {
      throw new Error("No project in context.");
    }
    layout.openPanel<void>(`${SDK.getExtensionContext().id}.chat-panel`, {
      title: "Etalii Spec Kit",
      size: PANEL_SIZE_LARGE,
      configuration: { id: ids[0], project: project.name },
    });
  } catch (error) {
    layout.openMessageDialog(`Could not open the chat: ${error instanceof Error ? error.message : String(error)}`, {
      title: "Etalii Spec Kit",
      showCancel: false,
    });
  }
}

SDK.register("etalii-spec-kit-menu", () => ({
  execute: (context: unknown) => void open(context),
}));
void SDK.init();
