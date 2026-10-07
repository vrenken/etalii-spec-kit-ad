---
description: "Create a work item, or write the specification of an existing one"
scripts:
  sh: scripts/bash/ado.sh
  ps: scripts/powershell/ado.ps1
  py: scripts/python/ado.py
---

# Specify a Work Item

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

Write the specification of one work item. With a description and a level this creates a new item; with a work item id it generates the specification of an item that already exists.

## Rules

- Specifications live in the **Etalii Specification** field of the work item, always as markdown. Never write them to the Description field and never create `spec.md`, `plan.md` or `tasks.md` files.
- Every read and write goes through `{SCRIPT}`. It prints JSON on success and `{"error": "..."}` with a non-zero exit code on failure. When it reports an error, stop and tell the user what it said. Do not work around a refusal with another tool.
- Stay inside the scope you were given: only change the work items named in this command. If you notice a problem elsewhere, report it instead of fixing it.
- Be exact. Do not invent requirements, people, dates or decisions. Mark anything you could not determine as `[NEEDS CLARIFICATION: question]`, at most three per item.
- Follow `.specify/extensions/ado/specification-guide.md` for the sections each level needs. **IF EXISTS**, also load `.specify/memory/constitution.md` and respect it.

## Outline

1. Decide the mode from the user input:
   - **Existing item**: the input names a work item id. Run `{SCRIPT} item-get <id>`. If it has a parent, also run `{SCRIPT} item-get <parent>` and read the parent specification so this one stays inside it.
   - **New item**: the input is a description. Run `{SCRIPT} config-show` for the hierarchy. Use the level the user named; when none is named use the top level. Then run `{SCRIPT} item-create --type "<level>" --title "<title>" --ask human` (add `--parent <id>` when the user named one). The ask is `human` because a person asked for it in this session.
1. Run `{SCRIPT} spec-begin <id> --requested-by-user`. This sets the specification state to **Worked on by agent** so the team can see the item is being edited.
1. Write the specification from the user input, the title and the parent specification. If the description is too thin to specify, say what is missing instead of filling the gaps with guesses.
1. Save the markdown to a temporary file outside the repository and run `{SCRIPT} spec-write <id> --file <path>`.
1. Run `{SCRIPT} spec-finish <id>`. This sets the state to **Ready for review by user** and clears any pending agent request. Always run it, also when you stop early, so the item is never left in **Worked on by agent**.
1. Report the id, title and state, and list every `[NEEDS CLARIFICATION]` so the user can answer them during review.
