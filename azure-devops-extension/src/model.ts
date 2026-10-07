// Pure logic for the Etalii Spec Kit backlog menu. Nothing here touches the
// Azure DevOps SDK, so it runs under `node --test`.
//
// Field reference names and stored labels must stay in step with
// extensions/ado/scripts/python/ado.py, which is what agents read them with.

export const FIELDS = {
  specificationState: "Custom.EtaliiSpecificationState",
  agentRequest: "Custom.EtaliiAgentRequest",
  agentRequestNotes: "Custom.EtaliiAgentRequestNotes",
} as const;

export const REQUIRES_FINETUNING = "↻ Requires finetuning by agent";

export type Action =
  | "describe"
  | "subdivide"
  | "decompose"
  | "plan"
  | "regenerate"
  | "refine"
  | "analyse";

/** Value stored in the Agent Request field; agents pick requests up by it. */
export const REQUEST_LABELS: Record<Action, string> = {
  describe: "Describe",
  subdivide: "Subdivide",
  decompose: "Decompose",
  plan: "Plan",
  regenerate: "Regenerate",
  refine: "Refine",
  analyse: "Analyse",
};

export interface BacklogLevel {
  rank?: number;
  workItemTypes?: { name?: string }[];
}

export interface BacklogConfiguration {
  portfolioBacklogs?: BacklogLevel[];
  requirementBacklog?: BacklogLevel;
  taskBacklog?: BacklogLevel;
}

/**
 * Work item type names per level, top level first and tasks last. The team's
 * own backlog configuration is the source, so an Initiative level (or any
 * other depth) shows up without configuring this plugin.
 */
export function levelsFromBacklogs(configuration: BacklogConfiguration): string[][] {
  const portfolio = [...(configuration.portfolioBacklogs ?? [])].sort(
    (a, b) => (b.rank ?? 0) - (a.rank ?? 0),
  );
  return [...portfolio, configuration.requirementBacklog, configuration.taskBacklog]
    .map((level) => (level?.workItemTypes ?? []).map((type) => type.name ?? "").filter(Boolean))
    .filter((names) => names.length > 0);
}

export function levelOf(workItemType: string, levels: string[][]): number {
  const wanted = workItemType.toLowerCase();
  return levels.findIndex((names) => names.some((name) => name.toLowerCase() === wanted));
}

/**
 * Actions offered on an item at `level` in a hierarchy `depth` levels deep.
 * Tasks (the last level) and types outside the hierarchy get none.
 */
export function actionsForLevel(level: number, depth: number): Action[] {
  const last = depth - 1;
  if (level < 0 || level >= last) {
    return [];
  }
  const actions: Action[] = ["describe"];
  if (level + 1 < last) {
    // The level directly above tasks holds stories: a feature is decomposed
    // into them, anything higher is subdivided.
    actions.push(level + 1 === last - 1 ? "decompose" : "subdivide");
  }
  if (level >= last - 2) {
    actions.push("plan");
  }
  actions.push("regenerate", "refine", "analyse");
  return actions;
}

/** Actions every selected item supports, in menu order. */
export function commonActions(itemLevels: number[], depth: number): Action[] {
  if (itemLevels.length === 0) {
    return [];
  }
  const [first, ...rest] = itemLevels.map((level) => actionsForLevel(level, depth));
  return first.filter((action) => rest.every((actions) => actions.includes(action)));
}

export function menuText(action: Action, childType?: string): string {
  switch (action) {
    case "describe":
      return "Generate specification";
    case "subdivide":
      return childType ? `Subdivide into ${plural(childType)}` : "Subdivide";
    case "decompose":
      return childType ? `Decompose into ${plural(childType)}` : "Decompose";
    case "plan":
      return childType ? `Plan into ${plural(childType)}` : "Plan";
    case "regenerate":
      return "Regenerate specification";
    case "refine":
      return "Refine specification";
    case "analyse":
      return "Analyse children for consistency";
  }
}

function plural(typeName: string): string {
  const lower = typeName.toLowerCase();
  return lower.endsWith("y") ? `${lower.slice(0, -1)}ies` : `${lower}s`;
}

/** Name of the type an action creates, for the menu text. */
export function childTypeFor(action: Action, level: number, levels: string[][]): string | undefined {
  if (action === "plan") {
    return levels[levels.length - 1]?.[0];
  }
  if (action === "subdivide" || action === "decompose") {
    return levels[level + 1]?.[0];
  }
  return undefined;
}

/** Work item ids from the differently shaped contexts the menus pass in. */
export function selectedIds(context: unknown): number[] {
  const source = (context ?? {}) as Record<string, unknown>;
  const candidates = [source.workItemIds, source.ids, source.id, source.workItemId].flatMap((value) =>
    Array.isArray(value) ? value : [value],
  );
  const ids = candidates
    .map((value) => (typeof value === "string" ? Number(value) : value))
    .filter((value): value is number => typeof value === "number" && Number.isInteger(value) && value > 0);
  return [...new Set(ids)];
}

export interface PatchOperation {
  op: "add";
  path: string;
  value: string;
}

/**
 * The change that queues a request for an agent. Refining also moves the
 * specification state, so the board shows the item is back with the agent.
 */
export function requestPatch(action: Action, notes: string): PatchOperation[] {
  const patch: PatchOperation[] = [
    { op: "add", path: `/fields/${FIELDS.agentRequest}`, value: REQUEST_LABELS[action] },
    { op: "add", path: `/fields/${FIELDS.agentRequestNotes}`, value: notes.trim() },
  ];
  if (action === "refine") {
    patch.push({ op: "add", path: `/fields/${FIELDS.specificationState}`, value: REQUIRES_FINETUNING });
  }
  return patch;
}
