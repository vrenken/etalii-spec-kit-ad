import assert from "node:assert/strict";
import { test } from "node:test";

import {
  analysisComment,
  childPatch,
  type ChatContext,
  findCycle,
  linkPatch,
  normalizeSettings,
  openingMessage,
  parseAnswer,
  readChildren,
  renderMarkdown,
  settingsProblem,
  specificationPatches,
  systemPrompt,
} from "./chat-model.ts";

const levels = [["Epic"], ["Feature"], ["User Story"], ["Task"]];
const item = { id: 471, type: "Feature", title: "Invoice download", specification: "## Goal\nDownload invoices.", specificationState: "Approved by user" };

function context(action: ChatContext["action"], overrides: Partial<ChatContext> = {}): ChatContext {
  return { action, item, children: [], levels, level: 1, ...overrides };
}

const fenced = (value: unknown) => "```json\n" + JSON.stringify(value, null, 2) + "\n```";

// -- settings

test("settings default to Claude and need only a key", () => {
  const settings = normalizeSettings(undefined);
  assert.deepEqual(settings, { provider: "anthropic", model: "claude-opus-5-5", apiKey: "", baseUrl: "" });
  assert.equal(settingsProblem(settings), "Enter an API key.");
  assert.equal(settingsProblem({ ...settings, apiKey: "k" }), undefined);
});

test("stored settings are trimmed and unknown providers fall back", () => {
  const settings = normalizeSettings({ provider: "weird" as never, model: " m ", apiKey: " k ", baseUrl: " https://x/v1// " });
  assert.deepEqual(settings, { provider: "anthropic", model: "m", apiKey: "k", baseUrl: "https://x/v1" });
});

