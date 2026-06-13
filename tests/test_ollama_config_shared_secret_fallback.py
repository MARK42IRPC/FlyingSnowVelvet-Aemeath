from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config.ollama_config as oc


class OllamaConfigSharedSecretFallbackTests(unittest.TestCase):
    def test_load_local_secret_overrides_falls_back_to_shared_secret_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            local_path = root / "workspace" / "resc" / "user" / "ai" / "ollama_secrets.json"
            shared_path = root / "shared" / "resc" / "user" / "ai" / "ollama_secrets.json"
            shared_path.parent.mkdir(parents=True, exist_ok=True)
            shared_path.write_text(
                json.dumps(
                    {
                        "api_key": "shared-api-key",
                        "yuanbao_hy_user": "shared-user",
                        "yuanbao_x_uskey": "shared-uskey",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.object(oc, "_local_secret_path", return_value=local_path), patch.object(
                oc, "get_shared_root_dir", return_value=root / "shared"
            ):
                payload = oc._load_local_secret_overrides()

            self.assertEqual(payload["api_key"], "shared-api-key")
            self.assertEqual(payload["yuanbao_hy_user"], "shared-user")
            self.assertEqual(payload["yuanbao_x_uskey"], "shared-uskey")


if __name__ == "__main__":
    unittest.main()
