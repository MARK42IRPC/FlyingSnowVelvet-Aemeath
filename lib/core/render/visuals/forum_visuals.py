"""论坛卡片与图片视图的后端中立视觉常量。

论坛图片控件（档位 D 的 `backends/qt/widgets/forum_images.py`）与论坛样式表
（产品面的 `lib/script/ui/forum_style.py`）都必须知道**同一圈描边的宽度**：
`ForumDetailImage` 算正文图的尺寸时要把边框刨掉（QLabel 不缩放超出内容区的位图，
算漏一步就会把图裁掉一圈），样式表里 `QLabel#ForumDetailImage` 又拿它当 `border`
的宽度。这条事实与后端无关，因此落在 `visuals/`，两边各自引用同一个值。

权威定义在本模块；产品面的 `forum_style.py` 按原名字重新导出（`FORUM_IMAGE_FRAME`），
既有调用面不变。
"""

from __future__ import annotations

from config.scale import scale_px

#: 详情页正文里那张整幅图的描边宽度（一条边的宽度）。消费方要取 2 倍才是左右两条边
#: 占掉的宽度（`ForumDetailImage._frame()`）。
FORUM_IMAGE_FRAME = scale_px(1, min_abs=1)

__all__ = [
    "FORUM_IMAGE_FRAME",
]
