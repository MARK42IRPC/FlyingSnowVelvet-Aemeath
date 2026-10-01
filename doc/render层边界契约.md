# Render 层边界契约

更新时间：2026-10-01

本文档定义 `lib/core/render/` 的目标结构与依赖边界。它不是阶段计划，而是结构改建完成后必须成立的规则。

**状态：第 6 节迁移顺序 1、2（目录切分）、3 已执行；`contract.py` 协议抽取仅覆盖 `lib/script/ui` 之外的部分，视觉/平台能力的注入式收敛仍在进行。** 目录与引用规则以本文档为准；改建前的事实源是 [Qt 边界契约](Qt边界契约.md) 与 [跨后端视觉表现契约](视觉表现契约.md)，那两份文档继续负责“哪些内容算视觉逻辑”和“什么算无 Qt”。第一章描述的是最终目标：`lib/script` 当前仍有约 80 个文件以具体路径引用后端。

本文只新增目录与引用规则，不改变任何视觉语义、数值来源或渲染结果。改建过程中出现分歧时，以 [视觉表现契约](视觉表现契约.md) 和当前 Qt 基准为事实源。

## 1. 为什么需要这一层

现有两个 bridge 都同时装着两件事：

- **绘制执行**：把声明式命令变成像素。只有这里可以碰 `QPainter` 和 DirectX 设备。
- **平台能力与运行时**：字体度量、屏幕几何、文本排版、调度、事件泵、窗口宿主、托盘、输入法、播放器。

两者允许的引用方完全不同。绘制执行应当只被路由层引用；平台能力则必须继续被业务层引用。现在 `lib/script` 有 62 个文件 import `qt_bridge`，其中绝大多数是在要 `font`、`screen`、`draw_backend`，这些引用是合法的。混在一个包里的后果是：任何“禁止业务层引用后端”的规则都无法写成可执行的断言。

把两者分开之后，收益是可度量的：

- Qt 的耦合面从“业务层 62 个文件的硬 import”收缩到“组合入口 + 路由”的少数引用点；
- 这是 PyQt5 从核心依赖降为可选后端依赖的前提，也是普通版发行包减重的前提；
- 新增后端（Vulkan）不再需要改动业务层任何文件。

## 2. 目标结构

```text
lib/core/render/
  router.py              按配置选择后端并装配；跨后端判定的唯一位置
  registry.py            后端注册表与服务读取入口
  visuals/               共享视觉事实源（后端中立类型、presenter、色板、屏幕算法）
  backends/
    base.py              后端共同接口：DesktopBackendBundle 与后端中立协议
    qt/
      drawing/           命令到 QPainter 的执行 + 绘制期渲染事实（presentation、text_metrics）
      runtime/           窗口、输入、调度、字体、屏幕、托盘、播放器
      widgets/           产品页面共享的 QWidget 基类（页面、锚点助手）
    dx/                  未切分的实验实现，available=False
    vulkan/              占位注册项，不提前建抽象
```

后端中立协议定义在 `backends/base.py` 而不是单独的 `contract.py`：它们只在"一个后端
装进进程"这一件事上有意义，和 `DesktopBackendBundle` 是同一个装配面的两面，拆成两个
文件只会让接线点分裂。`registry.py` 提供读取入口（`get_presentation_host`、
`get_font_provider`、`get_text_metrics_factory`）。

`visuals/` 是共享事实源，不是“Qt 的 visuals”。它必须能被 Qt、DX 和 Vulkan 同等引用，因此不得出现在任何单个后端子树之下。

Vulkan 在本文档生效时只是 `registry.py` 里的一条未启用描述符。没有第二个真实实现之前不为它设计抽象。

## 3. 三档规则

规则的粒度是档位，不是“整个后端不得被外部引用”。

**档位 A：绘制执行（`backends/*/drawing/`）**

