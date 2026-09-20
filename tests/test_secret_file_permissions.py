"""回归：密钥文件的落盘权限必须收紧到「仅当前用户」。

`<用户根>/user/secrets/` 下是论坛 token、AI 密钥与音乐平台 Cookie。默认继承共享根
目录的权限时会带上 `BUILTIN\\Users`/`Authenticated Users`，同机其它账户能直接读走。
这里锁定 `lib/core/secret_files.harden_secret_path()` 的行为，以及它被接上的写入点。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.core import secret_files
from lib.core.secret_files import harden_secret_path

_IS_WINDOWS = os.name == "nt"


def _acl_identities(path: Path) -> set[str]:
    """返回路径 DACL 里的主体名（仅 Windows）。"""
    import win32security

    descriptor = win32security.GetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
    )
    dacl = descriptor.GetSecurityDescriptorDacl()
    names = set()
    for index in range(dacl.GetAceCount()):
        # pywin32 的 ACL.GetAce 返回 (ace_header, mask, sid) 三元组
        _ace_header, _mask, sid = dacl.GetAce(index)
        try:
            names.add(win32security.LookupAccountSid(None, sid)[0])
        except Exception:
            names.add(str(sid))
    return names


class HardenSecretPathTests(unittest.TestCase):
    def test_missing_path_is_reported_as_not_hardened(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(harden_secret_path(Path(tmpdir) / "absent.json"))

    @unittest.skipUnless(_IS_WINDOWS, "仅 Windows 走 ACL")
    def test_windows_drops_broadly_readable_principals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            secret = Path(tmpdir) / "ai.json"
            secret.write_text('{"api_key": "x"}', encoding="utf-8")

            self.assertTrue(harden_secret_path(secret))

            remaining = _acl_identities(secret)
            self.assertNotIn("Users", remaining)
            self.assertNotIn("Authenticated Users", remaining)
            self.assertIn("Administrators", remaining)

    @unittest.skipUnless(_IS_WINDOWS, "仅 Windows 走 ACL")
    def test_directory_acl_is_inherited_by_new_secret_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            secrets = Path(tmpdir) / "secrets"
            secrets.mkdir()

            self.assertTrue(harden_secret_path(secrets))
            inheritor = secrets / "music.json"
            inheritor.write_text("{}", encoding="utf-8")

            self.assertNotIn("Users", _acl_identities(inheritor))

    @unittest.skipIf(_IS_WINDOWS, "POSIX 用 chmod")
    def test_posix_uses_owner_only_bits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            secret = Path(tmpdir) / "ai.json"
            secret.write_text("{}", encoding="utf-8")

            self.assertTrue(harden_secret_path(secret))

            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)

    def test_hardening_failure_never_raises(self):
        """加固失败只是少一层保护，不能影响调用方原本的写入结果。"""
        broken = "_harden_windows" if _IS_WINDOWS else "_harden_posix"
        with tempfile.TemporaryDirectory() as tmpdir:
            secret = Path(tmpdir) / "ai.json"
            secret.write_text("{}", encoding="utf-8")
            with patch.object(secret_files, broken, side_effect=OSError("denied")):
                self.assertFalse(harden_secret_path(secret))


class SecretWriteSitesHardenTests(unittest.TestCase):
    """密钥写入点必须真的接线到加固助手，避免只加助手却忘了调用。"""

    def test_ai_secret_write_hardens_the_file(self):
        from lib.script.ui import ai_settings_storage as storage

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "secrets" / "ai.json"
            with patch.object(storage, "_local_ai_secret_path", return_value=target), patch.object(
                storage, "harden_secret_path"
            ) as harden:
                storage._write_local_ai_secrets({"api_key": "k"})

            self.assertTrue(target.exists())
            harden.assert_called_once_with(target)

    def test_forum_session_write_hardens_the_file(self):
        from lib.core import forum_session

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "forum" / "session.json"
            with patch.object(forum_session, "session_path", return_value=target), patch.object(
                forum_session, "harden_secret_path"
            ) as harden:
                saved = forum_session.save_session(
                    forum_session.ForumSession(token="t", expires_at=2**40)
                )

            self.assertTrue(saved)
            self.assertTrue(target.exists())
            harden.assert_called_once_with(target)

    def test_netease_login_cache_hardens_the_file(self):
        from lib.script.cloudmusic import _mixin_login as login

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "cloudmusic_login_cache.json"
            cookie_jar = type("C", (), {"get_dict": lambda self: {"k": "v"}})()
            session = type("S", (), {"cookies": cookie_jar})()
            owner = object.__new__(login._LoginMixin)
            owner.provider_logged_in = lambda *_a, **_k: True

            with (
                patch.object(login, "_LOGIN_CACHE_FILE", target),
                patch.object(login, "harden_secret_path") as harden,
                patch("pyncm.apis.login.GetCurrentSession", return_value=session),
            ):
                self.assertTrue(owner._save_login_cache())

            self.assertTrue(target.exists())
            harden.assert_called_once_with(target)

    def test_qq_login_cache_hardens_the_file(self):
        from lib.script.cloudmusic import _mixin_login as login

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "qqmusic_login_cache.json"
            client = type("P", (), {"export_cookies": lambda self: {"k": "v"}})()
            owner = object.__new__(login._LoginMixin)

            with (
                patch.object(login, "_QQ_LOGIN_CACHE_FILE", target),
                patch.object(login, "harden_secret_path") as harden,
                patch.object(login, "get_qqmusic_provider_client", return_value=client),
            ):
                self.assertTrue(owner._save_qq_login_cache())

            self.assertTrue(target.exists())
            harden.assert_called_once_with(target)

    def test_kugou_login_cache_hardens_the_file(self):
        from lib.script.cloudmusic import _mixin_login as login

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "kugou_login_cache.json"
            client = type("P", (), {"export_cookies": lambda self: {"k": "v"}})()
            owner = object.__new__(login._LoginMixin)

            with (
                patch.object(login, "_KUGOU_LOGIN_CACHE_FILE", target),
                patch.object(login, "harden_secret_path") as harden,
                patch.object(login, "get_kugou_provider_client", return_value=client),
            ):
                self.assertTrue(owner._save_kugou_login_cache())

            self.assertTrue(target.exists())
            harden.assert_called_once_with(target)

    def test_user_storage_layout_hardens_the_secrets_dir(self):
        from config import user_storage_paths

        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_dir = Path(tmpdir) / "secrets"
            with (
                patch.object(
                    user_storage_paths, "get_user_secrets_dir", return_value=secrets_dir
                ),
                patch.object(user_storage_paths, "harden_secret_path") as harden,
            ):
                user_storage_paths.ensure_user_storage_layout()

            self.assertTrue(secrets_dir.is_dir())
            harden.assert_called_once_with(secrets_dir)


if __name__ == "__main__":
    unittest.main()
