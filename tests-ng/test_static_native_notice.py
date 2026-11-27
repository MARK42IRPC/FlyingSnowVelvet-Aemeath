"""Release and interaction contracts of the standalone Windows notice host."""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lib.core.render.popup_design import (
    POPUP_ACTION_TOKENS,
    WORKBENCH_LIGHT_TOKENS,
    popup_chrome_dimensions,
)
from scripts import build_offline_installer as builder


class NativeNoticeStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = builder.DEFAULT_INSTALLER_SOURCE / "src"
        cls.notice = (src / "native_notice.c").read_text(encoding="utf-8-sig")
        cls.entries = {
            name: (src / name).read_text(encoding="utf-8-sig")
            for name in ("main.c", "uninstaller.c", "launcher.c", "updater.c")
        }

    def test_native_header_exports_the_canonical_theme_without_app_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "installer_theme.h"
            # -S disables site-packages. A build-time theme import must only use
            # stdlib and must not initialize the application's user settings.
            subprocess.run([
                sys.executable, "-B", "-S", "-c",
                "import sys; "
                "from scripts.build_offline_installer import _write_installer_theme_header; "
                "from pathlib import Path; "
                "_write_installer_theme_header(Path(sys.argv[1])); "
                "assert 'config' not in sys.modules",
                str(path),
            ], cwd=builder.PRODUCT_ROOT, check=True, timeout=30)
            theme = path.read_text(encoding="ascii")
        chrome = popup_chrome_dimensions()
        for field in fields(chrome):
            self.assertIn(f"#define FSV_POPUP_{field.name.upper()} {getattr(chrome, field.name)}\n", theme)
        for name, hex_color in WORKBENCH_LIGHT_TOKENS.items():
            red, green, blue = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
            self.assertIn(f"#define FSV_COLOR_{name.upper()} RGB({red}, {green}, {blue})\n", theme)
        for role, states in POPUP_ACTION_TOKENS.items():
            for state, tokens in states.items():
                colors = ", ".join(f"FSV_COLOR_{token.upper()}" if token else "CLR_INVALID" for token in tokens)
                self.assertIn(f"#define FSV_POPUP_ACTION_{role.upper()}_{state.upper()} {{{colors}}}\n", theme)
        self.assertIn('#include "installer_theme.h"', self.notice)
        self.assertIn("FSV_POPUP_ACTION_DANGER_NORMAL", self.notice)
        self.assertIn("FSV_POPUP_NOTICE_HEIGHT", self.notice)

    def test_cancellation_never_advances_to_the_cleanup_helper(self):
        self.assertIn("button == notice->confirm ? NOTICE_CONFIRMED : NOTICE_DISMISSED", self.notice)
        self.assertIn("(confirm_label != NULL && owner == NULL)", self.notice)
        self.assertIn("SetFocus(notice.dismiss)", self.notice)
        self.assertIn("message.wParam == VK_RETURN || message.wParam == VK_ESCAPE", self.notice)
        self.assertIn("case WM_CLOSE:", self.notice)
        self.assertIn("return result == NOTICE_CONFIRMED;", self.notice)
        uninstaller = self.entries["uninstaller.c"]
        self.assertRegex(
            uninstaller,
            r'if \(fsv_notice_show\(window, L"确认卸载飞行雪绒", confirm, L"卸载飞行雪绒", TRUE\)\) '
            r'\{\s*if \(!launch_cleanup_helper\(',
        )

    def test_errors_share_one_host_and_one_system_fallback(self):
        for name, source in self.entries.items():
            with self.subTest(entry=name):
                self.assertIn('#include "native_notice.h"', source)
                self.assertNotIn("MessageBoxW(", source)
                self.assertIn("fsv_notice_error(NULL,", source)
        for name, title in (("main.c", "安装目录不可用"), ("main.c", "启动失败"), ("uninstaller.c", "卸载启动失败")):
            self.assertIn(f'fsv_notice_error(window, L"{title}"', self.entries[name])
        self.assertEqual(self.notice.count("MessageBoxW("), 1)
        fallback = self.notice.split("BOOL fsv_notice_message(", 1)[1]
        self.assertIn("== NOTICE_UNAVAILABLE", fallback)
        self.assertIn("MB_OK | MB_ICONERROR", fallback)
        updater = self.entries["updater.c"].split("\nfailure:\n", 1)[1]
        self.assertLess(updater.index("rollback_preserved_user("), updater.index("show_failure("))
        self.assertLess(updater.index("show_failure("), updater.index("launch_installed_app("))

    def test_build_links_notice_and_embeds_font_in_all_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commands = []

            def simulate_compile(_vsdevcmd, command, cwd):
                commands.append((command, cwd))
                if command.startswith("cl.exe") and "/link" in command:
                    if cwd.name == "installer":
                        (cwd / "FlyingSnowVelvetInstaller.base.exe").touch()
                    else:
                        output = command.split('/OUT:"', 1)[1].split('"', 1)[0]
                        (cwd / output).touch()

            def fake_font(output, **_kwargs):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"font stand-in")
                return output

            icon = builder.PRODUCT_ROOT / "resc" / "icon.ico"
            with patch.object(builder, "run_vs_command", side_effect=simulate_compile), \
                    patch.object(builder, "create_installer_font_subset", side_effect=fake_font), \
                    patch.object(builder, "_compile_zlib"):
                builder.compile_payload_binaries(root / "payload", builder.DEFAULT_INSTALLER_SOURCE, icon, root, root / "build")
                builder.compile_installer(None, None, builder.DEFAULT_INSTALLER_SOURCE, icon, root, root / "installer", online=True)
            self.assertCountEqual(
                [cwd.name for command, cwd in commands if command.startswith("cl.exe") and '"native_notice.c"' in command],
                ["installer", "launcher", "uninstaller", "updater"],
            )
            for binary in ("launcher", "uninstaller", "updater"):
                cwd = root / "build" / binary
                self.assertIn("IDR_HARMONY_FONT RCDATA", (cwd / "native.rc").read_text(encoding="ascii"))
                self.assertTrue((cwd / "native_notice.h").is_file())
            self.assertIn("IDR_HARMONY_FONT RCDATA", (root / "installer" / "resource.rc").read_text(encoding="ascii"))


if __name__ == "__main__":
    unittest.main()