- 只允许被 `router.py`、后端自带窗口，以及 `lib/script/ui/render_bridge.py` 这一个 UI 侧落点引用；
- 不得导入 `lib/script`（`render_bridge.py` 是 UI 侧的解析/转发层，不在本档内，故它引用本档不算反向依赖）；
- 不得复制产品颜色、字号、间距、圆角、阴影或效果常量；
- 不得反向读取业务对象、页面状态或 `config.config_ui` 来补全视觉信息；
- 子目录之间不得互相导入。

**档位 B：平台能力与运行时（`backends/*/runtime/`）**

- 允许被业务层使用，但只能经 `contract.py` 的协议；
- 业务层与 `lib/script/ui` 不得出现 `lib.core.render.backends.qt.runtime.*` 这类具体后端路径；
- 具体实现由组合入口在启动时注入，不由业务层 import。

**档位 C：后端专属 UI 宿主**

- DX 的 `lib/core/render/backends/dx/application_ui.py`、`lib/core/render/backends/dx/speaker_search.py`、`lib/core/render/backends/dx/speaker_playlist.py`、`lib/core/render/backends/dx/command_hint.py`、`lib/core/render/backends/dx/announcement.py` 属于后端专属窗口实现，不得被 `lib/script/ui` 或业务层引用；
- 它们消费 `visuals/` 的描述，不得重新决定面板填充、颜色或文字排版。

**档位 D：产品共享控件件（`backends/qt/widgets/`）**

- 放的是"产品页面要继承/调用的 QWidget 骨架"：工具页基类、锚点助手；
- `lib/script/ui` 允许直接继承，因为产品页面本身就是 QWidget 子类——这不是平台能力，
  抽象成协议只会得到带 Qt 返回值的协议，等于把耦合从路径挪到类型；
- 不得被 `lib/script` 下 `ui/` 以外的模块引用，也不得反向引用 `lib/script`。

一句话概括：**绘制实现不得共享，能力经协议共享，产物控件只被产品层继承。**

## 4. 已删除的 `core/render` 不得复活

历史结论记录在这里，避免重复推导。

`b7c720e`（“收敛项目架构与维护文档”）删除了 `lib/core/render/`：

`base.py`、`animation_renderer.py`、`manager.py`、`__init__.py`

被删除的原因不是路径名称，而是内容：

- 三个模块全部 `from PyQt5.QtGui import ...`，即 `lib/core` 里的 Qt 实现；
- `Renderer.render(painter, target_rect)` 直接把 `QPainter` 作为公开契约的一部分；
- `AnimationRenderer` 与 `lib/script/ui/world_objects/` 的世界对象动画重复实现缩放与翻转，且依赖 `config.config.ANIMATION`。

当时唯一的引用者是 `lib/core/object/base.py` 的 `GameObject`，而该文件本身也在同一次提交删除（`git show b7c720e --stat` 可复核）。因此 `core/render` 与 `object` 是一对被一起移除的 Qt 时代地基，不是“闲置目录”。

新结构与它的区别：

| | 已删除的旧 `core/render` | 本文档的 `lib/core/render` |
| --- | --- | --- |
| 后端位置 | 只有 Qt，且直接放在 `lib/core` 下 | 后端在 `backends/` 子树内，共享层不依赖任何后端 |
| 公开契约 | `QPainter`、`QPixmap` 出现在方法签名里 | 契约是纯数据与协议，不含任何 toolkit 类型 |
| 与业务的关系 | 业务直接继承 `Renderer`，画法不可共享 | 业务只构造命令与描述，画法唯一 |
| 数值来源 | 各实现自行读取 `config.config.ANIMATION` | 只经 `lib/core/services/` 与 `visuals/` 解析 |

结论：**可以复用 `render` 这个路径名，但不能复用它的形状。** 任何把 `QPainter` 召回 `lib/core` 直接命名的实现都是被删除的旧结构，无论放在哪个子目录。

`doc/维护手册.md` 中“旧 `core/object`、`core/render` 包不得恢复兼容壳”一句指的是上面的旧实现；本文档生效后该句由本文档第 3 节的档位规则取代，仍然禁止恢复旧形状，并新增允许的 Qt 位置。修改时两处必须同步，不得只改一处。

