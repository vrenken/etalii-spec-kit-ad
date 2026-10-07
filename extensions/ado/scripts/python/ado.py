#!/usr/bin/env python3
"""Azure DevOps work item helper for the ``ado`` extension (etalii-spec-kit-ad).

Specifications live in Azure DevOps work items instead of markdown files. This
script owns every deterministic step of that flow so that the agent prompts
never have to hand-build REST calls or decide state transitions themselves:

* connection setup and verification
* provisioning the custom fields on an inherited process
* reading and writing the markdown ``Specification`` field
* the specification state machine (Open / Ready for review by user /
  Approved by user / Requires finetuning by agent / Worked on by agent)
* creating child items with hierarchy and predecessor/successor links
* claiming, progressing and handing over implementation tasks
* the queue of agent requests raised from the Azure DevOps context menu

Only the Python standard library is used. Secrets are never written to disk:
the PAT is read from an environment variable, or a token is obtained from the
Azure CLI.

Usage: ado.py [--config PATH] <command> [options]   (see --help)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

API_VERSION = "7.1"
COMMENTS_API_VERSION = "7.1-preview.4"
ADO_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798"
CONFIG_RELATIVE_PATH = Path(".specify") / "extensions" / "ado" / "ado-config.yml"
DEFAULT_HIERARCHY = "Epic > Feature > User Story > Task"
DEFAULT_PAT_ENV = "AZURE_DEVOPS_EXT_PAT"

# Canonical key -> value stored in Azure DevOps. Reading is tolerant of a
# leading glyph (see ``canonical``), so the stored labels can gain symbols
# without breaking items written earlier.
# The glyph is part of the stored value so that it shows wherever the field
# does: backlog columns, board cards, queries and delivery plans.
ASK_LABELS = {"human": "\U0001F464 Human", "agent": "\U0001F916 Agent"}
SPEC_STATE_LABELS = {
    "open": "\u25CB Open",
    "ready-for-review": "\U0001F441 Ready for review by user",
    "approved": "\u2705 Approved by user",
    "requires-finetuning": "\u21BB Requires finetuning by agent",
    "worked-on": "\u2699 Worked on by agent",
}
REQUEST_LABELS = {
    "describe": "Describe",
    "subdivide": "Subdivide",
    "decompose": "Decompose",
    "plan": "Plan",
    "regenerate": "Regenerate",
    "refine": "Refine",
    "analyse": "Analyse",
}

F_ASK = "Custom.EtaliiAsk"
F_SPEC_STATE = "Custom.EtaliiSpecificationState"
F_SPEC = "Custom.EtaliiSpecification"
F_REQUEST = "Custom.EtaliiAgentRequest"
F_REQUEST_NOTES = "Custom.EtaliiAgentRequestNotes"
F_IMPL_OWNER = "Custom.EtaliiImplementationOwner"
F_IMPL_TOOL = "Custom.EtaliiImplementationTool"
F_IMPL_SITE = "Custom.EtaliiImplementationSite"
F_IMPL_BRANCH = "Custom.EtaliiImplementationBranch"
F_IMPL_WORKTREE = "Custom.EtaliiImplementationWorktree"

# (reference name, display name, type, picklist labels or None, task level only)
FIELD_DEFINITIONS: list[tuple[str, str, str, dict[str, str] | None, bool]] = [
    (F_ASK, "Etalii Ask", "string", ASK_LABELS, False),
    (F_SPEC_STATE, "Etalii Specification State", "string", SPEC_STATE_LABELS, False),
    (F_SPEC, "Etalii Specification", "html", None, False),
    (F_REQUEST, "Etalii Agent Request", "string", REQUEST_LABELS, False),
    (F_REQUEST_NOTES, "Etalii Agent Request Notes", "plainText", None, False),
    (F_IMPL_OWNER, "Etalii Implementation Owner", "string", None, True),
    (F_IMPL_TOOL, "Etalii Implementation Tool", "string", None, True),
    (F_IMPL_SITE, "Etalii Implementation Site", "string", None, True),
    (F_IMPL_BRANCH, "Etalii Implementation Branch", "string", None, True),
    (F_IMPL_WORKTREE, "Etalii Implementation Worktree", "string", None, True),
]

LAYOUT_GROUP = "Agent collaboration"

LINK_TYPES = {
    "parent": "System.LinkTypes.Hierarchy-Reverse",
    "child": "System.LinkTypes.Hierarchy-Forward",
    "predecessor": "System.LinkTypes.Dependency-Reverse",
    "successor": "System.LinkTypes.Dependency-Forward",
    "related": "System.LinkTypes.Related",
}

Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, Any]]


class AdoError(Exception):
    """A failure the caller should see as a message, not a traceback."""


# -- Labels -------------------------------------------------------------------


def canonical(value: object, labels: dict[str, str]) -> str | None:
    """Map a stored field value back to its canonical key.

    Leading non-alphanumeric characters (a glyph plus spacing) are ignored so
    that ``"Agent"`` and a glyph-prefixed ``"Agent"`` are the same value.
    """
    if not isinstance(value, str):
        return None
    text = _without_glyph(value)
    for key, label in labels.items():
        if text in (key.casefold(), _without_glyph(label)):
            return key
    return None


def _without_glyph(text: str) -> str:
    text = text.strip()
    while text and not text[0].isalnum():
        text = text[1:]
    return text.strip().casefold()


# -- Configuration ------------------------------------------------------------


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".specify").is_dir():
            return candidate
    return current


def parse_flat_yaml(text: str) -> dict[str, str]:
    """Parse the flat ``key: value`` subset the config file is limited to."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


def render_flat_yaml(values: dict[str, str]) -> str:
    lines = [
        "# Azure DevOps connection for etalii-spec-kit-ad.",
        "# Written by `speckit.ado.setup`. No secrets belong in this file.",
    ]
    for key, value in values.items():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}: "{escaped}"')
    return "\n".join(lines) + "\n"