test("another provider needs a model and an https base URL", () => {
  const base = normalizeSettings({ provider: "openai-compatible", apiKey: "k" });
  assert.equal(base.model, "");
  assert.equal(settingsProblem(base), "Enter a model name.");
  assert.match(settingsProblem({ ...base, model: "m" })!, /base URL/);
  assert.match(settingsProblem({ ...base, model: "m", baseUrl: "http://insecure/v1" })!, /https:\/\//);
  assert.equal(settingsProblem({ ...base, model: "m", baseUrl: "https://api.example/v1" }), undefined);
});

// -- prompts

test("the system prompt carries the hierarchy, the task and the answer format", () => {
  const prompt = systemPrompt(context("decompose", { childType: "User Story" }));
  assert.match(prompt, /Epic > Feature > User Story > Task/);
  assert.match(prompt, /Break this work item into stories/);
  assert.match(prompt, /Create User Story items/);
  assert.match(prompt, /## Acceptance scenarios/);
  assert.match(prompt, /"children": \[/);
});

test("the specification guide follows the level being written", () => {
  assert.match(systemPrompt(context("describe")), /## Success criteria/);
  assert.match(systemPrompt(context("describe", { level: 2 })), /## Acceptance scenarios/);
  assert.match(systemPrompt(context("plan", { childType: "Task" })), /## Definition of done/);
  assert.match(systemPrompt(context("subdivide", { level: 0, childType: "Feature" })), /## Success criteria/);
});

test("analysis asks for findings and refine asks before guessing", () => {
  assert.match(systemPrompt(context("analyse")), /"findings": \[/);
  assert.match(systemPrompt(context("refine")), /ask before proposing/);
});

test("the opening message shows the item, its parent, children and notes", () => {
  const message = openingMessage(
    context("analyse", {
      parent: { ...item, id: 470, type: "Epic", title: "Portal" },
      children: [{ ...item, id: 473, type: "User Story", title: "Single PDF", specification: "" }],
    }),
    "  be strict  ",
  );
  assert.match(message, /Work item: Feature 471 - Invoice download/);
  assert.match(message, /Parent: Epic 470 - Portal/);
  assert.match(message, /Existing child: User Story 473 - Single PDF/);
  assert.match(message, /\(no specification yet\)/);
  assert.match(message, /Instructions from the user\nbe strict/);
});

test("a breakdown without children says so, a rewrite does not mention them", () => {
  assert.match(openingMessage(context("plan"), ""), /Existing children\nNone\./);
  assert.doesNotMatch(openingMessage(context("describe"), ""), /Existing children/);
});

// -- reading answers

test("a specification answer splits into message and proposal", () => {
  const answer = parseAnswer(`Here is a draft.\n\n${fenced({ specification: "## Goal\nX" })}\n\nOne question remains.`, "describe");
  assert.equal(answer.message, "Here is a draft.\n\nOne question remains.");
  assert.deepEqual(answer.proposal, { kind: "specification", specification: "## Goal\nX" });
});

test("an answer without a json block is only a message", () => {
  assert.deepEqual(parseAnswer("What should change?", "refine"), { message: "What should change?" });
});

test("the last json block wins when the model sends several", () => {
  const answer = parseAnswer(`${fenced({ specification: "old" })}\n${fenced({ specification: "new" })}`, "regenerate");
  assert.deepEqual(answer.proposal, { kind: "specification", specification: "new" });
});

test("broken or empty proposals are reported, not applied", () => {
  assert.match(parseAnswer("```json\n{not json\n```", "describe").problem!, /not valid JSON/);
  assert.match(parseAnswer(fenced({ specification: "  " }), "describe").problem!, /no specification text/);
  assert.match(parseAnswer(fenced({ children: [] }), "plan").problem!, /no children/);
  assert.match(parseAnswer(fenced({}), "analyse").problem!, /no findings list/);
});

test("children are read with defaults", () => {
  const answer = parseAnswer(fenced({ children: [{ key: "a", title: " A ", specification: "s" }, { key: "b", title: "B", ask: "human", specification: "s", depends_on: ["a"] }] }), "plan");
  assert.deepEqual(answer.proposal, {
    kind: "children",
    children: [
      { key: "a", title: "A", ask: "agent", specification: "s", depends_on: [], related: [] },
      { key: "b", title: "B", ask: "human", specification: "s", depends_on: ["a"], related: [] },
    ],
  });
});

test("invalid plans are refused with the reason", () => {
  const child = { key: "a", title: "A", specification: "s" };
  const cases: [unknown, RegExp][] = [
    [[{ ...child, key: "" }], /no key/],
    [[{ ...child, title: " " }], /no title/],
    [[child, { ...child, title: "B" }], /used twice/],
    [[child, { ...child, key: "b", title: "a" }], /share the title/],
    [[{ ...child, specification: "" }], /no specification/],
    [[{ ...child, ask: "robot" }], /must be human or agent/],
    [[{ ...child, depends_on: "a" }], /not a list of keys/],
    [[{ ...child, depends_on: ["zz"] }], /not in the proposal/],
    [[{ ...child, related: ["a"] }], /refers to itself/],
    [[{ ...child, depends_on: ["b"] }, { key: "b", title: "B", specification: "s", depends_on: ["a"] }], /cycle: a -> b -> a/],
  ];
  for (const [children, expected] of cases) {
    assert.match(readChildren(children) as string, expected);
  }
});

test("cycles are found and diamonds are not cycles", () => {
  assert.deepEqual(findCycle(new Map([["a", ["b"]], ["b", ["c"]], ["c", ["a"]]])), ["a", "b", "c", "a"]);
  assert.equal(findCycle(new Map([["a", ["b", "c"]], ["b", ["d"]], ["c", ["d"]], ["d", []]])), undefined);
});

test("findings are normalised and empty ones dropped", () => {
  const answer = parseAnswer(fenced({ findings: [{ id: "473", severity: "high", finding: "Gap", action: "Refine" }, { finding: " " }] }), "analyse");
  assert.deepEqual(answer.proposal, { kind: "analysis", findings: [{ id: 473, severity: "high", finding: "Gap", action: "Refine" }] });
});

// -- Azure DevOps changes

test("a specification is written through the worked-on and ready-for-review states", () => {
  const [begin, write] = specificationPatches("## Goal\nX");
  assert.deepEqual(begin, [{ op: "add", path: "/fields/Custom.EtaliiSpecificationState", value: "⚙ Worked on by agent" }]);
  assert.deepEqual(write, [
    { op: "add", path: "/multilineFieldsFormat/Custom.EtaliiSpecification", value: "Markdown" },
    { op: "add", path: "/fields/Custom.EtaliiSpecification", value: "## Goal\nX" },
    { op: "add", path: "/fields/Custom.EtaliiSpecificationState", value: "\u{1F441} Ready for review by user" },
  ]);
  assert.ok(![...begin, ...write].some((operation) => operation.path.includes("System.Description")));
});

test("a child is created under its parent, ready for review, in the parent's area", () => {
  const child = { key: "a", title: "A", ask: "human" as const, specification: "s", depends_on: [], related: [] };
  const patch = childPatch(child, { url: "https://org/_apis/wit/workItems/471", areaPath: "Shop\\Web", iterationPath: "Shop\\S1" });
  const value = (path: string) => patch.find((operation) => operation.path === path)?.value;
  assert.equal(value("/fields/System.Title"), "A");
  assert.equal(value("/fields/Custom.EtaliiAsk"), "\u{1F464} Human");
  assert.equal(value("/fields/Custom.EtaliiSpecificationState"), "\u{1F441} Ready for review by user");
  assert.equal(value("/fields/Custom.EtaliiSpecification"), "s");
  assert.equal(value("/multilineFieldsFormat/Custom.EtaliiSpecification"), "Markdown");
  assert.deepEqual(value("/relations/-"), { rel: "System.LinkTypes.Hierarchy-Reverse", url: "https://org/_apis/wit/workItems/471" });
  assert.equal(value("/fields/System.AreaPath"), "Shop\\Web");
  assert.equal(value("/fields/System.IterationPath"), "Shop\\S1");
  assert.equal(childPatch(child, { url: "u" }).some((operation) => operation.path.endsWith("AreaPath")), false);
});

test("links are added once and existing links are left alone", () => {
  const child = { key: "b", title: "B", ask: "agent" as const, specification: "s", depends_on: ["a"], related: ["c"] };
  const urls = new Map([["a", "https://org/1"], ["c", "https://org/3"]]);
  const none = { predecessors: new Set<string>(), related: new Set<string>() };
  assert.deepEqual(linkPatch(child, urls, none).map((operation) => operation.value), [
    { rel: "System.LinkTypes.Dependency-Reverse", url: "https://org/1" },
    { rel: "System.LinkTypes.Related", url: "https://org/3" },
  ]);
  assert.deepEqual(linkPatch(child, urls, { predecessors: new Set(["https://org/1"]), related: new Set(["https://org/3"]) }), []);
});

test("the analysis comment is escaped html", () => {
  const [operation] = analysisComment([{ id: 473, severity: "high", finding: "Uses <script>", action: "Fix & retest" }]);
  assert.equal(operation.path, "/fields/System.History");
  assert.match(operation.value as string, /<b>high<\/b> \(#473\): Uses &lt;script&gt; <i>Suggested: Fix &amp; retest<\/i>/);
  assert.match(analysisComment([])[0].value as string, /children are consistent/);
});

// -- rendering

test("markdown is rendered and model output cannot inject markup", () => {
  const html = renderMarkdown("## Goal\nText with **bold**, *italic* and `code`.\n\n- one\n- two\n\n1. first\n\n<img src=x onerror=alert(1)>");
  assert.equal(
    html,
    "<h4>Goal</h4><p>Text with <b>bold</b>, <i>italic</i> and <code>code</code>.</p><ul><li>one</li><li>two</li></ul><ol><li>first</li></ol><p>&lt;img src=x onerror=alert(1)&gt;</p>",
  );
});