## 5. 与现有规则的衔接

- 自绘允许位置由 `lib/core/render/backends/qt/`、`lib/script/ui/` 与官方游戏包改为 `lib/core/render/backends/qt/`、`lib/script/ui/` 与官方游戏包。判定入口 `tests/test_code_structure_boundaries.py::test_ui_painting_stays_inside_the_toolkit_layers` 的允许前缀随之更新，不允许出现两个并存的白名单。
- `tests/test_qt_dependency_boundaries.py` 的白名单从 `lib/core/render/backends/qt/` 前缀改为 `lib/core/render/backends/qt/`；新增后端子树不在允许集合内，因此 Vulkan 的直接 `PyQt5` 导入不会因目录名而豁免。
- 官方游戏包 v1 的例外保持不变（见 [Qt 边界契约](Qt边界契约.md) 第 2 节）。
- `lib/core/render/visuals/` 的“不得导入 PyQt、bridge 或 `lib/script`”与现 `lib/core/render/visuals/` 一致，测试由目录改名同步。
- `router.py` 不自行读取配置文件，而是消费 `BackendSelection`；一个进程只有一个后端生效，语义沿用现有 `lib/core/render/router.py`。
- 用户或环境显式请求某个后端时，不接受静默回退；回退只发生在该后端初始化失败，且必须记录请求后端、实际后端与失败阶段。

## 6. 迁移顺序

顺序不能颠倒，否则规则无法断言。

1. 先切档位：把 `runtime` 从 `drawing` 中分出来，两个 bridge 内部先分目录，路径暂不变。
2. ~~再建 `contract.py`~~：已执行，但协议落在 `backends/base.py`（见第 2 节说明）。业务层对 `font`、`screen`、`text_metrics` 的需求已收敛为 `FontProvider` / `PresentationHost` / `TextMetrics`，由组合入口注入、经 `lib/script/ui/render_bridge.py` 取用。
3. 然后搬目录：`qt_bridge` / `dx_bridge` 移入 `backends/`，`graphics` 移入 `visuals/`，保持对外重新导出。
4. 最后改断言与文档：测试白名单、`doc/维护手册.md`、`doc/Qt边界契约.md`、本索引同步更新。

第 3 步会同时影响 55 个测试文件中的路径字符串与 `doc/维护手册.md` 的验证命令。搬动时保留一层重新导出会掩盖未完成的迁移，因此只在单次提交内使用，并在同一次提交结束时撤掉。

## 7. 验证

以下断言已是硬断言（第四轮执行后，见第 11 节）：

- `lib/core/render/backends/*/drawing/` 只被 `router.py`、后端自带窗口与 `lib/script/ui/render_bridge.py` 引用；
- `lib/core/render/` 整体不得导入 `lib.script`，`visuals/` 还不得导入 `PyQt5` 或任一 `backends`；
- `lib/core/render/backends/*/runtime/` 不得被 `lib/script` 以具体路径导入：`lib/script/ui` 的两份冻结清单现在都是空的；
- 唯一允许的后端路径例外是 `backends/qt/widgets/`（产品页面基类，档位 D）。
- `lib/core` 内不得出现 `QPainter` / `QPainterPath` / `QPixmap` / `QImage` / `Widget` 类型；
- 现有跨后端一致性测试保持通过：`tests.test_visual_presenters`、`tests.test_graphics_primitives_parity`、`tests.test_visual_backend_parity`。

结构规则改动属于接口改动，测试与文档必须在同一任务中更新。

## 8. 搬迁执行记录

本节记录已执行的结构搬迁。搬迁只改路径与引用，不改变任何行为。

目录映射：

- `lib/core/render/backends/qt/` -> `lib/core/render/backends/qt/`
- `lib/core/render/backends/dx/` -> `lib/core/render/backends/dx/`
- `lib/core/render/visuals/` -> `lib/core/render/visuals/`

