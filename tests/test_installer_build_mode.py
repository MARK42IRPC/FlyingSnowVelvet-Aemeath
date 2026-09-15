"""The installer's visible copy must follow the build mode it was compiled for.

The wizard paints its first page before it reads the PE trailer, so deriving the
online/offline wording from ``g_context.archive_size`` showed the online copy on
the offline installer's opening screen.  These checks pin the constant the build
bakes into ``payload_info.h`` and the places that must consume it.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from scripts import build_offline_installer as installer


ONLINE_OFFLINE_COPY = (
    "ONLINE SETUP",
    "为飞行雪绒选择一个安放的位置",
    "确认磁盘空间后将下载并安装完整资源",
    "正在下载并解压资源，请保持窗口打开",
    "安装程序较小，安装时将从资源镜像下载完整运行组件",
    "离线安装未完成",
)


class InstallerBuildModeTests(unittest.TestCase):
    def test_payload_info_header_bakes_the_build_mode(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fsv-payload-info-") as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir()
            (payload / "app.txt").write_bytes(b"payload")
            archive = root / "payload.zip"
            archive.write_bytes(b"archive")

            for online, expected in ((False, "FSV_ONLINE_BUILD 0"), (True, "FSV_ONLINE_BUILD 1")):
                with self.subTest(online=online):
                    info = root / f"payload_info_{int(online)}.h"
                    installer._write_payload_info_header(
                        payload, None if online else archive, info, online=online
                    )
                    text = info.read_text(encoding="ascii")
                    self.assertIn(expected, text)
                    self.assertIn("FSV_PAYLOAD_ARCHIVE_BYTES", text)

    def test_online_header_bakes_the_no_archive_sentinel(self) -> None:
        # 在线版 EXE 里只有几百字节的 bootstrap marker，分片归档压完就丢；常量写成哨兵 0，
        # 原生安装器只拿它把「归档就在 EXE 里」和「归档要另外下载」分开。
        with tempfile.TemporaryDirectory(prefix="fsv-payload-info-") as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir()
            (payload / "app.txt").write_bytes(b"payload")
            archive = root / "payload.zip"
            archive.write_bytes(b"archive" * 8)

            online_header = root / "online.h"
            installer._write_payload_info_header(
                payload, None, online_header, online=True
            )
            self.assertIn(
                "#define FSV_PAYLOAD_ARCHIVE_BYTES ((ULONGLONG)0ULL)",
                online_header.read_text(encoding="ascii"),
            )

            offline_header = root / "offline.h"
            installer._write_payload_info_header(
                payload, archive, offline_header, online=False
            )
            self.assertIn(
                f"#define FSV_PAYLOAD_ARCHIVE_BYTES ((ULONGLONG){archive.stat().st_size}ULL)",
                offline_header.read_text(encoding="ascii"),
            )

            with self.assertRaises(SystemExit):
                installer._write_payload_info_header(
                    payload, archive, root / "wrong-online.h", online=True
                )
            with self.assertRaises(SystemExit):
                installer._write_payload_info_header(
                    payload, None, root / "wrong-offline.h", online=False
                )

    def test_visible_copy_follows_the_compiled_build_mode(self) -> None:
        source = self.native_source()
        self.assertIn("BOOL online = FSV_ONLINE_BUILD ? TRUE : FALSE;", source)
        for marker in ONLINE_OFFLINE_COPY:
            with self.subTest(marker=marker):
                line = self.line_with(source, marker)
                self.assertTrue(
                    re.search(r"online", line, re.IGNORECASE),
                    f"文案未由编译期模式决定：{line.strip()}",
                )

    def test_payload_size_never_decides_the_copy(self) -> None:
        source = self.native_source()
        self.assertNotIn("archive_size != FSV_PAYLOAD_ARCHIVE_BYTES", source)

    @staticmethod
    def native_source() -> str:
        path = installer.DEFAULT_INSTALLER_SOURCE / "src" / "main.c"
        return path.read_text(encoding="utf-8")

    @staticmethod
    def line_with(source: str, marker: str) -> str:
        for line in source.splitlines():
            if marker in line:
                return line
        raise AssertionError(f"安装器源码缺少文案：{marker}")


if __name__ == "__main__":
    unittest.main()
