import assert from "node:assert/strict";
import { test } from "node:test";

import {
  actionsForLevel,
  childTypeFor,
  commonActions,
  levelOf,
  levelsFromBacklogs,
  menuText,
  requestPatch,
  selectedIds,
} from "./model.ts";

const agile = {
  portfolioBacklogs: [
    { rank: 2, workItemTypes: [{ name: "Feature" }] },
    { rank: 3, workItemTypes: [{ name: "Epic" }] },
  ],
  requirementBacklog: { rank: 1, workItemTypes: [{ name: "User Story" }, { name: "Bug" }] },
  taskBacklog: { rank: 0, workItemTypes: [{ name: "Task" }] },
};

test("levels come from the backlog configuration, top level first", () => {
  assert.deepEqual(levelsFromBacklogs(agile), [["Epic"], ["Feature"], ["User Story", "Bug"], ["Task"]]);
});

test("an extra portfolio level is picked up without configuration", () => {
  const withInitiative = {
    ...agile,
    portfolioBacklogs: [...agile.portfolioBacklogs, { rank: 4, workItemTypes: [{ name: "Initiative" }] }],
  };
  const levels = levelsFromBacklogs(withInitiative);
  assert.equal(levels.length, 5);
  assert.equal(levelOf("initiative", levels), 0);
  assert.deepEqual(actionsForLevel(0, 5), ["describe", "subdivide", "regenerate", "refine", "analyse"]);
  assert.equal(childTypeFor("subdivide", 0, levels), "Epic");
});

test("empty and missing backlog levels are skipped", () => {
  assert.deepEqual(levelsFromBacklogs({ taskBacklog: { workItemTypes: [{ name: "Task" }] } }), [["Task"]]);
  assert.deepEqual(levelsFromBacklogs({}), []);
});

test("each level of a four-level hierarchy gets the matching breakdown", () => {
  assert.deepEqual(actionsForLevel(0, 4), ["describe", "subdivide", "regenerate", "refine", "analyse"]);
  assert.deepEqual(actionsForLevel(1, 4), ["describe", "decompose", "plan", "regenerate", "refine", "analyse"]);
  assert.deepEqual(actionsForLevel(2, 4), ["describe", "plan", "regenerate", "refine", "analyse"]);
});

test("tasks and types outside the hierarchy get no actions", () => {
  assert.deepEqual(actionsForLevel(3, 4), []);
  assert.deepEqual(actionsForLevel(-1, 4), []);
  assert.equal(levelOf("Test Case", levelsFromBacklogs(agile)), -1);
});

test("a mixed selection only offers what every item supports", () => {
  assert.deepEqual(commonActions([0, 1], 4), ["describe", "regenerate", "refine", "analyse"]);
  assert.deepEqual(commonActions([1, 3], 4), []);
  assert.deepEqual(commonActions([], 4), []);
});

test("menu text names the level that will be created", () => {
  const levels = levelsFromBacklogs(agile);
  assert.equal(menuText("subdivide", childTypeFor("subdivide", 0, levels)), "Subdivide into features");
  assert.equal(menuText("decompose", childTypeFor("decompose", 1, levels)), "Decompose into user stories");
  assert.equal(menuText("plan", childTypeFor("plan", 1, levels)), "Plan into tasks");
  assert.equal(menuText("plan"), "Plan");
});

test("ids are read from every menu context shape", () => {
  assert.deepEqual(selectedIds({ workItemIds: [3, 4, 3] }), [3, 4]);
  assert.deepEqual(selectedIds({ ids: [7] }), [7]);
  assert.deepEqual(selectedIds({ id: 9, workItemType: "Epic" }), [9]);
  assert.deepEqual(selectedIds({ workItemId: "12" }), [12]);
});

test("contexts without usable ids yield nothing", () => {
  assert.deepEqual(selectedIds(undefined), []);
  assert.deepEqual(selectedIds({ id: 0 }), []);
  assert.deepEqual(selectedIds({ ids: ["abc", -1, 1.5, null] }), []);
});

test("a request stores the action and trimmed notes", () => {
  assert.deepEqual(requestPatch("plan", "  two tasks  "), [
    { op: "add", path: "/fields/Custom.EtaliiAgentRequest", value: "Plan" },
    { op: "add", path: "/fields/Custom.EtaliiAgentRequestNotes", value: "two tasks" },
  ]);
});

test("only refine sends the specification back to the agent", () => {
  const states = (action: Parameters<typeof requestPatch>[0]) =>
    requestPatch(action, "").filter((operation) => operation.path.endsWith("EtaliiSpecificationState"));
  assert.deepEqual(states("refine").map((operation) => operation.value), ["Requires finetuning by agent"]);
  for (const action of ["describe", "subdivide", "decompose", "plan", "regenerate", "analyse"] as const) {
    assert.deepEqual(states(action), []);
  }
});