引用规则：**不保留旧路径的兼容壳。** `lib/core/render/backends/qt`、`lib/core/render/backends/dx`、`lib/core/render/visuals` 搬迁后不存在，`tests/test_code_structure_boundaries.py::test_ui_painting_stays_inside_the_toolkit_layers`、`tests/test_qt_dependency_boundaries.py` 的白名单与 `lib/script/main.py`、`lib/core/pet_window.py`、`lib/script/cloudmusic/manager.py` 的“不得出现旧路径”断言全部指向新路径。旧路径再次出现即为违规，而不是豁免。

DX 处理：`directx` 描述符改为 `BackendDescriptor("directx", "DirectX", False, experimental=True)`，与 `opengl`、`vulkan` 同属未启用后端。`lib/core/render/backends/dx/` 保留既有实现与测试，但不再由 `configure_selected_desktop_backend()` 注册，也不出现在用户可选后端里；请求 `directx` 时按未启用后端处理。重新启用 DX 时把 `available` 改回 `True` 并恢复注册，属于独立决策，不由本次搬迁暗示。

搬迁是机械的：模块内容与公开符号不变，只有包路径变化。因此本次不新增重新导出层，也不保留任何 `lib.core.render.backends.qt` 形式的转发。

## 9. 第二轮执行记录（drawing/runtime 切分与根层落地）

本节记录在第 8 节之后的第二次结构改建。它改变了目录形状与引用路径，但不改变任何视觉语义或渲染结果。

根层（第 2 节 `contract.py` / `router.py` / `registry.py`）：

- `lib/core/backend_router.py` -> `lib/core/render/router.py`
- `lib/core/desktop_backend.py` -> `lib/core/render/registry.py`
- 新增 `lib/core/render/backends/base.py`：工厂别名与 `DesktopBackendBundle` 的形状定义；`registry.py` 只保留安装/卸载语义与读取入口，`router.py` 只做选择与装配。
- `lib/core/render/__init__.py` 首次落地，只写包说明，不做任何聚合导出。

Qt 后端分档（第 3 节档位 A / B）：

- `backends/qt/drawing/`：`draw_backend.py`、`render_core.py`、`gif_loader.py`、`colors.py`、`window.py`。
- `backends/qt/runtime/`：其余 25 个模块（窗口、输入、调度、字体、屏幕、文本度量、托盘、播放器、后端专属页面宿主等）。
- `backends/qt/__init__.py` 明确不做 `from .runtime import *` 之类的聚合导出：按子包前缀判定的档位规则必须能直接判定，聚合入口会使其失效。

DX 保持未切分：DX 仍是 `available=False` 的实验实现，没有第二个真实 `drawing/` 消费者之前不为它建平行目录。`doc/render层边界契约.md` 第 2 节画的 `backends/dx/drawing`、`backends/dx/runtime` 在 DX 重新启用时补齐。

档位规则的落地方式（重要，避免误读）：

- 规则本身是**冻结基线，只减不增**，不是「当前已满足」。`tests/test_qt_dependency_boundaries.py` 里
  `test_drawing_tier_is_reachable_only_from_the_router_and_backend_windows` 与
  `test_qt_runtime_tier_is_not_named_by_business_or_ui_code` 各自维护一份现存引用方清单：
  删除条目（迁移到共享 presenter 或后端中立协议）会通过，新增条目会失败。
  清单同时断言「基线里的文件已不再违规」就必须删掉条目，避免清单腐烂成许可。
- 基线数字（2026-10-01，第二轮结束时）：`lib/script/ui` 中 31 个文件仍直接引用 `drawing/`，51 个仍以具体路径引用 `runtime/`。
  这些是迁移债，不是新许可；清单的注释里写明了迁移方向。

同轮修掉的搬迁副作用：