class Config:
    def __init__(self, values: dict[str, str]):
        self.values = values
        self.organization_url = values.get("organization_url", "").rstrip("/")
        self.project = values.get("project", "")
        self.team = values.get("team", "")
        self.area_path = values.get("area_path", "")
        self.iteration_path = values.get("iteration_path", "")
        self.auth = values.get("auth", "azure-cli") or "azure-cli"
        self.pat_env = values.get("pat_env", DEFAULT_PAT_ENV) or DEFAULT_PAT_ENV
        self.hierarchy = parse_hierarchy(values.get("hierarchy") or DEFAULT_HIERARCHY)

    def validate(self) -> None:
        if not self.organization_url.lower().startswith("https://"):
            raise AdoError(
                "organization_url must be an https:// URL "
                "(for example https://dev.azure.com/contoso). Run speckit.ado.setup."
            )
        if not self.project:
            raise AdoError("project is not configured. Run speckit.ado.setup.")
        if self.auth not in ("azure-cli", "pat"):
            raise AdoError("auth must be 'azure-cli' or 'pat'.")

    @property
    def task_type(self) -> str:
        return self.hierarchy[-1]

    def level_of(self, work_item_type: str) -> int | None:
        for index, name in enumerate(self.hierarchy):
            if name.casefold() == work_item_type.casefold():
                return index
        return None


def parse_hierarchy(text: str) -> list[str]:
    levels = [part.strip() for part in text.replace("=>", ">").split(">")]
    levels = [level for level in levels if level]
    if len(levels) < 2:
        raise AdoError(
            "hierarchy needs at least two work item types, top level first, "
            "for example 'Epic > Feature > User Story > Task'."
        )
    if len({level.casefold() for level in levels}) != len(levels):
        raise AdoError("hierarchy lists the same work item type twice.")
    return levels


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise AdoError(f"No Azure DevOps configuration at {path}. Run speckit.ado.setup.")
    config = Config(parse_flat_yaml(path.read_text(encoding="utf-8")))
    config.validate()
    return config


# -- HTTP ---------------------------------------------------------------------


