from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.core import dsh_runtime_contract as dsh_config
from lib.script.office import runtime as office_runtime


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_source_bundle(root: Path) -> None:
    _write_json(root / "package.json", {
        "engines": {
            "node": dsh_config.NODE_VERSION,
            "npm": dsh_config.NPM_VERSION,
        },
        "dependencies": {"@deepseek-ai/dsh": dsh_config.DSH_VERSION},
    })
    _write_json(root / "package-lock.json", {
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"@deepseek-ai/dsh": dsh_config.DSH_VERSION}},
            "node_modules/@deepseek-ai/dsh": {"version": dsh_config.DSH_VERSION},
        },
    })
    _write_json(root / "profile" / "package.json", {
        "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base"]}},
        "dependencies": {"@fsv/dsh-office-bridge": "0.1.0"},
    })
    _write_json(root / "bridge" / "package.json", {
        "name": "@fsv/dsh-office-bridge",
    })
    for relative in (
        "profile/cordis.patch.yml",
        "bridge/index.mjs",
        "bridge/credentials.mjs",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// fixture\n", encoding="utf-8")


class DshRuntimeContractTests(unittest.TestCase):
    def test_node_download_urls_prefer_domestic_mirrors(self):
        urls = dsh_config.NODE_DOWNLOAD_URLS
        self.assertIn("npmmirror.com", urls[0])
        self.assertIn("nodejs.org", urls[-1])

    def test_repository_source_bundle_matches_fixed_contract(self):
        self.assertEqual(dsh_config.runtime_source_error(office_runtime.project_root()), "")

    def test_source_bundle_requires_profile_bridge_and_matching_lockfile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "runtime"
            _write_source_bundle(root)
            with patch.object(dsh_config, "dsh_runtime_root", return_value=root):
                self.assertEqual(dsh_config.runtime_source_error(Path(tmpdir)), "")

                (root / "bridge" / "index.mjs").unlink()
                self.assertIn(
                    "bridge/index.mjs",
                    dsh_config.runtime_source_error(Path(tmpdir)),
                )

                (root / "bridge" / "index.mjs").write_text("// restored\n", encoding="utf-8")
                lockfile = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
                lockfile["packages"][""]["dependencies"]["@deepseek-ai/dsh"] = "latest"
                _write_json(root / "package-lock.json", lockfile)
                self.assertIn(
                    "package-lock.json",
                    dsh_config.runtime_source_error(Path(tmpdir)),
                )

    def test_installed_runtime_requires_generic_adapter_and_bridge_dependencies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = Path(tmpdir) / "runtime"
            entry = runtime_root / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
            entry.parent.mkdir(parents=True)
            entry.write_text("// entry\n", encoding="utf-8")
            for package in dsh_config.REQUIRED_DSH_PACKAGES:
                _write_json(
                    runtime_root
                    / "node_modules"
                    / "@deepseek-ai"
                    / package
                    / "package.json",
                    {"version": dsh_config.DSH_VERSION},
                )
            with patch.object(dsh_config, "dsh_runtime_root", return_value=runtime_root):
                self.assertEqual(dsh_config.installed_runtime_error(Path(tmpdir)), "")
                (
                    runtime_root
                    / "node_modules"
                    / "@deepseek-ai"
                    / "dsh-llm-pi-ai"
                    / "package.json"
                ).unlink()
                self.assertIn(
                    "dsh-llm-pi-ai",
                    dsh_config.installed_runtime_error(Path(tmpdir)),
                )

    def test_openai_endpoint_normalization_preserves_version_prefix(self):
        self.assertEqual(
            office_runtime.normalize_openai_base_url(
                "https://example.test/v1/chat/completions/"
            ),
            "https://example.test/v1",
        )

    def test_office_runtime_exposes_bundled_skill_root(self):
        self.assertEqual(
            office_runtime.office_skill_root(),
            office_runtime.project_root() / "resc" / "agent",
        )
        self.assertEqual(
            office_runtime.normalize_openai_base_url("https://example.test/v1"),
            "https://example.test/v1",
        )

    def test_profile_uses_generic_route_without_vendor_reasoning_fields(self):
        runtime_root = dsh_config.dsh_runtime_root(office_runtime.project_root())
        profile = (runtime_root / "profile" / "cordis.patch.yml").read_text(encoding="utf-8")
        bridge = (runtime_root / "bridge" / "index.mjs").read_text(encoding="utf-8")

        self.assertIn("provider: fsv-office", profile)
        self.assertIn("id: llm-pi-ai", profile)
        self.assertIn("reasoningEfforts: false", profile)
        self.assertNotIn("provider: deepseek-official", profile)
        self.assertNotIn("reasoningEffort: high", profile)
        self.assertIn("process.env.FSV_OFFICE_SYSTEM_PROMPT", profile)
        self.assertNotIn("You are the office coding agent", profile)
        self.assertIn(
            "office coding agent inside Flying Snow Velvet",
            office_runtime.load_office_system_prompt(),
        )
        self.assertIn("fsv_office_reasoning_strategy", bridge)
        selection = bridge.split("function selectionFor(command)", 1)[1].split(
            "async function createTask", 1
        )[0]
        self.assertNotIn("reasoningEffort", selection)


if __name__ == "__main__":
    unittest.main()
