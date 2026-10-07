"""Tests for the bundled ``ado`` extension (extensions/ado/).

Validates the bundled layout, catalog and wheel registration, manifest
validation, install/uninstall through ``ExtensionManager``, that rendered
commands point at the script the extension ships, and that the bash and
PowerShell launchers reach the Python helper.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from specify_cli import _locate_bundled_extension
from tests.conftest import requires_bash
from tests.parity_helpers import HAS_PWSH

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
EXT_DIR = PROJECT_ROOT / "extensions" / "ado"
INSTALL_SPECKIT_VERSION = "1.0.12"

COMMANDS = {
    f"speckit.ado.{name}"
    for name in (
        "setup", "specify", "subdivide", "decompose", "plan", "regenerate",
        "refine", "analyze", "implement", "handover", "requests",
    )
}
SCRIPTS = ["scripts/bash/ado.sh", "scripts/powershell/ado.ps1", "scripts/python/ado.py"]


def _manifest() -> dict:
    return yaml.safe_load((EXT_DIR / "extension.yml").read_text(encoding="utf-8"))


def _command(name: str) -> str:
    return (EXT_DIR / "commands" / f"speckit.ado.{name}.md").read_text(encoding="utf-8")


class TestLayout:
    def test_manifest_lists_every_command_file(self):
        commands = _manifest()["provides"]["commands"]
        assert {c["name"] for c in commands} == COMMANDS
        for command in commands:
            assert (EXT_DIR / command["file"]).is_file()
            assert command["file"] == f"commands/{command['name']}.md"

    @pytest.mark.parametrize("rel_path", SCRIPTS + ["config-template.yml", "specification-guide.md", "README.md"])
    def test_ships(self, rel_path: str):
        assert (EXT_DIR / rel_path).is_file()

    def test_manifest_validates(self):
        from specify_cli.extensions import ExtensionManifest

        manifest = ExtensionManifest(EXT_DIR / "extension.yml")
        assert manifest.id == "ado"
        assert {c["name"] for c in manifest.commands} == COMMANDS

    def test_catalog_and_wheel_registration(self):
        catalog = json.loads((PROJECT_ROOT / "extensions" / "catalog.json").read_text(encoding="utf-8"))
        entry = catalog["extensions"]["ado"]
        assert entry["bundled"] is True
        assert entry["version"] == _manifest()["extension"]["version"]
        pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
        assert force_include["extensions/ado"] == "specify_cli/core_pack/extensions/ado"
        located = _locate_bundled_extension("ado")
        assert located is not None and (located / "extension.yml").is_file()


class TestCommandPrompts:
    @pytest.mark.parametrize("name", sorted(n.rsplit(".", 1)[-1] for n in COMMANDS - {"speckit.ado.setup"}))
    def test_specifications_stay_out_of_markdown_files_and_the_description(self, name: str):
        text = _command(name)
        assert "**Etalii Specification** field" in text
        assert "Never write them to the Description field" in text
        assert "never create `spec.md`, `plan.md` or `tasks.md` files" in text

    @pytest.mark.parametrize("name", ["specify", "regenerate", "refine"])
    def test_rewriting_commands_bracket_the_edit_with_the_state_transitions(self, name: str):
        text = _command(name)
        begin, write, finish = (text.index("{SCRIPT} " + step) for step in ("spec-begin", "spec-write", "spec-finish"))
        assert begin < write < finish

    @pytest.mark.parametrize("name", ["subdivide", "decompose", "plan"])
    def test_breakdown_commands_apply_with_their_own_action(self, name: str):
        text = _command(name)
        assert "{SCRIPT} apply <id> --action " + name + " --file" in text
        assert "depends_on" in text

    def test_setup_never_asks_for_the_token(self):
        assert "Never ask for the token itself" in _command("setup")

    def test_every_helper_subcommand_a_prompt_uses_exists(self):
        helper = (EXT_DIR / "scripts" / "python" / "ado.py").read_text(encoding="utf-8")
        known = set(re.findall(r'add_parser\(\s*"([a-z-]+)"', helper))
        used: set[str] = set()
        for path in (EXT_DIR / "commands").glob("*.md"):
            used |= set(re.findall(r"\{SCRIPT\} ([a-z][a-z-]+)", path.read_text(encoding="utf-8")))
        assert used and used <= known, used - known


class TestInstall:
    def _project(self, tmp_path: Path) -> Path:
        project = tmp_path / "project"
        (project / ".specify").mkdir(parents=True)
        (project / ".specify" / "init-options.json").write_text(
            json.dumps({"ai": "generic", "ai_skills": False, "script": "py"}), encoding="utf-8"
        )
        (project / ".specify" / "integration.json").write_text(
            json.dumps(
                {
                    "integration": "generic",
                    "default_integration": "generic",
                    "installed_integrations": ["generic"],
                    "integration_settings": {
                        "generic": {
                            "raw_options": "--commands-dir .myagent/commands",
                            "parsed_options": {"commands_dir": ".myagent/commands", "skills": False},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (project / ".myagent" / "commands").mkdir(parents=True)
        return project

    def test_install_renders_commands_against_the_shipped_script(self, tmp_path: Path):
        from specify_cli.extensions import ExtensionManager

        project = self._project(tmp_path)
        manager = ExtensionManager(project)
        manager.install_from_directory(EXT_DIR, INSTALL_SPECKIT_VERSION, register_commands=True)

        installed = project / ".specify" / "extensions" / "ado"
        for rel_path in SCRIPTS + ["specification-guide.md"]:
            assert (installed / rel_path).is_file(), rel_path
        for name in COMMANDS:
            content = (project / ".myagent" / "commands" / f"{name}.md").read_text(encoding="utf-8")
            assert "{SCRIPT}" not in content
            assert "__SPECKIT_COMMAND_" not in content
            assert ".specify/extensions/ado/scripts/python/ado.py" in content

        assert manager.remove("ado")
        assert not (project / ".myagent" / "commands" / "speckit.ado.setup.md").exists()


def _expect_setup_error(command: list[str], cwd: Path) -> None:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    assert result.returncode == 1, result
    assert "speckit.ado.setup" in json.loads(result.stderr)["error"]


class TestLaunchers:
    """Each entry point reaches the helper and relays its exit code and JSON error."""

    def test_python(self, tmp_path: Path):
        _expect_setup_error([sys.executable, str(EXT_DIR / SCRIPTS[2]), "config-show"], tmp_path)

    @requires_bash
    def test_bash(self, tmp_path: Path):
        _expect_setup_error(["bash", str(EXT_DIR / SCRIPTS[0]), "config-show"], tmp_path)

    @pytest.mark.skipif(not HAS_PWSH, reason="pwsh not available")
    def test_powershell(self, tmp_path: Path):
        _expect_setup_error(["pwsh", "-NoProfile", "-File", str(EXT_DIR / SCRIPTS[1]), "config-show"], tmp_path)


class TestBrowserPluginStaysInStep:
    """The browser plugin writes the same fields and labels the helper reads."""

    PLUGIN = PROJECT_ROOT / "azure-devops-extension" / "src"

    def _helper(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("ado_helper_for_plugin", EXT_DIR / "scripts" / "python" / "ado.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def _strings(self, name: str) -> set[str]:
        text = (self.PLUGIN / name).read_text(encoding="utf-8")
        # TypeScript writes glyphs as \u{1F916} or ⚙ escapes.
        decoded = re.sub(r"[\\]u\{([0-9A-Fa-f]+)\}", lambda m: chr(int(m.group(1), 16)), text)
        decoded = re.sub(r"[\\]u([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), decoded)
        return set(re.findall(r'"([^"\n]+)"', decoded))

    def test_field_reference_names_exist_in_the_helper(self):
        helper = self._helper()
        known = {definition[0] for definition in helper.FIELD_DEFINITIONS}
        used = {s for name in ("model.ts", "chat-model.ts") for s in self._strings(name) if s.startswith("Custom.Etalii")}
        assert used and used <= known, used - known

    def test_stored_labels_match_the_helper(self):
        helper = self._helper()
        strings = self._strings("model.ts") | self._strings("chat-model.ts")
        for label in (
            *helper.ASK_LABELS.values(),
            helper.SPEC_STATE_LABELS["worked-on"],
            helper.SPEC_STATE_LABELS["ready-for-review"],
            helper.SPEC_STATE_LABELS["requires-finetuning"],
            *helper.REQUEST_LABELS.values(),
        ):
            assert label in strings, label
