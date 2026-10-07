---
description: "Claim an approved task, implement it and keep its progress up to date"
scripts:
  sh: scripts/bash/ado.sh
  ps: scripts/powershell/ado.ps1
  py: scripts/python/ado.py
---

# Implement a Task

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

Implement one task and keep the team informed through the work item.

## Rules

- Specifications live in the **Etalii Specification** field of the work item, always as markdown. Never write them to the Description field and never create `spec.md`, `plan.md` or `tasks.md` files.
- Every read and write goes through `{SCRIPT}`. It prints JSON on success and `{"error": "..."}` with a non-zero exit code on failure. When it reports an error, stop and tell the user what it said. Do not work around a refusal with another tool.
- Stay inside the scope you were given: only change the work items named in this command. If you notice a problem elsewhere, report it instead of fixing it.
- Be exact. Do not invent requirements, people, dates or decisions. Mark anything you could not determine as `[NEEDS CLARIFICATION: question]`, at most three per item.
- Follow `.specify/extensions/ado/specification-guide.md` for the sections each level needs. **IF EXISTS**, also load `.specify/memory/constitution.md` and respect it.
- Only implement tasks whose specification is **Approved by user**. The script refuses to claim anything else.
- Implement what the task specifies and nothing more. If the specification is wrong or incomplete, stop, add a note with `{SCRIPT} progress <id> --owner <owner> --status note --note "<problem>"` and tell the user; do not silently deviate.

## Outline

1. Take the task id from the user input and run `{SCRIPT} item-get <id>`. Read its parent with `{SCRIPT} item-get <parent>` for context. Run `{SCRIPT} item-get` on each of its `predecessors`; if any is not finished, stop and report them.
1. Determine your owner identifier: `agent:<tool>:<thread>` where `<thread>` is the identifier your tool uses to resume this exact conversation. If you cannot determine it, ask the user; do not invent one. Use the same identifier for every call on this task.
1. Make sure you are on the branch and in the worktree where the work will happen, then run `{SCRIPT} claim <id> --owner <owner> --tool "<tool>"`. Branch, worktree and machine name are detected from the current directory; pass `--site` when the work runs somewhere other than this machine (for example a cloud session) and `--branch` or `--worktree` to override. Claiming moves the task to its in-progress state.
1. Implement the task. After each meaningful step run `{SCRIPT} progress <id> --owner <owner> --status note --note "<what was done, what is next>"` so a teammate can follow along and take over.
1. Run the checks the task's definition of done names. Do not mark a task done with failing or skipped checks.
1. Run `{SCRIPT} progress <id> --owner <owner> --status done --note "<summary, commits, how it was verified>"`.
1. If you cannot finish, use `__SPECKIT_COMMAND_ADO_HANDOVER__` instead of leaving the task claimed.
