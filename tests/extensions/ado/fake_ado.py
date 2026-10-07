"""In-memory stand-in for the Azure DevOps REST API used by the ado helper tests."""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

ORG = "https://dev.azure.com/contoso"

STATES = [
    {"name": "New", "category": "Proposed"},
    {"name": "Active", "category": "InProgress"},
    {"name": "Closed", "category": "Completed"},
]


class FakeAdo:
    """Transport that records every call and keeps work items in memory."""

    def __init__(self, *, customization: str = "inherited"):
        self.items: dict[int, dict[str, Any]] = {}
        self.comments: dict[int, list[str]] = {}
        self.calls: list[tuple[str, str]] = []
        self.fields: set[str] = {"System.Title"}
        self.type_fields: dict[str, set[str]] = {}
        self.picklists: list[dict[str, Any]] = []
        self.layouts: dict[str, list[dict[str, Any]]] = {}
        self.boards: dict[str, dict[str, Any]] = {
            "Stories": {
                "rules": {"fill": [{"name": "Blocked", "isEnabled": "true", "filter": "[System.Tags] contains 'Blocked'", "settings": {"background-color": "#F8D7DA"}}], "tagStyle": [{"name": "Blocked"}]},
                "cards": {"User Story": [{"fieldIdentifier": "System.Title"}], "Bug": [{"fieldIdentifier": "System.Title"}]},
            }
        }
        self.customization = customization
        self.types = {
            name: {"name": name, "referenceName": f"Microsoft.VSTS.WorkItemTypes.{name.replace(' ', '')}", "customization": "system"}
            for name in ("Epic", "Feature", "User Story", "Task")
        }
        self.status_override: int | None = None
        self.project_process = "p1"
        self.refuse_project_move = False
        self.processes: list[dict[str, Any]] = [{"name": "Shop Agile", "typeId": "p1", "customizationType": customization}]
        self._next_id = 100

    # -- test helpers ---------------------------------------------------------

    def add(self, work_item_type: str, title: str, *, parent: int | None = None, state: str = "New", **fields: Any) -> int:
        self._next_id += 1
        item_id = self._next_id
        self.items[item_id] = {
            "id": item_id,
            "fields": {"System.WorkItemType": work_item_type, "System.Title": title, "System.State": state, **fields},
            "relations": [],
        }
        if parent is not None:
            self._relate(item_id, "System.LinkTypes.Hierarchy-Reverse", parent)
        return item_id

    def writes(self) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0] != "GET" and not call[1].endswith("/wit/wiql")]

    def _relate(self, source: int, rel: str, target: int) -> None:
        reverse = {
            "System.LinkTypes.Hierarchy-Reverse": "System.LinkTypes.Hierarchy-Forward",
            "System.LinkTypes.Hierarchy-Forward": "System.LinkTypes.Hierarchy-Reverse",
            "System.LinkTypes.Dependency-Reverse": "System.LinkTypes.Dependency-Forward",
            "System.LinkTypes.Dependency-Forward": "System.LinkTypes.Dependency-Reverse",
            "System.LinkTypes.Related": "System.LinkTypes.Related",
        }[rel]
        self.items[source]["relations"].append({"rel": rel, "url": f"{ORG}/_apis/wit/workItems/{target}"})
        self.items[target]["relations"].append({"rel": reverse, "url": f"{ORG}/_apis/wit/workItems/{source}"})

    def _apply(self, item: dict[str, Any], operations: list[dict[str, Any]]) -> None:
        for operation in operations:
            path, value = operation["path"], operation["value"]
            if path.startswith("/fields/"):
                item["fields"][path[len("/fields/"):]] = value
            elif path.startswith("/multilineFieldsFormat/"):
                item.setdefault("multilineFieldsFormat", {})[path.rsplit("/", 1)[-1]] = value
            elif path == "/relations/-":
                self._relate(item["id"], value["rel"], int(value["url"].rsplit("/", 1)[-1]))
            else:
                raise AssertionError(f"unexpected patch path {path}")

    # -- transport ------------------------------------------------------------

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, Any]:
        parsed = urllib.parse.urlsplit(url)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        assert "api-version" in query, url
        assert headers["Authorization"] == "Basic test"
        self.calls.append((method, path))
        if self.status_override is not None:
            return self.status_override, {"message": "forced failure"}
        payload = json.loads(body) if body else None

        match = re.fullmatch(r"/contoso/Shop/_apis/wit/workitems/(\d+)", path)
        if match:
            item = self.items.get(int(match.group(1)))
            if item is None:
                return 404, {"message": "Work item does not exist"}
            if method == "PATCH":
                assert headers["Content-Type"] == "application/json-patch+json"
                self._apply(item, payload)
            return 200, item
        match = re.fullmatch(r"/contoso/Shop/_apis/wit/workitems/\$(.+)", path)
        if match and method == "POST":
            self._next_id += 1
            item = {"id": self._next_id, "fields": {"System.WorkItemType": match.group(1), "System.State": "New"}, "relations": []}
            self.items[item["id"]] = item
            self._apply(item, payload)
            return 200, item
        if path == "/contoso/_apis/wit/workitems":
            ids = [int(i) for i in query["ids"][0].split(",")]
            return 200, {"value": [self.items[i] for i in ids if i in self.items]}
        match = re.fullmatch(r"/contoso/Shop/_apis/wit/workitemtypes/(.+)/states", path)
        if match:
            return 200, {"value": STATES}
        match = re.fullmatch(r"/contoso/Shop/_apis/wit/workItems/(\d+)/comments", path)
        if match:
            self.comments.setdefault(int(match.group(1)), []).append(payload["text"])
            return 200, {}
        if path == "/contoso/Shop/_apis/wit/wiql":
            field = re.search(r"\[(Custom\.[A-Za-z]+)\] <> ''", payload["query"]).group(1)
            return 200, {"workItems": [{"id": i} for i, item in sorted(self.items.items()) if item["fields"].get(field)]}
        if path == "/contoso/_apis/projects/Shop":
            return 200, {"id": "shop-id", "name": "Shop", "capabilities": {"processTemplate": {"templateName": "Shop Agile", "templateTypeId": self.project_process}}}
        if path == "/contoso/_apis/projects/shop-id" and method == "PATCH":
            if self.refuse_project_move:
                return 400, {"message": "The project update is invalid."}
            self.project_process = payload["capabilities"]["processTemplate"]["templateTypeId"]
            return 200, {"status": "queued"}
        if path == "/contoso/_apis/work/processes":
            if method == "POST":
                created = {"name": payload["name"], "typeId": "p2", "customizationType": "inherited", "parentProcessTypeId": payload["parentProcessTypeId"]}
                self.processes.append(created)
                return 200, created
            return 200, {"value": self.processes}
        if path == "/contoso/_apis/work/processes/p2":
            return 200, {"name": "Etalii Agile", "customizationType": "inherited"}
        if path == "/contoso/Shop/_apis/wit/workitemtypes":
            return 200, {"value": list(self.types.values())}
        if path == "/contoso/_apis/wit/fields":
            if method == "POST":
                self.fields.add(payload["referenceName"])
                return 200, payload
            return 200, {"value": [{"referenceName": name} for name in sorted(self.fields)]}
        if path == "/contoso/_apis/work/processes/p1":
            return 200, {"name": "Shop Agile", "customizationType": self.customization}
        if path == "/contoso/_apis/work/processes/lists":
            self.picklists.append(payload)
            return 200, {"id": f"list-{len(self.picklists)}"}
        if path == "/contoso/_apis/work/processes/p1/workitemtypes":
            if method == "POST":
                derived = {"name": payload["name"], "referenceName": f"ShopAgile.{payload['name'].replace(' ', '')}", "customization": "inherited"}
                self.types[payload["name"]] = derived
                return 200, derived
            return 200, {"value": list(self.types.values())}
        match = re.fullmatch(r"/contoso/_apis/work/processes/p1/workitemtypes/([^/]+)/fields", path)
        if match:
            fields = self.type_fields.setdefault(match.group(1), set())
            if method == "POST":
                fields.add(payload["referenceName"])
                return 200, payload
            return 200, {"value": [{"referenceName": name} for name in sorted(fields)]}
        if path == "/contoso/Shop/_apis/work/boards":
            return 200, {"value": [{"name": name} for name in self.boards]}
        match = re.fullmatch(r"/contoso/Shop/_apis/work/boards/([^/]+)/(cardrulesettings|cardsettings)", path)
        if match:
            board = self.boards[match.group(1)]
            key = "rules" if match.group(2) == "cardrulesettings" else "cards"
            if method != "GET":
                assert method == ("PATCH" if key == "rules" else "PUT")
                board[key] = payload[key]
            return 200, {key: json.loads(json.dumps(board[key]))}
        layout = r"/contoso/_apis/work/processes/p1/workitemtypes/([^/]+)/layout"
        match = re.fullmatch(layout, path)
        if match:
            groups = self.layouts.setdefault(match.group(1), [])
            return 200, {"pages": [{"id": "page1", "pageType": "custom", "sections": [{"id": "Section1", "groups": groups}]}]}
        match = re.fullmatch(layout + r"/pages/page1/sections/(Section\d)/groups", path)
        if match and method == "POST":
            groups = self.layouts.setdefault(match.group(1), [])
            group = {"id": f"group-{len(groups) + 1}", "label": payload["label"], "controls": list(payload.get("controls", []))}
            groups.append(group)
            return 200, group
        match = re.fullmatch(layout + r"/groups/([^/]+)/controls", path)
        if match and method == "POST":
            group = next(g for g in self.layouts[match.group(1)] if g["id"] == match.group(2))
            group["controls"].append(payload)
            return 200, payload
        raise AssertionError(f"unexpected request {method} {path}")

    def form(self, type_reference: str) -> dict[str, list[str]]:
        """Group label -> control ids, as the work item form would show them."""
        return {g["label"]: [c["id"] for c in g["controls"]] for g in self.layouts.get(type_reference, [])}
