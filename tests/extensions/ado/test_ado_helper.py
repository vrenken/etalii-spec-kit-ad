"""Behaviour of the ``ado`` extension's work item helper (scripts/python/ado.py).

Everything runs against :class:`FakeAdo`, an in-memory transport, so these
tests cover the helper's decisions (state machine, scope checks, plan
validation, relations, provisioning) and the shape of the requests it sends.
They do not prove a live Azure DevOps organization accepts those requests.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from .fake_ado import ORG, FakeAdo

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HELPER = PROJECT_ROOT / "extensions" / "ado" / "scripts" / "python" / "ado.py"

_spec = importlib.util.spec_from_file_location("ado_helper", HELPER)
ado = importlib.util.module_from_spec(_spec)
sys.modules["ado_helper"] = ado
_spec.loader.exec_module(ado)


def make_config(hierarchy: str = "Epic > Feature > User Story > Task") -> ado.Config:
    return ado.Config({"organization_url": ORG, "project": "Shop", "auth": "pat", "hierarchy": hierarchy})


@pytest.fixture
def fake() -> FakeAdo:
    return FakeAdo()


@pytest.fixture
def client(fake: FakeAdo) -> ado.AdoClient:
    return ado.AdoClient(make_config(), transport=fake, authorization="Basic test")


def fields(fake: FakeAdo, item_id: int) -> dict:
    return fake.items[item_id]["fields"]


# -- Configuration ------------------------------------------------------------


class TestConfig:
    def test_init_writes_a_flat_file_that_loads_back(self, tmp_path: Path):
        path = tmp_path / ".specify" / "extensions" / "ado" / "ado-config.yml"
        code = ado.main(
            ["--config", str(path), "config-init", "--organization-url", ORG + "/", "--project", "Shop",
             "--hierarchy", "Initiative => Epic => Feature => User Story => Task"]
        )
        assert code == 0
        config = ado.load_config(path)
        assert config.organization_url == ORG
        assert config.hierarchy == ["Initiative", "Epic", "Feature", "User Story", "Task"]
        assert config.task_type == "Task"
        assert config.auth == "azure-cli"

    def test_shipped_template_parses_and_is_rejected_until_setup(self, tmp_path: Path):
        template = PROJECT_ROOT / "extensions" / "ado" / "config-template.yml"
        values = ado.parse_flat_yaml(template.read_text(encoding="utf-8"))
        assert values["hierarchy"] == "Epic > Feature > User Story > Task"
        assert values["pat_env"] == "AZURE_DEVOPS_EXT_PAT"
        path = tmp_path / "ado-config.yml"
        path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        with pytest.raises(ado.AdoError, match="speckit.ado.setup"):
            ado.load_config(path)

    @pytest.mark.parametrize("url", ["http://dev.azure.com/contoso", "dev.azure.com/contoso", ""])
    def test_rejects_non_https_organization(self, url: str):
        with pytest.raises(ado.AdoError, match="https://"):
            ado.Config({"organization_url": url, "project": "Shop"}).validate()

    @pytest.mark.parametrize("hierarchy", ["Task", "Epic > epic > Task", " > "])
    def test_rejects_unusable_hierarchy(self, hierarchy: str):
        with pytest.raises(ado.AdoError, match="hierarchy"):
            ado.parse_hierarchy(hierarchy)

    def test_missing_config_exits_non_zero_with_json_error(self, tmp_path: Path, capsys):
        assert ado.main(["--config", str(tmp_path / "nope.yml"), "config-show"]) == 1
        assert "speckit.ado.setup" in json.loads(capsys.readouterr().err)["error"]

    def test_pat_is_read_from_the_environment_only(self, monkeypatch):
        config = make_config()
        monkeypatch.setattr(ado, "read_environment", lambda name: "")
        with pytest.raises(ado.AdoError, match=config.pat_env):
            ado.acquire_authorization(config)
        monkeypatch.setattr(ado, "read_environment", lambda name: "secret" if name == config.pat_env else "")
        assert ado.acquire_authorization(config) == "Basic OnNlY3JldA=="

    def test_process_environment_wins_and_is_trimmed(self, monkeypatch):
        monkeypatch.setenv("ETALII_TEST_TOKEN", "  from-process ")
        assert ado.read_environment("ETALII_TEST_TOKEN") == "from-process"

    def test_unset_variable_is_empty_off_windows(self, monkeypatch):
        monkeypatch.delenv("ETALII_TEST_TOKEN", raising=False)
        monkeypatch.setattr(ado.sys, "platform", "linux")
        assert ado.read_environment("ETALII_TEST_TOKEN") == ""

    def test_windows_falls_back_to_the_stored_user_environment(self, monkeypatch):
        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class FakeWinreg:
            HKEY_CURRENT_USER = object()

            @staticmethod
            def OpenKey(root, name):
                assert name == "Environment"
                return Key()

            @staticmethod
            def QueryValueEx(key, name):
                if name == "ETALII_TEST_TOKEN":
                    return " stored ", 1
                raise OSError("not found")

        monkeypatch.delenv("ETALII_TEST_TOKEN", raising=False)
        monkeypatch.delenv("ETALII_TEST_MISSING", raising=False)
        monkeypatch.setattr(ado.sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "winreg", FakeWinreg)
        assert ado.read_environment("ETALII_TEST_TOKEN") == "stored"
        assert ado.read_environment("ETALII_TEST_MISSING") == ""


class TestAzureCliLookup:
    def test_falls_back_to_the_default_install_folder_when_path_is_stale(self, tmp_path, monkeypatch):
        cli = tmp_path / "Microsoft SDKs" / "Azure" / "CLI2" / "wbin" / "az.cmd"
        cli.parent.mkdir(parents=True)
        cli.write_text("", encoding="utf-8")
        monkeypatch.setattr(ado.shutil, "which", lambda name: None)
        monkeypatch.setenv("ProgramFiles", str(tmp_path))
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        assert ado.find_azure_cli() == str(cli)

    def test_prefers_the_cli_on_path(self, tmp_path, monkeypatch):
        on_path = str(tmp_path / "az")
        monkeypatch.setattr(ado.shutil, "which", lambda name: on_path)
        assert ado.find_azure_cli() == on_path

    def test_missing_cli_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ado.shutil, "which", lambda name: None)
        monkeypatch.setenv("ProgramFiles", str(tmp_path))
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        assert ado.find_azure_cli() is None
        config = ado.Config({"organization_url": ORG, "project": "Shop", "auth": "azure-cli"})
        with pytest.raises(ado.AdoError, match="was not found"):
            ado.acquire_authorization(config)

    def test_relative_path_hit_is_not_trusted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ado.shutil, "which", lambda name: "az.cmd")
        monkeypatch.setenv("ProgramFiles", str(tmp_path))
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        assert ado.find_azure_cli() is None


class TestStoreToken:
    def test_refuses_to_run_without_a_terminal(self):
        asked = []
        with pytest.raises(ado.AdoError, match="by the user in their own terminal"):
            ado.store_token("ETALII_TEST_TOKEN", interactive=False, prompt=asked.append)
        assert asked == []

    def test_empty_input_stores_nothing(self):
        with pytest.raises(ado.AdoError, match="nothing was stored"):
            ado.store_token("ETALII_TEST_TOKEN", interactive=True, prompt=lambda text: "  ")

    def test_off_windows_it_explains_instead_of_editing_profiles(self, monkeypatch):
        monkeypatch.setattr(ado.sys, "platform", "linux")
        result = ado.store_token("ETALII_TEST_TOKEN", interactive=True, prompt=lambda text: "tok3n-xyz")
        assert result["stored"] is False and "export ETALII_TEST_TOKEN=" in result["next"]
        assert "tok3n-xyz" not in json.dumps(result)

    def test_windows_stores_it_in_the_user_environment(self, monkeypatch):
        written = {}

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class FakeWinreg:
            HKEY_CURRENT_USER, KEY_SET_VALUE, REG_SZ = object(), 2, 1

            @staticmethod
            def OpenKey(root, name, reserved, access):
                assert (name, access) == ("Environment", 2)
                return Key()

            @staticmethod
            def SetValueEx(key, name, reserved, kind, value):
                written[name] = value

        monkeypatch.setattr(ado.sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "winreg", FakeWinreg)
        result = ado.store_token("ETALII_TEST_TOKEN", interactive=True, prompt=lambda text: " tok3n-xyz ")
        assert written == {"ETALII_TEST_TOKEN": "tok3n-xyz"}
        assert result["stored"] is True and "tok3n-xyz" not in json.dumps(result)

    def test_cli_refuses_when_stdin_is_not_a_terminal(self, tmp_path, monkeypatch):
        path = tmp_path / "ado-config.yml"
        path.write_text(ado.render_flat_yaml({"organization_url": ORG, "project": "Shop", "auth": "pat"}), encoding="utf-8")

        class NoTerminal:
            def isatty(self):
                return False

        monkeypatch.setattr(ado.sys, "stdin", NoTerminal())
        with pytest.raises(ado.AdoError, match="own terminal"):
            ado.run(ado.build_parser().parse_args(["--config", str(path), "token-set"]))


class TestLabels:
    @pytest.mark.parametrize("stored", ["Agent", "agent", "\U0001F916 Agent", "  \u2699  AGENT"])
    def test_canonical_ignores_a_leading_glyph(self, stored: str):
        assert ado.canonical(stored, ado.ASK_LABELS) == "agent"

    @pytest.mark.parametrize("stored", [None, "", "Robot", 3])
    def test_canonical_rejects_unknown_values(self, stored):
        assert ado.canonical(stored, ado.ASK_LABELS) is None


# -- Specification state machine ----------------------------------------------


class TestSpecificationState:
    def test_agent_item_goes_through_worked_on_to_ready_for_review(self, fake, client):
        item = fake.add("Feature", "Invoices", **{ado.F_ASK: "Agent", ado.F_SPEC_STATE: "Open"})
        assert ado.spec_begin(client, item, requested_by_user=False, takeover=False)["previous"] == "open"
        assert fields(fake, item)[ado.F_SPEC_STATE] == "⚙ Worked on by agent"
        ado.spec_write(client, item, "## Goal\nDownload invoices.")
        assert fields(fake, item)[ado.F_SPEC] == "## Goal\nDownload invoices."
        assert fake.items[item]["multilineFieldsFormat"] == {ado.F_SPEC: "Markdown"}
        ado.spec_finish(client, item)
        assert fields(fake, item)[ado.F_SPEC_STATE] == "👁 Ready for review by user"

    def test_specification_never_lands_in_the_description(self, fake, client):
        item = fake.add("Feature", "Invoices", **{ado.F_ASK: "Agent"})
        ado.spec_begin(client, item, requested_by_user=False, takeover=False)
        ado.spec_write(client, item, "text")
        assert "System.Description" not in fields(fake, item)

    def test_human_item_is_out_of_scope_without_an_invitation(self, fake, client):
        item = fake.add("Epic", "Portal", **{ado.F_ASK: "Human", ado.F_SPEC_STATE: "Open"})
        with pytest.raises(ado.AdoError, match="outside this agent's scope"):
            ado.spec_begin(client, item, requested_by_user=False, takeover=False)
        assert fake.writes() == []

    def test_approved_specification_is_protected(self, fake, client):
        item = fake.add("Epic", "Portal", **{ado.F_ASK: "Agent", ado.F_SPEC_STATE: "Approved by user"})
        with pytest.raises(ado.AdoError, match="approved by the user"):
            ado.spec_begin(client, item, requested_by_user=False, takeover=False)
        assert fake.writes() == []

    @pytest.mark.parametrize("invitation", ["flag", "request"])
    def test_a_user_invitation_opens_human_and_approved_items(self, fake, client, invitation):
        extra = {ado.F_REQUEST: "Refine", ado.F_REQUEST_NOTES: "shorter"} if invitation == "request" else {}
        item = fake.add("Epic", "Portal", **{ado.F_ASK: "Human", ado.F_SPEC_STATE: "Approved by user"}, **extra)
        ado.spec_begin(client, item, requested_by_user=invitation == "flag", takeover=False)
        assert fields(fake, item)[ado.F_SPEC_STATE] == "⚙ Worked on by agent"

    def test_finish_clears_the_pending_request(self, fake, client):
        item = fake.add("Epic", "Portal", **{ado.F_ASK: "Human", ado.F_REQUEST: "Refine", ado.F_REQUEST_NOTES: "shorter"})
        ado.spec_begin(client, item, requested_by_user=False, takeover=False)
        ado.spec_finish(client, item)
        assert fields(fake, item)[ado.F_REQUEST] == ""
        assert fields(fake, item)[ado.F_REQUEST_NOTES] == ""

    def test_second_agent_cannot_start_on_an_item_in_progress(self, fake, client):
        item = fake.add("Feature", "Invoices", **{ado.F_ASK: "Agent", ado.F_SPEC_STATE: "Worked on by agent"})
        with pytest.raises(ado.AdoError, match="already being worked on"):
            ado.spec_begin(client, item, requested_by_user=True, takeover=False)
        ado.spec_begin(client, item, requested_by_user=False, takeover=True)

    def test_write_and_finish_require_begin(self, fake, client):
        item = fake.add("Feature", "Invoices", **{ado.F_ASK: "Agent", ado.F_SPEC_STATE: "Open"})
        with pytest.raises(ado.AdoError, match="spec begin"):
            ado.spec_write(client, item, "text")
        with pytest.raises(ado.AdoError, match="nothing to finish"):
            ado.spec_finish(client, item)
        assert fake.writes() == []

    def test_empty_specification_is_refused(self, fake, client):
        item = fake.add("Feature", "Invoices", **{ado.F_ASK: "Agent", ado.F_SPEC_STATE: "Worked on by agent"})
        with pytest.raises(ado.AdoError, match="empty"):
            ado.spec_write(client, item, "  \n")

    def test_type_outside_the_hierarchy_is_refused(self, fake, client):
        item = fake.add("Bug", "Crash", **{ado.F_ASK: "Agent"})
        with pytest.raises(ado.AdoError, match="not in the\\s+configured hierarchy|not in the configured hierarchy"):
            ado.spec_begin(client, item, requested_by_user=True, takeover=False)


# -- Breaking items down ------------------------------------------------------


def plan(*children: dict) -> dict:
    return {"children": [{"specification": "## Objective\nDo it.", **child} for child in children]}


class TestChildTypes:
    @pytest.mark.parametrize(
        ("hierarchy", "parent_level", "action", "expected"),
        [
            ("Epic > Feature > User Story > Task", 0, "subdivide", "Feature"),
            ("Epic > Feature > User Story > Task", 1, "decompose", "User Story"),
            ("Epic > Feature > User Story > Task", 1, "plan", "Task"),
            ("Epic > Feature > User Story > Task", 2, "plan", "Task"),
            ("Initiative > Epic > Feature > User Story > Task", 0, "subdivide", "Epic"),
            ("Initiative > Epic > Feature > User Story > Task", 2, "decompose", "User Story"),
        ],
    )
    def test_action_resolves_to_the_right_level(self, hierarchy, parent_level, action, expected):
        assert ado.child_type_for(make_config(hierarchy), parent_level, action) == expected

    def test_task_cannot_be_broken_down(self):
        with pytest.raises(ado.AdoError, match="lowest level"):
            ado.child_type_for(make_config(), 3, "plan")

    def test_story_is_planned_not_decomposed(self):
        with pytest.raises(ado.AdoError, match="use 'plan'"):
            ado.child_type_for(make_config(), 2, "decompose")


class TestApplyPlan:
    def test_creates_children_with_parent_and_predecessor_links(self, fake, client):
        story = fake.add("User Story", "Download PDF", **{"System.AreaPath": "Shop\\Web", "System.IterationPath": "Shop\\S1"})
        result = ado.apply_plan(
            client, story, "plan",
            plan({"key": "api", "title": "Add endpoint"}, {"key": "ui", "title": "Add button", "depends_on": ["api"], "ask": "human"}),
        )
        api, ui = result["children"]["api"], result["children"]["ui"]
        assert result["child_type"] == "Task" and result["created"] == [api, ui] and result["links_added"] == 1
        assert ado.summarize(fake.items[ui])["parent"] == story
        assert ado.summarize(fake.items[ui])["predecessors"] == [api]
        assert ado.summarize(fake.items[api])["successors"] == [ui]
        assert fields(fake, ui)[ado.F_ASK] == "👤 Human" and fields(fake, api)[ado.F_ASK] == "🤖 Agent"
        assert fields(fake, api)[ado.F_SPEC_STATE] == "👁 Ready for review by user"
        assert fields(fake, api)[ado.F_SPEC] == "## Objective\nDo it."
        assert fake.items[api]["multilineFieldsFormat"] == {ado.F_SPEC: "Markdown"}
        assert fields(fake, api)["System.AreaPath"] == "Shop\\Web"
        assert fields(fake, api)["System.IterationPath"] == "Shop\\S1"

    def test_rerun_reuses_children_and_adds_no_duplicate_links(self, fake, client):
        story = fake.add("User Story", "Download PDF")
        the_plan = plan({"key": "api", "title": "Add endpoint"}, {"key": "ui", "title": "Add button", "depends_on": ["api"]})
        first = ado.apply_plan(client, story, "plan", the_plan)
        count = len(fake.items)
        second = ado.apply_plan(client, story, "plan", the_plan)
        assert len(fake.items) == count
        assert second["children"] == first["children"]
        assert second["created"] == [] and second["links_added"] == 0

    def test_can_depend_on_an_existing_work_item(self, fake, client):
        story = fake.add("User Story", "Download PDF")
        other = fake.add("Task", "Provision storage")
        result = ado.apply_plan(client, story, "plan", plan({"key": "api", "title": "Add endpoint", "depends_on": [other]}))
        assert ado.summarize(fake.items[result["children"]["api"]])["predecessors"] == [other]

    @pytest.mark.parametrize(
        ("bad_plan", "message"),
        [
            ({}, "non-empty 'children'"),
            ({"children": []}, "non-empty 'children'"),
            (plan({"key": "a", "title": " "}), "non-empty 'title'"),
            (plan({"key": "a", "title": "A"}, {"key": "a", "title": "B"}), "Duplicate child key"),
            (plan({"key": "a", "title": "A", "specification": ""}), "markdown 'specification'"),
            (plan({"key": "a", "title": "A", "ask": "robot"}), "expected 'human' or 'agent'"),
            (plan({"key": "a", "title": "A", "depends_on": ["zz"]}), "unknown depends_on target"),
            (plan({"key": "a", "title": "A", "depends_on": ["a"]}), "cannot refer to itself"),
            (plan({"key": "a", "title": "A", "depends_on": ["b"]}, {"key": "b", "title": "B", "depends_on": ["a"]}), "Dependency cycle"),
        ],
    )
    def test_invalid_plan_writes_nothing(self, fake, client, bad_plan, message):
        story = fake.add("User Story", "Download PDF")
        with pytest.raises(ado.AdoError, match=message):
            ado.apply_plan(client, story, "plan", bad_plan)
        assert fake.calls == []

    def test_wrong_action_for_the_level_writes_nothing(self, fake, client):
        task = fake.add("Task", "Add endpoint")
        with pytest.raises(ado.AdoError, match="lowest level"):
            ado.apply_plan(client, task, "plan", plan({"key": "a", "title": "A"}))
        assert fake.writes() == []


class TestCycles:
    def test_finds_a_long_cycle(self):
        cycle = ado.find_cycle({"a": ["b"], "b": ["c"], "c": ["a"], "d": []})
        assert cycle[0] == cycle[-1] and set(cycle) == {"a", "b", "c"}

    def test_diamond_is_not_a_cycle(self):
        assert ado.find_cycle({"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}) is None


# -- Analysis -----------------------------------------------------------------


class TestAnalyze:
    def test_consistent_tree_has_no_findings(self, fake, client):
        ok = {ado.F_ASK: "Agent", ado.F_SPEC_STATE: "Approved by user", ado.F_SPEC: "text"}
        story = fake.add("User Story", "Download PDF", **ok)
        first = fake.add("Task", "Add endpoint", parent=story, **ok)
        second = fake.add("Task", "Add button", parent=story, **ok)
        fake._relate(second, "System.LinkTypes.Dependency-Reverse", first)
        result = ado.analyze(client, story)
        assert result["findings"] == []
        assert [i["id"] for i in result["items"]] == [story, first, second]
        assert fake.writes() == []

    def test_reports_structural_problems(self, fake, client):
        epic = fake.add("Epic", "Portal", **{ado.F_ASK: "Human", ado.F_SPEC_STATE: "Open", ado.F_SPEC: "text"})
        feature = fake.add("Feature", "Invoices", parent=epic, **{ado.F_ASK: "Agent", ado.F_SPEC_STATE: "Approved by user", ado.F_SPEC: "text"})
        other = fake.add("Feature", "Payments", parent=epic)
        nested = fake.add("Epic", "Stray epic", parent=feature, **{ado.F_ASK: "Agent", ado.F_SPEC_STATE: "Open", ado.F_SPEC: "text"})
        codes = {(f["id"], f["code"]) for f in ado.analyze(client, epic)["findings"]}
        assert (feature, "approved-under-unapproved-parent") in codes
        assert (other, "missing-specification") in codes
        assert (other, "missing-ask") in codes
        assert (other, "missing-specification-state") in codes
        assert (other, "no-children") in codes
        assert (nested, "hierarchy-order") in codes
        assert (epic, "no-sequencing") in codes

    def test_reports_completion_order_and_cycles(self, fake, client):
        ok = {ado.F_ASK: "Agent", ado.F_SPEC_STATE: "Approved by user", ado.F_SPEC: "text"}
        story = fake.add("User Story", "Download PDF", state="Closed", **ok)
        first = fake.add("Task", "Add endpoint", parent=story, **ok)
        second = fake.add("Task", "Add button", parent=story, state="Closed", **ok)
        fake._relate(second, "System.LinkTypes.Dependency-Reverse", first)
        fake._relate(first, "System.LinkTypes.Dependency-Reverse", second)
        codes = {(f["id"], f["code"]) for f in ado.analyze(client, story)["findings"]}
        assert (first, "open-under-completed-parent") in codes
        assert (second, "completed-before-predecessor") in codes
        assert any(code == "dependency-cycle" for _, code in codes)

    def test_unknown_root_is_an_error(self, client):
        with pytest.raises(ado.AdoError, match="was not found"):
            ado.analyze(client, 999)


# -- Implementation ownership -------------------------------------------------


OWNER = "agent:claude:thread-7f3a"


def approved_task(fake: FakeAdo, **extra) -> int:
    return fake.add("Task", "Add endpoint", **{ado.F_ASK: "Agent", ado.F_SPEC_STATE: "Approved by user", **extra})


def do_claim(client, item, owner=OWNER, takeover=False):
    return ado.claim(client, item, owner=owner, tool="Claude", site="laptop", branch="feat/1-x", worktree="C:/wt", takeover=takeover)


class TestImplementation:
    def test_claim_records_where_the_work_happens_and_starts_the_task(self, fake, client):
        item = approved_task(fake)
        do_claim(client, item)
        values = fields(fake, item)
        assert values[ado.F_IMPL_OWNER] == OWNER
        assert values[ado.F_IMPL_TOOL] == "Claude"
        assert values[ado.F_IMPL_SITE] == "laptop"
        assert values[ado.F_IMPL_BRANCH] == "feat/1-x"
        assert values[ado.F_IMPL_WORKTREE] == "C:/wt"
        assert values["System.State"] == "Active"

    @pytest.mark.parametrize("state", ["Open", "Ready for review by user", "Requires finetuning by agent", "Worked on by agent", None])
    def test_claim_requires_an_approved_specification(self, fake, client, state):
        extra = {ado.F_SPEC_STATE: state} if state else {}
        item = fake.add("Task", "Add endpoint", **{ado.F_ASK: "Agent", **extra})
        with pytest.raises(ado.AdoError, match="not approved by the user"):
            do_claim(client, item)
        assert fake.writes() == []

    def test_claim_is_for_tasks_only(self, fake, client):
        item = fake.add("User Story", "Download PDF", **{ado.F_SPEC_STATE: "Approved by user"})
        with pytest.raises(ado.AdoError, match="tracked on Task items"):
            do_claim(client, item)

    def test_claim_respects_another_owner_unless_taking_over(self, fake, client):
        item = approved_task(fake, **{ado.F_IMPL_OWNER: "sanne@contoso.com"})
        with pytest.raises(ado.AdoError, match="owned by 'sanne@contoso.com'"):
            do_claim(client, item)
        assert fake.writes() == []
        do_claim(client, item, takeover=True)
        assert fields(fake, item)[ado.F_IMPL_OWNER] == OWNER
        assert "taken over from sanne@contoso.com" in fake.comments[item][0]

    def test_progress_done_closes_the_task_and_comments(self, fake, client):
        item = approved_task(fake)
        do_claim(client, item)
        result = ado.progress(client, item, owner=OWNER, status="done", note="All checks pass.")
        assert result == {"id": item, "state": "Closed", "commented": True}
        assert fake.comments[item] == ["All checks pass."]

    def test_progress_note_leaves_the_state_alone(self, fake, client):
        item = approved_task(fake)
        do_claim(client, item)
        ado.progress(client, item, owner=OWNER, status="note", note="Halfway.")
        assert fields(fake, item)["System.State"] == "Active"

    def test_progress_from_a_non_owner_is_out_of_scope(self, fake, client):
        item = approved_task(fake)
        do_claim(client, item)
        before = len(fake.writes())
        with pytest.raises(ado.AdoError, match="outside this agent's scope"):
            ado.progress(client, item, owner="agent:claude:other", status="done", note="")
        assert len(fake.writes()) == before

    def test_handover_records_how_to_resume(self, fake, client):
        item = approved_task(fake)
        do_claim(client, item)
        ado.handover(client, item, owner=OWNER, to="sanne@contoso.com", note="Endpoint done, tests left.")
        assert fields(fake, item)[ado.F_IMPL_OWNER] == "sanne@contoso.com"
        comment = fake.comments[item][-1]
        for expected in ("sanne@contoso.com", "Claude", "laptop", "feat/1-x", "C:/wt", "Endpoint done, tests left."):
            assert expected in comment
        # Where the work lives survives the handover so it can be resumed.
        assert fields(fake, item)[ado.F_IMPL_BRANCH] == "feat/1-x"

    def test_handover_needs_a_note_and_the_current_owner(self, fake, client):
        item = approved_task(fake)
        do_claim(client, item)
        with pytest.raises(ado.AdoError, match="needs a note"):
            ado.handover(client, item, owner=OWNER, to="x", note=" ")
        with pytest.raises(ado.AdoError, match="outside this agent's scope"):
            ado.handover(client, item, owner="someone-else", to="x", note="n")
        assert fields(fake, item)[ado.F_IMPL_OWNER] == OWNER


# -- Setup, provisioning, queue and CLI ---------------------------------------


class TestSetup:
    def test_connect_reports_missing_types_and_fields(self, fake):
        config = make_config("Initiative > Epic > Feature > User Story > Task")
        status = ado.connect(ado.AdoClient(config, transport=fake, authorization="Basic test"))
        assert status["missing_work_item_types"] == ["Initiative"]
        assert status["ok"] is False
        assert set(status["missing_fields"]) == {d[0] for d in ado.FIELD_DEFINITIONS}

    def test_dry_run_plans_without_writing(self, fake, client):
        result = ado.provision_fields(client, dry_run=True)
        assert "create field Custom.EtaliiSpecification" in result["actions"]
        assert "derive work item type Epic" in result["actions"]
        assert fake.writes() == []

    def test_provision_creates_fields_and_scopes_implementation_fields_to_tasks(self, fake, client):
        ado.provision_fields(client, dry_run=False)
        assert {d[0] for d in ado.FIELD_DEFINITIONS} <= fake.fields
        assert [p["items"] for p in fake.picklists if p["name"] == "Custom.EtaliiSpecificationState.Values"] == [
            ["○ Open", "👁 Ready for review by user", "✅ Approved by user", "↻ Requires finetuning by agent", "⚙ Worked on by agent"]
        ]
        assert ado.F_SPEC in fake.type_fields["ShopAgile.Epic"]
        assert ado.F_IMPL_OWNER not in fake.type_fields["ShopAgile.Epic"]
        assert ado.F_IMPL_OWNER in fake.type_fields["ShopAgile.Task"]
        # The fields are on the form: the specification in a group of its own.
        assert fake.form("ShopAgile.Epic") == {
            "Specification": [ado.F_SPEC],
            "Agent collaboration": [ado.F_ASK, ado.F_SPEC_STATE, ado.F_REQUEST, ado.F_REQUEST_NOTES],
        }
        assert fake.form("ShopAgile.Task")["Agent collaboration"][-5:] == [
            ado.F_IMPL_OWNER, ado.F_IMPL_TOOL, ado.F_IMPL_SITE, ado.F_IMPL_BRANCH, ado.F_IMPL_WORKTREE
        ]
        # A second run finds everything in place.
        assert ado.provision_fields(client, dry_run=False)["actions"] == []

    def test_system_process_is_refused(self):
        fake = FakeAdo(customization="system")
        client = ado.AdoClient(make_config(), transport=fake, authorization="Basic test")
        with pytest.raises(ado.AdoError, match="not an inherited\\s+process|not an inherited process"):
            ado.provision_fields(client, dry_run=False)
        assert fake.writes() == []

    @pytest.mark.parametrize("status", [401, 203])
    def test_rejected_credentials_are_reported(self, fake, client, status):
        fake.status_override = status
        with pytest.raises(ado.AdoError, match="rejected the credentials"):
            client.get_item(1)

    def test_a_refusal_carries_the_server_message(self, fake, client):
        fake.status_override = 403
        with pytest.raises(ado.AdoError, match="HTTP 403.: forced failure"):
            client.get_item(1)

    def test_an_account_unknown_to_the_organization_gets_a_way_out(self):
        text = ado.explain_refusal("Identity 0b03 has not been materialized, please use interactive login")
        assert "not a member of this organization" in text and "'pat'" in text
        assert "required permissions" in ado.explain_refusal(None)

    def test_error_bodies_with_a_byte_order_mark_are_parsed(self, monkeypatch):
        class Response:
            status = 200

            def read(self):
                return bytes([0xEF, 0xBB, 0xBF]) + b'{"message": "hello"}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(ado.urllib.request, "urlopen", lambda request, timeout: Response())
        assert ado.urllib_transport("GET", "https://example.invalid/x", {}, None) == (200, {"message": "hello"})

    def test_server_errors_carry_the_message(self, fake, client):
        with pytest.raises(ado.AdoError, match="HTTP 404: Work item does not exist"):
            client.get_item(1)


class TestInheritProcess:
    @pytest.fixture
    def system(self):
        fake = FakeAdo(customization="system")
        return fake, ado.AdoClient(make_config(), transport=fake, authorization="Basic test")

    def test_dry_run_plans_without_writing(self, system):
        fake, client = system
        result = ado.inherit_process(client, name="Etalii Agile", dry_run=True)
        assert result["actions"] == [
            "create process Etalii Agile inheriting from Shop Agile",
            "move project Shop to process Etalii Agile",
        ]
        assert fake.writes() == []

    def test_creates_the_process_and_moves_only_this_project(self, system):
        fake, client = system
        ado.inherit_process(client, name="Etalii Agile", dry_run=False, wait_seconds=0)
        assert fake.project_process == "p2"
        assert fake.processes[-1]["parentProcessTypeId"] == "p1"
        assert [call for call in fake.writes()] == [
            ("POST", "/contoso/_apis/work/processes"),
            ("PATCH", "/contoso/_apis/projects/shop-id"),
        ]

    def test_project_already_on_an_inherited_process_is_left_alone(self, fake, client):
        assert ado.inherit_process(client, name="Etalii Agile", dry_run=False)["actions"] == []
        assert fake.writes() == []

    def test_refused_move_says_how_to_do_it_by_hand(self, system):
        fake, client = system
        fake.refuse_project_move = True
        with pytest.raises(ado.AdoError, match="Change process > Etalii Agile"):
            ado.inherit_process(client, name="Etalii Agile", dry_run=False, wait_seconds=0)
        assert fake.project_process == "p1"

    def test_existing_process_with_another_parent_is_refused(self, system):
        fake, client = system
        fake.processes.append({"name": "Etalii Agile", "typeId": "p9", "parentProcessTypeId": "other"})
        with pytest.raises(ado.AdoError, match="does not inherit from Shop Agile"):
            ado.inherit_process(client, name="etalii agile", dry_run=False)
        assert fake.writes() == []

    def test_name_is_required(self, system):
        with pytest.raises(ado.AdoError, match="needs a name"):
            ado.inherit_process(system[1], name=" ", dry_run=True)


class TestBoardSymbols:
    def test_stored_values_carry_the_glyph(self):
        assert ado.ASK_LABELS == {"human": "\U0001F464 Human", "agent": "\U0001F916 Agent"}
        for key, label in ado.SPEC_STATE_LABELS.items():
            assert not label[0].isalnum() and ado.canonical(label, ado.SPEC_STATE_LABELS) == key

    def test_dry_run_plans_without_writing(self, fake, client):
        result = ado.style_boards(client, dry_run=True)
        assert result["actions"] == [
            "set card tints on board Stories",
            "show Ask and Specification State on Stories cards",
        ]
        assert fake.writes() == []

    def test_tints_come_first_and_existing_rules_survive(self, fake, client):
        ado.style_boards(client, dry_run=False)
        rules = fake.boards["Stories"]["rules"]
        assert [r["name"] for r in rules["fill"]] == ["Etalii: waiting on a person", "Etalii: agent-owned", "Blocked"]
        assert rules["fill"][0]["filter"] == "[Custom.EtaliiSpecificationState] = '\U0001F441 Ready for review by user'"
        assert rules["fill"][1]["filter"] == "[Custom.EtaliiAsk] = '\U0001F916 Agent'"
        assert rules["tagStyle"] == [{"name": "Blocked"}]

    def test_card_fields_are_added_only_to_hierarchy_types(self, fake, client):
        ado.style_boards(client, dry_run=False)
        cards = fake.boards["Stories"]["cards"]
        assert [f["fieldIdentifier"] for f in cards["User Story"]] == ["System.Title", ado.F_ASK, ado.F_SPEC_STATE]
        assert cards["Bug"] == [{"fieldIdentifier": "System.Title"}]

    def test_second_run_changes_nothing(self, fake, client):
        ado.style_boards(client, dry_run=False)
        before = len(fake.writes())
        assert ado.style_boards(client, dry_run=False)["actions"] == []
        assert len(fake.writes()) == before


class TestCli:
    @pytest.fixture
    def cli(self, fake, tmp_path, capsys):
        path = tmp_path / "ado-config.yml"
        path.write_text(ado.render_flat_yaml({"organization_url": ORG, "project": "Shop", "auth": "pat"}), encoding="utf-8")

        def invoke(*argv: str):
            args = ado.build_parser().parse_args(["--config", str(path), *argv])
            return ado.run(args, lambda config: ado.AdoClient(config, transport=fake, authorization="Basic test"))

        return invoke

    def test_requests_lists_only_items_with_a_pending_request(self, fake, cli):
        fake.add("Epic", "Quiet")
        asked = fake.add("Epic", "Portal", **{ado.F_REQUEST: "Subdivide", ado.F_REQUEST_NOTES: "three features"})
        result = cli("requests")
        assert [(r["id"], r["agent_request"], r["agent_request_notes"]) for r in result["requests"]] == [
            (asked, "subdivide", "three features")
        ]

    def test_item_create_sets_ask_state_and_parent(self, fake, cli):
        epic = fake.add("Epic", "Portal")
        result = cli("item-create", "--type", "Feature", "--title", " Invoices ", "--ask", "human", "--parent", str(epic))
        assert result["title"] == "Invoices" and result["ask"] == "human"
        assert result["specification_state"] == "open" and result["parent"] == epic

    def test_item_create_rejects_a_type_outside_the_hierarchy(self, fake, cli):
        with pytest.raises(ado.AdoError, match="not in the configured hierarchy"):
            cli("item-create", "--type", "Bug", "--title", "x", "--ask", "human")
        assert fake.writes() == []

    def test_link_is_idempotent_and_refuses_self_links(self, fake, cli):
        first, second = fake.add("Task", "A"), fake.add("Task", "B")
        assert cli("link", str(second), str(first), "--type", "predecessor")["added"] is True
        assert cli("link", str(second), str(first), "--type", "predecessor")["added"] is False
        assert ado.summarize(fake.items[first])["successors"] == [second]
        with pytest.raises(ado.AdoError, match="linked to itself"):
            cli("link", str(first), str(first), "--type", "related")

    def test_apply_reports_invalid_json(self, fake, cli, tmp_path):
        story = fake.add("User Story", "Download PDF")
        bad = tmp_path / "plan.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(ado.AdoError, match="not valid JSON"):
            cli("apply", str(story), "--action", "plan", "--file", str(bad))