def acquire_authorization(config: Config) -> str:
    if config.auth == "pat":
        token = os.environ.get(config.pat_env, "").strip()
        if not token:
            raise AdoError(
                f"Environment variable {config.pat_env} is empty. Set it to a "
                "personal access token with Work Items (read & write) scope."
            )
        return "Basic " + base64.b64encode(f":{token}".encode()).decode("ascii")
    resolved = shutil.which("az")
    if not resolved or not os.path.isabs(resolved):
        raise AdoError("Azure CLI (az) was not found. Install it or switch auth to 'pat'.")
    try:
        result = subprocess.run(
            [resolved, "account", "get-access-token", "--resource", ADO_RESOURCE_ID, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        token = json.loads(result.stdout).get("accessToken", "") if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        token = ""
    if not token:
        raise AdoError("Could not get a token from the Azure CLI. Run `az login` first.")
    return f"Bearer {token}"


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, Any]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except urllib.error.URLError as error:
        raise AdoError(f"Could not reach Azure DevOps: {error.reason}") from error
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        # A sign-in page instead of JSON means the credentials were rejected.
        return status, {"message": raw[:200].decode("utf-8", "replace")}


class AdoClient:
    def __init__(self, config: Config, transport: Transport | None = None, authorization: str | None = None):
        self.config = config
        self._transport = transport or urllib_transport
        self._authorization = authorization
        self._states: dict[str, list[dict[str, Any]]] = {}

    # URL helpers

    def _org(self, path: str) -> str:
        return f"{self.config.organization_url}/_apis/{path}"

    def _project(self, path: str) -> str:
        return f"{self.config.organization_url}/{urllib.parse.quote(self.config.project)}/_apis/{path}"

    def work_item_url(self, item_id: int) -> str:
        return self._org(f"wit/workItems/{int(item_id)}")

    def request(
        self,
        method: str,
        url: str,
        body: Any = None,
        *,
        patch: bool = False,
        api_version: str = API_VERSION,
        query: dict[str, str] | None = None,
    ) -> Any:
        if self._authorization is None:
            self._authorization = acquire_authorization(self.config)
        params = dict(query or {})
        params["api-version"] = api_version
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {"Authorization": self._authorization, "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json-patch+json" if patch else "application/json"
            data = json.dumps(body).encode("utf-8")
        status, payload = self._transport(method, full_url, headers, data)
        if status in (401, 403) or status == 203:
            raise AdoError(f"Azure DevOps rejected the credentials (HTTP {status}).")
        if status >= 400:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise AdoError(f"Azure DevOps returned HTTP {status}: {message or 'no details'}")
        return payload

    # Work items

    def get_item(self, item_id: int) -> dict[str, Any]:
        return self.request("GET", self._project(f"wit/workitems/{int(item_id)}"), query={"$expand": "relations"})

    def get_items(self, ids: list[int]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for start in range(0, len(ids), 200):
            chunk = ",".join(str(i) for i in ids[start : start + 200])
            payload = self.request("GET", self._org("wit/workitems"), query={"ids": chunk, "$expand": "relations"})
            items.extend(payload.get("value", []))
        return items

    def create_item(self, work_item_type: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
        url = self._project(f"wit/workitems/${urllib.parse.quote(work_item_type)}")
        return self.request("POST", url, operations, patch=True)

    def update_item(self, item_id: int, operations: list[dict[str, Any]]) -> dict[str, Any]:
        return self.request("PATCH", self._project(f"wit/workitems/{int(item_id)}"), operations, patch=True)

    def add_comment(self, item_id: int, text: str) -> None:
        url = self._project(f"wit/workItems/{int(item_id)}/comments")
        self.request("POST", url, {"text": text}, api_version=COMMENTS_API_VERSION)

    def query_ids(self, wiql: str) -> list[int]:
        payload = self.request("POST", self._project("wit/wiql"), {"query": wiql})
        return [int(row["id"]) for row in payload.get("workItems", [])]

    def states(self, work_item_type: str) -> list[dict[str, Any]]:
        if work_item_type not in self._states:
            url = self._project(f"wit/workitemtypes/{urllib.parse.quote(work_item_type)}/states")
            self._states[work_item_type] = self.request("GET", url).get("value", [])
        return self._states[work_item_type]

    def state_for_category(self, work_item_type: str, category: str) -> str:
        for state in self.states(work_item_type):
            if state.get("category") == category:
                return state["name"]
        raise AdoError(f"Work item type {work_item_type!r} has no state in category {category}.")

    def category_of(self, item: dict[str, Any]) -> str | None:
        fields = item.get("fields", {})
        for state in self.states(fields.get("System.WorkItemType", "")):
            if state.get("name") == fields.get("System.State"):
                return state.get("category")
        return None


# -- Work item helpers --------------------------------------------------------


def op(field: str, value: Any) -> dict[str, Any]:
    return {"op": "add", "path": f"/fields/{field}", "value": value}


def markdown_format_op(field: str) -> dict[str, Any]:
    return {"op": "add", "path": f"/multilineFieldsFormat/{field}", "value": "Markdown"}


def relation_op(link: str, target_url: str) -> dict[str, Any]:
    return {"op": "add", "path": "/relations/-", "value": {"rel": LINK_TYPES[link], "url": target_url}}


def related_ids(item: dict[str, Any], link: str) -> list[int]:
    ids = []
    for relation in item.get("relations") or []:
        if relation.get("rel") == LINK_TYPES[link]:
            tail = str(relation.get("url", "")).rstrip("/").rsplit("/", 1)[-1]
            if tail.isdigit():
                ids.append(int(tail))
    return ids


def summarize(item: dict[str, Any]) -> dict[str, Any]:
    fields = item.get("fields", {})
    owner = fields.get("System.AssignedTo")
    return {
        "id": item.get("id"),
        "type": fields.get("System.WorkItemType"),
        "title": fields.get("System.Title"),
        "state": fields.get("System.State"),
        "assigned_to": owner.get("uniqueName") if isinstance(owner, dict) else owner,
        "ask": canonical(fields.get(F_ASK), ASK_LABELS),
        "specification_state": canonical(fields.get(F_SPEC_STATE), SPEC_STATE_LABELS),
        "specification": fields.get(F_SPEC) or "",
        "agent_request": canonical(fields.get(F_REQUEST), REQUEST_LABELS),
        "agent_request_notes": fields.get(F_REQUEST_NOTES) or "",
        "implementation": {
            "owner": fields.get(F_IMPL_OWNER) or "",
            "tool": fields.get(F_IMPL_TOOL) or "",
            "site": fields.get(F_IMPL_SITE) or "",
            "branch": fields.get(F_IMPL_BRANCH) or "",
            "worktree": fields.get(F_IMPL_WORKTREE) or "",
        },
        "parent": next(iter(related_ids(item, "parent")), None),
        "children": related_ids(item, "child"),
        "predecessors": related_ids(item, "predecessor"),
        "successors": related_ids(item, "successor"),
        "related": related_ids(item, "related"),
    }


def read_text_argument(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise AdoError(f"Cannot read {path}: {error}") from error


def require_level(config: Config, item: dict[str, Any]) -> int:
    work_item_type = item.get("fields", {}).get("System.WorkItemType", "")
    level = config.level_of(work_item_type)
    if level is None:
        raise AdoError(
            f"Work item {item.get('id')} is a {work_item_type!r}, which is not in the "
            f"configured hierarchy ({' > '.join(config.hierarchy)})."
        )
    return level


# -- Specification state machine ----------------------------------------------


def spec_begin(client: AdoClient, item_id: int, *, requested_by_user: bool, takeover: bool) -> dict[str, Any]:
    """Mark an item as being worked on by an agent before its text changes.

    An agent stays inside its scope: it may only start on an item it owns
    (Ask = Agent, not yet approved), an item with a pending agent request, or
    one the user explicitly pointed it at in this session.
    """
    item = client.get_item(item_id)
    require_level(client.config, item)
    info = summarize(item)
    if info["specification_state"] == "worked-on" and not takeover:
        raise AdoError(
            f"Work item {item_id} is already being worked on by an agent. "
            "Pass --takeover only when that work was abandoned."
        )
    invited = requested_by_user or info["agent_request"] is not None
    if not invited:
        if info["ask"] != "agent":
            raise AdoError(
                f"Work item {item_id} is a human ask and has no pending agent request. "
                "It is outside this agent's scope."
            )
        if info["specification_state"] == "approved":
            raise AdoError(
                f"The specification of work item {item_id} is approved by the user. "
                "It can only be changed on the user's request."
            )
    client.update_item(item_id, [op(F_SPEC_STATE, SPEC_STATE_LABELS["worked-on"])])
    return {"id": item_id, "specification_state": "worked-on", "previous": info["specification_state"]}


def spec_write(client: AdoClient, item_id: int, markdown: str) -> dict[str, Any]:
    if not markdown.strip():
        raise AdoError("Refusing to write an empty specification.")
    info = summarize(client.get_item(item_id))
    if info["specification_state"] != "worked-on":
        raise AdoError(
            f"Work item {item_id} is not marked 'Worked on by agent'. Run `spec begin {item_id}` first."
        )
    client.update_item(item_id, [markdown_format_op(F_SPEC), op(F_SPEC, markdown)])
    return {"id": item_id, "written": True, "characters": len(markdown)}


def spec_finish(client: AdoClient, item_id: int) -> dict[str, Any]:
    info = summarize(client.get_item(item_id))
    if info["specification_state"] != "worked-on":
        raise AdoError(f"Work item {item_id} is not marked 'Worked on by agent'; nothing to finish.")
    operations = [op(F_SPEC_STATE, SPEC_STATE_LABELS["ready-for-review"])]
    if info["agent_request"] is not None:
        operations += [op(F_REQUEST, ""), op(F_REQUEST_NOTES, "")]
    client.update_item(item_id, operations)
    return {"id": item_id, "specification_state": "ready-for-review"}


# -- Creating children with relations -----------------------------------------


def child_type_for(config: Config, parent_level: int, action: str) -> str:
    """Resolve which work item type an action creates below ``parent_level``.

    ``plan`` always produces the lowest level (tasks). ``decompose`` and
    ``subdivide`` produce the next level down; they are two names for the same
    step so the wording can follow the hierarchy (an epic is subdivided, a
    feature is decomposed).
    """
    last = len(config.hierarchy) - 1
    if parent_level >= last:
        raise AdoError(f"A {config.hierarchy[last]} is the lowest level and cannot be broken down.")
    if action == "plan":
        return config.hierarchy[last]
    if action in ("subdivide", "decompose"):
        if parent_level + 1 == last:
            raise AdoError(
                f"The level below {config.hierarchy[parent_level]} is {config.hierarchy[last]}; use 'plan'."
            )
        return config.hierarchy[parent_level + 1]
    raise AdoError(f"Unknown action {action!r}; expected subdivide, decompose or plan.")


def validate_plan(plan: Any) -> list[dict[str, Any]]:
    children = plan.get("children") if isinstance(plan, dict) else None
    if not isinstance(children, list) or not children:
        raise AdoError("The plan file needs a non-empty 'children' list.")
    keys: set[str] = set()
    for child in children:
        if not isinstance(child, dict):
            raise AdoError("Every child in the plan must be an object.")
        key, title = child.get("key"), child.get("title")
        if not isinstance(key, str) or not key.strip():
            raise AdoError("Every child needs a non-empty string 'key'.")
        if not isinstance(title, str) or not title.strip():
            raise AdoError(f"Child {key!r} needs a non-empty 'title'.")
        if key in keys:
            raise AdoError(f"Duplicate child key {key!r} in the plan.")
        keys.add(key)
        if not isinstance(child.get("specification"), str) or not child["specification"].strip():
            raise AdoError(f"Child {key!r} needs a markdown 'specification'.")
        if child.get("ask", "agent") not in ASK_LABELS:
            raise AdoError(f"Child {key!r} has ask {child.get('ask')!r}; expected 'human' or 'agent'.")
    graph: dict[str, list[str]] = {}
    for child in children:
        for name in ("depends_on", "related"):
            targets = child.get(name, [])
            if not isinstance(targets, list):
                raise AdoError(f"'{name}' of child {child['key']!r} must be a list.")
            for target in targets:
                if isinstance(target, int) and not isinstance(target, bool):
                    continue  # an existing work item outside this plan
                if target not in keys:
                    raise AdoError(f"Child {child['key']!r} refers to unknown {name} target {target!r}.")
                if target == child["key"]:
                    raise AdoError(f"Child {child['key']!r} cannot refer to itself in '{name}'.")
        graph[child["key"]] = [t for t in child.get("depends_on", []) if isinstance(t, str)]
    cycle = find_cycle(graph)
    if cycle:
        raise AdoError("Dependency cycle in the plan: " + " -> ".join(cycle))
    return children


def find_cycle(graph: dict[Any, list[Any]]) -> list[Any] | None:
    """Return one dependency cycle as a node path, or None when acyclic."""
    done: set[Any] = set()
    for start in graph:
        if start in done:
            continue
        path: list[Any] = []
        on_path: set[Any] = set()
        stack: list[tuple[Any, int]] = [(start, 0)]
        while stack:
            node, index = stack.pop()
            if index == 0:
                path.append(node)
                on_path.add(node)
            targets = graph.get(node, [])
            if index < len(targets):
                stack.append((node, index + 1))
                target = targets[index]
                if target in on_path:
                    return path[path.index(target) :] + [target]
                if target not in done and target in graph:
                    stack.append((target, 0))
            else:
                path.pop()
                on_path.discard(node)
                done.add(node)
    return None


def apply_plan(client: AdoClient, parent_id: int, action: str, plan: Any) -> dict[str, Any]:
    """Create the planned children under ``parent_id`` and link them.

    The whole plan is validated before the first write. Re-running is safe: a
    child whose title already exists under the parent is reused, not duplicated,
    and links that already exist are left alone.
    """
    config = client.config
    children = validate_plan(plan)
    parent = client.get_item(parent_id)
    child_type = child_type_for(config, require_level(config, parent), action)
    parent_fields = parent.get("fields", {})

    existing = {
        str(item.get("fields", {}).get("System.Title", "")).strip().casefold(): item
        for item in client.get_items(related_ids(parent, "child"))
        if item.get("fields", {}).get("System.WorkItemType", "").casefold() == child_type.casefold()
    }
    ids: dict[str, int] = {}
    items: dict[int, dict[str, Any]] = {}
    created: list[int] = []
    for child in children:
        found = existing.get(child["title"].strip().casefold())
        if found is None:
            operations = [
                op("System.Title", child["title"].strip()),
                op("System.AreaPath", config.area_path or parent_fields.get("System.AreaPath", config.project)),
                op("System.IterationPath", config.iteration_path or parent_fields.get("System.IterationPath", config.project)),
                op(F_ASK, ASK_LABELS[child.get("ask", "agent")]),
                op(F_SPEC_STATE, SPEC_STATE_LABELS["ready-for-review"]),
                markdown_format_op(F_SPEC),
                op(F_SPEC, child["specification"]),
                relation_op("parent", client.work_item_url(parent_id)),
            ]
            found = client.create_item(child_type, operations)
            created.append(int(found["id"]))
        ids[child["key"]] = int(found["id"])
        items[int(found["id"])] = found

    links = 0
    for child in children:
        item_id = ids[child["key"]]
        operations = []
        for name, link in (("depends_on", "predecessor"), ("related", "related")):
            present = set(related_ids(items[item_id], link))
            for target in child.get(name, []):
                target_id = target if isinstance(target, int) else ids[target]
                if target_id not in present:
                    operations.append(relation_op(link, client.work_item_url(target_id)))
        if operations:
            client.update_item(item_id, operations)
            links += len(operations)
    return {
        "parent": parent_id,
        "child_type": child_type,
        "children": ids,
        "created": created,
        "reused": sorted(set(ids.values()) - set(created)),
        "links_added": links,
    }


# -- Analysis -----------------------------------------------------------------


def collect_subtree(client: AdoClient, root_id: int) -> dict[int, dict[str, Any]]:
    items: dict[int, dict[str, Any]] = {}
    pending = [root_id]
    while pending:
        batch = [i for i in dict.fromkeys(pending) if i not in items]
        pending = []
        for item in client.get_items(batch) if batch else []:
            items[int(item["id"])] = item
            pending.extend(related_ids(item, "child"))
    if root_id not in items:
        raise AdoError(f"Work item {root_id} was not found.")
    return items


def analyze(client: AdoClient, root_id: int) -> dict[str, Any]:
    """Return the subtree below ``root_id`` plus the structural findings.

    Only checks that need no judgement are made here; whether the child
    specifications actually cover the parent is left to the agent.
    """
    config = client.config
    items = collect_subtree(client, root_id)
    info = {item_id: summarize(item) for item_id, item in items.items()}
    findings: list[dict[str, Any]] = []

    def report(item_id: int, code: str, message: str) -> None:
        findings.append({"id": item_id, "code": code, "message": message})

    for item_id, data in info.items():
        level = config.level_of(data["type"] or "")
        if level is None:
            report(item_id, "type-outside-hierarchy", f"{data['type']} is not in the configured hierarchy.")
            continue
        if not data["specification"].strip():
            report(item_id, "missing-specification", "The specification field is empty.")
        if data["ask"] is None:
            report(item_id, "missing-ask", "Human or Agent ask is not set.")
        if data["specification_state"] is None:
            report(item_id, "missing-specification-state", "Specification state is not set.")
        if level < len(config.hierarchy) - 1 and not data["children"]:
            report(item_id, "no-children", f"This {data['type']} has not been broken down yet.")
        category = client.category_of(items[item_id])
        for child_id in data["children"]:
            child = info.get(child_id)
            if child is None:
                continue
            child_level = config.level_of(child["type"] or "")
            if child_level is not None and child_level <= level:
                report(child_id, "hierarchy-order", f"A {child['type']} may not sit below a {data['type']}.")
            if child["specification_state"] == "approved" and data["specification_state"] != "approved":
                report(child_id, "approved-under-unapproved-parent", f"Approved, but parent {item_id} is not approved.")
            if category == "Completed" and client.category_of(items[child_id]) not in ("Completed", "Removed"):
                report(child_id, "open-under-completed-parent", f"Still open while parent {item_id} is completed.")
        for predecessor_id in data["predecessors"]:
            predecessor = items.get(predecessor_id)
            if predecessor is None:
                continue
            if category == "Completed" and client.category_of(predecessor) not in ("Completed", "Removed"):
                report(item_id, "completed-before-predecessor", f"Completed before predecessor {predecessor_id}.")
        siblings = [info[c] for c in data["children"] if c in info]
        if len(siblings) > 1 and not any(s["predecessors"] or s["successors"] for s in siblings):
            report(item_id, "no-sequencing", "The children have no predecessor/successor links; a delivery plan cannot order them.")

    cycle = find_cycle({item_id: [p for p in data["predecessors"] if p in info] for item_id, data in info.items()})
    if cycle:
        report(cycle[0], "dependency-cycle", "Predecessor cycle: " + " -> ".join(str(i) for i in cycle))
    return {"root": root_id, "items": [info[i] for i in sorted(info)], "findings": findings}


# -- Implementation ownership -------------------------------------------------


def git_value(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def require_task(client: AdoClient, item: dict[str, Any]) -> dict[str, Any]:
    info = summarize(item)
    if (info["type"] or "").casefold() != client.config.task_type.casefold():
        raise AdoError(
            f"Work item {info['id']} is a {info['type']}; implementation is tracked on {client.config.task_type} items."
        )
    return info


def claim(client: AdoClient, item_id: int, *, owner: str, tool: str, site: str, branch: str, worktree: str, takeover: bool) -> dict[str, Any]:
    item = client.get_item(item_id)
    info = require_task(client, item)
    if info["specification_state"] != "approved":
        raise AdoError(
            f"The specification of task {item_id} is not approved by the user "
            f"(state: {info['specification_state'] or 'not set'}). Implementation may not start."
        )
    current = info["implementation"]["owner"]
    if current and current != owner and not takeover:
        raise AdoError(
            f"Task {item_id} is owned by {current!r}. Use `handover`, or --takeover when that owner is gone."
        )
    operations = [
        op(F_IMPL_OWNER, owner),
        op(F_IMPL_TOOL, tool),
        op(F_IMPL_SITE, site),
        op(F_IMPL_BRANCH, branch),
        op(F_IMPL_WORKTREE, worktree),
    ]
    if client.category_of(item) == "Proposed":
        operations.append(op("System.State", client.state_for_category(info["type"], "InProgress")))
    client.update_item(item_id, operations)
    if current and current != owner:
        client.add_comment(item_id, f"Implementation taken over from {current} by {owner} ({tool} on {site}).")
    return {"id": item_id, "owner": owner, "tool": tool, "site": site, "branch": branch, "worktree": worktree}


def require_owner(info: dict[str, Any], owner: str) -> None:
    current = info["implementation"]["owner"]
    if current != owner:
        raise AdoError(
            f"Task {info['id']} is owned by {current or 'nobody'}, not {owner!r}. It is outside this agent's scope."
        )


def progress(client: AdoClient, item_id: int, *, owner: str, status: str, note: str) -> dict[str, Any]:
    item = client.get_item(item_id)
    info = require_task(client, item)
    require_owner(info, owner)
    category = {"start": "InProgress", "done": "Completed"}.get(status)
    state = info["state"]
    if category and client.category_of(item) != category:
        state = client.state_for_category(info["type"], category)
        client.update_item(item_id, [op("System.State", state)])
    if note.strip():
        client.add_comment(item_id, note)
    return {"id": item_id, "state": state, "commented": bool(note.strip())}


def handover(client: AdoClient, item_id: int, *, owner: str, to: str, note: str) -> dict[str, Any]:
    info = require_task(client, client.get_item(item_id))
    require_owner(info, owner)
    if not note.strip():
        raise AdoError("A handover needs a note saying what is done, what is left and how to resume.")
    impl = info["implementation"]
    client.update_item(item_id, [op(F_IMPL_OWNER, to)])
    client.add_comment(
        item_id,
        f"Implementation handed over by {owner} to {to or 'nobody (unassigned)'}.\n\n"
        f"Resume from: tool {impl['tool'] or '-'}, site {impl['site'] or '-'}, "
        f"branch {impl['branch'] or '-'}, worktree {impl['worktree'] or '-'}.\n\n{note.strip()}",
    )
    return {"id": item_id, "owner": to, "previous_owner": owner}


# -- Setup and provisioning ---------------------------------------------------


def connect(client: AdoClient) -> dict[str, Any]:
    config = client.config
    project = client.request(
        "GET", client._org(f"projects/{urllib.parse.quote(config.project)}"), query={"includeCapabilities": "true"}
    )
    types = client.request("GET", client._project("wit/workitemtypes")).get("value", [])
    type_names = {t.get("name", "").casefold() for t in types}
    missing_types = [name for name in config.hierarchy if name.casefold() not in type_names]
    known = {f.get("referenceName") for f in client.request("GET", client._org("wit/fields")).get("value", [])}
    template = (project.get("capabilities") or {}).get("processTemplate") or {}
    return {
        "project": project.get("name"),
        "process": template.get("templateName"),
        "process_id": template.get("templateTypeId"),
        "hierarchy": config.hierarchy,
        "missing_work_item_types": missing_types,
        "missing_fields": [d[0] for d in FIELD_DEFINITIONS if d[0] not in known],
        "ok": not missing_types,
    }


def provision_fields(client: AdoClient, *, dry_run: bool) -> dict[str, Any]:
    """Create the custom fields and add them to the hierarchy's work item types.

    Works on inherited processes only: a system process (Agile, Scrum, Basic,
    CMMI as shipped) and on-premises XML processes cannot be changed through
    this API.
    """
    config = client.config
    status = connect(client)
    if status["missing_work_item_types"]:
        raise AdoError("These hierarchy types do not exist in the project: " + ", ".join(status["missing_work_item_types"]))
    process_id = status["process_id"]
    if not process_id:
        raise AdoError("Could not determine the project's process.")
    process = client.request("GET", client._org(f"work/processes/{process_id}"))
    if process.get("customizationType") != "inherited":
        raise AdoError(
            f"Project {config.project} uses the {process.get('name')} process, which is not an inherited "
            "process. Create an inherited process, move the project to it, and run this again."
        )
    actions: list[str] = []
    for reference, name, field_type, labels, _ in FIELD_DEFINITIONS:
        if reference not in status["missing_fields"]:
            continue
        actions.append(f"create field {reference}")
        if dry_run:
            continue
        body: dict[str, Any] = {"name": name, "referenceName": reference, "type": field_type, "usage": "workItem"}
        if labels:
            picklist = client.request(
                "POST",
                client._org("work/processes/lists"),
                {"name": f"{reference}.Values", "type": "String", "items": list(labels.values()), "isSuggested": False},
            )
            body.update({"isPicklist": True, "picklistId": picklist["id"]})
        client.request("POST", client._org("wit/fields"), body)

    base = f"work/processes/{process_id}/workitemtypes"
    process_types = client.request("GET", client._org(base)).get("value", [])
    for level, type_name in enumerate(config.hierarchy):
        match = next((t for t in process_types if t.get("name", "").casefold() == type_name.casefold()), None)
        if match is None:
            raise AdoError(f"Work item type {type_name!r} is not part of process {process.get('name')}.")
        reference = match["referenceName"]
        if match.get("customization") == "system":
            # A system type must be derived into the process before it can take fields.
            actions.append(f"derive work item type {type_name}")
            if not dry_run:
                derived = client.request(
                    "POST",
                    client._org(base),
                    {"name": match["name"], "inheritsFrom": reference, "color": match.get("color"), "icon": match.get("icon"), "description": match.get("description")},
                )
                reference = derived["referenceName"]
        # In a dry run a system type has not been derived, so there is nothing
        # to read yet: every field and form control is still to be added.
        readable = not (dry_run and match.get("customization") == "system")
        present = set()
        layout: dict[str, Any] = {}
        if readable:
            present = {
                f.get("referenceName")
                for f in client.request("GET", client._org(f"{base}/{reference}/fields")).get("value", [])
            }
            layout = client.request("GET", client._org(f"{base}/{reference}/layout")) or {}
        wanted = [
            d for d in FIELD_DEFINITIONS if not d[4] or level == len(config.hierarchy) - 1
        ]
        for field_reference, _, _, _, _ in wanted:
            if field_reference in present:
                continue
            actions.append(f"add {field_reference} to {type_name}")
            if not dry_run:
                client.request("POST", client._org(f"{base}/{reference}/fields"), {"referenceName": field_reference})
        actions.extend(_provision_layout(client, f"{base}/{reference}/layout", layout, type_name, wanted, dry_run))
    return {"process": process.get("name"), "dry_run": dry_run, "actions": actions}


def _provision_layout(
    client: AdoClient,
    layout_path: str,
    layout: dict[str, Any],
    type_name: str,
    wanted: list[tuple[str, str, str, dict[str, str] | None, bool]],
    dry_run: bool,
) -> list[str]:
    """Put the fields on the work item form so people can read and edit them.

    The markdown specification gets a group of its own (a large text field
    cannot share one); the remaining fields go into one "Agent collaboration"
    group. Controls that are already on the form are left where they are.
    """
    pages = [p for p in layout.get("pages", []) if not p.get("isContributed")]
    page = next((p for p in pages if p.get("pageType") == "custom"), pages[0] if pages else None)
    groups = [g for p in pages for s in p.get("sections", []) for g in s.get("groups", [])]
    on_form = {c.get("id") for g in groups for c in g.get("controls", [])}
    actions: list[str] = []

    def add_group(section: str, body: dict[str, Any]) -> dict[str, Any]:
        if page is None:
            raise AdoError(f"The form of {type_name} has no page to add fields to.")
        url = client._org(f"{layout_path}/pages/{page['id']}/sections/{section}/groups")
        return client.request("POST", url, body)

    if F_SPEC not in on_form:
        actions.append(f"show {F_SPEC} on the {type_name} form")
        if not dry_run:
            control = {"id": F_SPEC, "label": "", "controlType": "HtmlFieldControl", "visible": True, "readOnly": False, "isContributed": False}
            add_group("Section1", {"label": "Specification", "visible": True, "controls": [control]})

    plain = [d for d in wanted if d[0] != F_SPEC and d[0] not in on_form]
    if plain:
        group = next((g for g in groups if g.get("label") == LAYOUT_GROUP), None)
        actions.extend(f"show {d[0]} on the {type_name} form" for d in plain)
        if not dry_run:
            if group is None:
                group = add_group("Section2", {"label": LAYOUT_GROUP, "visible": True})
            for field_reference, name, _, _, _ in plain:
                client.request(
                    "POST",
                    client._org(f"{layout_path}/groups/{group['id']}/controls"),
                    {"id": field_reference, "label": name.removeprefix("Etalii "), "visible": True, "readOnly": False, "isContributed": False},
                )
    return actions


# -- Board symbols ------------------------------------------------------------

# Card tints, first match wins: an item waiting on a person outranks the
# agent tint, so "needs you" is never hidden behind "an agent owns this".
CARD_RULES = [
    ("Etalii: waiting on a person", f"[{F_SPEC_STATE}] = '{SPEC_STATE_LABELS['ready-for-review']}'", "#FFF8DF"),
    ("Etalii: agent-owned", f"[{F_ASK}] = '{ASK_LABELS['agent']}'", "#F4ECFB"),
]
CARD_FIELDS = [F_ASK, F_SPEC_STATE]


def style_boards(client: AdoClient, *, dry_run: bool) -> dict[str, Any]:
    """Show ownership on the team's boards: two card fields and two tints.

    Existing card rules and card fields are kept. Our own rules are replaced
    in place and put first, so re-running is safe.
    """
    config = client.config
    scope = urllib.parse.quote(config.project) + (f"/{urllib.parse.quote(config.team)}" if config.team else "")
    base = f"{config.organization_url}/{scope}/_apis/work/boards"
    actions: list[str] = []
    for board in client.request("GET", base).get("value", []):
        name = board["name"]
        board_url = f"{base}/{urllib.parse.quote(name)}"

        rules = client.request("GET", f"{board_url}/cardrulesettings").get("rules") or {}
        ours = [
            {"name": rule, "isEnabled": "true", "filter": query, "settings": {"background-color": color, "title-color": "#000000"}}
            for rule, query, color in CARD_RULES
        ]
        fill = rules.get("fill") or []
        merged = ours + [r for r in fill if r.get("name") not in {rule[0] for rule in CARD_RULES}]
        if merged != fill:
            actions.append(f"set card tints on board {name}")
            if not dry_run:
                client.request("PATCH", f"{board_url}/cardrulesettings", {"rules": {**rules, "fill": merged}})

        cards = client.request("GET", f"{board_url}/cardsettings").get("cards") or {}
        changed = False
        for type_name, shown in cards.items():
            if config.level_of(type_name) is None:
                continue
            present = {field.get("fieldIdentifier") for field in shown}
            missing = [f for f in CARD_FIELDS if f not in present]
            if missing:
                shown.extend({"fieldIdentifier": f} for f in missing)
                changed = True
        if changed:
            actions.append(f"show Ask and Specification State on {name} cards")
            if not dry_run:
                client.request("PUT", f"{board_url}/cardsettings", {"cards": cards})
    return {"dry_run": dry_run, "actions": actions}


def config_init(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    values = {
        "organization_url": args.organization_url.strip().rstrip("/"),
        "project": args.project.strip(),
        "team": (args.team or "").strip(),
        "area_path": (args.area_path or "").strip(),
        "iteration_path": (args.iteration_path or "").strip(),
        "auth": args.auth,
        "pat_env": args.pat_env,
        "hierarchy": " > ".join(parse_hierarchy(args.hierarchy)),
    }
    Config(values).validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_flat_yaml(values), encoding="utf-8")
    return {"config": str(path), **values}


# -- CLI ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ado.py", description="Azure DevOps work item helper for Spec Kit.")
    parser.add_argument("--config", help="Path to ado-config.yml (default: .specify/extensions/ado/ado-config.yml)")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("config-init", help="Write the connection configuration")
    init.add_argument("--organization-url", required=True)
    init.add_argument("--project", required=True)
    init.add_argument("--team")
    init.add_argument("--area-path")
    init.add_argument("--iteration-path")
    init.add_argument("--auth", choices=["azure-cli", "pat"], default="azure-cli")
    init.add_argument("--pat-env", default=DEFAULT_PAT_ENV)
    init.add_argument("--hierarchy", default=DEFAULT_HIERARCHY)

    commands.add_parser("config-show", help="Print the configuration")
    commands.add_parser("connect", help="Verify the connection, hierarchy and custom fields")
    fields = commands.add_parser("fields-provision", help="Create the custom fields on the inherited process")
    fields.add_argument("--dry-run", action="store_true")

    get = commands.add_parser("item-get", help="Print one work item")
    get.add_argument("id", type=int)

    create = commands.add_parser("item-create", help="Create a top-level or child work item")
    create.add_argument("--type", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--ask", choices=sorted(ASK_LABELS), required=True)
    create.add_argument("--parent", type=int)

    begin = commands.add_parser("spec-begin", help="Set 'Worked on by agent' before changing a specification")
    begin.add_argument("id", type=int)
    begin.add_argument("--requested-by-user", action="store_true")
    begin.add_argument("--takeover", action="store_true")
    write = commands.add_parser("spec-write", help="Write the markdown specification field")
    write.add_argument("id", type=int)
    write.add_argument("--file", required=True, help="Markdown file, or - for stdin")
    finish = commands.add_parser("spec-finish", help="Set 'Ready for review by user' when done")
    finish.add_argument("id", type=int)

    apply = commands.add_parser("apply", help="Create children with hierarchy and dependency links")
    apply.add_argument("id", type=int)
    apply.add_argument("--action", choices=["subdivide", "decompose", "plan"], required=True)
    apply.add_argument("--file", required=True, help="Plan JSON file, or - for stdin")

    link = commands.add_parser("link", help="Link two work items")
    link.add_argument("id", type=int)
    link.add_argument("target", type=int)
    link.add_argument("--type", choices=["predecessor", "successor", "related"], required=True)

    analyze_parser = commands.add_parser("analyze", help="Dump a subtree and its structural findings")
    analyze_parser.add_argument("id", type=int)

    claim_parser = commands.add_parser("claim", help="Take implementation ownership of a task")
    claim_parser.add_argument("id", type=int)
    claim_parser.add_argument("--owner", required=True, help="Person, or agent thread identifier")
    claim_parser.add_argument("--tool", required=True, help="Where the work runs: Claude, VS Code, ...")
    claim_parser.add_argument("--site", default=socket.gethostname())
    claim_parser.add_argument("--branch")
    claim_parser.add_argument("--worktree")
    claim_parser.add_argument("--takeover", action="store_true")

    progress_parser = commands.add_parser("progress", help="Report progress on an owned task")
    progress_parser.add_argument("id", type=int)
    progress_parser.add_argument("--owner", required=True)
    progress_parser.add_argument("--status", choices=["start", "note", "done"], required=True)
    progress_parser.add_argument("--note", default="")

    handover_parser = commands.add_parser("handover", help="Hand an owned task to someone else")
    handover_parser.add_argument("id", type=int)
    handover_parser.add_argument("--owner", required=True)
    handover_parser.add_argument("--to", default="", help="New owner; empty leaves the task unassigned")
    handover_parser.add_argument("--note", required=True)

    commands.add_parser("requests", help="List agent requests raised from Azure DevOps")
    boards = commands.add_parser("boards-style", help="Show human and agent ownership on the team boards")
    boards.add_argument("--dry-run", action="store_true")
    return parser


def run(args: argparse.Namespace, client_factory: Callable[[Config], AdoClient] = AdoClient) -> Any:
    config_path = Path(args.config) if args.config else find_project_root() / CONFIG_RELATIVE_PATH
    if args.command == "config-init":
        return config_init(config_path, args)
    config = load_config(config_path)
    if args.command == "config-show":
        return {"config": str(config_path), **config.values}
    client = client_factory(config)
    if args.command == "connect":
        return connect(client)
    if args.command == "fields-provision":
        return provision_fields(client, dry_run=args.dry_run)
    if args.command == "item-get":
        return summarize(client.get_item(args.id))
    if args.command == "item-create":
        if config.level_of(args.type) is None:
            raise AdoError(f"{args.type!r} is not in the configured hierarchy ({' > '.join(config.hierarchy)}).")
        operations = [
            op("System.Title", args.title.strip()),
            op(F_ASK, ASK_LABELS[args.ask]),
            op(F_SPEC_STATE, SPEC_STATE_LABELS["open"]),
        ]
        if config.area_path:
            operations.append(op("System.AreaPath", config.area_path))
        if config.iteration_path:
            operations.append(op("System.IterationPath", config.iteration_path))
        if args.parent:
            operations.append(relation_op("parent", client.work_item_url(args.parent)))
        return summarize(client.create_item(args.type, operations))
    if args.command == "spec-begin":
        return spec_begin(client, args.id, requested_by_user=args.requested_by_user, takeover=args.takeover)
    if args.command == "spec-write":
        return spec_write(client, args.id, read_text_argument(args.file))
    if args.command == "spec-finish":
        return spec_finish(client, args.id)
    if args.command == "apply":
        try:
            plan = json.loads(read_text_argument(args.file))
        except ValueError as error:
            raise AdoError(f"The plan file is not valid JSON: {error}") from error
        return apply_plan(client, args.id, args.action, plan)
    if args.command == "link":
        if args.id == args.target:
            raise AdoError("A work item cannot be linked to itself.")
        if args.target in related_ids(client.get_item(args.id), args.type):
            return {"id": args.id, "target": args.target, "type": args.type, "added": False}
        client.update_item(args.id, [relation_op(args.type, client.work_item_url(args.target))])
        return {"id": args.id, "target": args.target, "type": args.type, "added": True}
    if args.command == "analyze":
        return analyze(client, args.id)
    if args.command == "claim":
        return claim(
            client,
            args.id,
            owner=args.owner,
            tool=args.tool,
            site=args.site,
            branch=args.branch if args.branch is not None else git_value("rev-parse", "--abbrev-ref", "HEAD"),
            worktree=args.worktree if args.worktree is not None else git_value("rev-parse", "--show-toplevel"),
            takeover=args.takeover,
        )
    if args.command == "progress":
        return progress(client, args.id, owner=args.owner, status=args.status, note=args.note)
    if args.command == "handover":
        return handover(client, args.id, owner=args.owner, to=args.to, note=args.note)
    if args.command == "boards-style":
        return style_boards(client, dry_run=args.dry_run)
    if args.command == "requests":
        project = config.project.replace("'", "''")
        ids = client.query_ids(
            "SELECT [System.Id] FROM WorkItems "
            f"WHERE [System.TeamProject] = '{project}' AND [{F_REQUEST}] <> '' "
            "ORDER BY [System.ChangedDate] ASC"
        )
        return {"requests": [summarize(item) for item in client.get_items(ids)] if ids else []}
    raise AdoError(f"Unknown command {args.command!r}.")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except AdoError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
