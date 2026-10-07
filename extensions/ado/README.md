# Azure DevOps Work Items Extension (etalii-spec-kit-ad)

Keeps Spec Kit specifications in Azure DevOps work items instead of markdown
files, so agents and a human team work from one backlog.

- Each work item carries its specification as **markdown in a dedicated
  field**, `Etalii Specification`. The Description field is not used.
- The hierarchy is whatever your process uses, for example
  `Epic > Feature > User Story > Task` or
  `Initiative > Epic > Feature > User Story > Task`.
- Agents record what they are doing on the item itself, so people can review,
  approve, redirect and take over.

## Install and set up

```bash
specify extension add ado
```

Then, in your agent, run `/speckit.ado.setup`. It asks for the organization
URL, the project, how to sign in and the hierarchy, writes
`.specify/extensions/ado/ado-config.yml`, verifies the connection, and offers
to provision the custom fields.

Sign-in is either the Azure CLI (`az login`) or a personal access token.

- **Azure CLI** works when the organization is connected to the Microsoft Entra
  directory you sign in to. An organization owned by a personal Microsoft
  account usually is not, and then refuses the sign-in; use a token there.
- **Token**: setup gives you a `token-set` command to run in your own terminal.
  It asks for the token with hidden input and stores it in your user
  environment (default variable `AZURE_DEVOPS_EXT_PAT`). The token is never
  written to the project and the agent never sees it. It needs *Work Items:
  Read, write & manage* and *Project and Team: Read*.

Custom fields need an **inherited process**. When the project is still on a
system process (Agile, Scrum, Basic or CMMI as shipped), setup offers to create
an inherited copy and move only this project to it. On-premises XML processes
are not supported.

## Fields

| Field | On | Values |
|---|---|---|
| Etalii Ask | every level | 👤 Human, 🤖 Agent |
| Etalii Specification State | every level | ○ Open, 👁 Ready for review by user, ✅ Approved by user, ↻ Requires finetuning by agent, ⚙ Worked on by agent |
| Etalii Specification | every level | markdown |
| Etalii Agent Request | every level | Describe, Subdivide, Decompose, Plan, Regenerate, Refine, Analyse |
| Etalii Agent Request Notes | every level | free text |
| Etalii Implementation Owner | tasks | a person, or `agent:<tool>:<thread>` |
| Etalii Implementation Tool | tasks | where the work runs: Claude, VS Code, ... |
| Etalii Implementation Site | tasks | the machine or cloud session |
| Etalii Implementation Branch | tasks | git branch |
| Etalii Implementation Worktree | tasks | git worktree path |

The implementation fields exist so that work can be resumed when an agent
thread ends: they say who had the task, in which tool, on which system, and on
which branch and worktree.

## Seeing who owns what

The glyph is part of the stored value, so ownership reads the same in the
backlog, on board cards, in queries and in delivery plans.

- **Boards**: `/speckit.ado.setup` offers to style the team boards. Cards show
  the Ask and Specification State; agent-owned cards are tinted violet and
  cards waiting on a person amber. Existing card rules are kept.
- **Backlog**: add the *Etalii Ask* and *Etalii Specification State* columns
  through **Column options**. Azure DevOps stores backlog columns per person,
  so this cannot be done for the team.

## Commands

| Command | What it does |
|---|---|
| `/speckit.ado.setup` | Connect to Azure DevOps and provision the fields |
| `/speckit.ado.specify` | Create an item, or write the specification of an existing one |
| `/speckit.ado.subdivide` | Break a portfolio item into the next level (epic into features) |
| `/speckit.ado.decompose` | Break a feature into stories |
| `/speckit.ado.plan` | Break a feature or story into dependency-ordered tasks |
| `/speckit.ado.regenerate` | Rewrite a specification from scratch |
| `/speckit.ado.refine` | Improve a specification from feedback |
| `/speckit.ado.analyze` | Check children against their parent and each other (read-only) |
| `/speckit.ado.implement` | Claim an approved task, implement it, report progress |
| `/speckit.ado.handover` | Hand a task to a person or another agent |
| `/speckit.ado.requests` | Work through the requests people raised from the Azure DevOps menus |

## How agents and people share the backlog

**Specification state.** Before an agent changes a specification it sets the
state to *Worked on by agent*, and when it is done it sets *Ready for review by
user*. A person then sets *Approved by user*, or *Requires finetuning by agent*
with notes.

**Scope.** The helper script refuses, rather than warns, when an agent steps
outside its assignment:

- An agent may only edit a specification it owns (Ask = Agent, not yet
  approved), one with a pending agent request, or one the user pointed it at.
- A task can only be claimed when its specification is *Approved by user*.
- A task owned by someone else cannot be claimed, progressed or handed over
  without an explicit takeover.

**Relations.** Breaking an item down creates the children with parent links
and with predecessor/successor links from the plan's `depends_on`, validated
for cycles before anything is written. These links are what a delivery plan is
ordered by. Re-running a breakdown reuses children by title instead of
duplicating them.

**Progress and handover.** Claiming a task moves it to its in-progress state
and records owner, tool, site, branch and worktree. Progress notes become
comments. A handover requires a note and records how to resume.

## Talking to Azure DevOps

All writes go through `scripts/python/ado.py` (standard library only), which
calls the Azure DevOps REST API. Keeping the state transitions and scope
checks in one script is what makes them enforceable. The
[Azure DevOps MCP server](https://github.com/microsoft/azure-devops-mcp) can be
used alongside it for searching and reading work items.

The browser plugin in [`azure-devops-extension/`](../../azure-devops-extension/README.md)
adds the menu actions on the backlog and board.

## Status

The helper is covered by tests against an in-memory Azure DevOps. It has not
yet been run against a live organization; expect to adjust details of field
and layout provisioning on first contact.
