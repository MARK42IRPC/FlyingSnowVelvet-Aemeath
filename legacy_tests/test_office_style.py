"""办公面样式的收敛：QSS 与档位配色是后端中立数据，控件树辅助在 Qt 宿主。

`lib/script/ui/office_style.py` 曾经整份文件 import `PyQt5`，只因为它和两个控件树辅助
函数住在一起；而它 600 行里的 500 行是把工作台 token 拼成 QSS 的纯字符串逻辑。本轮把
两者拆开，这里钉住三件事：

- 样式产出的字节不变（迁移不能改变任何一条规则）。
- 中立镜像的字号档与产品面的权威定义逐值一致。
- 门面确实不再 import `PyQt5`。

**注意**：产品面模块一律在测试方法内 import。`office_style` 会经
`workbench_settings_layout` / `office.contracts` 触碰 `config`，而 `config` 一旦被首次
import 就按当时的用户根固化主题；在模块级 import 会在别的测试（例如
`test_office_effort_slider`，它在自己的模块头里把 `AEMEATH_DESK_PET_HOME` 指到临时目录）
之前把本机主题冻住，导致那些测试按本机亮色主题取色而失败。这里同时按同款约定把用户根
指向临时目录。
"""

from __future__ import annotations

import ast
import atexit
import os
import shutil
import tempfile
import unittest
from pathlib import Path

