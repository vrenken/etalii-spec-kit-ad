---
description: "Refine the specification of an initiative, epic, feature or story from feedback"
scripts:
  sh: scripts/bash/ado.sh
  ps: scripts/powershell/ado.ps1
  py: scripts/python/ado.py
---

# Refine a Specification

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

Improve an existing specification from feedback while keeping what is already right.

## Rules

- Specifications live in the **Etalii Specification** field of the work item, always as markdown. Never write them to the Description field and never create `spec.md`, `plan.md` or `tasks.md` files.
- Every read and write goes through `{SCRIPT}`. It prints JSON on success and `{"error": "..."}` with a non-zero exit code on failure. When it reports an error, stop and tell the user what it said. Do not work around a refusal with another tool.
- Stay inside the scope you were given: only change the work items named in this command. If you notice a problem elsewhere, report it instead of fixing it.
- Be exact. Do not invent requirements, people, dates or decisions. Mark anything you could not determine as `[NEEDS CLARIFICATION: question]`, at most three per item.
- Follow `.specify/extensions/ado/specification-guide.md` for the sections each level needs. **IF EXISTS**, also load `.specify/memory/constitution.md` and respect it.

## Outline

1. Take the work item id from the user input. Run `{SCRIPT} item-get <id>`. The feedback is the user input plus `agent_request_notes` on the item. If there is no feedback at all, ask what should change.
1. Run `{SCRIPT} spec-begin <id> --requested-by-user`. This sets the specification state to **Worked on by agent** so the team can see the item is being edited.
1. Apply the feedback as the smallest change that satisfies it. Keep the structure, the wording of untouched sections and all resolved decisions. Resolve `[NEEDS CLARIFICATION]` markers the feedback answers.
1. Save the markdown to a temporary file outside the repository and run `{SCRIPT} spec-write <id> --file <path>`.
1. Run `{SCRIPT} spec-finish <id>`. This sets the state to **Ready for review by user** and clears any pending agent request. Always run it, also when you stop early, so the item is never left in **Worked on by agent**.
1. Report what changed, section by section, in a few lines. If the change makes existing children inconsistent, name them and suggest `__SPECKIT_COMMAND_ADO_ANALYZE__`.
