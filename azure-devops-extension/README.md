# Etalii Spec Kit for Azure DevOps

Browser plugin for Azure Boards that goes with the `ado` Spec Kit extension
([`extensions/ado`](../extensions/ado/README.md)). It adds an **Etalii Spec
Kit** group to the work item menu on the backlog, on board cards and on the
work item form:

| Menu action | Offered on |
|---|---|
| Generate specification | every level above tasks |
| Subdivide into ... | portfolio levels, for example an epic into features |
| Decompose into ... | the level above stories, for example a feature into user stories |
| Plan into tasks | features and stories |
| Regenerate specification | every level above tasks |
| Refine specification | every level above tasks |
| Analyse children for consistency | every level above tasks |

The levels are read from the team's backlog configuration, so an Initiative
level, or any other depth, works without configuring the plugin.

## What a menu action does

Picking an action opens a chat panel for that one work item. The plugin sends
the item, its parent and its existing children to a model, and the model
answers with a short message and a proposal:

- a specification, for *Generate*, *Regenerate* and *Refine*;
- a set of child items with their order, for *Subdivide*, *Decompose* and *Plan*;
- a list of findings, for *Analyse*.

You can reply to ask for changes; each answer carries the complete updated
proposal. Nothing is written to Azure DevOps until you press **Apply to Azure
DevOps**. Applying then:

- writes a specification to the *Etalii Specification* field as markdown, passing
  through *Worked on by agent* to *Ready for review by user*;
- creates the child items under the parent with their Ask, specification and
  *Ready for review by user* state, and adds predecessor and related links. A
  child whose title already exists is kept, not duplicated, and a proposal with
  a dependency cycle is refused;
- adds the findings to the item's discussion.

The model cannot see your source code. For work that needs it, such as planning
tasks against real files, use **Queue for an agent session**: it stores the
request on the work item, and an agent session with the repository picks it up
with `/speckit.ado.requests`.

## Model settings

The first time, the panel asks for the model to use; **Model settings** in the
panel changes it later.

| Setting | Notes |
|---|---|
| Provider | Claude (Anthropic), or any provider with an OpenAI-compatible API |
| Model | Defaults to `claude-opus-5-5` for Claude; required for other providers |
| Base URL | Only for other providers, for example `https://api.openai.com/v1` |
| API key | Your own key for that provider |

The settings are stored per user in Azure DevOps, so each person uses their own
key and nobody else can read it. The call goes from your browser straight to
the provider: the work item text you chat about is sent to that provider, and
the key is held in the browser while the panel is open. Use a key with a
spending limit, and check that sending backlog content to the provider fits
your organization's rules.

A provider must allow calls from a web page (CORS). Anthropic and OpenAI do;
some self-hosted gateways need that enabled.

## Fields

Azure DevOps does not allow a plugin to add fields to a process. The custom
fields are created by `/speckit.ado.setup` (see the extension README), which
also puts them on the work item form. Install the fields first; without them
the menu actions report that the item could not be updated.

## Build and install

```bash
npm install
npm test
npm run package
```

`npm run package` writes a `.vsix` to `out/`, which can be uploaded by hand at
<https://marketplace.visualstudio.com/manage>.

## Automatic publishing

The workflow `.github/workflows/publish-azure-devops-extension.yml` tests,
builds and publishes the plugin whenever a change under
`azure-devops-extension/` reaches `main`, and can be started by hand from the
Actions tab. It needs one repository secret, `VS_MARKETPLACE_TOKEN`: an Azure
DevOps personal access token for the publisher's account, created for **All
accessible organizations** with the scope **Marketplace: Manage**.

Each run publishes the major and minor version from `vss-extension.json` with
a patch built from the run number and attempt, because the Marketplace refuses a version it has
already seen. The extension is private; share it once with your organization
from the publisher page, and later versions arrive there by themselves.

## Status

The menu, prompt, answer-parsing and apply logic is unit tested, and the plugin
type-checks and builds. The chat panel has not yet been exercised inside a live
organization.
