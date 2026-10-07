---
description: "Pick up the agent requests people raised from the Azure DevOps backlog menu"
scripts:
  sh: scripts/bash/ado.sh
  ps: scripts/powershell/ado.ps1
  py: scripts/python/ado.py
---

# Process Agent Requests

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

People ask for agent work from the Azure DevOps backlog and board menus. Each request is stored on the work item until an agent picks it up. This command works through that queue.

## Rules

- Specifications live in the **Etalii Specification** field of the work item, always as markdown. Never write them to the Description field and never create `spec.md`, `plan.md` or `tasks.md` files.
- Every read and write goes through `{SCRIPT}`. It prints JSON on success and `{"error": "..."}` with a non-zero exit code on failure. When it reports an error, stop and tell the user what it said. Do not work around a refusal with another tool.
- Stay inside the scope you were given: only change the work items named in this command. If you notice a problem elsewhere, report it instead of fixing it.
- Be exact. Do not invent requirements, people, dates or decisions. Mark anything you could not determine as `[NEEDS CLARIFICATION: question]`, at most three per item.
- Follow `.specify/extensions/ado/specification-guide.md` for the sections each level needs. **IF EXISTS**, also load `.specify/memory/constitution.md` and respect it.

## Outline

1. Run `{SCRIPT} requests`. If the list is empty, say so and stop.
1. Show the queue: id, type, title, request and notes. When the user input names ids or a request type, handle only those.
1. Handle the requests oldest first, one work item at a time, by following the matching command exactly as if the user had invoked it with the work item id and the `agent_request_notes` as input:

   | Request | Command |
   |---|---|
   | describe | `__SPECKIT_COMMAND_ADO_SPECIFY__` |
   | subdivide | `__SPECKIT_COMMAND_ADO_SUBDIVIDE__` |
   | decompose | `__SPECKIT_COMMAND_ADO_DECOMPOSE__` |
   | plan | `__SPECKIT_COMMAND_ADO_PLAN__` |
   | regenerate | `__SPECKIT_COMMAND_ADO_REGENERATE__` |
   | refine | `__SPECKIT_COMMAND_ADO_REFINE__` |
   | analyse | `__SPECKIT_COMMAND_ADO_ANALYZE__` |

1. A request is cleared when `{SCRIPT} spec-finish <id>` runs. For subdivide, decompose, plan and analyse, which do not rewrite the item itself, wrap the work in `{SCRIPT} spec-begin <id>` and `{SCRIPT} spec-finish <id>` so the request is cleared and the item returns to **Ready for review by user**.
1. If one request fails, report it and continue with the next. Finish with a summary per request.
