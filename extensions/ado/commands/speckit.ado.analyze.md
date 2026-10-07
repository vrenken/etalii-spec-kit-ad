---
description: "Analyse whether the children of an item are consistent with it and with each other"
scripts:
  sh: scripts/bash/ado.sh
  ps: scripts/powershell/ado.ps1
  py: scripts/python/ado.py
---

# Analyse Consistency

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

Check whether the children of an initiative, epic, feature or story are consistent with it and with each other. This command is **read-only**: it changes no work item.

## Rules

- Specifications live in the **Etalii Specification** field of the work item, always as markdown. Never write them to the Description field and never create `spec.md`, `plan.md` or `tasks.md` files.
- Every read and write goes through `{SCRIPT}`. It prints JSON on success and `{"error": "..."}` with a non-zero exit code on failure. When it reports an error, stop and tell the user what it said. Do not work around a refusal with another tool.
- Stay inside the scope you were given: only change the work items named in this command. If you notice a problem elsewhere, report it instead of fixing it.
- Be exact. Do not invent requirements, people, dates or decisions. Mark anything you could not determine as `[NEEDS CLARIFICATION: question]`, at most three per item.
- Follow `.specify/extensions/ado/specification-guide.md` for the sections each level needs. **IF EXISTS**, also load `.specify/memory/constitution.md` and respect it.

## Outline

1. Take the work item id from the user input and run `{SCRIPT} analyze <id>`. The output has every item in the subtree and a `findings` list of structural problems the script detected (missing specifications, hierarchy order, state conflicts, missing or cyclic predecessor/successor links).
1. Add the checks that need judgement, for each parent and its direct children:
   - **Coverage**: every requirement and success criterion of the parent is delivered by at least one child.
   - **Scope creep**: no child introduces something the parent does not ask for.
   - **Contradictions**: children do not contradict the parent or each other (terminology, data, limits, behaviour).
   - **Overlap**: no two children deliver the same thing.
   - **Sequencing**: the predecessor/successor links match the real order; point out missing and unnecessary links.
   - **Ownership**: items that need a human decision are not marked as an agent ask.
1. Report a table with one row per finding: work item id, severity (critical, high, medium, low), finding, suggested action. List the script findings first, unchanged. End with a count per severity and the command that would fix each group, such as `__SPECKIT_COMMAND_ADO_REFINE__` or `{SCRIPT} link`.
1. Do not fix anything. Ask the user which findings to act on.
