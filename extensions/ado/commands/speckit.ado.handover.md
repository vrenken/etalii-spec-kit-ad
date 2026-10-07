---
description: "Hand a task over to a person or another agent so the work can be resumed"
scripts:
  sh: scripts/bash/ado.sh
  ps: scripts/powershell/ado.ps1
  py: scripts/python/ado.py
---

# Hand Over a Task

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

Hand a task you own to a person or another agent, or release it, with enough information to resume.

## Rules

- Specifications live in the **Etalii Specification** field of the work item, always as markdown. Never write them to the Description field and never create `spec.md`, `plan.md` or `tasks.md` files.
- Every read and write goes through `{SCRIPT}`. It prints JSON on success and `{"error": "..."}` with a non-zero exit code on failure. When it reports an error, stop and tell the user what it said. Do not work around a refusal with another tool.
- Stay inside the scope you were given: only change the work items named in this command. If you notice a problem elsewhere, report it instead of fixing it.
- Be exact. Do not invent requirements, people, dates or decisions. Mark anything you could not determine as `[NEEDS CLARIFICATION: question]`, at most three per item.
- Follow `.specify/extensions/ado/specification-guide.md` for the sections each level needs. **IF EXISTS**, also load `.specify/memory/constitution.md` and respect it.

## Outline

1. Take the task id and the new owner from the user input and run `{SCRIPT} item-get <id>`. The new owner is a person (their name or email) or another agent thread (`agent:<tool>:<thread>`); empty releases the task for anyone to pick up.
1. Commit and push the work in progress so the new owner can reach it. Do not hand over uncommitted work without saying so.
1. Write the handover note: what is done, what is left, how to verify it, open questions, and anything surprising. Be specific enough that the new owner does not have to rediscover it.
1. Run `{SCRIPT} handover <id> --owner <your owner identifier> --to "<new owner>" --note "<note>"`. The script records the note as a comment together with the tool, site, branch and worktree the work was done in.
1. To take over a task whose owner is gone, the new owner runs `{SCRIPT} claim <id> --owner <owner> --tool "<tool>" --takeover`.