# 与其余办公测试一致：指向临时用户根，避免读到本机的明暗主题配置。
_TEST_HOME = tempfile.mkdtemp(prefix="office-style-test-")
os.environ["AEMEATH_DESK_PET_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)


def _imports_of(module) -> list[str]:
    names: list[str] = []
    source = Path(module.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


class OfficeStyleNeutralityTests(unittest.TestCase):
    def test_settings_font_mirror_matches_the_product_side(self):
        """中立镜像的 `SETTINGS_FONT_SIZE` 必须与产品面权威值一致。

        镜像的理由是 `lib/core/render` 不得 import `lib.script`；一旦镜像漂移，办公面
        与设置页的字号就会分叉，而这条断言把漂移变成红灯。
        """
        from lib.core.render.visuals import office_chrome
        from lib.script.ui import office_style
        from lib.script.ui.workbench_settings_layout import (
            SETTINGS_FONT_SIZE,
            SETTINGS_HINT_FONT_SIZE,
        )

        self.assertEqual(office_chrome.SETTINGS_FONT_SIZE, SETTINGS_FONT_SIZE)
        self.assertEqual(office_chrome.SETTINGS_HINT_FONT_SIZE, SETTINGS_HINT_FONT_SIZE)
        self.assertEqual(office_style.SETTINGS_FONT_SIZE, SETTINGS_FONT_SIZE)

    def test_effort_colours_ramp_from_pink_to_cyan(self):
        """五档档位色由工作台 pink 起点、cyan 终点线性插值，档位数来自产品契约。

        `mode=None` 沿用环境主题（与迁移前 `get_workbench_colors(None)` 同义），因此这里
        按模式分别取权威 token 对照，而不是假设默认就是深色。
        """
        from lib.script.ui import office_style
        from lib.script.workbench.theme import get_workbench_colors

        for mode in (None, "dark", "light"):
            with self.subTest(mode=mode):
                colors = office_style.office_effort_colors(mode)
                c = get_workbench_colors(mode)
                self.assertEqual(len(colors), 5)
                self.assertEqual(colors[0], c.pink)
                self.assertEqual(colors[-1], c.cyan)
        self.assertNotEqual(
            office_style.office_effort_colors("dark"),
            office_style.office_effort_colors("light"),
        )

    def test_stylesheet_is_byte_identical_for_every_mode_and_page(self):
        """门面产出的样式表必须与中立实现逐字节一致（含窗口按钮那段追加 QSS）。"""
        from lib.core.render.visuals import office_chrome
        from lib.script.ui import office_style

        for mode in (None, "dark", "light"):
            for standalone in (False, True):
                for page_name in ("OfficeWorkbenchPage", "OfficeModePage"):
                    with self.subTest(mode=mode, standalone=standalone, page=page_name):
                        self.assertEqual(
                            office_style.office_stylesheet(
                                mode, page_name=page_name, standalone=standalone
                            ),
                            office_chrome.office_stylesheet(
                                mode,
                                page_name=page_name,
                                standalone=standalone,
                                effort_steps=5,
                            ),
                        )

    #: 迁移前（`HEAD` 版本）各模式的 QSS SHA-256；比对时把 `font-family` 归一化。
    _PREMIGRATION_QSS_SHA256 = {
        ("dark", False, "OfficeWorkbenchPage"):
            "2e99c334128aeeb316b852093bfadfe7befa6950b2e0d90641830b98008b7f7c",
        ("dark", False, "OfficeModePage"):
            "d883c78a6fadbb2750f691682ac07ddcf1dfaad7ca7e87baf3a52fc9805028cf",
        ("dark", True, "OfficeWorkbenchPage"):
            "339e2868d39c2c6f85ffa76ca93dbf2239d0a48b7aa9533cc5ca9f965bb37606",
        ("dark", True, "OfficeModePage"):
            "55398972d06f2799de4efb74fa645a7ff27de7a51690ef04757ef11d8752f50f",
        ("light", False, "OfficeWorkbenchPage"):
            "1807346df9a7a9ded3e2f585014a06935c6bfb2394af928faca4f192b4fd7048",
        ("light", False, "OfficeModePage"):
            "eaaddebc13962bcd7868b8b84ce3c21e51766f9ebfdd0550b3c2e7215ee79ab8",
        ("light", True, "OfficeWorkbenchPage"):
            "5f6ac7b77b1f47eb75816016734f3426213edf59b3d72588a4e18b5f011e95a7",
        ("light", True, "OfficeModePage"):
            "64074081f24045d1b898e26b3a22b1eeaffed42befc2ae9a2e39b35089245348",
    }
    _PREMIGRATION_EFFORT_SHA256 = {
        "dark": "bbdbf5dc544ea74844b3baa283557f4969b4c5b259477d04c1c4d43a9e1632ae",
        "light": "dbe6cf19699a06458415ce403f558e5deee404fada3c68f820f47cb422ad5c91",
    }

    @staticmethod
    def _normalize_sheet(sheet: str) -> str:
        """把环境相关的片段归一化后再比哈希。

        `font-family` 声明含用户配置的字体族（本机是自装字体，CI 上通常是系统默认），
        把它钉进期望哈希会让这条断言变成"换台机器就红"。归一化整条 `font-family`
        声明，其余**逐字节**比对，因此任何规则改动、任何一个 `scale_px()` 数值漂移都
        仍然会变红。
        """
        import re

        return re.sub(r"font-family: [^;]*;", "font-family: <FONT>;", sheet)

    def setUp(self):
        """把全局绘制缩放钉到 1.0：哈希表是在 1.0 缩放下录的。

        `config_runtime` 在导入期应用 `DRAW['scale']`，`user_scale_config` 应用用户缩放，
        两者都是进程级全局量；整树收集时其它测试模块会把它们改掉。不固定它们，这里比对的
        就不是"规则有没有变"，而是"谁先跑"。
        """
        from config.scale import (
            get_draw_scale,
            get_user_scale,
            set_draw_scale,
            set_user_scale,
        )

        # `scale_px()` = round(raw * 绘制缩放 * 用户缩放)，两个都是进程级全局量，
        # 都要固定：哈希表是在 1.0 / 1.0 下录的。
        self._previous_scale = (get_draw_scale(), get_user_scale())
        set_draw_scale(1.0)
        set_user_scale(1.0)
        self.addCleanup(set_draw_scale, self._previous_scale[0])
        self.addCleanup(set_user_scale, self._previous_scale[1])

    def test_stylesheet_is_byte_identical_to_the_premigration_output(self):
        """15 组 QSS 与收敛前的 `office_stylesheet()` 逐字节相等。

        期望值是迁移前（`HEAD` 版本）的 `office_stylesheet()` 在各模式下的 SHA-256；
        中立实现改成工作台 token 映射后必须逐字节复刻，包括追加的窗口按钮那段 QSS。
        只有 `font-family` 按 `_normalize_sheet()` 归一化——它取自用户配置的字体，
        与"规则有没有变"无关。

        **注意**：`mode=None` 走环境主题，其产出取决于用户配置，因此不在本表内；
        它由 `test_default_mode_follows_the_active_theme` 按解析出的模式比对。
        """
        import hashlib

        from lib.script.ui import office_style

        for (mode, standalone, page_name), digest in self._PREMIGRATION_QSS_SHA256.items():
            with self.subTest(mode=mode, standalone=standalone, page=page_name):
                sheet = office_style.office_stylesheet(
                    None if mode == "None" else mode,
                    page_name=page_name,
                    standalone=standalone,
                )
                actual = hashlib.sha256(
                    self._normalize_sheet(sheet).encode("utf-8")
                ).hexdigest()
                self.assertEqual(
                    actual,
                    digest,
                    f"{mode}/{standalone}/{page_name} 与迁移前不再逐字节相等",
                )

    def test_effort_colours_are_byte_identical_to_the_premigration_output(self):
        """三组档位色与收敛前的 `office_effort_colors()` 逐值相等。"""
        import hashlib

        from lib.script.ui import office_style

        for mode, digest in self._PREMIGRATION_EFFORT_SHA256.items():
            with self.subTest(mode=mode):
                colors = office_style.office_effort_colors(mode)
                self.assertEqual(
                    hashlib.sha256(repr(tuple(colors)).encode("utf-8")).hexdigest(),
                    digest,
                )

    def test_default_mode_follows_the_active_theme(self):
        """`mode=None` 沿用环境主题，必须与解析出的那套主题逐字节相同。

        `None` 的产出取决于用户配置，钉死具体哈希会随本机主题变红；这里改为与
        `resolve_workbench_mode(None)` 给出的模式比对，既精确又不依赖环境。
        """
        from lib.core.render.visuals.workbench_tokens import resolve_workbench_mode
        from lib.script.ui import office_style

        resolved = resolve_workbench_mode(None)
        self.assertEqual(
            office_style.office_stylesheet(None),
            office_style.office_stylesheet(resolved),
        )
        self.assertEqual(
            office_style.office_effort_colors(None),
            office_style.office_effort_colors(resolved),
        )

    def test_stylesheet_effort_steps_come_from_the_product_contract(self):
        """门面必须把 `REASONING_EFFORTS` 的长度喂给中立实现，而不是写死五档。"""
        from lib.script.office.contracts import REASONING_EFFORTS
        from lib.script.ui import office_style

        sheet = office_style.office_stylesheet()
        for index in range(len(REASONING_EFFORTS)):
            self.assertIn(f'QLabel#OfficeEffortLevel[level="{index}"]', sheet)

    def test_facade_and_neutral_module_do_not_import_qt(self):
        """门面与中立模块都不许 import `PyQt5`；控件树辅助只在 Qt 宿主里。"""
        from lib.core.render.visuals import office_chrome
        from lib.script.ui import office_style

        for module in (office_style, office_chrome):
            names = _imports_of(module)
            self.assertFalse(
                [name for name in names if name == "PyQt5" or name.startswith("PyQt5.")],
                module.__name__,
            )

    def test_neutral_module_stays_out_of_product_and_toolkit(self):
        """中立样式模块不得 import `lib.script`，也不得直接引用 Qt 宿主实现。"""
        from lib.core.render.visuals import office_chrome

        banned = [
            name
            for name in _imports_of(office_chrome)
            if name == "lib.script"
            or name.startswith("lib.script.")
            or name == "lib.core.render.backends.qt"
            or name.startswith("lib.core.render.backends.qt.")
        ]
        self.assertEqual(banned, [])

    def test_facade_uses_the_qt_host_through_the_bridge(self):
        """两个控件树辅助必须经 `render_bridge` 落点，门面自己不持有 Qt 实现。"""
        from lib.core.render.backends.qt.widgets import office_widgets
        from lib.script.ui import office_style, render_bridge

        self.assertTrue(hasattr(render_bridge, "apply_office_widget_fonts"))
        self.assertTrue(hasattr(render_bridge, "create_office_accent_bar"))
        self.assertTrue(hasattr(render_bridge, "apply_settings_page_fonts"))
        self.assertTrue(hasattr(office_widgets, "apply_office_widget_fonts"))
        self.assertTrue(hasattr(office_widgets, "create_office_accent_bar"))
        self.assertTrue(hasattr(office_style, "apply_office_fonts"))
        self.assertTrue(hasattr(office_style, "create_office_accent_bar"))


if __name__ == "__main__":
    unittest.main()
