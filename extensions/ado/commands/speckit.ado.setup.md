---
description: "Connect this project to Azure DevOps and provision the custom fields"
scripts:
  sh: scripts/bash/ado.sh
  ps: scripts/powershell/ado.ps1
  py: scripts/python/ado.py
---

# Set Up Azure DevOps

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

Connect this project to an Azure DevOps project so that specifications are stored in work items.

## Outline

1. Run `{SCRIPT} config-show`. If it prints a configuration, show it and ask whether to keep it or replace it. If the user keeps it, continue at step 5. An error saying the project is not configured is expected on first use.
1. Ask the user for the connection details. Ask only for what the user input did not already provide, and never guess:
   - **Organization URL**, for example `https://dev.azure.com/contoso`
   - **Project** name
   - **Team**, **area path** and **iteration path** for new items (optional; empty means children inherit from their parent)
   - **Sign-in**: `azure-cli` (the user has run `az login`) or `pat`
   - For `pat`: the **name of the environment variable** that holds the token (default `AZURE_DEVOPS_EXT_PAT`). Never ask for the token itself, never accept one pasted into the conversation, and never write one to a file. The token needs the *Work Items (read & write)* scope, plus *Process (read & write)* for step 6.
   - **Hierarchy**: the work item types from the top level down to tasks, for example `Epic > Feature > User Story > Task` or `Initiative > Epic > Feature > User Story > Task`
1. Run `{SCRIPT} config-init --organization-url <url> --project <name> --auth <azure-cli|pat> --hierarchy "<levels>"` and add `--team`, `--area-path`, `--iteration-path` and `--pat-env` when given.
1. Tell the user where the configuration was written: `.specify/extensions/ado/ado-config.yml`. It contains no secrets and can be committed so the whole team shares it.
1. Run `{SCRIPT} connect`.
   - `missing_work_item_types`: the hierarchy names types the project does not have. Show them and ask the user to correct the hierarchy, then repeat from step 3.
   - `missing_fields`: the custom fields are not provisioned yet. Continue with step 6.
1. If fields are missing, run `{SCRIPT} fields-provision --dry-run`, show the planned actions, and ask the user to confirm. Changing a process affects every project that uses it, so only run `{SCRIPT} fields-provision` after an explicit yes. If the script reports that the project is not on an inherited process, relay that and stop; the user has to move the project to an inherited process first.
1. Mention the two optional pieces: the Azure DevOps browser plugin in `azure-devops-extension/` (fields on the form, backlog menu actions), and the Azure DevOps MCP server for searching work items from the agent.
1. Report the connection, the hierarchy and what was provisioned.
