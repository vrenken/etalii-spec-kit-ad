# Specification Guide

Every work item carries its specification as markdown in the **Etalii
Specification** field. Use the sections below for the level you are writing.
Keep sections in this order, leave none out, and write "None" when a section
has nothing in it. Describe what and why; keep implementation detail for tasks.

## Portfolio levels (initiative, epic, feature)

```markdown
## Goal
One paragraph: the outcome and who benefits.

## Scope
**In scope**
- ...

**Out of scope**
- ...

## Requirements
- **R1**: A testable statement.

## Success criteria
- **S1**: A measurable, technology-agnostic outcome.

## Assumptions and dependencies
- ...

## Open questions
- [NEEDS CLARIFICATION: ...]
```

## Story level

```markdown
## Story
As a <role> I want <capability> so that <benefit>.

## Acceptance scenarios
1. **Given** <state>, **when** <action>, **then** <outcome>.

## Requirements
- **R1**: A testable statement. Trace: parent R<n>.

## Edge cases
- ...

## Open questions
- [NEEDS CLARIFICATION: ...]
```

## Task level

```markdown
## Objective
What this task delivers, in one or two sentences. Trace: story R<n>.

## Where
Files, modules or systems this task touches.

## Steps
1. ...

## Definition of done
- [ ] A check that can be run or observed.
```

## Rules for every level

- Each requirement of a child traces to a requirement of its parent.
- No more than three `[NEEDS CLARIFICATION]` markers per item; make a
  reasonable assumption for the rest and record it.
- Do not restate the parent. Link to it by work item id instead.