- 目录多一层后，`application_runtime.py` 与 `effect_system.py` 里按 `__file__` 层级推导项目根的 `parents[5]` 改为 `parents[6]`；前者曾使窗口图标路径失配，后者曾使特效资源根指向错误目录。
- `tests/test_code_structure_boundaries.py::test_shared_visual_modules_are_backend_neutral` 在上一轮搬迁中仍 glob 已不存在的 `lib/core/graphics/`，断言静默失效；现已指向 `render/visuals/` 并断言扫描集合非空。
- `tests/test_qt_application_seam.py` 的目录锚点与 `tests/test_visual_presenters.py` 的两个文件路径字符串同步到新位置。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2035 通过（1 例 `tests.test_dsh_office_sidecar` 在满负载下超时，单跑通过，属既有环境抖动）。
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过、7 跳过。
- `py -3 -m ruff check lib config scripts tests` 归零；`py -3 -m compileall -q config lib scripts tests` 通过。

## 10. 第三轮执行记录（UI 侧绘制落点收敛）

本节记录在上一轮之后把档位 A 的 UI 侧引用清零的过程。它同样不改视觉语义，只改控件的取色/取点/取绘制实现的方式。

**新增 `lib/script/ui/render_bridge.py` 作为唯一的 UI 侧落点。** 控件不再各自 `import lib.core.render.backends.qt.drawing.*`，
而是向这一层要能力：

- `create_draw_backend()`：已配置后端时取 registry 里的绘制实现；未配置时（控件单元测试、隔离 helper）直接构造 Qt 实现。
  回退到空实现是刻意排除的：Qt 是产品唯一受支持后端，控件像素断言必须画在真实实现上，空实现会把回归伪装成“通过”。
- `create_component_layer()` / `create_component_layer_request()`：控件自用的绘制回调层（排序与注册后端无关，只有回调是 Qt）。
- `qimage_from_raster_frame()`：核心 RGBA 帧转 QWidget 可直接绘制的图像对象。
- `qt_color(token)` / `qt_color_name(token)` / `ensure_qcolor(value)`：主题色的 Qt 表示。色板事实源仍是
  `lib/core/render/visuals/palette.py`（`COLORS` / `UI_THEME`），本层只做 `Color -> QColor` 的边界转换。
- `qpoint_from_point(value)`：锚点/坐标载荷转 `QPoint`。

**落地结果（2026-10-01）：** `lib/script/ui` 里对 `drawing/` 的直接引用从 31 个文件降到 **0**；
基线集合 `frozen_ui_draw_importers` 随之清空，测试改为断言它保持为空。保留的例外只有一处：
官方游戏包 v1 的控件沿用 Qt 页面约定（见 [Qt 边界契约](Qt边界契约.md) 第 2 节）。

**没有做的事（避免误读）：** `registry.py` 没有被“顺手”扩成万能注册表。
`create_component_layer`、`create_component_layer_request`、`convert_raster_frame`、`register_*_factory`、
`configure_widget_render_factories` 这些名字**不存在**，也不应被复活——控件需要的解析/转发都留在 `render_bridge.py`，
`registry.py` 只保留「后端装进来之后，服务从哪里取」。

**同轮修正的一个真实回归：** 一度让控件改走 `create_draw_backend()` 并回退到空实现，导致 6 个像素比对测试
（`test_bubble_visual`、`test_forum_color_picker`、`test_qr_dialog_shares_chrome_and_theme`、`test_workbench_window` 等）
静默画不出东西。现在的落点明确在未配置后端时回退到 Qt 实现，就是本次修正的结果。

**同轮新增的边界断言：** 控件直接 `import PyQt5` 的名单冻结在 `frozen_ui_qt_importers`（73 个文件，只减不增）。
控件本身是 `QWidget`，这份清单是产品事实，但它的规模必须可审计：新增控件直接写字面 `PyQt5` 会被叫停，
必须显式登记并说明理由。真正会随架构演进缩小的是上面那份“直接引用 `drawing/`”的清单。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2035 通过、10 跳过。
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过、7 跳过。
- `py -3 -m ruff check lib config scripts tests` 归零；`py -3 -m compileall -q config lib scripts tests` 通过。

## 11. 第四轮执行记录（后端中立协议与统一数据类型）

