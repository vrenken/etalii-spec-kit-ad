---
description: "Decompose a feature into stories"
scripts:
  sh: scripts/bash/ado.sh
  ps: scripts/powershell/ado.ps1
  py: scripts/python/ado.py
---

# Decompose a Feature into Stories

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

Break a feature into stories: the level directly above tasks.

## Rules

- Specifications live in the **Etalii Specification** field of the work item, always as markdown. Never write them to the Description field and never create `spec.md`, `plan.md` or `tasks.md` files.
- Every read and write goes through `{SCRIPT}`. It prints JSON on success and `{"error": "..."}` with a non-zero exit code on failure. When it reports an error, stop and tell the user what it said. Do not work around a refusal with another tool.
- Stay inside the scope you were given: only change the work items named in this command. If you notice a problem elsewhere, report it instead of fixing it.
- Be exact. Do not invent requirements, people, dates or decisions. Mark anything you could not determine as `[NEEDS CLARIFICATION: question]`, at most three per item.
- Follow `.specify/extensions/ado/specification-guide.md` for the sections each level needs. **IF EXISTS**, also load `.specify/memory/constitution.md` and respect it.

## Outline

1. Take the work item id from the user input. If there is none, ask for it.
1. Run `{SCRIPT} analyze <id>` to load the item, its specification and any children that already exist.
1. Check that the next level down is the story level (the level directly above tasks); `{SCRIPT} config-show` prints the hierarchy. If the parent specification is empty, stop and suggest `__SPECKIT_COMMAND_ADO_SPECIFY__` first.
1. Design the stories. Each story is one user-visible slice that can be tested independently, with acceptance scenarios in Given/When/Then form. Each one must be traceable to a statement in the parent specification, and together they must cover the parent without overlapping. Reuse the exact title of an existing child you want to keep; the script matches on title and will not duplicate it.
1. Work out the order. For every child list the siblings that must be finished first in `depends_on`. This becomes predecessor/successor links in Azure DevOps and is what a delivery plan is built from, so do not leave it out and do not add dependencies that are not real. Use `related` for items that touch the same area without ordering.
1. Decide `ask` per child: `agent` when an agent can specify and deliver it, `human` when it needs a person (a decision, access, a conversation with a stakeholder).
1. Write the plan to a temporary JSON file outside the repository:

   ```json
   {
     "children": [
       {
         "key": "a",
         "title": "Short, specific title",
         "ask": "agent",
         "specification": "markdown following the specification guide",
         "depends_on": [],
         "related": []
       },
       {
         "key": "b",
         "title": "Another child",
         "ask": "human",
         "specification": "markdown",
         "depends_on": ["a"],
         "related": []
       }
     ]
   }
   ```

   `depends_on` and `related` take keys from this file, or the numeric id of an existing work item.
1. Run `{SCRIPT} apply <id> --action decompose --file <path>`. The plan is validated in full, including dependency cycles, before anything is created.
1. Report the created and reused ids with their titles, the dependency order, and every `[NEEDS CLARIFICATION]` you left. The new items are in **Ready for review by user**; tell the user they need to approve them before work continues.
