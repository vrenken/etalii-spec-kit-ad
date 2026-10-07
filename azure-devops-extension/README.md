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

## How a menu action reaches an agent

A browser plugin cannot run an agent. Picking an action asks for optional
instructions and then stores the request on the work item (the *Etalii Agent
Request* and *Etalii Agent Request Notes* fields). *Refine* also sets the
specification state to *Requires finetuning by agent*.

An agent session picks the requests up with `/speckit.ado.requests`. Run that
by hand, or on a schedule for an unattended queue.

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

`npm run package` writes a `.vsix` to `out/`. Before packaging, set
`publisher` in `vss-extension.json` to a Visual Studio Marketplace publisher
you own, then upload the `.vsix` there, share it with your organization and
install it.

## Status

The menu logic is unit tested and the plugin type-checks and builds. It has
not yet been installed in a live organization.