本轮把"控件彻底不依赖 Qt"从口号变成结构：控件不再需要 Qt 类型参与布局算术，Qt 只
出现在控件自身必然要用到的边界上。

**新增后端中立协议（`backends/base.py`）**

- `PresentationHost`：屏幕归属、位置夹取、控件全局矩形/点、按屏幕坐标移动控件。
  返回与接收的都是核心 `Rect` / `Point`，不是 `QRect` / `QPoint`。Qt 实现位于
  `backends/qt/drawing/presentation.py`（屏幕归属是绘制期渲染事实）。
- `FontProvider`：`ui_font` / `digit_font` / `cmd_font` / `ui_font_family` /
  `apply_ui_font_tree`。返回后端自己的字体对象——控件是工具包控件，收敛的是"从哪里取"。
- `TextMetrics`：presenter 决定换行、省略号与基线，后端只报告推进量与该文本实际使用的
  基线高度。Qt 实现 `QtTextMetrics` 随本轮从 `runtime/` 迁入 `backends/qt/drawing/`
  （它由绘制期 `QFontMetrics` / `QTextLayout` 驱动，属于档位 A）。

三个协议都是 `DesktopBackendBundle` 上的可选工厂：DX 尚未接线时留空，`render_bridge`
回退到 Qt 与核心算法，而不是崩溃。这是排期问题，不是契约缺口。

**统一数据类型**

- `visuals/types.py::Rect` 补 `center` / `right` / `bottom` 属性（此前只有 `top_left` /
  `size`），使 `QRect` 上最常见的三个只读用法有中立对应物。
- 51 个控件里 `clamp_rect_position` / `get_screen_geometry_for_point` /
  `widget_global_rect` / `move_widget_to_global` 的返回值由 Qt 类型改为核心类型。
  控件侧因此出现一批 `int(rect.x)` 改写（原来是 `rect.x()`）——这正是本轮要实现的效果：
  布局算术语义不再绑定 Qt。`clamp_rect_position` 保持 `x, y, _ = ...` 三元解包形状不变。
- 更新 `backends/qt/drawing/window.py::coerce_qpoint` 的 8 个引用方：锚点载荷转 `QPoint`
  的调用现经 `render_bridge.qpoint_from_point`，语义仍是事件总线要求的整数 `QPoint`。

**目录调整**

- 新增 `backends/qt/widgets/`（档位 D）：`workbench_page.py`（工具页基类，5 个窗口继承）
  与 `anchors.py`（QWidget 锚点助手，7 个控件使用）从 `runtime/` 迁出。它们是"产品页面
  要继承/调用的 QWidget 骨架"，不是可被协议抽象的平台能力。
- `runtime/text_metrics.py` 删除（迁入 `drawing/text_metrics.py`），`runtime/widget_anchors.py`
  与 `runtime/workbench_page.py` 删除（迁入 `widgets/`）。三条旧路径不保留转发模块。

**结果（2026-10-01）**

- `lib/script/ui` 对 `backends/qt/runtime/` 的直接引用：51 个文件 → **0**；`frozen_ui_runtime_importers`
  随之清空并断言保持为空。
- `lib/script/ui` 允许残留的后端路径只剩 `backends/qt/widgets/`（12 个文件，档位 D）。
- 新增断言 `test_retired_qt_runtime_shims_are_not_named_by_business_code`：已迁走的三条
  旧路径不得再被 `lib/script` 引用。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2036 通过、10 跳过。
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过、7 跳过。
- `py -3 -m ruff check lib config scripts tests` 归零；`py -3 -m compileall -q config lib scripts tests` 通过。

**仍未完成（下一轮的输入）**

控件仍 `import PyQt5`（控件的 QWidget/QPainter 事实）。真正压缩这一面需要让产品控件不再
继承 `QWidget`，那是"控件层换实现"级别的改动，不是引用路径收敛。本轮已把可协议化的部分
（屏幕几何、字体、文本度量）全部协议化，剩下的 Qt 面是控件本体。
