# Render 层边界契约

更新时间：2026-10-08

本文档定义 `lib/core/render/` 的目标结构与依赖边界。它不是阶段计划，而是结构改建完成后必须成立的规则。

**状态：第 6 节迁移顺序 1、2（目录切分）、3 已执行；图层能力已收敛到 `lib/core/render/layers/`（第 16 节）；后端中立协议与统一数据类型已落地（第 11 节），控件层“描述 + 后端渲染”已滚动迁移气泡框（第 12 节）、说明书、语音指示器与播放进度条（第 13 节）、音响音量/频段双滑条、搜索结果框与命令提示框（第 18 节）、右键矩形动作按钮一族八个按钮（第 19 节）、确认/提示框（第 20 节，首个模态宿主）；浮窗外壳（样式/主题判定/拖拽策略/窗口按钮）已从 `lib/script/ui` 下沉到渲染层（第 21 节）；办公线性图标的 SVG 规格也已抽成后端中立事实源（第 22 节）；点击粒子辅助的按钮翻译改经 `render_bridge`、`_particle_helper.py` 出列（第 23 节）；论坛样式的底纹混色改为纯十六进制实现、`forum_style.py` 出列（第 24 节）；二维码登录浮窗的自动收起/穿透/窗口标志/自愈能力下沉到基类，`yuanbao_login_dialog.py` 与 `cloudmusic_login_dialog.py` 出列（第 25、26 节）；排布解算已收敛到 `visuals/` 并由 `PlacementSpec` 统一解算（第 14 节，档位 0/1），右键按钮族的逐控件锚点事件链已收敛为声明式 `AnchorGraph` 且 Qt 改为消费共享布局（第 15 节，档位 2/3）；`WindowHost` 的被动宿主激活语义已与 Qt/DX 焦点策略对齐（第 17 节）。** 目录与引用规则以本文档为准；改建前的事实源是 [Qt 边界契约](Qt边界契约.md) 与 [跨后端视觉表现契约](视觉表现契约.md)，那两份文档继续负责“哪些内容算视觉逻辑”和“什么算无 Qt”。第一章描述的是最终目标；产品控件面（`lib/script/ui` 直接 `import PyQt5`）仍需逐个控件迁移，当前待迁清单以 `tests/test_qt_dependency_boundaries.py` 的 `frozen_ui_qt_importers` 为准，滚动顺序见第 13 节末尾；翻页按钮族已于第二十七轮迁出（第 27 节）；音响菜单控制按钮族已于第二十八轮迁出（第 28 节）；音响菜单族共享样式已于第二十九轮出列（第 29 节，档位 D 新增 `QtPainterHost` 绘制宿主）；办公面样式与控件树辅助已于第三十轮出列（第 30 节）；窗口级描述与审批弹窗已于第三十一轮出列（第 31 节，档位 D 新增窗口描述宿主与 isuals/window_spec*.py 两级描述层）；帮助浮窗已于第三十二轮出列（第 32 节，窗口描述宿主补齐工具窗 / 滚动正文 / 淡入淡出 / 描边外壳）；公告 / 更新浮窗已于第三十三轮出列（第 33 节，新增 UI 线程调度宿主，控制器不再被迫继承 `QObject`）。剩余大文件的推进次序已于 2026-10-07 修订为「先按模块拆分瘦身、再逐个收敛 Qt」（第 34 节），滚动清单因此改按四批推进；批次 0（拆分登记机制）与批次 1（`ai_settings_panel.py` 瘦身：贡献名单解析、文案/名称表、配置 schema、删死代码）已完成（第 35–38 节）；批次 2 首轮把 `forum_board.py` 的图片视图族与列表两行下沉到档位 D 的 `backends/qt/widgets/forum_images.py`（第 39 节），续轮把 `forum_texture.py` 的底纹规格抽到 `visuals/forum_texture_visuals.py`（第 40 节）、把办公聊天的 Markdown→富文本抽到 `visuals/office_chat_rich.py`（第 41 节），又把这页的 OpenAI 兼容接口解析抽到 `lib/core/services/api_endpoints.py`、顺手把 `API_TIMEOUT_SECS` 一族从产品包下沉到 `lib/core/services/network_policy.py`（第 42 节），把设置面板的「通用配置」取值校验抽到 `ui/ai_settings_validation.py`并清掉一条 N 卡能力探测死链（第 43 节）、把字段说明文本抽到 `ui/ai_settings_descriptions.py`（第 44 节）、把配置编辑器取值解析抽到 `ui/ai_settings_config_parse.py`（第 45 节）、把语音包安装器浮窗的 QSS 并入 `visuals/workbench_chrome.py`（第 46 节），八轮均为纯移动、零差异（第 43 节另含等价清理）；批次 2 的 A 类到此抽尽，下一轮转批次 3 的 C 类切分，盘点与起点建议见第 47 节；批次 3 首轮把设置面板的「支持作者 / 贡献者」两页整体切到 `ui/ai_settings_about.py`（视图 + 控制器 + 描述，第 48 节，冻结清单 42 → 43），第二轮把「桌宠更新」页切成 `ui/ai_settings_update.py`（视图 + 控制器，第 49 节，冻结清单 43 → 44）。第三轮把面板 `_apply_style()` 的整段 QSS 下沉到 `lib/core/render/visuals/ai_settings_panel_visuals.py`（第 50 节，纯移动、逐字符零差异，冻结清单仍为 44 项）。第四轮把面板的「配置编辑器控件族」切到 `ui/ai_settings_editors.py`（视图控件 + 工厂，第 51 节，冻结清单 44 → 45）。第五轮把「配置分类页骨架 + 外部配置字段族」切到 `ui/ai_settings_config_page.py`（第 52 节，冻结清单 45 → 46）。第六轮把面板的 AI 主页面（`_build_ui` + 取值/回填闭环）切到 `ui/ai_settings_page.py`（第 53 节，冻结清单 46 → 47）；第七轮把「配置取值 / 回填 / 恢复 / 异步保存」控制器切到 `ui/ai_settings_config_store.py`（第 54 节，冻结清单 47 → 48）；第八轮把面板外壳（窗口生命周期 + 自绘边框 + 悬浮标签栏）切到 `ui/ai_settings_shell.py`（第 55 节，冻结清单 48 → 49）。第九轮转回 `forum_board.py`，把发帖屏（装配 + 正文编辑操作 + 发布与计数器）整体切到 `ui/forum_composer.py`（第 56 节，冻结清单 49 → 50）。本次续作（2026-10-10）接着把 `forum_account_sections`（第 57 节，50 → 51）、`forum_detail`（第 58 节，51 → 52）、`forum_wall` + `forum_wall_layout`（第 59 节，52 → 53）、`bug_tracker_data`（第 60 节，53 → 54）、`voice_package_widgets`（第 61 节，54 → 55）、`workbench_widgets`（第 62 节，55 → 56）、`playlist_queue_buttons`（第 63 节，56 → 57）、`cmd_window_widgets`（第 64 节，57 → 58）、`forum_body`（第 65 节，58 → 59）、`game_manager_widgets`（第 66 节，59 → 60）、`speaker_button_shell`（第 67 节，无 `import PyQt5`，不入册）、`bug_tracker_widgets`（第 68 节，60 → 61）、`game_runtime_panel`（第 69 节，61 → 62）逐一切出，均为纯移动（方法/类体对 `HEAD` 逐字节相同）。第 70 节起进入「非纯移动」轮：`ai_settings_page.py` 的 508 行 `_build_ui` 按设置区块切成 `ui/ai_settings_page_sections.py`（1179 → 728 行，62 → 63；以语句序列 oracle 判定等价）。

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
  layers/                图层能力的唯一落点（绘制层 / 排序 / 层内 z 槽 / 顶层窗口层级）
  visuals/               共享视觉事实源（后端中立类型、presenter、色板、屏幕/锚点算法、布局/链路解算）
  backends/
    base.py              后端共同接口：DesktopBackendBundle 与后端中立协议
    qt/
      drawing/           命令到 QPainter 的执行 + 绘制期渲染事实（presentation、text_metrics）
      runtime/           窗口、输入、调度、字体、屏幕、托盘、播放器
      widgets/           产品页面 QWidget 基类与控件窗口宿主（页面、锚点、control_host）
    dx/                  未切分的实验实现，available=False
    vulkan/              占位注册项，不提前建抽象
```

后端中立协议定义在 `backends/base.py` 而不是单独的 `contract.py`：它们只在"一个后端
装进进程"这一件事上有意义，和 `DesktopBackendBundle` 是同一个装配面的两面，拆成两个
文件只会让接线点分裂。`registry.py` 提供读取入口（`get_presentation_host`、
`get_font_provider`、`get_text_metrics_factory`）。

`visuals/` 是共享事实源，不是“Qt 的 visuals”。它必须能被 Qt、DX 和 Vulkan 同等引用，因此不得出现在任何单个后端子树之下。

Vulkan 在本文档生效时只是 `registry.py` 里的一条未启用描述符。没有第二个真实实现之前不为它设计抽象。

`visuals/layout.py` 是**排布解算的唯一共享入口**：`PlacementSpec` 描述“把自身 `self_anchor_id`
对到目标矩形的 `target_anchor_id`，加偏移，再夹取回屏幕”，`AnchorPlacement` 是解算产物
（窗口左上角 + 所在屏幕）。控件层不再各写一份锚点算术，只声明 `PlacementSpec`；点目标
（上游只给出一个全局锚点）用 `resolve_from_point()`，屏幕居中用 `resolve_centered()`。
`anchors.py`（矩形取锚点）与 `screen.py`（屏幕夹取）是它的下层纯函数；三者同属 `visuals/`，
可互相导入，且都不得触及任何后端。

`visuals/anchor_graph.py` 是**一族窗口链路解算的唯一共享入口**（档位 2）：`AnchorNode` 描述
“本节点贴到哪个上游节点的哪个锚点”，`AnchorGraph.resolve()` 按声明顺序一次解出整族矩形。
`COMMAND_ACTION_GRAPH` 是右键按钮族链路的唯一声明，`resolve_command_action_panel_layout()`
由它派生，Qt 与 DX 因此消费同一份链路。与 `layout.py` 同样只依赖后端中立几何。

## 3. 档位规则

规则的粒度是档位，不是“整个后端不得被外部引用”。档位前缀区分两类含义：**档位 A–D 是引用边界**
（谁可以 import 谁），**档位 0–3 是布局收敛的迁移次序**。

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

**档位 D：产品共享控件件与控件窗口宿主（`backends/qt/widgets/`）**

- 放两类东西：
  1. “产品页面要继承/调用的 QWidget 骨架”——工具页基类、锚点助手；
  2. “渲染一个控件描述的后端窗口宿主”——`control_host.py`，把
     `lib/core/render/visuals/controls.py` 的描述渲染成真实顶层窗口；
- `lib/script/ui` 允许直接继承页面基类，因为产品页面本身就是 QWidget 子类——这不是平台能力，
  抽象成协议只会得到带 Qt 返回值的协议，等于把耦合从路径挪到类型；
- 控件窗口宿主**不得**静态引用档位 A（`drawing/`）：`DrawBackend` 与 `PresentationHost`
  必须由 `render_bridge` 注入。档位 D 是“控件工具包事实”的落点，档位 A 是“像素执行”的落点，
  混在一个文件会让档位 A 的引用清单再次失守；
- 同一约束适用于“在调用方自己的 `QPainter` 上执行批次”的宿主（`control_painter_host.py`
  的 `QtPainterHost`）：产品控件本身就是 `QWidget`，`paintEvent` 起 `QPainter` 是工具包事实，
  不属于档位 A 的“又一个绘制实现”。判定看**它怎么活下来**：持有 `QWidget.rect()` / `QPainter`、
  且绘制实现由 `render_bridge` 注入的是档位 D；自己构造 `QtDrawBackend()`、自己做窗口枚举的
  才是档位 A；
- 渲染一个**窗口描述**的后端宿主（`spec_host.py` 的 `QtSpecWindow`）同属档位 D：它比
  `control_host.py` 高一级（装配的是整个窗口而不是单个叶控件），但做的仍是控件工具包事实
  ——窗口标志、控件树与布局、按钮图标位图、无窗眉时的拖动与居中落位、关闭语义。屏幕归属
  与绘制实现同样由 `render_bridge` 注入（`presentation_host()`），因此它不 import 档位 A；
- 不得被 `lib/script` 下 `ui/` 以外的模块引用，也不得反向引用 `lib/script`。

一句话概括：**绘制实现不得共享，能力经协议共享，产物控件只被产品层继承。**

### 档位 0–3：布局收敛的迁移次序

**档位 0：排布解算的事实源（`lib/core/render/visuals/` 的 `resolve_*_layout` / `resolve_*_geometry`）**

- 命令框、气泡、右键按钮族、二维码面板的几何必须先在这里解析为纯几何结果；Qt 与 DX 只执行
  同一份结果，不得各自重算；
- 数值以迁移前的 Qt 基准为准，由 `tests/test_render_layout_algorithms.py` 逐项钉住，不是
  “当前实现恰好如此”；
- `COMMAND_ACTION_BUTTONS` 的名称与宽高必须与 `lib/script/ui/*_button.py` 的 `WIDTH`/`HEIGHT`
  一致，两者是同一份事实的两个落点，任一边单方面改动都会让守卫失败；
- 改动这些数值属于契约改动：必须同时改断言与本文档。

**档位 1：叶控件窗口落位（`visuals/layout.py` 的 `PlacementSpec`）**

- 顶层浮窗控件声明目标锚点、自身锚点与偏移，落位由 `PlacementSpec.resolve_placement()` 统一
  解算并夹取；`lib/script/ui/render_bridge.py` 的 `resolve_placement()` / `place_at_point()` /
  `centered_placement()` 是控件侧唯一取用入口；
- 已收敛的控件不得再调用 `render_bridge.clamp_rect_position()`：自己夹取屏幕等于把档位 1 的
  算术又抄回控件层；
- 唯一允许的例外是仍需 Qt 专属操作的控件（如 `right_click_ui_layer.py` 的 `adopt()` /
  `setParent()` 子窗口收编），在宿主支持“收编子窗口”后并入档位 1；`right_click_ui_layer.py`
  现在同时是按钮族的档位 2 解算点（`family_rects()` / `_resolve_family()`），但仍只做 Qt 事实
  （子窗口收编、并集几何、mask），落位算术全部来自 `visuals/`；
- `visuals/layout.py` 的引用规则与 `visuals/` 其余模块相同：可被 `lib/script/ui`、
  `backends/*` 与同目录 presenter 引用；自身不得 import `PyQt5`、任一 `backends/*`、
  `lib.script` 或 `config.config_ui`，也不得落在任何单个后端子树下。

**档位 2：整族一次解算（`visuals/anchor_graph.py` 的 `AnchorGraph`）**

- 一族窗口的相互锚点关系必须声明成 `AnchorNode` 列表（贴到哪个上游节点、各自的锚点、
  偏移、逻辑尺寸），由 `AnchorGraph.resolve()` 一次解算；`RightClickUiLayer` 是右键按钮族
  的唯一解算点，八个按钮不再各自订阅 `UI_ANCHOR_RESPONSE` / `UI_CREATE` 算落位；
- 节点控件通过 `render_bridge.family_placement()` 向宿主索取整族结果，宿主缺席时该端口
  返回 `None`（`AnchorGraphTests` 用纯几何断言 `resolve()` 与共享布局逐格相等）；
- `COMMAND_ACTION_GRAPH` 是右键按钮族链路的唯一声明：`resolve_command_action_panel_layout()`
  现在由它派生，不再手抄一份绝对偏移；节点名、宽高、按钮文件名三者的对应由
  `COMMAND_ACTION_UI_IDS` 与 `tests/test_render_layout_algorithms.py` 交叉钉住；
- `visuals/anchor_graph.py` 的引用规则与 `visuals/layout.py` 相同：只依赖后端中立几何。

**档位 3：Qt 消费共享布局**

- Qt 的 `RightClickUiLayer` 从 `command_dialog` 的全局矩形出发，用 `COMMAND_ACTION_GRAPH`
  解出整族矩形；命令框仍发一次 `UI_ANCHOR_RESPONSE`（`window_id='command_dialog'`），
  提示框、麦克风指示器等族外跟随者按既有协议跟随，事件协议对族外保持不变；
- 按钮族的“同一份事实”只剩一条链路：`COMMAND_ACTION_GRAPH` → `resolve_command_action_panel_layout()`
  → Qt/DX 两个宿主的真实矩形；`tests/test_right_click_ui_layer.py` 断言宿主解算的整族矩形与
  共享布局逐格相等。

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
- 唯一允许的后端路径例外是 `backends/qt/widgets/`（产品页面基类 + 控件窗口宿主，档位 D）；
- 已迁移的产品控件不再出现在 `frozen_ui_qt_importers` 里，且不得再继承任何 Qt 基类；
- 排布解算的数值基线由 `tests/test_render_layout_algorithms.py` 钉住：命令框按钮族名称/逐按钮
  矩形/整体尺寸、气泡偏移与四角夹取（含负原点的屏幕）、命令框左右翻转与夹取、二维码面板五块
  矩形；`COMMAND_ACTION_BUTTONS` 与 `lib/script/ui/*_button.py` 的 `WIDTH`/`HEIGHT` 必须一致；
- 档位 1 的落位解算由同一测试钉住：`PlacementSpec` 的四种解算形态、`RectActionButtonControl`
  与 `MediaProgressControl` 走共享解算，且已收敛的叶控件不再出现 `clamp_rect_position`；
- 档位 2/3 的链路由 `AnchorGraphTests` 与 `tests/test_right_click_ui_layer.py` 钉住：
  `COMMAND_ACTION_GRAPH.resolve()` 与 `resolve_command_action_panel_layout()` 逐格相等，
  `RightClickUiLayer.family_rects()` 与共享布局逐格相等，节点控件的真实全局位置等于图的解；
  `COMMAND_ACTION_UI_IDS` 的每个 `_ui_id` 必须在对应 `lib/script/ui/*_button.py` 里是字面量。
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

本轮之后控件仍 `import PyQt5`（73 个文件；控件的 QWidget/QPainter 事实）。真正压缩这一面
需要让产品控件不再继承 `QWidget`，那是"控件层换实现"级别的改动，不是引用路径收敛。
本轮已把可协议化的部分（屏幕几何、字体、文本度量）全部协议化，剩下的 Qt 面是控件本体——
第 12 节开始按控件逐个迁移。

## 12. 第五轮执行记录（控件层：描述 + 后端渲染）

前四轮把“平台能力”协议化后，`lib/script/ui` 对 `backends/qt/runtime/` 与 `drawing/` 的引用都归零，
但仍有 72 个控件直接 `import PyQt5`。原因不是落点没收干净，而是这些控件**本身是 `QWidget` 子类**：
再抽一层协议只会得到“返回 `QRect` 的协议”，把耦合从路径挪到类型。本轮因此改的是控件层形状本身。
本轮只迁一个控件作为样板（气泡框），把模式钉死后再滚动铺开。

**新增共享描述层（`lib/core/render/visuals/controls.py`，后端中立）**

- `BubbleControl`：气泡的可见性、当前消息、待显示队列、`min/max` tick 状态机、锚点解算、
  透明度目标与绘制批次。`on_tick()` 返回 `TICK_*` 动作码，`add()` 返回替换/排队决定，
  `click_intent()` 返回产品意图——控件宿主只执行，不判断。
- `BubbleInfo` / `PointerEvent` / `PointerClick`：消息、指针事件与指针意图的中立数据类型。
  绘制事实源仍是 `build_bubble_visual` 的批次，两个后端共用同一份。
- `AnchorPlacement` 现已定义在 `visuals/layout.py`（见第 14 节），`controls.py` 只重新导出以保持
  既有调用面；锚点算术的事实源随之从 `controls.py` 移到 `layout.py`。
- 本模块不 import `PyQt5`，也不 import 任何 `backends/*`：可以在没有桌面后端的进程里
  完成排版、排队与点击判定（`tests/test_control_layer_descriptions.py` 用屏蔽 `PyQt5`
  的子进程验证这一点）。

**新增控件窗口宿主（`backends/qt/widgets/control_host.py`，档位 D）**

- `QtControlHost` 持有真实顶层窗口：窗口标志、透明度动画、绘制批次执行、指针事件翻译、
  剪贴板与 z-order 注册。它**不静态引用档位 A**：`DrawBackend` 与 `PresentationHost`
  由 `render_bridge.create_control_host()` 注入，档位 A 的引用清单因此保持不变。
- 淡出回调只在动画真正播完时触发（`stop_animation()` 打断即取消），与迁移前的
  “新气泡打断旧淡出”语义一致。

**产品控件迁移（`lib/script/ui/bubble.py`）**

- `Bubble` 不再是 `QWidget` 子类，也不再 import `PyQt5`：它订阅事件、维护队列，
  把 `BubbleControl` 的描述交给宿主渲染。为兼容既有调用方保留了 `adjust_size_to_text`、
  `fade_in`、`hide_bubble`、`clear_queue`、`remove_bubbles`、`get_text_size`、
  `get_anchor_point`、`isVisible`、`hide`、`close`，并继续用属性视图转发
  `_current_bubble` / `_pending_queue` / `_anchor_point` / `_anchor_available`。
- 点击产物（左键关闭、右键复制并关闭、点击粒子）改由描述层判定；粒子发射走
  `_particle_helper.publish_click_particle_at()`，Qt 控件与无 Qt 控件共用同一份映射。

**新增的三项中立取用（仍走 `render_bridge` 落点）**

- `create_control_host(**kwargs)`：控件窗口宿主的唯一构造口；
- `pointer_position()`：当前指针位置，返回核心 `Point`（Qt 事实留在 `drawing/window.py`）；
- `QtControlHost.geometry_rect()`：窗口几何，返回核心 `Rect` 而不是 `QRect`。

**结果（2026-10-01）**

- `frozen_ui_qt_importers`：73 → **72**，`lib/script/ui/bubble.py` 已移出；新增断言要求它
  既不 import `PyQt5` 也不继承任何 Qt 基类。
- 档位 A 的直接引用清单**不新增条目**：控件窗口宿主通过注入满足档位 A 规则。
- 像素基准不变：`test_bubble_visual` 的四项断言（换行与尺寸、逐像素批次、富文本推进量）
  全部保持通过；新增 `test_control_layer_descriptions` 另外验证“宿主画出的像素与直接画
  批次逐字节一致”，以及指针事件 → 中立事件 → 产品意图的翻译链路。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2054 通过、10 跳过。
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过、7 跳过。
- `py -3 -m ruff check lib config scripts tests` 归零；`py -3 -m compileall -q config lib scripts tests` 通过。

**同轮的越界回归与守卫（2026-10-01 追加）**

第四轮把接缝返回值从 `QRect`/`QPoint` 换成核心几何后，`command_dialog._is_mouse_far_from_family()`
里遗留的 `widget_global_rect(widget).center()` 直到该 TICK 分支真的跑到才抛
`TypeError: 'Point' object is not callable`——构造期、导入期与既有单元测试都看不到它。已改为
`.center`（属性）。同一模式的全仓再审计结论：`lib/script`、`lib/core`、`scripts` 里只剩这一处；
`command_dialog._pet_top_left` 与 `restore_button` 的 `pet_pos.x()` 仍成立，因为那两处的
`_pet_top_left` / 局部名确实保存着 `QPoint`（`configure_selection` 仍是纯 Qt 类）。

新增的守卫把整类写法挡在 CI 里：

- `CoreGeometryCallStyleTests::test_no_core_geometry_is_called_like_a_qt_type`：静态扫描
  「绑定了核心几何的名字」（含别名链 `a = producer(); b = a`）是否被按 Qt 方法写法取用，
  命中即以 `文件:行:名字` 报错。
- `CommandDialogGeometryIntegrationTests::test_mouse_distance_check_runs_on_core_geometry`：
  真的构造命令框与右键层、驱动 TICK，并盯住事件中心的错误日志——事件中心会吞掉回调异常，
  只看「有没有抛异常」等于没断言。

## 13. 第六轮执行记录（控件层滚动迁移：说明书 / 语音指示器 / 播放进度条）

第五轮把模式钉死后，本轮按同一套三步开始滚动铺开。只迁一个控件，但这一轮把**宿主
的通用能力**补齐了，后面的控件不必再各拉一套：两个迁完的控件都在用同一个 `QtControlHost`。

**控件窗口宿主新增的三项通用能力（`backends/qt/widgets/control_host.py`，档位 D）**

- `auto_hide_ms` / `on_auto_hide` 与 `start_auto_hide()` / `stop_auto_hide()`：可选的单次
  自动隐藏计时器。不给构造参数就不创建 `QTimer`，`start_auto_hide()` 是空操作——气泡框
  因此不承担一个用不到的定时器；说明书则拿到"显示 5 秒后自动收起"这条产品行为。
- `description_at(global_pos, restricted_names=())`：Qt 命中测试（`widgetAt` → `parent()`
  链 → `topLevelWidgets` 兜底），返回光标下控件声明的 `_description`。**产品策略不进宿主**：
  "哪些窗口算受限面板"由调用方以名字传入，宿主只负责"受限面板只有在真正激活时才放行"
  这条 Qt 语义，并永远跳过自己。
- 宿主依旧不静态引用档位 A：`DrawBackend` / `PresentationHost` 仍由 `render_bridge` 注入。

**描述层新增（`lib/core/render/visuals/controls.py`，后端中立）**

- `TOOLTIP_IDLE` / `TOOLTIP_SHOW` / `TOOLTIP_HIDE` 动作码；`scaled_opacity()` 从
  `BubbleControl` 里提出来成为模块级函数，两个控件共用同一份夹取规则。
- `TooltipHoverState`：悬停计时。光标位置是屏幕事实，但"静止够久了没有"是产品判定——
  位置喂进来，动作码给出去。`advance()` 只在**刚**达到阈值那一 tick 返回 `TOOLTIP_SHOW`，
  移动时返回 `TOOLTIP_HIDE`；`initial_position` 是构造时的光标快照，静止计数从它起算。
- `TooltipControl`：文本、换行、尺寸、位置解算与透明度目标。排版事实源仍是共享
  `build_tooltip_visual`，Qt 与 DirectX 两个后端产出同一份批次。
- 该模块依旧不 import `PyQt5`、不 import 任何 `backends/*`，新增的说明书逻辑也一并
  在屏蔽 `PyQt5` 的子进程里被验证。

**产品控件迁移（`lib/script/ui/tooltip_panel.py`）**

- `TooltipPanel` 不再是 `QWidget`：保留 `width()` / `height()` / `isVisible()` / `hide_now()`
  等既有调用面（`shutdown.py`、`tray_menu.py`、`app/qt_application_ui.py` 无需改动），
  同时保留 `_wrap_text` / `_build_visual` / `_reposition` / `_stationary_ticks` 等内部
  读法，方便既有测试与调试继续使用。
- Qt 命中测试整段搬进宿主：控件层只剩 `_RESTRICTED_DESCRIPTION_WINDOWS` 这一条产品策略。

**新增的中立取用（仍走 `render_bridge` 落点）**

- `pointer_cursor()`：当前指针的原始 Qt 位置。说明书需要把同一个位置既用于夹取算术、
  又交给 Qt 做命中测试，`pointer_position()` 返回核心 `Point` 会丢掉这层身份。
- `screen_rect_for_cursor(cursor, fallback_widget=None)`：光标对象所在屏幕的核心 `Rect`。

**结果（2026-10-01）**

- `frozen_ui_qt_importers`：72 → **71**，`lib/script/ui/tooltip_panel.py` 已移出。
- 档位 A / 档位 B 的直接引用清单**均不新增条目**：新能力都由宿主与 `render_bridge` 提供。
- `test_visual_presenters` 的"宿主只执行共享视觉"名单同步收缩为仍未迁移的 Qt 宿主。

**迁移过程中被运行期测试抓住的两个坑（已钉进守卫）**

它们都只在真的把控件跑起来时才暴露，静态断言看不见：

- `initial_position` 没喂进描述层时，"静止满 20 tick"会整体晚一拍——首帧被当成基线
  而不是计数起点。
- 淡出没有标记 `fade_out=True` 时，窗口会淡到全透明却**仍然可见**，继续拦截鼠标；
  这个坑在离屏平台上看不出来（平台不报错），只有断言"淡出结束后 `isVisible()` 为假"
  才会失败。

新增 `TooltipPanelBehaviorTests::test_hover_shows_then_moves_hide_the_panel`：真的构造
面板与一个带 `_description` 的 Qt 窗口，驱动 TICK 走完"静止 → 显示 → 移动 → 淡出 → 收起"，
并盯住事件中心的错误日志（事件中心会吞掉回调异常，只看"有没有抛异常"等于没断言）。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2056 通过、10 跳过。
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过、7 跳过。
- `py -3 -m ruff check lib config scripts tests` 归零；`py -3 -m compileall -q config lib scripts tests` 通过。

**同轮追加：语音指示器（`lib/script/ui/mic_stt_indicator.py`）**

第二个迁完的控件，验证"三步模式"可以连续复用。

- 描述层新增 `MicSttControl` 与 `HOVER_NONE` / `HOVER_SHOW` / `HOVER_HIDE` 动作码：
  可见性、监听中/语音活跃状态、悬停半径判定与"离开超过 `hide_delay` 才收起"都在这里。
  距离用的是**矩形中心**到指针的平方距离，与迁移前逐字一致；`update_hover()` 只在
  "靠近且正在监听"时返回 `HOVER_SHOW`，避免每帧重复要求显示。
- 控件保留 `_visible` / `_listening` / `_speech_active` 属性视图、`width()` / `height()` /
  `update()` / `hide()` / `close()`，`pet_window_ui` 与关机清理路径无需改动。
- 宿主新增 `pointing_cursor=True`：指示器原来手工 `setCursor(Qt.PointingHandCursor)`，
  现在由构造参数表达。
- `frozen_ui_qt_importers`：71 → **70**。

**第二个只有运行期才暴露的坑（已钉进守卫）**

`QWidget.setFixedSize(SIZE, SIZE)` 换成宿主之后，窗口尺寸**不会自己出现**：没调
`apply_size()` 时宿主是 640x480 的默认值，位置与悬停命中判定会一起算错，而构造期、
导入期与静态断言都看不出来。守卫是
`MicSttIndicatorBehaviorTests::test_indicator_follows_state_hover_and_clickthrough`：
真构造指示器与宿主，断言 `width()/height()` 等于 `SIZE`，再走完
"开始监听 → 靠近保持 → 离开未超时仍可见 → 超过延时收起 → 停止监听 → 穿透开关"
整条链路。

**同轮追加：播放进度条（`lib/script/ui/progress_panel.py`）**

第三个迁完的控件，也是第一个有**拖动**交互的控件。

- 描述层新增 `MediaProgressControl`：进度、剩余时长、拖动状态、"拖动时按当前进度反推
  剩余时间"的算术、tick 节奏（每 20 tick 请求一次进度）与位置解算。滑条区域直接读
  共享 presenter 的 `visual.slider_rect`，x ↔ 进度的换算不再抄第二份版面参数。
- **宿主新增 `on_pointer_release`**：这是本轮唯一的结构扩展。原控件在
  `mouseReleaseEvent` 里提交进度并发布 `MUSIC_SEEK`；宿主此前只有 press 回调，
  少了"松手"这一环时进度会永远停在拖动中、seek 永远发不出去——而这在构造期与
  静态断言里都看不见（见下面的守卫）。
- 控件保留 `_visible` / `_progress` / `_remaining` / `_dragging` / `_drag_progress`
  等属性视图，`playlist_panel` 的 `set_position_below_playlist(self.geometry())` 调用面不变。
- `frozen_ui_qt_importers`：70 → **69**。

**勘误（2026-10-03）**

第 12、13 节里 `frozen_ui_qt_importers` 的每一步都比真实值少 1：迁移前实际是 **74**，
四步后的当前值是 **70**（`74 → 73 → 72 → 71 → 70`）。例如第 12 节写的「73 → 72」，
真实是「74 → 73」；本节写的「70 → 69」，真实是「71 → 70」。清单本身没有腐烂——当前
70 项与仓库里真实 `import PyQt5` 的 70 个 `lib/script/ui/*.py` 逐条一致（见
`tests/test_qt_dependency_boundaries.py` 的 stale 断言）。上面的执行记录保留原样以免
改写历史，以本节勘误与 `HEAD` 的清单为准。

**滚动清单（下一个控件）**

模式已固定为三步：把控件状态搬进 `visuals/controls.py`（或同级新模块）→ 控件本体删掉
`QWidget` 基类与 `PyQt5` → 从 `frozen_ui_qt_importers` 删除条目。

> **次序修订（2026-10-07，第 34 节）：** 下面这份清单的推进次序已改。剩余文件里有若干"一个文件
> 等于一整块页面"的大文件（`ai_settings_panel.py`、`forum_board.py` 等），继续按"一个文件一个控件"
> 推进会让一次改动同时承担拆分与去 Qt。新次序是**先按模块拆分瘦身，再逐个收敛 Qt**，分四批
> 推进，细节见第 34 节。清单条目本身仍然有效，只是不再是直接的执行顺序。

建议顺序：

1. ~~`rect_action_button_style.py` 一族~~ **已于第十四轮迁出**（见第 19 节）：八个按钮
   改为 `lib/script/ui/rect_action_button_runtime.py` 的 `RectActionButtonRuntime`（描述 +
   宿主装配），`rect_action_button_style.py` 已删除，`frozen_ui_qt_importers` 66 → 57。
   原前置项不是问题：`QtControlHost` 本身是 `QWidget`，右键图层原有的 `adopt()`（内部
   `setParent()`）直接收编各按钮宿主即可，无需新增"收编子窗口"能力。
2. 其余顶层浮窗控件。音响双滑条（`speaker_volume_slider.py` / `speaker_band_slider.py`）、
   音响搜索结果框（`speaker_search_result_box.py`）与命令提示框（`command_hint_box.py`）
   已于第十一 / 十二轮迁出（见第 18 节）；拖动类控件现在有 `on_pointer_move` + 拖动捕获与
   `on_pointer_release` 可用，列表类控件可用描述层 `row_rects` 反查命中行。
3. 带子控件树与 `exec_()` 的对话框（`confirm_dialog`、`update_dialog`、`forum_*`、
   `office_*`）放最后，它们需要宿主先支持子控件与模态，属于下一轮的结构扩展。
   前置项已于第十五轮（模态宿主，第 20 节）与第三十一轮（窗口描述宿主，第 31 节）补齐：
   `confirm_dialog` 与 `office_approval_dialog` 已迁出，其余带树对话框可按同一模式逐个
   搬走；办公面其余 `office_*` 文件是 `QWidget` 页面而不是带树对话框，不属本项。
   工具类浮窗（淡入淡出 + `LayerManager` 层级 + 滚动正文 + 描边外壳）同属本项的落点，
   宿主能力已于第三十二轮补齐，`help_window` 已迁出（第 32 节）；`update_dialog` /
   `announcement_dialog` 已于第三十三轮迁出（第 33 节）。
4. `world_objects/*.py`（时钟、沙发、雪球等）与 `game_runtime.py` 是另一类长尾，
   它们更多是"动画 + 命中"，可在控件族收干净后单独一轮处理。
   其中 `game_runtime.py` 的宿主窗口按工作台窗口处理：普通窗口 + 无边框，既不置顶
   也不进 `LayerManager`；窗口内的自绘仍按 `Layer.PANEL` 参与画布内排序。

## 14. 第七轮执行记录（档位 0/1：布局解算收敛）

本轮不改视觉语义，只把“窗口落在哪”的事实源从各控件收进 `visuals/`。前六轮解决的是
“画什么”（命令批次）与“状态在哪”（控件描述）；这一轮解决的是“贴在哪一边”。

**档位 0：先把既有解算钉死（`tests/test_render_layout_algorithms.py`，新增）**

收敛布局之前，`resolve_*_layout` / `resolve_*_geometry` 已经存在且被 DX 使用，Qt 侧却
另有逐控件锚点算术，两者没有任何测试同时盯住。本轮先补确定性守卫：

- 命令框按钮族：8 个按钮的**名称、顺序、逐按钮矩形、整体尺寸**；并额外断言
  `COMMAND_ACTION_BUTTONS` 的宽高与 `lib/script/ui/*_button.py` 里定义 `WIDTH`/`HEIGHT` 的
  常量字面量逐项相等（用 AST 取值，不 import Qt 控件），名称与控件里的按钮文字也对齐。
  这份布局此前是一份“手抄”，两份事实源没有任何断言相连，任一边改动都不会被发现。
- 气泡：偏移是否**先于**夹取生效、四角夹取、以及原点为负的第二块屏幕（多屏场景）。
- 命令框：右侧放得下就贴右、放不下翻左、两侧都放不下时夹取、纵向夹取。
- 二维码面板：默认 `320x430` 与内部五块矩形，以及放大到 `420x560` 时居中块的变化。

**档位 1：新增 `lib/core/render/visuals/layout.py`（共享事实源）**

- `AnchorPlacement` 从 `visuals/controls.py` 迁到这里，`controls.py` 重新导出以保持既有
  调用面（`bubble.py` / `progress_panel.py` / `tooltip_panel.py` 与宿主都不用改）。
- `PlacementSpec`：`(target_anchor_id, self_anchor_id, offset_x, offset_y)` 的声明，加三种解算
  形态——`resolve_placement()`（解完夹取，返回 `AnchorPlacement`）、`resolve_point()`（只解不夹，
  保留 `RectActionButtonControl.anchored_top_left()` 的旧语义）、`resolve_from_point()`（目标只有
  一个全局锚点）、`resolve_centered()`（屏幕居中）。
- 它泛化的是 `RectActionButtonControl` 早就存在的参数面（`anchored_top_left` / `placement`），
  不是另造第二套 API：`controls.py` 里那三个方法现在只是转调 `PlacementSpec`。
- `MediaProgressControl.placement()` 的“播放列表正上方”也改由同一 spec 表达
  （`self_anchor=bottom_left` + `offset_y=-gap`），`clamp_rect_position` 调用从 `controls.py` 移除。

**`lib/script/ui/render_bridge.py` 新增档位 1 端口**

- `resolve_placement()` / `place_at_point()` / `centered_placement()`：控件侧唯一取用入口，
  转发给 `visuals/layout.py`。桥本身仍属解析/转发层，不新增 Qt 事实。

**已收敛的落位点（`lib/script/ui` 下 `clamp_rect_position` 调用清零）**

- 命令框附属按钮 7 个：`clickthrough_button`、`close_button`、`restore_button`、
  `launch_wuwa_button`、`more_functions_button`、`chat_mode_button`、`interaction_mode_button`；
- `scale_button.py` 两个按钮类（放大贴穿透按钮右锚点、缩小贴放大按钮右锚点）；
- `mic_stt_indicator`（主宠左上角 + 固定偏移）；
- 点锚点 + 偏移：`command_hint_box`、`speaker_search_result_box`；
- 面板锚点：`page_turn_buttons`（两个翻页按钮）、`speaker_control_buttons`（六个按钮，
  锚点矩形取零尺寸、偏移沿用原显式坐标）、`playlist_panel` 的删除/立即播放按钮；
- 左右翻转族（右侧优先、受阻翻左，翻转判定留在业务侧）：`speaker_search_dialog`、`playlist_panel`；
- 居中浮窗：`announcement_dialog`、`update_dialog`、`help_window`、`voice_package_installer`、
  `qr_dialog_base`（`resolve_centered`），以及 `office_approval_dialog`
  （参考父窗或屏幕中心，`center` 对 `center`）。

这些都是**同形不同值**的落位：目标锚点、自身锚点、偏移三个数不同，五步算术完全相同。
守卫是 `tests/test_render_layout_algorithms.py::test_no_ui_control_clamps_its_own_window_position`：
`lib/script/ui` 下除 `render_bridge.py` 外的任何文件出现 `clamp_rect_position` 都会失败。

**同轮追加：剩余 8 处落位并入（第二批提交）**

- 第一批之后仍在自己夹取屏幕的控件这次一次收完：`command_hint_box`（命令框左下锚点）、
  `speaker_search_result_box`（搜索框左下锚点）、`qr_dialog_base`（居中）、
  `page_turn_buttons` 的两个翻页按钮、`speaker_control_buttons` 的六个按钮、
  `playlist_panel` 的删除/立即播放按钮，以及左右翻转族
  `speaker_search_dialog` 与 `playlist_panel`。
- 翻转族的做法值得记一笔：**“右侧放不下就翻到左侧”这个判定留在业务侧**，
  只有两次候选位置的解算与夹取走 `PlacementSpec`。把判定也搬进 render 层会引入
  “翻转策略”这种带产品选择的配置，超出“落位算术”的范围；档位 2 的 `AnchorGraph`
  才是它的归宿。
- `speaker_control_buttons` 的六个按钮原先各自夹取屏幕，但目标本来就是面板左上锚点这一
  个点：改成零尺寸锚点矩形 + 显式偏移后，六个按钮共享一次 `screen_rect_for_point()`。
- `lib/script/ui` 下的 `clamp_rect_position` 调用至此清零，只剩 `render_bridge.py` 自身；
  守卫从“逐文件登记”升级为全目录断言，并把 21 个已收敛控件登记进 `MIGRATED_LEAF_CONTROLS`。

**本轮没动的部分（后续档位）**

- `right_click_ui_layer.py` 的 `adopt()` / `setParent()` 子窗口收编仍不参与落位解算
  （它是宿主并集几何，不是锚点对齐），等宿主支持“收编子窗口”后再谈是否并入。
- 档位 2（`AnchorGraph`）与档位 3（Qt 复用 `resolve_command_action_panel_layout`）需要改事件协议
  或窗口宿主，另起一轮。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2083 通过、10 跳过。
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过、7 跳过。
- `py -3 -m ruff check lib config scripts tests` 归零；`py -3 -m compileall -q config lib scripts tests` 通过。

## 15. 第八轮执行记录（档位 2/3：右键按钮族链路收敛）

本轮把右键按钮族从“两条互不相干的事实”收敛成一条。

**档位 2：新增 `lib/core/render/visuals/anchor_graph.py`**

- `AnchorNode` 声明一个节点贴到哪个上游节点的哪个锚点、自身锚点、偏移与逻辑尺寸；
  `AnchorGraph.resolve()` 按声明顺序做一次拓扑解算，返回 `node_id -> 屏幕矩形`，
  可选 `scale` 放大逻辑几何、可选 `screen` 夹取回屏幕；
- `COMMAND_ACTION_GRAPH` 是八个按钮的唯一链路声明（穿透 → 缩放/启动 → 聊天/更多 → 交互），
  与 `COMMAND_ACTION_BUTTONS` 的名称/宽高同源；`COMMAND_ACTION_UI_IDS` 记录节点名到
  Qt 控件 `_ui_id` 的对应；
- `resolve_command_action_panel_layout()` 改为由该图派生：逻辑几何常量（行顶 `-34/-66/-98`、
  行高 `32`）与图同源，函数只负责按声明顺序取值并汇总面板尺寸，不再手抄绝对偏移；
  这样 Qt 与 DX 消费的是同一份链路（档位 3 的前置）。

**档位 3：Qt 宿主改为消费共享布局**

- `RightClickUiLayer` 新增 `register_family_node()` / `family_rects()` / `_resolve_family()`：
  登记命令框与八个按钮后，每帧按 `COMMAND_ACTION_GRAPH` 一次解出整族矩形并落位，
  命令框只发一次 `UI_ANCHOR_RESPONSE` 供族外跟随者使用；
- 八个按钮删除各自的 `_on_anchor_response` / `_on_ui_create` / `_target_ui_id` / `_self_anchor_id` /
  `_offset_*` 与逐控件 `place_at_point()`；`_update_position()` 改为
  `render_bridge.family_placement(self, node_id)`，即向宿主要整族结果；
- `pet_window_ui.py` 在 `adopt()` 之后登记九个节点，声明“谁是族的根、谁贴谁”。

**本轮没动的部分**

- 提示框、麦克风指示器、播放列表等族外控件仍按既有 `UI_ANCHOR_RESPONSE` 协议跟随命令框；
  它们只有一个上游，不构成“一族”，不需要锚点图。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2095 通过、10 跳过。
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过、7 跳过。
- `py -3 -m ruff check lib config scripts tests` 归零；`py -3 -m compileall -q config lib scripts tests` 通过。

## 16. 第九轮执行记录（图层能力收敛到 `lib/core/render/layers/`）

本轮把散落在 `lib/core/` 顶层的图层能力收成一个包，并把「画布绘制层」与「顶层窗口层级」掰开。

**目录落点**

```text
lib/core/render/layers/
  __init__.py    唯一入口：顶层立即导出 spec/order/draws，窗口与宿主按需惰性导出
  spec.py        Layer：一处画布内部的绘制层枚举（值来自 config/config_layer.py）
  order.py       normalize_layer / layer_name / draw_order_key / order_render_values
  draws.py       层内 z 槽：BASE..OVERLAY_THIRD 一条递增阶梯
  hosts.py       LayerWindowHost / WindowHost 协议与 passive 实现
  windows.py     WindowLayer / LayerWindow / WindowsLayerManager / get_layer_manager
```

- 旧路径 `lib/core/layer.py`、`lib/core/layer_manager.py`、`lib/core/window_host.py` 与
  `lib/core/render/visuals/ordering.py` 全部删除，不保留兼容壳；
- 引用规则：业务层与 `visuals/` 只从 `lib.core.render.layers` 取图层能力，
  不再各引一处；`visuals/` 只依赖 `spec` / `order` / `draws` 三块纯数据，
  窗口管理器与宿主协议因反向依赖 `visuals.types` 改为惰性取值，避免
  `visuals -> layers -> windows -> registry -> visuals` 在导入期成环。

**两个枚举**

- `Layer`：一处 `DrawBatch` 内部的绘制层，绘制命令与 `DrawScene` / `DrawRequest` 继续用它；
- `WindowLayer`：顶层窗口整块的桌面 z-order，`get_layer_manager().register(...)` 一律用它；
- 两者共享 `config/config_layer.py` 的同一份 `LAYER_VALUES`，
  数值相同但语义与使用方不同，不再混用同一个名字。

**层内 z 槽**

- presenter 里手写的 `z=1..7` 字面量改为 `layers.draws` 的具名槽
  （`BASE` / `FRAME` / `INNER` / `MIDDLE` / `CONTENT` / `OVERLAY` / `OVERLAY_SECOND` / `OVERLAY_THIRD`）；
- 数值与迁移前逐项一致，渲染结果逐字节不变，`tests/test_unified_draw_order.py`
  与像素比对测试是基线。

**守卫**

- `tests/test_code_structure_boundaries.py::test_layer_capabilities_live_only_under_the_layers_package`
  钉住 `layers/` 的文件清单与四条旧路径不复活；
- `tests/test_qt_dependency_boundaries.py::test_layer_manager_only_uses_backend_neutral_window_hosts`
  的检查目标改为 `layers/windows.py` 与 `layers/hosts.py`。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2101 通过、10 跳过。
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过、7 跳过。
- `py -3 -m ruff check lib config scripts tests` 归零；`py -3 -m compileall -q config lib scripts tests` 通过。

## 17. 第十轮执行记录（宿主激活语义与契约一致化）

本轮修掉一条从 `lib/core/window_host.py` 迁入 `layers/` 起就存在的契约错位，不改变目录结构。

**问题**

- `PassiveWindowHost.activate()` 只在 `is_visible()` 且未开穿透时置 `_active = True`，
  但 `set_clickthrough(True)` 不会撤销已有的激活态；于是「先 `activate()` 再开穿透」的
  时序下 `is_active()` 仍为 `True`，与 Qt 后端「穿透时 `activateWindow()` 直接 early-return」
  的焦点策略相反，也与 `doc/DX后端实现方案.md` 的「装饰窗口才 `WS_EX_NOACTIVATE`，
  可编辑窗口正常激活」不一致。

**改动**

- `layers/hosts.py` 的 `PassiveWindowHost.set_clickthrough()` 在开启穿透时清掉 `_active`，
  统一为「穿透窗口不持有激活」；`activate()` 的守卫不变，仍描述「按后端焦点策略请求激活」。
- `tests/test_window_host_contract.py` 把断言拆成两段：`activate()` 后断言 `is_active()`，
  开穿透后再断言 `not is_active()`，让被动宿主的时序语义显式可见，而不是靠一次快照蒙对。

**边界**

- 只动 passive 宿主的测试替身语义；Qt/DX 真实后端不迁移、不改 ABI，`_active` 对生产代码
  仍非读取路径（生产代码只读 `is_clickthrough_enabled()`）。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2102 通过、10 跳过。
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过、7 跳过。
- `py -3 -m ruff check lib config scripts tests` 归零；`py -3 -m compileall -q config lib scripts tests` 通过。

## 18. 第十一轮执行记录（音响音量 / 频段双滑条收敛）

本轮把音响右键 UI 里的两条滑条收进「描述 + 后端渲染」，都是第一个**被动拖动**类控件
（前几轮的拖动只有播放进度条的「按下—松手」提交）。

**描述层（`lib/core/render/visuals/controls.py`）**

- `RectSliderControl`：水平音量滑条的比例、刻度吸附、`x ↔ 比例` 换算、拖动标记与
  透明度缩放。形状、刻度、手柄与 `track_rect` 全部取自共享 presenter 的
  `build_slider_visual()`，`ratio_from_x()` 只是 `slider_ratio_at(track_rect(), x)` 的转发，
  版面参数不再抄第二份。`set_ratio()` 返回 `(吸附后比例, 是否变化)`，是否发事件由控件决定。
- `BandSliderControl`：竖向频段滑条，频段读数经 `speaker` 句柄读写
  `lib.core.speaker_band`；命中（`band_hit_test`）与 `y ↔ 比例`（`band_ratio_at`）沿用
  `speaker_band_visuals` 的既有算术。
- 两个类都进 `controls.__all__`，与 `BubbleControl` / `MicSttControl` / `MediaProgressControl`
  同处一个事实源。

**宿主扩展（`lib/core/render/backends/qt/widgets/control_host.py`）**

- 新增 `on_pointer_move` 回调与 `mouseMoveEvent` 转发：拖动过程中「块跟随指针」需要每一次
  移动都回到描述层重算，之前宿主只在按下/松手各回调一次。
- 新增 `capture_on_press`：按下时 `grabMouse()`、松手时 `releaseMouse()`。滑条窗口很窄，
  指针移出窗口后原来的 `mouseMoveEvent` 会断流——这是本轮唯一的结构扩展，且是通用能力，
  不是滑条专属分支。两个回调都做了 `RuntimeError` 兜底（宿主已析构时静默）。

**控件本体**

- `speaker_volume_slider.py` / `speaker_band_slider.py` 删除 `QWidget` 基类与 `PyQt5` 引用，
  改为持有描述层对象 + `create_control_host()`；对外接口
  （`move` / `apply_geometry` / `set_speaker` / `width` / `height` / `x` / `y` / `isVisible` /
  `fade_in` / `fade_out` / `cleanup` / `close` / `deleteLater` / `band` / `is_visible` /
  `bound_speaker`）保持不变，`speaker_control_buttons.py` 的调用面不动。
- 拖动提交仍在控件侧：音量松手发 `MUSIC_VOLUME` 并提示百分比，频段松手提示当前频段，
  与迁移前一致。

**守卫**

- `tests/test_control_layer_descriptions.py` 的已迁移清单加入两个控件（不得再继承 Qt 基类）。
- `tests/test_qt_dependency_boundaries.py` 的 `frozen_ui_qt_importers` 移除两个条目。
- `tests/test_speaker_band_ui.py` 的交互测试改为直接驱动宿主回调
  （`_on_pointer` / `_on_pointer_move` / `_on_pointer_release`），并断言 `build_visual()` 的
  版面，不再伪造 `QMouseEvent`。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2106 通过、10 跳过。
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过、7 跳过。
- `py -3 -m ruff check lib config scripts tests` 归零；`py -3 -m compileall -q config lib scripts tests` 通过。

**本轮没动的部分**

- `frozen_ui_qt_importers`：70 → **67**（本轮双滑条 + 结果框三项移出）。
- `rect_action_button_style.py` 一族当时仍卡在「宿主收编子窗口」前置项上，未动（该项已于第十四轮迁出，见第 19 节）。

**同轮追加：迁出后的族内几何读取回归**

两条滑条上线后暴露了一处迁移期没发现的读取：`speaker_search_dialog` 与 `playlist_panel`
的 `_is_mouse_far_from_family()` 仍在读 `widget.geometry().center()`，而这两族里的滑条与播放
进度条已经迁出 `QWidget`。点到音响/打开播放列表后，自动隐藏的 TICK 分支会抛
`AttributeError`，事件中心把它吞成一条日志，所以界面"看起来正常、只是不收起"。两处改为
`widget_global_rect(widget).center`（核心 `Rect` 属性，与第 13 节 `command_dialog` 那次同源）。

守卫加了两道：静态扫描 `lib/script/ui` 里对控件变量取 `.geometry()`（只登记"跨控件测量"
这一种写法，宿主窗口自己读 `self.geometry()` 合法）；运行期真构造音响搜索 UI、让族内滑块
可见后驱动 `_is_mouse_far_from_family()`，再盯事件中心错误日志。第一条断言第一版漏了
"跳过不可见控件"这条分支，回归躲过去了——补上可见性前置后才复现出
`AttributeError: 'SpeakerVolumeSlider' object has no attribute 'geometry'`。

**同轮追加二：音响搜索结果框（`lib/script/ui/speaker_search_result_box.py`）**

第 13 节清单第 2 项里的另一只顶层浮窗控件，与滑条同批推进。

- 描述层新增 `SearchResultListControl`：列表数据、翻页（循环）、选中行、搜索中标记、
  尺寸自适应（`search_result_panel_size`）与绘制批次；`row_at_y()` 用共享
  `visual.row_rects` 反查命中行，替换掉控件里手算的 `(y - border) // 行高`——行高与边框
  现在只有 `media_panel_visuals` 一份事实。
- 控件本体删除 `QWidget` 基类与 `PyQt5` 引用，改为 `create_control_host()`；`width()` /
  `height()` / `x()` / `y()` / `isVisible()` 转发宿主。对外接口
  （`clear_results` / `set_results` / `set_searching` / `navigate` / `turn_page` /
  `fade_in(dialog)` / `fade_out` / `hide` / `close` / `update`）不变。
- `SpeakerSearchDialog` 不再给结果框 `installEventFilter`（它已不是 `QWidget`）；方向键导航
  在输入框有焦点时仍由输入框的事件过滤器处理。
- 点击粒子仍是“先发后判”：左/右键在控件内任意位置都发射 `click` / `pink_click`，
  未命中行时不发播放/入队事件——与迁移前逐字一致。
- `frozen_ui_qt_importers`：70 → **67**（本轮双滑条 + 结果框三项）。

**同轮追加三：命令提示框（`lib/script/ui/command_hint_box.py`）**

第 13 节清单第 2 项里的最后一只顶层浮窗控件，与搜索结果框同批推进。

- 描述层新增 `CommandHintControl`：默认三行提示与 `#` 命令列表两种模式、每页 5 行、
  循环翻页、选中行、`get_completion()` 补全串与尺寸自适应；命中行改用共享
  `visual.row_rects` 反查 `row_at_y()`，页指示器命中走 `page_indicator_contains()`，
  行高与边框不再在控件里抄第二份。
- 控件本体删除 `QWidget` 基类与 `PyQt5` 引用，改为 `create_control_host()`；
  `width()` / `height()` / `x()` / `y()` / `isVisible()` / `update()` / `hide()` / `close()`
  转发宿主。对外接口（`update_input` / `get_completion` / `navigate` / `turn_page` /
  `fade_in` / `fade_out`）与 `CommandDialog` 调用面不变。
- 提示框已不是 `QWidget`，`pet_window_ui` 不再把它 `adopt()` 进右键图层；它自己持有顶层
  宿主，靠 `UI_CREATE` / `UI_ANCHOR_RESPONSE` 锚到命令框 `bottom_left` + `_GAP_Y`，
  页翻按钮仍是 `QWidget`，继续由图层 `adopt`。
- `frozen_ui_qt_importers`：67 → **66**（本轮双滑条 + 结果框 + 提示框四项移出）。

**同轮追加四：命令框与右键图层的事件订阅释放（teardown 崩溃根因修复）**

迁出提示框后单独跑 `test_control_layer_descriptions.py` 会在全部用例通过后以
`0xC0000409` 退出，报一条 `AttributeError: 'CommandDialog' object has no attribute
'_entry'`（`command_dialog.eventFilter`）。根因是 `CommandDialog` 与 `RightClickUiLayer`
都只订阅、不释放：命令框订阅了 `TICK` / `FRAME` / `UI_ANCHOR_RESPONSE` / `UI_CREATE` /
`UI_COMMAND_TOGGLE` / `UI_HINT_PICK` / `UI_CLICKTHROUGH_TOGGLE`，并挂了 `focusChanged`
与 `_entry.installEventFilter(self)`；右键图层订阅了 `FRAME` /
`UI_CLICKTHROUGH_TOGGLE`。事件中心的监听表持有强引用回调，测试销毁控件后这些槽仍在，
继续把 Qt 事件投回一个已析构的 Python 包装上——`eventFilter` 里的 `self._entry` 因包装的
`__dict__` 已被清空而抛异常，PyQt 的“槽内未捕获异常”路径随即 `abort()`。修复：
`CommandDialog.closeEvent` → `_dispose()` 摘掉事件过滤器、断开 `focusChanged`、注销全部
事件订阅；`RightClickUiLayer.close_layer()` → `_dispose()` 注销 `FRAME` /
`UI_CLICKTHROUGH_TOGGLE`；`eventFilter` 内再补一道 `getattr(self, '_entry', None)` 兜底，
防已进入队列的陈旧事件重新踩到同一处。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2113 通过、10 跳过。
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过、7 跳过。
- `py -3 -m ruff check lib config scripts tests` 归零；`py -3 -m compileall -q config lib scripts tests` 通过。

## 19. 第十四轮执行记录（右键矩形动作按钮族收敛）

第 13 节滚动清单第 1 项：`rect_action_button_style.py` 一族的八个按钮（鼠标穿透 / 放大 /
缩小 / 关闭 / 启动鸣潮 / 聊天模式 / 交互模式 / 更多功能）连同它们的共享基类文件一并收进
「描述 + 后端渲染」。这是最后一个成规模的产品控件族，收完后 `frozen_ui_qt_importers`
从 **66 降到 57**，`rect_action_button_style.py` 删除。

**新增共享运行时（`lib/script/ui/rect_action_button_runtime.py`）**

- `RectActionButtonRuntime`：一个矩形动作按钮的「描述 + 宿主」装配。构造时建
  `RectActionButtonControl`（形状 / 文字 / 透明度 / hover / 点击粒子都在描述层）、建
  `create_control_host()` 宿主，并把悬停、指针、淡出结束三类回调接回控件自己的产品意图。
  八个按钮的差异只剩「文字、节点 id、点下去做什么」，装配代码只有这一份。
- `emit_click_particle(control, event)`：把描述层给的 `click_particle_id` 翻成
  `publish_click_particle_at`，与迁移前逐字一致。
- 淡出粒子在宿主旧几何处发 `right_fade`（`PARTICLE_REQUEST`）。按钮不再各设空闲计时，
  组的自动隐藏仍只由命令框的鼠标距离守卫负责（见下「迁移期修复的两处回归」）。

**描述层（`lib/core/render/visuals/controls.py`）**

- `RectActionButtonControl.build_visual(font)` 收口到共享 presenter
  `build_rect_action_button_visual()`，控件侧只提供 `FontSpec`，不再自己拼 `DrawBatch`。

**重写后的八个按钮**

- `clickthrough_button.py` / `close_button.py` / `scale_button.py`（`ScaleUpButton` /
  `ScaleDownButton` 现在是两个各自独立的类，不再互相继承）/ `chat_mode_button.py` /
  `interaction_mode_button.py` / `more_functions_button.py` / `launch_wuwa_button.py` /
  `restore_button.py` 全部删除 `QWidget` 基类与 `PyQt5` 引用，各持一个 `RectActionButtonRuntime`。
  对外接口（`fade_in` / `fade_out` / `move` / `width` / `height` / `x` / `y` / `isVisible` /
  `update` / `hide` / `close`）不变，`_ui_id` 仍是文件里的字面量。
- `chat_mode_button` / `interaction_mode_button` 的动态文字经 `runtime.control.text = ...`
  后 `update()`，与旧 `setText` + `update` 等价。
- `launch_wuwa_button` 的启动动作收口到共享的 `lib/script/app/wuwa_launcher.py`，删掉了旧的
  400 行路径自动发现子类。
- `restore_button` 是族外顶层浮窗：自己持有宿主，靠锚点事件贴位，不再由右键图层 `adopt`。

**宿主与消费者**

- `QtControlHost` 不需要新增「收编子窗口」能力——它本身就是 `QWidget`，右键图层原有的
  `adopt()`（内部 `setParent()`）直接收编八个按钮的 `_runtime.host`，仍然每帧只移动一个
  原生窗口。第 13 节一度设想的「宿主得先支持把控件窗口收编成子窗口」前置项因此不存在。
- `pet_window_ui` 的 `layer.adopt(...)` 与 `register_family_node(...)` 改为传各按钮的
  `_runtime.host`；`command_dialog._iter_family_widgets` 经 `_family_window(button)` 取宿主；
  `close_button_handler` 的点击命中改用宿主几何。

**守卫（与迁移同一提交更新）**

- `tests/test_control_layer_descriptions.py`：已迁移清单加入八个按钮（含
  `scale_button.py` 的 `ScaleUpButton` / `ScaleDownButton` 两条），要求均无基类、不得出现
  `geometry` / `pos` / `frameGeometry`。
- `tests/test_qt_dependency_boundaries.py`：`frozen_ui_qt_importers` 移除八个按钮文件与
  `rect_action_button_style.py`（66 → 57，清单仍与仓库真实 `import PyQt5` 逐条一致）。
- `tests/test_right_click_ui_layer.py`：族内集成测试改经 `_member_window(name)` 命中宿主。
- `tests/test_visual_presenters.py` 删除只针对旧 `rect_action_button_style.py` 的用例；
  `tests/test_ui_input_event_consumers.py` 的还原按钮用例改读核心 `Point` 属性。

**迁移期修复的三处回归**

- 关闭按钮的鼠标进入/离开：`close_button_handler` 一直用 `self._button.parent()` 判
  "进/出的是不是主人窗口那一侧"，而 `CloseButton` 已不是 `QWidget`、没有 `parent()`，
  于是每次鼠标进出都抛 `AttributeError` 被事件中心吞成日志（表现为关闭按钮不再自动
  淡入/淡出）。改为统一取 `_runtime.host`，未迁移控件仍回退 `parent()`。
- 八个动作按钮的自动收起：旧实现的 `_idle_timeout` / `_last_activity_time` 是死代码
  （赋值后从不读取），真正的自动隐藏只由 `CommandDialog` 的鼠标距离守卫在 TICK 时负责。
  迁移时若把它接成宿主的 `auto_hide_ms`，八个按钮会在命令框打开约 `idle_close_ms` 后
  各自消失，与"鼠标还在旁边"相矛盾。现在不再给按钮设空闲计时，`_reset_idle_timer`
  转发一并移除。
- 命令列表高亮不跟随鼠标：提示框与搜索结果框原先在各自 `QWidget` 里
  `setMouseTracking(True)`，迁到共享 `QtControlHost` 后宿主没打开鼠标跟踪，Qt 便只在
  按住按键时才投递 `mouseMoveEvent`——表现是"移动鼠标时高亮不动、按下才跟着跳"。
  `QtControlHost` 现在在传入 `on_pointer_move` 时自行 `setMouseTracking(True)`，四个悬停
  类控件（命令提示框、搜索结果框、音量/频段滑条）一起恢复。
- 三道守卫：`tests/test_ui_input_event_consumers.py` 用带 `_runtime.host` 的探针驱动
  MOUSE_ENTER/MOUSE_LEAVE 并断言事件日志无异常；`tests/test_control_layer_descriptions.py`
  一条把 `idle_close_ms` 压到 300ms、打开命令框后只跑事件循环（不发 TICK）断言八个按钮
  仍全部可见，另一条断言宿主开启了鼠标跟踪并向它投递真实 `QMouseEvent` 后高亮行跟着变。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2115 通过、10 跳过
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过、7 跳过。
- `py -3 -m ruff check lib config scripts tests` 归零；`py -3 -m compileall -q config lib scripts tests` 通过。

**本轮没动的部分**

- `frozen_ui_qt_importers`：66 → **57**（一族九个文件一次移出）。
- 滚动清单第 2 项（其余顶层浮窗）、第 3 项（带子控件树与 `exec_()` 的对话框）、第 4 项
  （`world_objects/*` 与 `game_runtime`）仍未动。

## 20. 第十五轮执行记录（模态宿主：确认/提示框收敛）

第 13 节滚动清单第 3 项的结构前置项：`exec_()` 类对话框需要"宿主支持模态"。本轮先补
这一层，并用共享的确认/提示框验证它——它是全应用模态对话的公共入口，行为已被
`tests/test_confirm_dialog.py` 钉住。

**新增后端宿主（`lib/core/render/backends/qt/widgets/message_box_host.py`）**

- `QtMessageBoxHost`：一个真实 `QMessageBox` 的持有者与模态执行器，属档位 D。它只做
  Qt 事实：构造、图标、标准按钮组合、按钮文字/对象名、默认键与逃逸键、样式表应用、
  `WindowLayer.DIALOG` 注册与 `exec_()` 模态循环。返回值翻译回后端中立名
  （`yes` / `ok` / `no` / `cancel` / `escape`）。
- 图标用后端中立名（`information` / `warning` / `question` / `critical`）传入，由宿主
  翻译成 `QMessageBox.Icon`。换后端时替换的是这一层，不是确认框语义。

**解析/转发层（`lib/script/ui/render_bridge.py`）**

- 新增 `create_message_box_host(parent, *, object_name="")`，与 `create_control_host`
  同属档位 A：控件只声明"问什么、有哪些按钮、用哪种配色"，真实对话框由后端提供。

**控件（`lib/script/ui/confirm_dialog.py`）**

- 删除 `PyQt5` 引用与 `QMessageBox`/`QWidget` 依赖，改由 `render_bridge` 拿宿主。
  样式表生成、对象名常量、按钮语义（破坏性按钮用 danger 色、取消键为默认与逃逸键）
  逐字保留；`ask_confirmation(...) -> bool` 与 `show_message(...) -> None` 调用面不变，
  `ai_settings_panel` / `tray_icon` 等调用方无需改动。
- 新增图标常量 `ICON_INFORMATION` / `ICON_WARNING` / `ICON_QUESTION` / `ICON_CRITICAL`
  进 `__all__`，替代原先引用 `QMessageBox.Icon` 的调用面。

**守卫**

- `tests/test_confirm_dialog.py` 改为经 `_build_dialog` 拿到宿主、用 `host.widget()` 读
  底层 `QMessageBox` 断言按钮语义；`exec_` 的补丁落在宿主模块上，不再依赖控件模块
  暴露 `QMessageBox`。
- `tests/test_qt_dependency_boundaries.py` 的 `frozen_ui_qt_importers` 移除
  `confirm_dialog.py`（57 → 56）。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2115 通过、10 跳过。
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过、7 跳过。
- `py -3 -m ruff check lib config scripts tests` 归零；`py -3 -m compileall -q config lib scripts tests` 通过。

**本轮没动的部分**

- `frozen_ui_qt_importers`：57 → **56**（本轮移出确认框一项）。
- 第 3 项里其余 `exec_()` 对话框（`update_dialog`、`voice_package_installer`、
  `game_manager_window` 的确认框、`office_*`）与第 4 项（`world_objects/*`、
  `game_runtime`）仍未动；模态宿主已就位，它们可按同一模式逐个迁出。

## 21. 第二十一轮执行记录：浮窗外壳抽到渲染层

问题：`lib/script/ui/workbench_floating.py`（358 行）同时是"浮窗外壳事实源"和"Qt 实现"。
它的 QSS 生成、主题变更判定、拖拽策略与窗口按钮工厂要么与 `QWidget` 无关，要么可拆，
却被 5 个浮窗（二维码登录 / 更新 / 下载 / 公告 / 帮助）、主工作台窗口与测试共同依赖。

拆法沿用第 19/20 轮的套路：**后端中立数据进 `visuals/`，Qt 实现在 `backends/qt/widgets/`**。

**后端中立（`lib/core/render/visuals/workbench_chrome.py`）**

- `floating_window_stylesheet(mode=None)`：接收显式模式（`None` 走实时 UI 配置），
  只依赖 `visuals/workbench_tokens` 的 token 映射与 `config.scale` / `config.font_config`。
- `is_workbench_theme_change(event)`：纯数据判定。
- `FLOATING_WINDOW_OBJECT_NAME`。

它不 import Qt、也不 import `lib.script`——所以"同一套浮窗样式"能被 DX 宿主共用。

**Qt 落点（`lib/core/render/backends/qt/widgets/`）**

- `floating_window.py`：`QtWorkbenchFloatingWindow`（原 `WorkbenchFloatingWindow` 本体）、
  `FloatingDragFilter`、`FloatingWindowThemeWatcher`。
- `window_buttons.py`：`create_window_button()`（原生标准图标的最小化/关闭按钮工厂），
  此前散在 `workbench_components.py`，现在是主窗口与浮窗共用的一份。

**产品侧**

- `lib/script/ui/workbench_floating.py` 358 → 39 行，改为再导出垫片：
  `WorkbenchFloatingWindow` 是 `QtWorkbenchFloatingWindow` 的历史别名，
  `create_window_button` / `floating_window_stylesheet` / `is_workbench_theme_change`
  同样从渲染层再导出，既有导入路径不变。
- `lib/script/ui/workbench_components.py` 只保留产品部件（`WorkbenchPetAboutButton`、
  `WorkbenchOverviewPage`），窗口按钮工厂改从 `widgets/window_buttons` 取。

**一处必须记下的等价性**：中立层不得 import `lib.script`（`test_code_structure_boundaries`
的 render 纯净性断言），而窗口按钮 QSS 助手住在 `lib.script.workbench.theme`。于是
中立层内联了 `_window_button_stylesheet(mode)`，用同一份 `WORKBENCH_*_TOKENS` 复现；
`tests/test_workbench_chrome.py` 钉死它与 `window_button_stylesheet(mode)` 逐字符一致，
任何一键位/取值漂移都会立刻失败。浮窗整体 QSS 与收敛前逐字符相同。

**新增测试**：`tests/test_workbench_chrome.py` —— 中立层无 Qt / 无 `lib.script`、样式是模式
的纯函数、内联按钮 QSS 与共享助手一致、浮窗 QSS 覆盖工作台 token、旧导入名与 Qt 宿主是
同一实现、主题变更判定语义。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2115 通过（含新增 7 项），10 跳过
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过，7 跳过
- `py -3 -m ruff check lib config scripts tests` 干净；`py -3 -m compileall -q config lib scripts tests` 通过

**本轮没动的部分**

- `frozen_ui_qt_importers` 计数不变（56）：`workbench_floating.py` / `workbench_components.py`
  仍是 Qt 之上的产品部件，只是把可复用外壳事实下沉。
- 第 3 项其余 `exec_()` 对话框与第 4 项（`world_objects/*`、`game_runtime`）仍未动。

## 22. 第二十二轮执行记录：办公线性图标抽成后端中立规格

问题：`lib/script/ui/office_icons.py`（99 行）把"图标长什么样"（9 段 SVG path、逻辑尺寸、
描边宽度/端点）和"怎么变成 `QIcon`"（`QSvgRenderer` + `QPixmap`）写在同一个文件里，于是
一个纯图形的数据表被 Qt 拖进了产品控件的冻结清单。

拆法：**图形事实进 `visuals/`，Qt 渲染进 `backends/qt/widgets/`**。

**后端中立（`lib/core/render/visuals/office_icons.py`）**

- `OFFICE_ICON_NAMES`：公开图标名（`new` / `delete` / `browse` / `cancel` / `submit` /
  `reject` / `warning` / `allow` / `allow_task`）。
- `office_icon_svg(name, color)`：按名字与描边色生成成品 SVG 文本。
- `office_icon_size(name)`：逻辑尺寸（已过 `scale_px`）。

这些是纯字符串与整数，Qt 与（将来的）DX 宿主共用同一份事实源。

**Qt 落点（`lib/core/render/backends/qt/widgets/office_icons.py`）**

- `render_office_icon(name, color)` → `QIcon`；`render_office_icon_pixmap(name, color, px)`
  → `QPixmap`。原 `_render_svg`（`QSvgRenderer` + 2x `QPixmap`）原样保留在这里。

**产品侧**

- `lib/script/ui/office_icons.py` 改为门面垫片：九个既有函数名（`office_new_icon` 等）
  改为委托 `render_bridge`，不再 import `PyQt5`；`frozen_ui_qt_importers` 56 → 55。
- `render_bridge` 新增 `render_office_icon()` / `render_office_icon_pixmap()` 两个落点。
- `office_approval_dialog.py` 里"警告图标 → `pixmap(24,24)`"的一步改用
  `render_office_icon_pixmap("warning", …, 24)`；`office_page.py` 的五个按钮图标走
  门面函数，调用面不变。

**等价性**：`tests/test_office_icons.py` 把九个名字的成品 SVG 逐字符钉死（并断言渲染出的
`QIcon` 非空、`pixmap` 尺寸正确、门面旧名仍可用），因此图形与收敛前逐字节相同。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2128 通过（含新增 6 项），10 跳过
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过，7 跳过
- `py -3 -m ruff check lib config scripts tests` 干净；`py -3 -m compileall -q config lib scripts tests` 通过

**本轮没动的部分**

- `frozen_ui_qt_importers`：56 → **55**（本轮移出 `office_icons.py` 一项，累计 73 → 55）。
- 第 3 项其余 `exec_()` 对话框与第 4 项（`world_objects/*`、`game_runtime`）仍未动。

## 23. 第二十三轮执行记录：点击粒子辅助改走后端中立按钮名

问题：`lib/script/ui/_particle_helper.py` 只为拿到"按的是左键还是右键"而 import `PyQt5`，
于是这份与工具包无关的按钮映射被拖进冻结清单。

拆法（最小、不新增落点）：

- `visuals/controls.py` 的 `BUTTON_LEFT` / `BUTTON_RIGHT` / `BUTTON_PARTICLES` 已是产品事实。
- `render_bridge` 新增 `pointer_button_name(event)`：把 `Qt.LeftButton` / `RightButton` /
  `MiddleButton` 翻译成中立名。它是档位 A 的既定翻译落点，不是新的越界面。
- `_particle_helper.py` 改为 `BUTTON_PARTICLES.get(pointer_button_name(event))`，
  删除 `from PyQt5.QtCore import Qt`；`publish_click_particle_at` 本就是中立的。
  （它的 5 个调用方仍是未迁的 Qt 控件，但它们只是传入一个 Qt 鼠标事件，调用面不变。）

**关于一处被否掉的方案**：曾尝试把音响右键菜单族样式（`speaker_menu_style.py`）整体下沉到
`backends/qt/widgets/`，但该文件既画 `DrawBatch`（`drawing/` 的能力）又是按钮 Qt 混入（`scripts`
产品部件）——放进 `widgets/` 会命中"widgets 不得引用 drawing"与"render 不得 import lib.script"
两条硬边界。若要真正迁它，需要先在档位 A 增加一个中立的"执行绘制批次"宿主能力，属后续专轮，本轮
不做。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2132 通过（含新增 4 项），10 跳过
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过，7 跳过
- `py -3 -m ruff check lib config scripts tests` 干净；`py -3 -m compileall -q config lib scripts tests` 通过

**本轮没动的部分**

- `frozen_ui_qt_importers`：55 → **54**（本轮移出 `_particle_helper.py`；累计 73 → 54）。
- 第 3 项其余 `exec_()` 对话框与第 4 项（`world_objects/*`、`game_runtime`）仍未动。

## 24. 第二十四轮执行记录：论坛样式去掉 QColor 依赖

问题：`lib/script/ui/forum_style.py` 只为一处整数混色 import `QColor`
（`forum_texture_color` 把主题中性色与卡片 accent 按比例混合），整份样式模块因此被
拖进冻结清单。

拆法（原地中性化，不新增落点）：

- 用 stdlib 的 `int(hex, 16)` 解析 `#rrggbb`，按与 `QColor` 相同的 `round()` 逐通道混色，
  再 `"#{:02x}{:02x}{:02x}".format(...)` 输出小写十六进制。删除 `from PyQt5.QtGui import QColor`。
- 模块其余部分（`forum_stylesheet` 的 QSS 生成、字号自适应、accent 取色）本就只返回字符串
  或调用 `lib.script.workbench.theme`（产品层），无 Qt 事实。

**等价性**：`tests/test_forum_style.py` 把 14 组（模式 × accent，含合法/非法/空/None）的
输出逐字符钉死；收敛前用 `QColor` 跑出的 28 组结果与收敛后逐字节一致。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2135 通过（含新增 3 项），10 跳过
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过，7 跳过
- `py -3 -m ruff check lib config scripts tests` 干净；`py -3 -m compileall -q config lib scripts tests` 通过

**本轮没动的部分**

- `frozen_ui_qt_importers`：54 → **53**（本轮移出 `forum_style.py`；累计 73 → 53）。
- 第 3 项其余 `exec_()` 对话框与第 4 项（`world_objects/*`、`game_runtime`）仍未动。

**探测记录（两条被否掉的路径）**：把音响菜单族样式或 `world_objects/*` 下沉到 `widgets/`
都会撞上同一堵墙——`widgets/` 既不得 import `drawing/`（绘制执行走注册表，但 `QColor` 转换在
`drawing/colors`），也不得 import `lib.script`（主题色 `get_workbench_colors` 在产品层）。
真正解锁它们需要先在档位 A/D 增加一个「中立执行绘制批次 + 主题色转换」的宿主能力（且不能
把产品 `lib.script.workbench.theme` 拉进 `render/`），属后续专轮。

## 25. 第二十五轮执行记录：二维码登录浮窗的宿主能力下沉到基类

问题：`yuanbao_login_dialog.py` 只为一枚自动收起 `QTimer` 与一处穿透切换而 import `PyQt5`；
这两件事对二维码登录浮窗族是共通的宿主行为，本不该各写一份。

拆法（把能力上收到 Qt 基类，产品子类去 Qt）：

- `BaseQrDialog`（`qr_dialog_base.py`，已在冻结清单）新增：
  - `self._auto_close_timer`（单次 `QTimer`，`timeout` → `hide_dialog`）；
  - `hide_dialog()` 先 `self._auto_close_timer.stop()`；
  - `set_clickthrough(enabled)`：`setAttribute(WA_TransparentForMouseEvents, …)`。
- `yuanbao_login_dialog.py`：删除 `from PyQt5.QtCore import Qt, QTimer`，改用继承来的定时器
  与 `set_clickthrough`，不再覆写 `hide_dialog`（停表已在基类）。`show_dialog` 仍在开始时
  停表，语义不变。`frozen_ui_qt_importers` 54 → 53 → 现 52。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2138 通过（含新增 3 项），10 跳过
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过，7 跳过
- `py -3 -m ruff check lib config scripts tests` 干净；`py -3 -m compileall -q config lib scripts tests` 通过

**本轮没动的部分**

- `frozen_ui_qt_importers`：54 → **52**（本轮移出 `yuanbao_login_dialog.py`；累计 73 → 52）。
  同类可继续：`cloudmusic_login_dialog.py` 的自动收起/停表可复用同一基类能力（其另有
  `WindowDoesNotAcceptFocus` 标志与 hide/close 的"禁止收起"拦截面，属其自身行为）。
- 第 3 项其余 `exec_()` 对话框与第 4 项（`world_objects/*`、`game_runtime`）仍未动。

## 26. 第二十六轮执行记录：网易云登录浮窗去 Qt

问题：`cloudmusic_login_dialog.py` 仍 import `PyQt5`，只为三件事：登录窗标志
（`Window | FramelessWindowHint | WindowStaysOnTopHint | WindowDoesNotAcceptFocus`）、
两处 `QTimer.singleShot(0, _restore_if_needed)` 自愈、以及一处穿透 `setAttribute`。

拆法（继续把共用宿主行为上收到 `BaseQrDialog`）：

- `window_flags` 参数接受新哨兵 `"login"`：由基类解析出登录窗标志（不进任务栏、不抢焦点），
  子类不再拼 `Qt.*`；整型 flags 仍照旧透传。
- 基类新增 `restore_soon()` / `_restore_if_needed()`：下轮事件循环把窗口重新置前，
  用于"禁止收起"浮窗被系统隐藏后的自愈。
- 穿透复用上一轮的 `set_clickthrough()`。
- `cloudmusic_login_dialog.py` 删除 `from PyQt5.QtCore import Qt, QTimer` 与自带的
  `_restore_if_needed`、`get_layer_manager` 导入；`frozen_ui_qt_importers` 52 → 51。

验证：

- `py -3 -m unittest discover -s tests -p "test_*.py" -q`：2141 通过（含 `tests/test_yuanbao_login_dialog.py` 追加 3 项），10 跳过
- `py -3 -m unittest discover -s tests/dx -p "test_*.py" -q`：122 通过，7 跳过
- `py -3 -m ruff check lib config scripts tests` 干净；`py -3 -m compileall -q config lib scripts tests` 通过

**本轮没动的部分**

- `frozen_ui_qt_importers`：53 → **51**（本轮移出 `cloudmusic_login_dialog.py`；累计 73 → 51）。
- 第 3 项其余 `exec_()` 对话框与第 4 项（`world_objects/*`、`game_runtime`）仍未动。

## 27. 第二十七轮执行记录：翻页按钮去 Qt

滚动清单第 2 项的最后一个纯浮窗控件：`lib/script/ui/page_turn_buttons.py` 的
`_PageTurnButton`（上一页 / 下一页）。它原来同时是 `QWidget` 与 `SpeakerActionButtonMixin`，
只用 Qt 做四件事：面板底壳、居中三角形图标、透明度动画、`rect().contains()` 的点内判定。

拆法：

- 共享绘制新增 `application_visuals.build_page_turn_button_visual()`：面板底壳复用
  `panel_visuals.action_button_commands()`（黑框 → 青中框 → 粉底 + 悬停/按下换色），
  居中三角形用核心 `PathCommand` + `build_polygon_path()` 生成。两个后端拿到同一份批次，
  箭头不再由宿主用 `QPainter.drawPolygon` 各画一遍。
- 描述层新增 `PageTurnButtonControl`（`visuals/controls.py`）：箭头方向、悬停/按下状态与
  `state()`（`normal` / `hover` / `pressed` / `pressed_flat`，与迁移前的
  `hovered`/`pressed` 组合逐字一致）、绘制批次与点击粒子名。底色取自
  `visuals/palette.py` 的 `COLORS["black"]`，与迁移前 `qt_color('black')` 同源。
- 产品控件改为"描述 + 宿主装配"：`create_control_host()` 建窗口，落位仍走
  `resolve_placement()` / `widget_global_rect()`。对外接口（`show_btn` / `hide_btn` /
  `move` / `width` / `height` / `x` / `y` / `isVisible` / `close`）不变。
- 宿主新增一项通用能力 `accepts_focus=False`（`QtControlHost`）：翻页按钮一类的附属控件
  不该抢键盘焦点，迁移前各写一遍的 `setFocusPolicy(Qt.NoFocus)` 收到宿主参数里。

**点击判定的等价性（唯一需要宿主新语义的地方）**

迁移前的 `mouseReleaseEvent` 用 `self.rect().contains(event.pos())` 决定"这次松手算不算提交"。
控件不再是 `QWidget` 后没有 `rect()`，而**不能用宿主几何代替**：宿主窗口就是按钮本体，
按几何夹取会让"按下后拖出按钮再松手"永远提交。现在由描述层旁的控件保存最近一次指针是否
仍在按钮矩形内（`QtControlHost` 只在按下时投递 `mouseMoveEvent`，与 Qt 原语义一致），
`tests/test_control_layer_descriptions.py::PageTurnButtonBehaviorTests` 覆盖
"点内提交 / 拖出取消 / 拖回再提交"三条路径。

**消费者与收编**

- `pet_window_ui` 的 `layer.adopt(...)` 与 `speaker_search_dialog._iter_family_widgets()`
  原先直接收编按钮本体（`QWidget`）并用 `widget_global_rect()` 量它；现在改为收编托管宿主
  `_prev_btn._host`，两个后者的调用面不变。
- `playlist_panel` / `command_hint_box` / `speaker_search_result_box` 的
  `make_page_buttons` / `update_page_buttons_position` / `hide_btn()` 调用面全部保持。

**本轮没动的部分**

- `frozen_ui_qt_importers`：51 → **50**（本轮移出 `page_turn_buttons.py`；累计 73 → 50）。
- 第 3 项其余 `exec_()` 对话框与第 4 项（`world_objects/*`、`game_runtime`）仍未动。

## 28. 第二十八轮执行记录：音响菜单控制按钮族去 Qt

滚动清单里的最大一族：`lib/script/ui/speaker_control_buttons.py`（暂停/播放、下一曲、
登录音乐、平台模式、播放模式、搜索优先级、播放列表、一键历史 / 清空 / 本地 / 喜欢、
音量加 / 减共 12 个按钮，加一个负责落位的 `SpeakerControlButtons` 管理器）。它们是
典型的"状态机 + `QWidget`"控件：形状、悬停/按下、几何图标、文字、淡入淡出、点击判定
与窗口标志混在一个类里。第 23 节曾把它的共享样式（`speaker_menu_style.py`）记为
**受阻**——需要先有"执行绘制批次 + 主题色"的中立宿主，该宿主在第二十七轮落成
（`QtControlHost` + `render_bridge.create_control_host()`），本轮据此把它迁出。

拆法：

- 共享绘制新增 `application_visuals.build_speaker_action_button_visual()`：面板底壳直接
  复用 `panel_visuals.action_button_commands()`（黑框 → 青中框 → 粉底 + 悬停/按下换色，
  与迁移前 `speaker_menu_style.paint_speaker_action_button` 同一份配方），中位内容按
  `glyph` 画几何图标（`pause` / `play` / `next_track`：`RectCommand` 竖条 +
  `PathCommand` + `build_polygon_path()` 三角）或居中文字；三枚图标合成器
  `append_speaker_pause_glyph()` / `append_speaker_play_glyph()` /
  `append_speaker_next_track_glyph()` 独立成器，供后续需要同一枚图标的控件（如播放列表
  把文字标签换成图标）直接复用。
- 描述层新增 `SpeakerActionButtonControl`（`visuals/controls.py`）：图标名、文字、悬停 /
  按下状态与 `state()`（`normal` / `hover` / `pressed` / `pressed_flat`，与迁移前的
  `hovered`/`pressed` 组合逐字一致）、绘制批次与点击粒子名。文字按钮与图标按钮共用同一个
  描述，差异只有 `glyph` 与 `label()`。
- 产品控件改为"描述 + 宿主装配"：`create_control_host()` 建窗口，落位仍走
  `resolve_placement()` / `screen_rect_for_point()`。动态文案（登录态、平台模式名、
  播放模式、搜索优先级）改为每帧从子类 `label()` 读出，不再由子类各写一份 `_draw_icon`。
- 锚点回答不再经过 Qt 控件：播放/暂停按钮响应 `UI_CREATE` 时用它自己的核心几何 + 新的
  中立助手 `render_bridge.local_anchor_point(anchor_id, width, height)` 直接产出
  `QPoint` 语义的整数全局锚点（`render_bridge.core_point()` 负责把载荷归一化成核心
  `Point`）。这一改动同时让 `speaker_control_buttons.py` 不再需要
  `backends/qt/widgets/anchors.py` 的 `publish_widget_anchor_response()`。

**顺手修掉的两处同源偏差**

第二十七轮的共享翻页三角形是在"抄一遍"里落地的，逐像素对照迁移前的 Qt 绘制后暴露
两处偏差，本轮一并修正（现由测试钉死）：

- 图标中心：迁移前用 `QRect.center()`，它是 `x + (w - 1) // 2`，不是 `x + w / 2`。
  `QRect(4, 4, 32, 24)` 的中心是 `(19, 15)` 而非 `(20, 16)`；差一个像素时整枚几何图标
  会偏移。共享事实源新增 `application_visuals.glyph_center()`，暂停 / 播放 / 下一曲 /
  翻页四枚图标全部改读它。
- 箭头方向：上一页 `_direction == -1` 在 Qt 里画的是**朝左**的三角，共享实现曾按相反的
  符号展开，上一页/下一页的箭头左右互换了。本轮改正，并由
  `tests/test_speaker_playlist_visuals.py::SpeakerActionButtonVisualTests` 断言
  "上一页箭头尖落在图标中心左侧、下一页落在右侧、两侧对称"。

几何图标整段绘制在迁移前都跑在 `QPainter.Antialiasing(True)` 下，因此暂停双竖线与下一曲
竖条的共享命令也标 `antialias=True`（文字与面板底壳仍是 `False`）——这一条同样由逐像素
对照确定。

**点击判定的等价性**

与第二十七轮同源：迁移前 `mouseReleaseEvent` 用 `self.rect().contains(event.pos())` 决定
"这次松手算不算提交"，迁出 `QWidget` 后由控件记录最近一次指针是否仍在按钮矩形内
（`QtControlHost` 只在按下时投递 `mouseMoveEvent`，与原语义一致）。
`tests/test_control_layer_descriptions.py::SpeakerControlButtonBehaviorTests` 覆盖
"点内提交 / 拖出取消 / 悬停与按下换态 / 图标换形 / 动态标签逐帧读取 / 不抢焦点"。

**验证（2026-10-06）**

- 逐像素对照：13 组音响按钮（图标 × 悬停/按下 × 文字）与 8 组翻页按钮（两向 × 四态）
  的宿主渲染结果与迁移前的 `QPainter` 代码**逐字节相等**。
- `frozen_ui_qt_importers`：50 → **49**（本轮移出 `speaker_control_buttons.py`；
  累计 73 → 49）。

**本轮没动的部分**

- `speaker_menu_style.py` 仍是 Qt 混入（`workbench_components.py` / `speaker_search_dialog.py` /
  `playlist_panel.py` 继续用它画底壳），但它的绘制配方已由共享
  `action_button_commands()` 提供——把 `paint_*` 助手改成 shim 属下一轮。
- 第 3 项其余 `exec_()` 对话框与第 4 项（`world_objects/*`、`game_runtime`）仍未动。


## 29. 第二十九轮执行记录：音响菜单族样式去 Qt

第二十八轮留下的尾巴：`lib/script/ui/speaker_menu_style.py`。它当时是唯一还 import
`PyQt5` 的"共享样式"模块，被 `workbench_components.py`（工作台「关于」按钮）、
`speaker_search_dialog.py`（搜索框 + 搜索按钮）与 `playlist_panel.py`（队列删除 /
立即播放按钮）三处共用。它同时是 Qt 混入与一个**自建执行器**——模块顶部
`_DRAW_BACKEND = create_draw_backend()`，这是第二十八轮已经消掉、这里是最后一处
"控件自己造绘制实现"的残留。

拆法与第 27 / 28 轮同源，但多了一个此前没有的缺口：

- 颜色：`_C_*` 从 `QColor` 改成核心 `Color`（`UI_THEME` / `COLORS` / `Color(*entry_bg)`）。
  QSS 要的 `#rrggbb` 文本由新增的 `render_bridge.qt_color_name()` 给出，
  `speaker_search_dialog` 的 `_hex()` 改为经 `render_bridge.create_painter_host().color_name()`。
  共享色板仍是唯一事实源，`_C_*` 只是它的命名视图。
- 绘制：面板壳与动作按钮的配方下沉到 `application_visuals.build_speaker_panel_visual()`
  （直接复用 `panel_visuals.panel_shell_commands()`）与既有的
  `build_speaker_action_button_visual()`；描述层新增 `SpeakerPanelSpec` /
  `SpeakerActionButtonSpec` 两个纯数据描述（`width/height/hovered/pressed/layer/opacity`）。
  两者的状态名与 `SpeakerActionButtonControl.state()` 逐字一致，避免"门面一套状态、描述层
  另一套"的双份事实。
- **缺口**：控件本身是 `QWidget` 子类，它的 `paintEvent` 必须自己起 `QPainter`。
  第二十八轮之前的控件都改成了"非 QWidget + 窗口宿主"，没有这个问题；这一族不行。
  解决方式是新增档位 D 宿主 `backends/qt/widgets/control_painter_host.py` 的
  `QtPainterHost`：在**调用方给的** `QPainter` 上执行批次，并提供
  `color()` / `color_name()` / `rect()` / `qrect()` / `font()` 五个边界转换。
  绘制实现与字体从外面注入（`render_bridge.create_painter_host()`），因此档位 D
  没有静态引用档位 A。`render_bridge` 同时补 `painter_color()`，
  供控件在 `paintEvent` 里给 `QPen` / `setBrush` 取 Qt 颜色值。

`ApplicationPanelVisual` 的第三个字段 `action_rect` 改名为 `content_rect`
（"内容区"才是它的含义；`action_rect` 只被 `test_qr_panel_visual` 引用），并保留同名
只读属性作为兼容别名，二维码面板的调用面不变。

**顺手修掉的回归（本轮真实踩到）**

`painter_color()` 最初只认核心 `Color` 与通道元组。工作台主题令牌
（`get_workbench_colors().text`）给的是 `#rrggbb` **文本**，于是它的第一个字符 `'#'`
被当成通道值送去 `int()`，工作台「关于」按钮在绘制时抛 `ValueError`。全量测试因此
红了一次。现在 `QtPainterHost.color()` 按输入形态分派：核心 `Color` / 通道元组 /
`#rgb|#rrggbb|#rrggbbaa|#aarrggbb` 文本 / `QColor`，非法文本显式 `ValueError`，
并由 `test_painter_host_colour_boundary_accepts_tokens_hex_and_colours` 钉死四种输入。

**验证（2026-10-06）**

- 逐字节对照：6 组面板尺寸 × 1 态 + 6 组尺寸 × 4 态按钮，共 **30 组**门面渲染结果与
  迁移前（`HEAD` 版本）的 `QPainter` 代码**逐字节相等**，内容区也逐项相等；
  另加 **20 组（含 `x`/`y` 非零的 4 组）** 原点对照，同样逐字节相等（这组才是拦住
  "丢原点"的那条，见下）。
- 真实控件改前 / 改后像素对照：队列删除按钮、队列立即播放按钮、工作台「关于」按钮
  三只控件在 `QWidget.render()` 下的 ARGB32 字节完全一致（1600 / 1600 / 9520 字节，
  差异 0 字节）。
- `frozen_ui_qt_importers`：49 → **48**（本轮移出 `speaker_menu_style.py`；累计 73 → 48）。
  全量 `unittest` 2171 通过 / 10 跳过；`tests/dx` 122 通过 / 7 跳过；ruff + compileall 干净。

**同轮修掉的进度条视图缺口**

跑真实交互时暴露：`progress_panel` 迁出 `QWidget` 时只转发了 `width/height/isVisible`，
而同族的 `playlist_panel` 仍在调 `progress_panel.x()` / `.y()`（迁移前那是 `QWidget.x()`）。
崩溃点在 `_update_control_buttons_position()`，异常发生在
`_set_control_buttons_visible(True)` **之前**，所以整族控制按钮（含搜索按钮）一起没出现——
症状看着像"搜索按钮消失"，根因在进度条的对外视图面。

`ProgressPanel` 补回 `x()` / `y()` 转发，与 `page_turn_buttons` / `speaker_control_buttons`
的对外视图面统一；`MigratedControlHostViewTests` 三条断言钉死：视图方法齐全
（`width/height/x/y/isVisible`）、`_update_control_buttons_position()` 整条链在真实控件上
跑通并真的摆了九个控制按钮、同族模块不得对已迁出 `QWidget` 的控件调用未转发的几何方法。
这是一类**只在运行期、只在特定调用链上**才出现的缺口——构造期、导入期与控件自身的测试
都看不到它。

**同轮修掉的按钮落位缺口（比上一条更隐蔽）**

进度条崩溃修完、按钮族"出现了"，但「搜索歌曲」按钮仍然不在它该在的位置：本轮把门面
`paint_speaker_action_button()` / `paint_speaker_menu_panel()` 从"直接把 `QRect` 交给
`panel_visuals` 的共享配方"改成"只把宽高交给描述层"，而 `SpeakerActionButtonSpec` /
`SpeakerPanelSpec` 与两个 `build_*` 构建器都硬编码 `Rect(0, 0, w, h)`——**原点在换手时丢了**。
搜索框的 `paintEvent` 在同一个 painter 上并排画输入区（`x = 0`）与按钮（`x = _INPUT_W`），
按钮因此被画到 `0.._BTN_W`、"整块压在输入区上"，看起来就是搜索按钮消失。

之所以没被拦住：此前的逐字节对照全部用 `QRect(0, 0, w, h)`，原点恒为零时"丢原点"和
"保留原点"是同一份像素。修法是给 `SpeakerPanelSpec` / `SpeakerActionButtonSpec` 与两个
构建器加可选 `origin`（默认原点，单控件宿主的调用面不变），门面把真实原点传下去；并补
两条**修前必红**的断言：非零原点的面板/按钮逐字节对照（`SpeakerMenuStyleFacadeTests`），
以及整个搜索框抓到 `QImage` 后按钮区必须落在 `x = _INPUT_W` 右侧、输入区不得被覆盖
（`test_search_button_paints_to_the_right_of_the_input_box`）。

**本轮没动的部分**

- `speaker_search_dialog.py` / `playlist_panel.py` / `workbench_components.py` 仍是
  `QWidget` 页面或控件，本轮只改它们的取色与画法，未动窗口生命周期与交互。
- 第 3 项其余 `exec_()` 对话框与第 4 项（`world_objects/*`、`game_runtime`）仍未动。

## 30. 第三十轮执行记录：办公面样式去 Qt

`lib/script/ui/office_style.py` 是"共享样式"里剩下的最后一处 Qt 依赖，形态与前几轮不同：
它**整份文件**只 import 了 `PyQt5.QtWidgets` 的九个控件类，但那九个类只被两个辅助函数用到，
而文件里约 500 行是把工作台 token 拼成 QSS 的纯字符串逻辑。也就是说，一条气泡内边距常量
也拖着一份 `PyQt5`。

拆法沿用第 21 轮浮窗外壳与第 24 轮论坛样式的思路——**先分清"数据"与"工具包事实"**：

- 数据（后端中立）：整份 `office_stylesheet()`、`office_effort_colors()` 的档位插值、气泡
  内边距、设置页字号档、`OFFICE_HINT_LABELS` / `OFFICE_BOLD_LABELS`，落在
  `lib/core/render/visuals/office_chrome.py`。配色改读 `visuals/workbench_tokens.py` 的
  token 映射，顺带去掉"`lib.script.ui.office_style` → `lib.script.workbench.theme` →
  `lib.core.render.visuals`"这条绕回渲染层的路径。
- 工具包事实：`apply_office_fonts()` 要遍历控件树、`create_office_accent_bar()` 要 `new`
  出 `QWidget`，落到 `lib/core/render/backends/qt/widgets/office_widgets.py`。

**两条不反向依赖的约束决定了签名**（这是本轮唯一需要留意的设计点）：

1. `lib/core/render` 不得 import `lib.script`（`test_render_layer_never_imports_product_modules`），
   于是推理强度的档位数量不能由中立模块去读 `lib.script.office.contracts`。改为调用方传参：
   `office_effort_colors(mode, *, effort_steps)` / `office_stylesheet(..., effort_steps=...)`，
   产品侧门面把 `len(REASONING_EFFORTS)` 喂进去。
2. 同一条约束也让中立的 QSS 不能去 import `workbench_settings_layout` 拿字号档。处理方式与
   `workbench_chrome` 镜像窗口按钮 QSS 一致：在 `office_chrome` 里放**中立镜像常量**，
   由测试钉住它与产品面权威值逐值相等。

`render_bridge` 新增 `apply_settings_page_fonts()` / `apply_office_widget_fonts()` /
`create_office_accent_bar()` 三个转发落点，与 `create_control_host` / `create_painter_host` /
`render_office_icon` 同级；`office_style.py` 改为保留历史导入名的薄门面，办公页、审批弹窗
与既有测试的调用面不变。

**验证（2026-10-06）**

- 逐字节对照：三种模式（`None` / `dark` / `light`）× `standalone` 两种 × 两个页名，
  共 **15 组** QSS 与收敛前的 `office_stylesheet()` 输出**逐字符相等**；`office_effort_colors()`
  在三种模式下同样逐值相等。对照用迁移前的 `HEAD` 版本作为 oracle。
- 新增 `tests/test_office_style.py` 六条断言：字号档镜像一致、档位色按模式取权威 token、
  15 组 QSS 与中立实现一致、`effort_steps` 确实来自产品契约、门面与中立模块都不 import
  `PyQt5`、中立模块不 import `lib.script` 与 Qt 宿主。
- `frozen_ui_qt_importers`：48 → **47**。全量 `unittest` 2177 通过 / 10 跳过；
  `tests/dx` 122 通过 / 7 跳过；ruff + compileall 干净。

**本轮没动的部分**

- `office_effort_slider.py` / `office_chat_view.py` / `office_page.py` / `office_mode_page.py` /
  `office_mode_settings.py` / `office_manager_card.py` / `office_approval_dialog.py` /
  `office_approval_controller.py` 仍是 `QWidget` / `QDialog` 页面与控件，本轮只改它们取样式
  的入口。
- 第 3 项其余 `exec_()` 对话框与第 4 项（`world_objects/*`、`game_runtime`）仍未动。

## 31. 第三十一轮执行记录：窗口级描述层与审批弹窗去 Qt

前三十轮收敛的是**单个叶控件**：`visuals/controls.py` 描述一个气泡 / 进度条 / 按钮，
`backends/qt/widgets/control_host.py` 把它装成一个真实顶层窗口。第十五轮补上模态宿主
（`message_box_host.py`）之后，"带子控件树的对话框"仍缺一级描述：**一个窗口长什么样**。
本轮补这一级，并用办公审批弹窗验证它。

**描述层分成两级（`lib/core/render/visuals/`）**

- `window_spec.py`：窗口描述的**词汇**。节点有 `LabelSpec` / `IconSpec` / `AccentBarSpec` /
  `TextAreaSpec` / `ButtonSpec` / `SpacerSpec` / `StretchSpec`，容器是 `LayoutSpec`
  （行/列、边距、间距、伸缩、`frame`），根是 `WindowSpec`（窗口标志、模态、拖动手柄 id、
  关闭语义、尺寸档、样式表、窗口图标路径）。对齐、按钮角色与颜色语义都是中立的字符串枚举；
  尺寸、间距、字号一律由产品面算好后填进来，描述层因此不 import `config`。
- `window_specs.py`：按窗口语义组装词汇的**产品共享装配件**（本轮只有
  `office_approval_window_spec()`）。条件子控件是描述层的一等公民：没有 `command_text`
  就不生成命令预览节点，产品窗口不必自己判断树怎么长。

**后端宿主（档位 D，`backends/qt/widgets/spec_host.py`）**

- `QtSpecWindow` 按 `WindowSpec` 装配真实控件树，对外给三样东西：`find(node_id)` 取控件、
  `apply_theme()` 重刷样式与图标色、`resolve_with()` / `dismiss_without_decision()` /
  `note_widget_closed()` 三个**语义动作**。
- 语义动作落在宿主而不是产品窗口：按钮点击、关闭按钮、Esc/窗口管理器关窗都收敛成同一个
  决定，因此"关闭即拒绝"这类产品规则不会因为换了一条关闭路径而漏发。
- 按钮点击直接调 `resolve_with()`（发一次语义 + `accept()`）；宿主自己造的承载窗口用
  `QtSpecDialog` / `QtSpecWidget` 两个薄子类，把 `showEvent`（居中）与 `closeEvent`
  （补发关闭语义）转发回描述层。
- 屏幕归属与绘制实现由 `render_bridge` 经 `presentation_host()` 注入，宿主不静态引用
  档位 A；`render_bridge.create_spec_window()` 是控件侧唯一落点，与 `create_control_host` /
  `create_message_box_host` 同级。

**产品窗口（`lib/script/ui/office_approval_dialog.py`）**

- 删除 `PyQt5` 引用、不再继承 `QDialog`：只做三件事——收状态、产出 `WindowSpec`、把
  `reject` / `allow` / `allow_task` 语义翻译成 `decision_made`。
- 为兼容既有调用方与测试，保留原 `QDialog` 属性面：`approval_id` / `decision_made` /
  `_resolve()` / `dismiss_without_decision()` / `close()` / `open()` / `raise_()` /
  `activateWindow()` / `findChild()` / `styleSheet()` / `windowFlags()` /
  `testAttribute()` / `destroyed`。后四项转发到底层 `QWidget`，调用方不需要知道自己拿到的
  不再是 `QDialog`；`decision_made` 是后端中立的信号替身（只保留 `connect` / `emit` /
  `disconnect`），控制器与测试的用法不变。

**顺手修掉的一处既有缺陷**

`office_approval_controller._poll()` 原先用闭包连 `destroyed`：

```python
dialog.destroyed.connect(lambda _obj=None, owned=dialog: self._clear_dialog(owned))
```

Qt 对接收者只持弱引用，而闭包会一直攥住 `self`；一旦 `_poll` 的帧先被回收，回调里的
`self` 就成了空单元格，弹窗销毁时会抛
`NameError: cannot access free variable 'self' ... in enclosing scope`。改为连绑定方法
（`dialog.destroyed.connect(self._on_dialog_destroyed)`）并去掉不再需要的 `_clear_dialog`。
既有测试之所以没抓到，是因为它们都没有让对话框真正走到 Qt 销毁；本轮迁移顺手把它暴露了。

**验证（2026-10-06）**

- 新增 `tests/test_window_spec.py` 十条断言：描述层在 PyQt5 被屏蔽的进程里完成装配与查询；
  两个中立模块不 import `PyQt5` / `lib.script` / 任一后端 / `config`；窗口宿主不静态引用
  档位 A；审批弹窗的控件树、对象名、图标、命令预览的条件性、三颗按钮的语义、无窗眉标志与
  居中落位逐项由描述产出；生产清单的收缩与迁移一致。
- 全量 `unittest` 2183 → **2193** 通过 / 10 跳过（新增十条）；`tests/dx` 122 通过 / 7 跳过；
  `ruff check .` 归零；`compileall` 通过；`git diff --check` 干净。
- `frozen_ui_qt_importers`：47 → **46**。`MIGRATED_LEAF_CONTROLS` 同时移出
  `office_approval_dialog.py`（它已不再是"由宿主持有的叶控件"，而是"交出窗口描述的产品窗口"）。

**本轮没动的部分**

- 审批弹窗之外的带树对话框（`update_dialog`、`forum_*`、`voice_package_installer`、
  `help_window`、`announcement_dialog`、`playlist_panel`、`qr_dialog_base`、
  `speaker_search_dialog`）仍是 Qt 页面与控件；窗口级描述宿主已就位，可按同一模式逐个搬走。
- `office_*` 其余文件（`office_page` / `office_mode_page` / `office_mode_settings` /
  `office_manager_card` / `office_chat_view` / `office_effort_slider`）是 `QWidget` 页面，
  不是带树对话框，属滚动清单第 4 项的长尾。
- `world_objects/*` 与 `game_runtime.py` 仍未动。
## 32. 第三十二轮执行记录：帮助浮窗去 Qt（窗口描述宿主补工具窗能力）

第三十一轮把「一个窗口长什么样」抽成了两级描述，但只验证了**模态对话框**那一种形状。
本轮补上浮窗系（淡入淡出、`LayerManager` 层级、滚动正文、描边外壳），并用帮助浮窗验证它。
顺便把设置页 / 论坛 / 帮助共用的平滑滚动容器从产品模块下沉到后端控件宿主。

**描述层新增的窗口能力（`lib/core/render/visuals/`）**

- `WindowSpec` 新增：`kind`（`WINDOW_TOOL` 走 `Qt.Tool` 而不是 `Qt.Dialog`）、`layer`
  （顶层层级名，宿主据此注册 `LayerManager`）、`fixed_size`、`fade`（`windowOpacity` 淡入淡出）、
  `hide_semantics`（只收起窗口、不结束生命周期的语义 id）、`border_frame` / `border_width`
  （`paintEvent` 描边外壳）。
- 词汇节点补齐浮窗要件：`LabelSpec.plain_text`、`ButtonSpec.tool_button` /
  `fixed_size` / `accessible_name` / `valign`、`LayoutSpec.frame` 之外的 `fixed_width` /
  `fixed_height` / `align` / `valign` / `scroll` / `scroll_object_name` /
  `scroll_host_object_name` / `scroll_margin`。
- `window_specs.py` 新增 `help_window_spec()`：窗眉（accent 竖条 + 标题 + 来源）、可滚动正文、
  关闭按钮的对象名与结构全部由描述给出；空正文走调用方传入的兜底文案。

**后端宿主（档位 D，`backends/qt/widgets/spec_host.py`）**

- 工具窗：`kind == WINDOW_TOOL` 时用 `QtSpecWidget` + `Qt.Tool`（不进任务栏、不抢焦点），
  `windowOpacity` 动画时长取共享的 `lib/core/services/ui_presentation.ui_fade_duration_ms()`。
- 层级：`show_window()` 首次显示时按 `spec.layer` 注册 `LayerManager`，`cleanup()` /
  原生关闭时注销；`hide_dialog()` 收起但不销毁，`close` 一类的 `hide_semantics` 因此不会
  触发 `accept()`（工具窗没有「决定」这一说）。
- 滚动：`LayoutSpec.scroll` 让容器孩子住进新下沉的
  `backends/qt/widgets/smooth_scroll.py`（`SmoothScrollArea`，原
  `lib/script/ui/workbench_settings_layout.py` 的私有类），设置页 / 论坛 / 帮助因此共用同一份
  滚动手感；QSS 选择器仍是 `QScrollArea#HelpScroll, QWidget#HelpScrollHost`。
- 描边外壳：`border_frame` 让承载窗口的 `paintEvent` 先画「外描边 + 内容底色」两层矩形，
  与公告 / 更新浮窗的既有画法一致；颜色在**每次重绘时**取 token，主题切换自动跟随。
- 另一个既有偏差：对话框里按钮从不带父布局对齐，导致「贴顶的关闭按钮」变成垂直居中
  （窗眉里偏移半行高）。`ButtonSpec.valign` 与 `_build_widget` 的对齐回填修掉了它。

**产品窗口（`lib/script/ui/help_window.py`，418 → 364 行）**

- 不再是 `QWidget` 子类、不再 import 任何 `PyQt5` 窗口/控件符号：只订阅事件、收集
  「标题 + 正文」、把 `close` 语义翻译成 `hide_dialog()`。为兼容既有调用方与测试，保留
  `_header_label` / `_body` / `_scroll` / `_close_button` 属性面与 `show_help()` /
  `wants_visible()` / `hide_dialog()` / `cleanup()` / `widget()`。
- `HelpWindowController` 仍是 `QObject`（`_dispatch_requested` 用 `Qt.QueuedConnection`），
  与并列的 `office_approval_controller.py` 同一理由，因此 `help_window.py` 仍在
  `frozen_ui_qt_importers` 里并写明原因；它同时已从 `MIGRATED_LEAF_CONTROLS` 出列
  （不再自己夹取屏幕，落位交给描述宿主）。

**验证（2026-10-06）**

- 迁移 oracle：迁移后的壳层与收敛前的 `HEAD` 版本**逐像素相等**——两种主题各一张
  440×380 全图比对，差异像素 0（收敛过程中先测出 112 像素差异，定位到关闭按钮对齐后归零）。
- `tests/test_window_spec.py` 17 → **25** 条断言：新增工具窗标志与固定尺寸、纯文本正文与
  滚动视口、空正文兜底、关闭只收起不销毁、`DIALOG` 层级注册与 `cleanup()` 注销、
  壳层几何关系（窗眉固定高 / 关闭贴顶 / accent 居中 / 正文住滚动视口）。
- 全量 `unittest` 2197 → **2204** 通过 / 10 跳过；`tests/dx` 122 通过 / 7 跳过；
  `ruff check .` 归零；`compileall` 通过；`git diff --check` 干净。

**本轮没动的部分**

- `update_dialog` / `announcement_dialog` / `speaker_search_dialog` / `playlist_panel` /
  `voice_package_installer` / `qr_dialog_base` / `cmd_window` / `command_dialog` 仍是 Qt
  页面与控件；浮窗系的宿主能力已就位，可按同一模式逐个搬走（`update_dialog` /
  `announcement_dialog` 与帮助浮窗同形，是最直接的下一批）。
- `world_objects/*` 与 `game_runtime.py` 仍未动。


## 33. 第三十三轮执行记录：公告 / 更新浮窗去 Qt（控制器不再被迫继承 QObject）

第三十一、三十二轮把「窗口描述 + 后端宿主」补齐到模态对话框与工具浮窗。本轮收掉两个
同形的产品浮窗（公告、更新），并解决它们共同的、此前没被正视的问题：**为什么后台控制器
要继承 `QObject`**。

**问题：控制器为了"把回调挪回 UI 线程"而被迫 import PyQt5**

公告 / 论坛 / 帮助 / 办公模式页的控制器都在计算线程上做后台工作，回调必须回到 UI 线程执行。
早先的做法是每个控制器在自己的 `QObject` 上声明一个 `pyqtSignal(object)`，连
`Qt.QueuedConnection`——**只为这一次线程切换**，控制器就背上了 `QObject` 基类与 `PyQt5` 依赖。
这属于"把后端事实藏进业务控制器"，正是档位规则要挡住的。

**新增档位 D 宿主：`backends/qt/widgets/ui_dispatch.py` 的 `UiDispatcher`**

把这条 Qt 事实收成一处，语义是「保证回调在 UI 线程执行」：从其它线程调用走 `QueuedConnection`
投递，在自身线程调用就地执行（等价直接连接，避免在无 `exec_()` 的单元测试里把回调排进永不派发
的队列）。另有 `flush()`（同步执行尚未被事件循环取走的跨线程回调）与 `clear()`（清理时丢弃）。
`render_bridge.create_ui_dispatcher()` 是控件侧唯一落点。控制器只持有它的返回值，不再声明信号。

**描述层扩容（`lib/core/render/visuals/`）**

- `window_spec.py` 新增 `RichTextSpec`（`QTextBrowser` 只读 HTML：`document_stylesheet` /
  `font_size` / `document_margin` / `open_external_links`）与 `ProgressBarSpec`（`QProgressBar`）；
  并为既有词汇补齐 `stretch`（在父布局里占的伸缩权重）、`min_height`（最小高度档）、
  `default_font`（用后端 UI 字体渲染按钮）、`collapse_when_empty`（子控件全隐藏时连父布局间距
  一起收起）、`border_mid` / `border_fill`（描边外壳的中间内衬色与内容底色 token）。
- `window_specs.py` 新增 `announcement_window_spec()` 与 `update_window_spec()`。

**后端宿主（档位 D，`backends/qt/widgets/spec_host.py`）**

按新词汇装配真实控件树：富文本视图、进度条、嵌套布局（`_build_nested_layout`）、按钮行的
"全空即收起"、描边外壳的两种画法（默认两层 / `border_mid` 内衬线的三层）。屏幕归属与绘制
实现仍由 `render_bridge` 注入，宿主不静态引用档位 A。

**`render_bridge` 新增转发（档位 D 的控件侧唯一落点）**

`create_floating_window_base()` / `create_window_button()` / `create_ui_dispatcher()` /
`floating_window_classes()` / `window_button_icons()`。`lib/script/ui` 的浮窗族要继承的
工具包基类、窗眉按钮工厂、UI 线程调度宿主都从这里取，控件与门面不再静态 import 档位 D。

**产品窗口（出列冻结清单）**

- `announcement_dialog.py`（401 行改动）：`DesktopPetAnnouncementDialog` 不再是
  `WorkbenchFloatingWindow` 子类、不再 import `PyQt5`；`AnnouncementController` 不再继承
  `QObject`，`pyqtSignal` 用后端中立替身 `_AnnouncementSignal`（只保留 `connect` / `emit` /
  `disconnect`）。
- `update_dialog.py`（343 行改动）：同形改造，`_UpdateSignal` 替身。
- `help_window.py`（18 行）：配合上述转发调整。
- `workbench_floating.py`（40 行）：改为纯再导出垫片，基类与窗眉按钮工厂经 `render_bridge`
  转发（不再直接 import `backends/qt`）。

`frozen_ui_qt_importers`：46 → **42**（移出上述四个文件，无新增）。

**验证（2026-10-08 收尾）**

- `tests/test_workbench_chrome.py` / `test_workbench_floating_windows.py` /
  `test_render_layout_algorithms.py` 同步改为断言"描述 + 宿主"形态（`_spec.stylesheet` /
  `border_frame` / `layer`、`hide_dialog()`、`render_bridge` 转发面），不再断言
  `isinstance(..., WorkbenchFloatingWindow)`。
- 收尾时清掉两处收敛遗留的死导入：`spec_host.py` 的 `QEvent` / `QObject`、
  `announcement_dialog.py` 的 `get_layer_manager` / `WindowLayer`（层级注册已移入描述宿主）。
- `py -3 -m ruff check .` 归零；`compileall` 通过；`git diff --check` 干净。
  定向用例（chrome / floating / layout / announcement / update / help / window_spec /
  边界）全绿。

**本轮没动的部分**

- `forum_window` / `playlist_panel` / `speaker_search_dialog` / `voice_package_installer` /
  `qr_dialog_base` / `cmd_window` / `command_dialog` 仍是 Qt 页面与控件。
- `world_objects/*` 与 `game_runtime.py` 仍未动。


## 34. 第 0 轮方案变更：瘦身先行，再收敛 Qt（滚动清单次序修订）

更新时间：2026-10-07

本节是一次**方案修订**，不改变任何既有边界规则，也不描述已完成的迁移。它修订第 13 节末尾
「滚动清单（下一个控件）」的推进次序：**先用模块拆分给大文件瘦身，再让拆分后的模块逐个收敛
Qt**。第 13 节开头的三步模式（状态搬进 `visuals/` → 控件删 `QWidget`/`PyQt5` → 出列冻结清单）
仍然是每一次收敛的动作；本节改的是**一轮里对哪个文件、按什么粒度做这件事**。

### 34.1 为什么要改

滚动清单原来的推进方式默认「一个文件即一个控件」。当文件就是控件本身时，迁移可以整文件完成，
像素 oracle 也只面对一个改动。但 `frozen_ui_qt_importers` 里剩下的文件已经不平整：

- `lib/script/ui/ai_settings_panel.py` 一个文件承载了整块设置面板：顶部 60+ 个模块级 import，
  6 个类、225 个 `def`、14 处外部引用点。它不是"一个控件"，而是若干页面 + 纯逻辑 + 硬件探测 +
  贡献名单解析被塞进同一个模块。
- `lib/script/ui/forum_board.py`（2236 行）混着图像视图族、帖子/回复行与主论坛页；
  `playlist_panel.py`（966 行）、`forum_window.py`（976 行）同形。

直接对这类文件做 Qt 收敛，会让一次改动同时承担**拆分**与**去 Qt**两件事：一旦像素 oracle 报出
差异，无法判断来自结构调整还是来自去 Qt；反过来，拆分本身也会重新划分"谁碰 Qt"，冻结清单会在
同一文件上被连续两轮触碰，`stale` 与"新增耦合"两条断言同时告警。**把两件事合并成一轮，等于把
两种失败模式叠在一起。**

### 34.2 修订后的次序（三类 + 四批）

按"文件里有哪些东西"先分类，再决定动作：

- **A 类：纯能力抽离，零风险，最先做。** 目标是"不碰 Qt、不碰 UI 装配、可被多文件共用"的逻辑，
  抽到 `lib/core/` 或 `lib/script/workbench/`。`ai_settings_panel.py` 里的现成料：贡献名单解析
  （`_parse_contribution_records` 一族）、硬件水印/显存探测（`_MEMORYSTATUSEX`、
  `_query_hardware_watermark_lines`、`_gpu_pick_score`）、配置值格式化与校验、`_section_help_text`
  文案表。它们是纯函数，抽出后主文件立即缩水，且**不改变冻结清单**——验证只需跑现有测试。
  落点建议 `lib/script/ui/ai_settings_text.py`、`lib/script/ui/ai_settings_hardware.py`（无 Qt），
  更彻底时进 `lib/core/services/`。
- **B 类：需跨文件共享的 Qt 宿主，先下沉到 `backends/qt/widgets/` 再拆。** 这是解决"拆分后两个
  新文件都想用同一个私有控件"的前置。合法落点是档位 D；本轮之前的 `UiDispatcher`、`spec_host`、
  `smooth_scroll`、`message_box_host` 已是同型先例。对 `forum_board.py`，三个图像视图
  （`ForumImageView`/`ForumImageThumb`/`ForumDetailImage`）应作为一个共享族落到
  `backends/qt/widgets/forum_images.py`；`ForumPostRow`/`ForumReplyRow` 同族；`ui/` 只留
  `ForumBoardPage`。
- **C 类：产品页本体，做「视图 + 控制器 + 描述」切分，并与 Qt 收敛在同一轮交付。** 这是主战场。
  `ai_settings_panel.py` 切分为 `AISettingsPanel`（视图）+ 按 tab 的 page/section 模块 + 无 Qt 的
  设置控制器（状态、保存、校验、默认值合并）；`playlist_panel.py` 切分为队列按钮族（已在描述宿主
  路线上）+ `PlaylistPanel` 本体 + 无 Qt 队列模型。切完后每个 page 通常能独立去 Qt，一页一轮，
  `frozen_ui_qt_importers` 稳步下降且每轮都可提交。

据此，滚动清单的推进改成四批：

1. **批次 0（规则前置，不是迁移）：** 冻结清单与耦合清单的登记机制要能容纳"一个文件拆成多个"。
   在 `tests/test_qt_dependency_boundaries.py` 与 `tests/test_code_structure_boundaries.py` 中，
   拆分出的新文件必须能显式登记——否则拆分必然触发"新增耦合/新增 Qt 面"失败。子包路径同样要登记，
   不能只让顶层 `lib/script/ui/*.py` 有登记位。
2. **批次 1：A 类抽离**（`ai_settings_panel` 的纯函数族、`forum_board` 的纯辅助）。
3. **批次 2：B 类下沉**（`forum_board` 图像族、`ForumPostRow`/`ForumReplyRow`），每下沉一族跑一次
   `tests/test_forum_board_ui.py` 与视觉 parity。
4. **批次 3：C 类按 tab/页逐块拆 + 同步去 Qt**，小批量多轮提交，延续既有「控件层收敛第 N 轮」节奏。

### 34.3 每轮的验收门槛

拆分轮与收敛轮共用同一套 oracle，但比对对象不同：

- **纯移动轮（A/B 类）：** 新增文件必须逐行等价，用 `git diff -M --find-copies` 确认是 copy/move；
  跑对应模块测试 + `ruff check` + `compileall`。本类预期**逐字节/逐像素零差异**。
- **涉及像素的轮（C 类）：** 沿用第 32 节确立的 oracle——迁移后壳层与收敛前 `HEAD` 逐像素相等，
  明暗两主题各一张全图比对，差异像素 0；结果写入 `CHANGELOG.md`。
- **每轮结束**跑 `py -3 -m unittest tests.test_qt_dependency_boundaries tests.test_code_structure_boundaries -q`，
  确保冻结清单与耦合清单同步更新。

### 34.4 风险

- 拆分会让"新文件里 import 了产品包"立刻触发 `test_ui_to_product_coupling_is_frozen` 失败。
  这是设计使然，批次 0 必须先备好登记机制，否则每拆一次都要临时改测试。
- `ai_settings_panel.py` 顶着 14 处引用点与 60+ 顶层 import，不得一次切完；按 tab 切、每个 tab 一轮。
  该文件在批次 3 第 1~7 轮按此切到只剩面板外壳（5439 → 858 行），每轮"纯移动 + 零差异"均有
  第 48~54 节的 oracle 记录；外壳（`__init__` / 自绘 / 动画 / 生命周期 / 悬浮标签页）尚未切分，
  因为它的每个方法都要读 `self` 上的面板状态，先切需要先引入独立的窗口基类或状态包。
- 先拆后收敛会让收敛轮的像素基线变成拆分后的新文件。因此 A/B 类必须做到"纯移动、零差异"，
  保证收敛轮的差异只有一个来源。


## 35. 批次 0 / 批次 1 执行记录（规则前置与设置面板纯逻辑抽离）

更新时间：2026-10-08

本节是第 34 节四批方案的执行记录。批次 0 是规则前置，批次 1 是第一次 A 类抽离。

### 35.1 批次 0：拆分登记机制（先验证，再固化）

第 34 节要求"拆出的新文件必须能显式登记"。实测结论是**机制本就成立，缺的是说明与守卫**：

- 两份清单都按 `rglob("*.py")` 扫描 `lib/script/ui/`，嵌套子包（`lib/script/ui/foo/bar.py`）
  会被扫到，不会漏；
- `frozen_ui_qt_importers <= {path for path in qt_imports if path.startswith("lib/script/ui/")}`
  只要求前缀匹配，嵌套子包路径本就合法；
- 用临时探针实测：拆出的同级/嵌套文件若仍 `import PyQt5`，`unexpected` 断言会列出它；
  若 import 产品包，`test_ui_to_product_coupling_is_frozen` 的 `found - expected` 会列出它。
  也就是说**拆分不会被迫放宽断言**——它只是要求你把新文件登记进去。

据此做的改动只有两处，都是让规则可读、可回归：

- `tests/test_qt_dependency_boundaries.py` 与 `tests/test_code_structure_boundaries.py`
  各补一段注释，写明拆分场景下该登记到哪份清单、不 import Qt 的拆出文件不要登记；
- 新增 `tests/test_code_structure_boundaries.py::test_split_ui_modules_are_registered_not_silently_allowed`：
  钉住"扫描面覆盖子包 + 前缀判定接受子包 + 两个清单的条目都落在 `lib/script/ui/` 前缀下"。

### 35.2 批次 1：贡献名单解析抽到后端中立模块

第一个 A 类目标选 `ai_settings_panel.py` 的贡献名单一族，因为它是纯函数、无 Qt、无 UI 装配，
且被 `test_offline_distribution.py` 间接覆盖，抽离后立刻可验证。

- 新增 `lib/script/ui/ai_settings_contributions.py`（229 行，不 import `PyQt5`）：
  `project_root()` / `contribution_list_path()` / `sponsor_author_image_path()` /
  `read_text_with_fallback()` / `extract_first_url()` / `normalize_contribution_name()` /
  `guess_contribution_fallback_name()` / `split_contribution_header()` /
  `parse_contribution_records()` / `load_contribution_records()`，以及
  `_CONTRIBUTION_IGNORED_TITLE_PARTS` / `_CONTRIBUTION_HIDDEN_ROLES` / `_MANUAL_CONTRIBUTION_RECORDS`。
- `contribution_list_path()` / `sponsor_author_image_path()` / `load_contribution_records()`
  接受可选 `root`。面板的 `_contribution_list_path()` / `_sponsor_author_image_path()` /
  `_load_contribution_records()` 变成三个委托，`root` 传本文件的 `_project_root()`；
  这样既有测试的 `mock.patch.object(panel, "_project_root", ...)` 覆盖点不变。
- `_MANUAL_CONTRIBUTION_RECORDS` 不再从面板重新导出（面板内无引用）。它现在的规范位置是
  `ai_settings_contributions._MANUAL_CONTRIBUTION_RECORDS`；`test_offline_distribution.py`
  的引用已改到规范模块。
- `_read_text_with_fallback` / `_extract_first_url` / `_normalize_contribution_name` /
  `_guess_contribution_fallback_name` / `_split_contribution_header` /
  `_parse_contribution_records` 以及两个常量从面板删除，面板顶部据此删掉已无用的 `import re`。

`ai_settings_panel.py`：5439 → 5261 行（净 -178）。**冻结清单不变**——本次拆出的
`ai_settings_contributions.py` 不含 Qt，不登记；面板仍在 `frozen_ui_qt_importers` 里。

### 35.3 验证（2026-10-08）

- 行为等价：用 `git show HEAD:lib/script/ui/ai_settings_panel.py` 的旧实现与工作树的
  新模块，对真实事实源 `doc/贡献名单和主播的狗盆/开发贡献.txt` 逐条比对——
  `parse_contribution_records` 15 条全等，`load_contribution_records` 14 条全等。
- 新增 `tests/test_ai_settings_contributions.py` 12 条断言：角色行/URL 条目/兜底名字/
  屏蔽词/忽略标题片段/归一化/首链接提取、真实文件的手工条目与隐藏角色、`root` 覆盖，
  以及"模块不 import Qt"与"PyQt5 被阻断的进程里可导入并解析"。
- `py -3 -m unittest tests.test_ai_settings_contributions`：12 通过。
  `tests.test_offline_distribution`：26 通过。边界测试：
  `tests.test_qt_dependency_boundaries tests.test_code_structure_boundaries` 52 通过。
- 全量 `unittest discover -s tests`：2226 用例、1 失败（`test_dsh_office_sidecar` 的
  DSH 外部任务超时，单独运行 2/2 通过，属环境性失败）、10 跳过。
  `tests/dx`：122 通过 / 7 跳过。`compileall` 通过；`git diff --check` 干净。
- 新增/修改文件 `ruff check` 归零。（工作树里既有未提交的 `spec_host.py` /
  `announcement_dialog.py` 存在 4 处 F401，属任务开始前已有的改动，未触碰。）

### 35.4 下一批

- 批次 1 剩余：`forum_board.py` 的纯辅助函数；`ai_settings_panel.py` 的配置值格式化/校验、
  `_section_help_text` 文案表。
- 批次 2（B 类）：`forum_board.py` 的图像视图族与 `ForumPostRow`/`ForumReplyRow` 下沉
  `backends/qt/widgets/`。
- 一个已探明的约束：`ai_settings_panel.py` 的硬件/水印一族与 `lib/script/app/startup_probe.py`
  是**同算法、不同 dict 键**的两份实现（前者吃 PowerShell `Name`/`AdapterRAM`，后者吃归一化
  后的 `name`/`adapter_ram`）。要合并必须先在共享层统一键名，属行为改动，不能按"纯移动"处理。


## 36. 批次 1 续：设置面板文案/名称表抽离（并确认硬件一族是死代码）

更新时间：2026-10-08

第 35 节批次 1 抽走了贡献名单解析。本轮继续抽 `ai_settings_panel.py` 的第二块纯逻辑：
配置分组的帮助文案与配置字典/键的中文名。

新增 `lib/script/ui/ai_settings_labels.py`（419 行，不 import `PyQt5`）：

- `SECTION_HELP_TEXTS`（24 组 `(分类 id, 字典名) -> 帮助整段`）、`DICT_FRIENDLY_NAME`、
  `KEY_FRIENDLY_NAME`（212 行键名表）；
- `section_help_text()` / `friendly_section_name()` / `friendly_field_section_name()` /
  `friendly_key_name()` / `animation_folder_display_name()`。

这批块**自包含**：AST 检查它们引用的自由全局为零，外部（tests / scripts / lib）对这些
私有名的引用也为零，面板内只有面板自己在用。因此是纯移动，`ai_settings_panel.py`
5261 → 4862 行（净 -399）。`frozen_ui_qt_importers` 不变（新模块不含 Qt）。

等价性：用 `git show HEAD:lib/script/ui/ai_settings_panel.py` 的旧表与旧函数，对
`SECTION_HELP_TEXTS` 的全部 24 键、字典名表全部键、键名表全部 `(dict, key)` 组合、
匿名目录名的多组输入逐项比对——表相等、函数输出零差异。

新增 `tests/test_ai_settings_labels.py` 11 条断言：三张表的查询与兜底、`CLOUD_MUSIC`
`launch_wuwa_path` 特例、`_anima` 后缀剥离、以及"不 import Qt / PyQt5 被阻断时可导入"。

### 36.1 顺带确认：面板里的硬件/水印一族是死代码（未删除）

批次 1 当初把"硬件探测"列为抽离目标之一。实测发现它们已经不是活代码：

- `ai_settings_panel.py` 的硬件一族（`_query_hardware_watermark_lines`、`_gpu_pick_score`、
  `_is_virtual_or_software_gpu`、`_format_gb_text`、`_get_total_memory_bytes`、
  `_MEMORYSTATUSEX`、`_get_powershell_executable`、`_run_capture_text` /
  `_decode_process_output`、`_to_int`）与 `lib/script/app/startup_probe.py` 是**同算法、
  不同 dict 键**的两份实现：前者吃 PowerShell 原始 `Name` / `AdapterRAM`，后者吃归一化后的
  `name` / `adapter_ram`。
- 面板现在走 `startup_probe.load_saved_watermark_payload()`（第 84 行导入），
  `_query_hardware_watermark_lines` 在全仓**没有任何调用点**（含字符串引用）。也就是说
  这一族只剩 `startup_probe` 那条链路在用，面板里的副本是历史遗留。
- 因此**不抽离**（那只会把死代码搬个地方）：正确处置是删除，属行为无关的清理，留待单独一轮
  做，避免和"纯移动"混在一个提交里，也避免误删 `_gpu_mode_from_num_gpu` / `_num_gpu_from_mode`
  这两个仍有调用的函数（它们与硬件探测不是同一族，必须保留）。

### 36.2 验证（2026-10-08）

- 全量 `unittest discover -s tests`：2249 用例、10 跳过，全通过。
- 新增/修改文件 `ruff check` 归零，全仓 `ruff check .` 归零；`compileall` 通过；
  `git diff --check` 干净。

### 36.3 下一批

- 批次 1 剩余纯逻辑：`_SECTION_HELP_TEXTS` 之外的配置值格式化/校验一族
  （`_is_supported_config_value` / `_format_config_editor_value` / `_range_pair_signature` /
  `_choice_label_for_value` / `_hardcoded_general_default` 等，仍需先做自由全局检查）。
- 待单独一轮：删除面板里已确认死掉的硬件/水印一族。

## 37. 批次 1 续：设置面板配置 schema 与取值格式化抽离

更新时间：2026-10-08

第 36 节抽走了文案/名称表。本轮抽第三块、也是最大的一块纯逻辑：**通用配置 schema 与取值
格式化**——531 行、17 个符号，是 `ai_settings_panel.py` 里最大的一块无 Qt 代码。

新增 `lib/script/ui/ai_settings_config_schema.py`（585 行，不 import `PyQt5`）：

- schema 表：`CATEGORY_KEY_ALLOWLIST`（各分类允许哪些字典的哪些键）、`GENERAL_BOOL_KEYS`、
  `GENERAL_NUMERIC_RULES`、`GENERAL_TUPLE_INT_RULES`、`GENERAL_RANGE_RELATIONS`、
  `VOLUME_SLIDER_FIELDS`、`GENERAL_DECIMAL_SLIDER_SPECS`、`GENERAL_CHOICE_FIELD_OPTIONS`、
  `GENERAL_CONFIG_DEFAULTS`、`GENERAL_MIXED_SECTION_FIELDS`；
- 取值函数：`category_section_entries` / `choice_label_for_value` / `hardcoded_general_default` /
  `range_pair_signature` / `friendly_range_name` / `is_supported_config_value` /
  `format_config_editor_value`。

外部依赖只有后端中立来源：`copy`、`config.config_layer.LAYER_VALUES`、
`lib.core.render.router.get_backend_descriptors`、`SEanima.clip` 的两个默认动画目录常量，
以及第 36 节的 `ai_settings_labels.friendly_key_name`。面板与新模块读的是同一份数据。

`ai_settings_panel.py`：4862 → 4318 行（-544，含 13 行新导入）。面板里随之删掉的死导入：
`LAYER_VALUES`、`get_backend_descriptors`、`SEanima.clip` 的两个默认动画目录常量。
`frozen_ui_qt_importers` 不变。

等价性：用 `git show HEAD` 的旧实现与新模块逐符号比对（表 + 函数共 17 项，去掉前导下划线
后源码归一）——**全部相等**。

新增 `tests/test_ai_settings_config_schema.py` 11 条断言：表结构与不变量（allowlist 的键集合
类型、数值规则区间下界不高于上界、区间关系能解析出 min/max 签名）、取值函数行为
（`format_config_editor_value` 对非字符串走 `repr`、`is_supported_config_value` 接受标量与
扁平序列且拒绝 `None`/`dict`/嵌套）、以及"不 import Qt / PyQt5 被阻断时可导入"。

### 37.1 验证（2026-10-08）

- 全量 `unittest discover -s tests`：2260 用例、10 跳过；1 失败为 `test_dsh_office_sidecar`
  的 DSH 外部任务超时（单独运行 2/2 通过，属环境性，与本次改动无关）。
- 全仓 `ruff check .` 归零；`compileall` 通过；`git diff --check` 干净。

### 37.2 下一批

- 批次 1 的 `ai_settings_panel.py` 纯逻辑已基本抽完（贡献名单、文案/名称表、配置 schema）。
  剩余仍是 `QWidget` 页面的本体（`AISettingsPanel` 4318 行），属批次 3。
- 待单独一轮：删除第 36.1 节确认的死代码（面板里的硬件/水印一族）。

## 38. 批次 1 收尾：删除设置面板里已死的硬件/水印一族

更新时间：2026-10-08

第 36.1 节确认 `ai_settings_panel.py` 里的硬件/水印一族是死代码。本轮把它删掉。

删除的十个符号（`ai_settings_panel.py` 237–392 行的连续块）：`_decode_process_output` /
`_run_capture_text` / `_get_powershell_executable` / `_to_int` / `_MEMORYSTATUSEX` /
`_get_total_memory_bytes` / `_is_virtual_or_software_gpu` / `_gpu_pick_score` /
`_format_gb_text` / `_query_hardware_watermark_lines`，以及随之不再使用的 `import ctypes` /
`import json`。

删除依据（可复核）：

- `_query_hardware_watermark_lines` 在**全仓**（含 tests / scripts / 文档字符串）没有任何
  调用点；该族其余函数只被彼此调用，`AISettingsPanel` 不引用其中任何一个。
- 面板的硬件水印走的是 `lib/script/app/startup_probe.load_saved_watermark_payload()`
  （模块顶部第 84 行导入），与这族重复；`startup_probe` 那条链路仍在用，未受影响。
- **必须保留**的邻近函数：`_gpu_mode_from_num_gpu` / `_num_gpu_from_mode` 与三个 `_GPU_MODE_*`
  常量，它们与硬件探测不是一族、且有真实调用（下拉框数据、保存/加载时的 num_gpu 换算）。

效果：`ai_settings_panel.py` 4318 → 4159 行。`frozen_ui_qt_importers` 不变，行为不变（删的
是无人调用的代码）。

### 38.1 验证（2026-10-08）

- 全量 `unittest discover -s tests`：2260 用例、10 跳过，**全通过**。
- 全仓 `ruff check .` 归零；`compileall` 通过；`git diff --check` 干净。

### 38.2 阶段小结（批次 1 完成）

`ai_settings_panel.py` 从 5439 行降到 4159 行（-1280），分四步完成，全部是行为等价的纯移动或
删死代码，冻结清单始终不变：

- 第 35 节：贡献名单解析 → `ai_settings_contributions.py`（229 行）；
- 第 36 节：文案/名称表 → `ai_settings_labels.py`（419 行）；
- 第 37 节：配置 schema 与取值格式化 → `ai_settings_config_schema.py`（585 行）；
- 第 38 节：删除死代码硬件一族。

剩余 4159 行是 `AISettingsPanel` 本体（`QWidget` 页面），属批次 3 的"视图 + 控制器"切分与
去 Qt，不再属于批次 1。下一批按第 34 节次序转向 `forum_board.py` 的纯辅助与图像族下沉。

## 39. 批次 2：`forum_board.py` 的图片族 / 列表行下沉（档位 D 首个"控件族"宿主）

更新时间：2026-10-08

本节是第 34 节四批方案里**批次 2（B 类：需跨文件共享的 Qt 宿主先下沉）**的第一轮，
也是档位 D 第一次承载"产品控件族"而不只是页面基类 / 窗口宿主。

### 39.1 为什么这批只能下沉、不能先抽纯能力

`forum_board.py`（2236 → 1723 行）里**没有 A 类可抽**：六个顶层 helper（`_font` /
`_block_alignment` / `_like_mark` / `_apply_like_state` / `_clear_layout` / `_color_span_at`）
全都直接碰 Qt（`Qt.Align*`、`QToolButton.style()`、`iter_color_tokens` + `QLabel`）。它们不是
"恰好 import 了 Qt 的纯逻辑"，而是"控件排版策略"本身——抽到 `lib/core/` 只会把 `QToolButton`
一起搬过去。所以本文件的正确顺序是**先按控件族下沉，再谈抽离**（第 34 节 B 类）。

### 39.2 下沉边界（逐行等价，零像素差异）

新增 `lib/core/render/backends/qt/widgets/forum_images.py`（619 行），承接：

- **图片视图族**：`ForumImageView`（认 `image_id`、等字节、铺位图）→ `ForumImageThumb`
  （正方形小预览，列表行与发帖页共用）→ `ForumDetailImage`（正文里就地铺开、按栏宽等比缩放、
  `setFixedHeight()` 钉高度并挂父控件 resize 过滤器）。
- **列表两行**：`ForumPostRow`（整行可点、点赞按钮吃掉自己的点击）、`ForumReplyRow`
  （楼层 / 作者 / 回复与点赞）。
- **共用的排版辅助**：`_font` / `_block_alignment` / `_like_mark` / `_apply_like_state` /
  `_clear_layout` / `_color_span_at`，以及 `LIST_THUMB_SIZE` / `COMPOSE_THUMB_SIZE` /
  `THUMB_MEMORY_LIMIT` / `BODY_WIDTH_HINT` / `FORUM_IMAGE_MAX_PIXELS` / `EXCERPT_LENGTH`。
- `lib/script/ui/forum_board.py` 只留 `ForumBoardPage`（页面本体与网络回调），并把上述名字
  **按原语义重新导出**：`forum_board.ForumPostRow` / `ForumImageThumb` 等既有导入面（含
  `forum_account.py` 与 `tests/test_forum_board_ui.py`）一个字都不用改。

判定依据是档位 D 的既有标准（第 3 节）：持有 `QWidget.rect()` / `QPixmap`、且**绘制实现不由
本模块构造**的就是档位 D。`forum_images.py` 不 import 档位 A（`drawing/`），位图由
`CommunityService.load_thumbnail()` 取回的字节经 `set_data()` 交进来。

### 39.3 两处必须"上移"才成立的事实

下沉会撞上两条既有硬约束（`test_render_layer_never_imports_product_modules`：`render/` 不得
import `lib.script`；档位 D 不得静态引用档位 A），处理方式都是把**事实源上移**而不是加豁免：

1. **图片描边宽度**：`FORUM_IMAGE_FRAME` 原先只写在产品面的 `forum_style.py`，但
   `ForumDetailImage` 算尺寸时也要刨掉同一圈边框（算漏一步 QLabel 就会裁掉一条边）。
   权威定义移到 `lib/core/render/visuals/forum_visuals.py`（后端中立），`forum_style.py`
   按原名字重新导出供 QSS 使用；两边由同一份常量取值。
2. **UI 字体入口**：`_font()` 原先直接调 `render_bridge.ui_font`。`forum_images.py` 改为持有一个
   可注入的字体入口 `_font_factory`，默认值走**档位 B** 的字体服务
   （`registry.get_font_provider().ui_font`，未注册时退回 `runtime/font.get_ui_font`）；
   `forum_board.py` 导入时用 `forum_images.configure_font_factory(get_ui_font)` 显式注入产品面
   的同一入口，因此每个控件拿到的 `QFont` 与下沉前逐字段相同，且不依赖导入顺序。

### 39.4 验收门槛（第 34.3 节"纯移动轮"）

- **逐像素零差异**：改前（`HEAD` = `9ac6ab8`）与改后在独立工作树里渲染同一个论坛页的
  三屏（列表 / 详情 / 发帖页，各 760×620），逐点比对 **差异像素 0**（`QImage.pixel()` 全等）。
- 目视复核：未登录态的点赞心跳、正文配图按栏宽铺开、缩略图描边与标签胶囊均与改前一致。
- 目标测试：`tests/test_forum_board_ui.py`、`tests/test_forum_layout_ui.py`、
  `tests/test_forum_community_window.py`、`tests/test_forum_account_ui.py`、
  `tests/test_forum_window.py`、`tests/test_forum_style.py` 共 269 项通过；
  边界测试 `test_qt_dependency_boundaries` / `test_code_structure_boundaries` / `test_window_spec`
  同步通过（`frozen_ui_qt_importers` 不变：下沉目标是 `lib/core/render/backends/qt/widgets/`，
  不在该清单的扫描面内，`ui/` 侧只少了不少代码、没有新增 Qt 文件）。
- 全量 `unittest discover -s tests`：2260 用例、10 跳过，全通过；`ruff check .` 干净；
  `compileall` 通过；`git diff --check` 干净。

### 39.5 下一批

`forum_board.py` 剥掉控件族后剩 1723 行，主要是 `ForumBoardPage` 本体（工具栏 / 列表 / 详情 /
发帖四个 builder 与网络回调），属批次 3 的"视图 + 控制器 + 描述"切分与去 Qt。批次 2 的余下
候选按第 34 节次序继续。

## 40. 批次 2 续：`forum_texture.py` 的底纹规格抽到 `visuals/`（A 类，零像素差异）

更新时间：2026-10-08

`lib/script/ui/forum_texture.py` 696 → 508 行。抽走的是**底纹长什么样**那一半（与 Qt 无关的
纯算术），绘制那一半留在原文件：

- 新增 `lib/core/render/visuals/forum_texture_visuals.py`（222 行）：`CARD_TEXTURE_*` 全部
  候选与取值范围、`CardTexture` 规格、`texture_seed()` / `card_texture()`（内容哈希 → 规格），
  以及绘制期要用的几何助手 `tile_geometry` / `tile_starts` / `noise` / `focus_distance` /
  `cell_mix` / `stroke_width` / `gradient_weight` / `gradient_alpha`。
- `forum_texture.py` 保留 `paint_card_texture()` / `_texture_layer()` / `_device_scale()` 与
  各 `_paint_*`，并按原名字重新导入（公开常量与 `CardTexture` / `card_texture` / `texture_seed`
  直接再导出，私有助手按 `_原名` 别名导入），因此既有导入面一个字都不用改。

判定依据：这些函数只做哈希、`random.Random` 抽样、整数/浮点几何与坐标噪波，不碰 `QColor` /
`QImage` / `QPainter`；`visuals/` 又是「后端中立数据与算法」的既有落点（第 3 节）。抽离后
`forum_texture.py` 仍 import `PyQt5`（绘制还在），`frozen_ui_qt_importers` 因此不变。

### 40.1 验收（第 34.3 节「纯移动轮」）

- **逐像素零差异**：同一个底纹 oracle（四张固定卡片 × 深色主题，240×180 位图）在 `HEAD`
  与改后渲染，`QImage.pixel()` 全等，差异 **0**。
- 目视复核：`hatch` / `rings` / `diamonds` / `tiles` 四族花纹的线宽、密度与相位与改前一致。
- `tests/test_forum_window.py`、`tests/test_forum_style.py` 与两条边界测试通过（114 项）。
- `ruff check .` 干净；`compileall` 通过；`git diff --check` 干净。

## 41. 批次 2 续：办公聊天 Markdown→富文本抽到 `visuals/`（A 类，零差异）

更新时间：2026-10-08

`lib/script/ui/office_chat_view.py` 342 → 284 行。抽走的是**怎么把 Markdown 变成富文本
HTML** 那一半：新增 `lib/core/render/visuals/office_chat_rich.py`（125 行），承接代码字体
名、六个正则（围栏 / 项目符号 / 编号 / 行内代码 / 粗体 / 斜体）与五个纯函数
（`_code_span` / `_fence_html` / `_inline_md` / `md_to_rich`，其中 `md_to_rich` 为公开名）。

判定依据：这五个函数只做 `re.sub` 与 `html.escape`，产物是字符串，不碰 Qt；颜色由调用方
（产品面按工作台主题取出的代码底色 / 前景色）传参，因此本模块既不 import `PyQt5`、也不
import `lib.script` 或 `config`，可直接落在 `visuals/`。`office_chat_view.py` 仍 import
`PyQt5`（控件树还在），`frozen_ui_qt_importers` 不变。

### 41.1 验收（第 34.3 节「纯移动轮」）

- **逐字符零差异**：`HEAD` 的 `_md_to_rich` 与改后的 `md_to_rich` 对同一组 10 个用例
  （空串 / 粗斜体行内代码 / 标题 / 围栏代码块 / 有序无序列表 / 多段 / 未闭合围栏 / HTML 转义）
  输出逐字符相等，0 处不一致。
- `tests/test_office_style.py`、`tests/test_office_workbench_page.py` 与两条边界测试通过（78 项）。
- `ruff check .` 干净；`compileall` 通过；`git diff --check` 干净。

## 42. 批次 2 续：办公/手动 API 解析抽到 `lib/core/services/`，并拆掉一处 core→产品包的反向依赖（A 类）

更新时间：2026-10-08

这一轮的目标文件是 `lib/script/ui/office_mode_settings.py`。它是批次 1 之后 `ui/` 里同时
「碰 Qt 最多」与「混着最多纯逻辑」的一个：办公后端下拉、独立 API 表单与三个控件助手之外，
还塞着一整套 OpenAI 兼容接口的地址补全、`/models` 端点换算、模型列表解析与一次 HTTP 探测，
以及本机 DeepSeek Harness 的读取。

### 42.1 抽走的那一半：`lib/core/services/api_endpoints.py`

新增 `lib/core/services/api_endpoints.py`（122 行），承接五个函数与一份预设表：
`MANUAL_API_PROVIDER_PRESETS`、`normalize_api_base_url`、`manual_api_models_url`、
`parse_api_models`、`fetch_api_models`。判定依据与批次 1 一致：它们只做字符串规整、
正则判定、字典解析与一次 `requests.get`，不碰 `QColor` / `QPaint` / 控件，
所以是 A 类；落点选 `lib/core/services/`，与 `music_playback.py` / `ui_presentation.py`
同层（第 3 节：后端中立的产品能力）。

`office_mode_settings.py` 按原名重新导入这五个公开名（带 `# noqa: F401` 再导出），
`ai_settings_panel.py` 现有 `from lib.script.ui.office_mode_settings import ... ` 的取用面
一个字都不用改；`re` 与 `requests` 从该文件的顶层 import 里消失。

### 42.2 顺手修掉的一处反向依赖：网络策略下沉

抽离过程中暴露出一个既有问题：`API_TIMEOUT_SECS` 原先住在
`lib/script/chat/network_policy.py`，而新落点 `lib/core/services/` 引用它就会形成
**`lib/core` → `lib/script`** 的反向依赖，直接撞上
`tests/test_code_structure_boundaries.py::test_core_does_not_import_product_modules`。
它其实只是三个常量（超时 10.0s / 重试 3 次 / 总尝试 4 次），与产品包里的任何实现无关。

处置：搬到 `lib/core/services/network_policy.py`（单一实现），
`lib/script/chat/network_policy.py` 退化成一层同名重导出垫片，`welfare_api_config.py` 与
`api_client_openai.py` 的既有导入路径继续可用。这是**纯移动 + 垫片**，没有新增耦合方向。

### 42.3 留在原地的那一层：本机 DSH 探测

`probe_local_dsh()` 必须 `from lib.script.office import local_dsh`（读启动期探测缓存），
这是产品包依赖，不能进 `lib/core`。结论是它**留在 `lib/script/ui/office_mode_settings.py`**，
并继续登记在 `test_ui_to_product_coupling_is_frozen` 的
`("lib/script/ui/office_mode_settings.py", "lib.script.office")` 条目上——清单因此从两项
（`lib.script.chat.network_policy`、`lib.script.office`）减到一项，而不是清零。

同时修掉一处**既有脆弱点**：构造函数原写作 `probe or probe_local_dsh`，把函数对象在导入时
就捕获进实例，测试与宿主事后替换模块属性对已建实例无效。`tests/test_office_mode_page.py`
原有的 `_page_with_probe` 之所以看着能过，正是因为当时唯一被 patch 的 `office_mode_settings.probe_local_dsh`
既是代理又是唯一实现；真正的实现读的是全局缓存，探针形同虚设。现改为
`probe or (lambda: probe_local_dsh())`，走**模块全局**取值；测试改为 patch 真正的所有者，
本机 DSH 探测分支第一次被真正驱动，构造期读到的就是测试给定的状态。

### 42.4 验收（第 34.3 节「纯移动轮」）

- **逐行等价**：`git show HEAD:lib/script/ui/office_mode_settings.py` 里
  `MANUAL_API_PROVIDER_PRESETS` 与四个解析函数，AST 抽出来与 `api_endpoints.py` 中的同名定义
  逐行相等，0 处不一致（`probe_local_dsh` 亦为逐行等价恢复）。
- **行为等价**：15 组地址（空串 / `None` / 前后空白 / 裸域名 / 已带协议 / 带尾斜杠 /
  `localhost:11434/v1` / `127.0.0.1:8000` / `0.0.0.0:1234` / `[::1]:9000` / `//host/v1` /
  完整 `chat/completions` 端点 / 大小写混写）与 7 组 `/models` 载荷（正常 / 空 / 非 dict /
  `data` 非列表 / 缺 id / `None` / 列表）在两份实现上逐字符一致，预设表 `repr` 相等。
- 全量 `2260` 项测试通过（`10` skipped）；`ruff check .` 干净；`compileall` 通过；
  `git diff --check` 干净。
- 冻结清单 `frozen_ui_qt_importers` 仍为 42：`office_mode_settings.py` 的控件树还在，
  本轮只是让它变薄。

## 43. 批次 2 续：设置面板「通用配置」取值校验抽到 `ui/ai_settings_validation.py`（A 类，含一处死链清理）

更新时间：2026-10-08

`lib/script/ui/ai_settings_panel.py` 4158 → 3991 行。本轮抽走的是**取值校验规则**那一段：
六个方法（`_validate_general_config_values` 整段入口、`_validate_general_config_value` 逐键、
`_validate_general_config_relations` 跨键，以及 `_get_choice_field_options` /
`_raise_config_value_error` / `_validate_general_numeric` 三个助手）搬进
`lib/script/ui/ai_settings_validation.py`（252 行，无 Qt）。

### 43.1 抽离时唯一需要拆解的一处耦合

规则本身不碰 Qt，也不碰 `config`：它们读的是批次 1 已抽出的
`ai_settings_config_schema` 四张表（`GENERAL_BOOL_KEYS` / `GENERAL_TUPLE_INT_RULES` /
`GENERAL_NUMERIC_RULES` / `GENERAL_RANGE_RELATIONS`）与 `ai_settings_labels.friendly_key_name`。
唯二例外是：

- `_get_choice_field_options` 要看动画目录的可用值。这份"有哪些目录"由面板以
  `folder_options=` 注入（`AISettingsPanel._folder_options` 仍留在面板，读
  `lib.script.SEanima.clip`），新模块因此不 import `SEanima`；
- `cache_dir` / `local_music_dir` / `launch_wuwa_path` 三个路径字段要判断"是否绝对路径"，
  需要工程根目录。原先调面板模块级的 `_project_root()`，现在由面板按调用传入
  `project_root=`。

新模块抛 `ConfigValueError`（`ValueError` 子类）。面板的历史契约是抛 `ValueError`，
且调用点与测试都按 `ValueError` 捕获；面板的六个壳方法因此把 `ConfigValueError` 直接
透出——它是 `ValueError` 的子类，`except ValueError` 与消息文本都不变。

### 43.2 顺带清掉的一条死链（N 卡能力探测）

抽离时发现 `_nvidia_gpu_present` 恒为 `False`：写它的
`_refresh_nvidia_acceleration_capability_async()`（原第 2191 行）**已经没有任何调用点**——
`_build_workbench_page()` 与 `show_centered()` 里的调用在某次重构中消失了，只剩
`from lib.core.nvidia_gpu import has_nvidia_gpu` 这个导入、三个初始化属性与一个
`generation` 计数器。后果是 `_update_gsv_settings_visibility()` 里
`cuda_checkbox.setVisible(voice_available and nvidia_present)` 的第二项恒假，N 卡开关
实际上从不显示；采集时 `gsv_nvidia_cuda_acceleration` 也恒为 `False`。

处置：删掉无人调用的探测方法、`has_nvidia_gpu` 导入与三个属性，`visible` 判断与采集
只保留语音包可用性一项。**对外表现逐项不变**（死链下两项本就等价），并且新增
`test_nvidia_switch_visibility_follows_the_voice_package_only` 把"开关只由语音包决定"
钉成断言，避免以后有人以为这里还有 N 卡探测。

### 43.3 验收（第 34.3 节「纯移动轮」）

- **行为等价**：把 `HEAD` 的六个方法原样摘出成独立类，与抽离后的模块对同一批输入
  对比异常类型与消息——30 组单键取值（bool 开关 / 整数与浮点范围 / `NaN` / `inf` /
  整数元组正逆序与上下界 / 三个路径字段 / 快捷键 / 动态目录选项 / 未知键）与 5 组
  整段入口（逐键通过、非法结构、跨键关系、合法复合）差异 **0**。
- 全量 `2259` 项测试通过（`10` skipped）；`ruff check .` 干净；`compileall` 通过；
  `git diff --check` 干净。
- 冻结清单 `frozen_ui_qt_importers` 仍为 42：新文件不 import `PyQt5`；
  `tests/test_code_structure_boundaries.py` 的 `ui -> 产品包` 清单不变
  （新模块不 import `chat` / `office` / `music` / `gsvmove`）。

## 44. 批次 2 续：设置面板字段说明文本抽到 `ui/ai_settings_descriptions.py`（A 类）

更新时间：2026-10-09

`lib/script/ui/ai_settings_panel.py` 3991 → 3942 行。本轮抽走的是**配置字段的说明文本怎么拼**：
四个纯函数搬进 `lib/script/ui/ai_settings_descriptions.py`（106 行，无 Qt）。

- `build_config_single_description(dict_name, key, value, friendly_name)`：单值字段的四行说明
  （分节名 · 友好名 / 配置键 / 类型 / 默认值），含两处特例——动画倍速键把默认值改写成
  `0.5x` 并追加「推荐3.0s / 推荐默认」，`STARTUP.ui_cache_preload` 追加缓存上限提示。
- `build_config_range_description(...)`：数值范围字段的两端默认值预览。
- `description_value_type(value)`：取值 → 中文类型名（布尔 / 整数 / 小数 / 文本 / 元组 / 列表）。
- `description_preview_value(value, max_len=72)`：默认值预览的超长截断。

判定依据：四个函数只读批次 1 已抽出的 `ai_settings_config_schema`
（`choice_label_for_value` / `format_config_editor_value`）与 `ai_settings_labels`
（`friendly_field_section_name` / `friendly_section_name`），不碰 Qt、不碰控件、不读 `self`
的其余状态（原来的 `self._description_preview_value` 一类只是命名空间，没有实例语义）。
面板按原名（加前导下划线）再导出，四个调用点与 `tests/test_workbench_settings_layout.py`
的断言一个字都不用改。

### 44.1 验收（第 34.3 节「纯移动轮」）

- **行为等价**：把 `HEAD` 的四个方法原样摘出成独立类做对照，40 组输入差异 **0**——
  12 组单值说明（含两个动画倍速特例、`ui_cache_preload`、动态目录选项、未知键、
  200 字符长值）、3 组范围说明、`description_preview_value` 的 8 组取值 × 两种 `max_len`、
  `description_value_type` 的 9 组取值。
- 全量 `2259` 项测试通过（`10` skipped）；`ruff check .` 干净；`compileall` 通过；
  `git diff --check` 干净。
- 冻结清单 `frozen_ui_qt_importers` 仍为 42；`ui -> 产品包` 耦合清单不变（新模块不 import
  `chat` / `office` / `music` / `gsvmove`）。

## 45. 批次 2 续：配置编辑器取值解析抽到 `ui/ai_settings_config_parse.py`（A 类）

更新时间：2026-10-09

`lib/script/ui/ai_settings_panel.py` 3942 → 3889 行。本轮抽走的是**控件取值 → 配置值**那一段：
两个函数搬进 `lib/script/ui/ai_settings_config_parse.py`（125 行，无 Qt）。

- `parse_text_by_template(text, template)`：按模板类型把编辑框文本转成目标类型——文本原样、
  布尔认 `1/true/yes/on`、整数与小数走 `int()` / `float()`、其余交给 `ast.literal_eval`
  （元组与列表模板因此天然可用）。解析失败抛 `ValueError`。
- `parse_editor_value(field, *, widget)`：按 `kind` 分派——范围对、数组、音量滑块、小数滑块、
  单值（复选 / 下拉 / 行编辑）——产出 `{配置键: 解析后的值}`；外部维护字段
  （`external_autostart` / `external_announcement_suppression`）直接返回空。

### 45.1 抽离时拆解的那处耦合

原 `_parse_editor_value` 有五个 `isinstance(editor, QXxx)` 判定与一次
`self._volume_value_from_percent()` 换算，这是它留在面板里的唯一理由。抽离时把这六件事收成
一个注入参数：新模块只依赖一个 `EditorWidgets` 协议
（`is_text_editor` / `is_slider` / `is_decimal_field` / `is_check_box` / `is_combo_box` /
`volume_value_from_percent`），面板以 `widget=type(self)` 把自身传进去，并在
`_volume_value_from_percent` 之后补了五个静态谓词。

这样做的收益是双向的：新模块不 import `PyQt5`、不认识任何控件类，因此将来换成别的工具包时
这段解析逻辑不用动；面板侧仍然只有一个 Qt 类型判定的落点，加控件类型只改这一处。

### 45.2 验收（第 34.3 节「纯移动轮」）

- **行为等价**：把 `HEAD` 的两个方法原样摘出成独立类做对照，36 组输入差异 **0**——
  25 组字段描述（整数 / 小数 / 布尔四种写法 / 文本 / 元组 / 列表模板、结构不匹配的范围对、
  tuple 与 list 两种数组模板、长度不符的数组、非容器模板、复选、两种下拉、音量滑块、
  小数滑块、小数滑块用错控件、两类外部字段、缺 `kind`、把滑块当单值编辑器）与
  `parse_text_by_template` 的 11 组文本 × 模板组合。
  - 说明：非法 `literal_eval` 输入两侧都抛同一个 `ValueError`，其消息里含 AST 节点地址
    （`<ast.Name object at 0x...>`），该十六进制地址必然不同，属正常现象，已单独核对。
  - 本轮的对照脚本刻意不创建 `QApplication`：该环境下 offscreen 平台创建 `QApplication`
    会以 `0xC0000409` 崩在 `PyQt5` 初始化里（与本轮改动无关，`git stash` 后同样复现）。
    改用与真实控件同名的袖珍替身族注入谓词，可以完整覆盖 `parse_editor_value` 的分支。
- 全量 `2259` 项测试通过（`10` skipped）；`ruff check .` 干净；`compileall` 通过；
  `git diff --check` 干净。
- 冻结清单 `frozen_ui_qt_importers` 仍为 42；`ui -> 产品包` 耦合清单不变。

## 46. 批次 2 续：语音包安装器浮窗 QSS 抽到 `visuals/workbench_chrome.py`（A 类，逐字符零差异）

更新时间：2026-10-09

`lib/script/ui/voice_package_installer.py` 里的
`VoicePackageInstallerDialog.floating_stylesheet()`（70 行）搬进
`lib/core/render/visuals/workbench_chrome.py`，成为模块级
`voice_installer_stylesheet(mode=None)`。

判定依据：它是一段**只有字符串拼接**的 QSS —— 外层直接复用既有的
`floating_window_stylesheet()`，自身追加下拉框尺寸档、两条进度条各自的 chunk 配色
（`#VoiceDownloadProgress` 青 / `#VoiceExtractProgress` 粉）与三个按钮 id 的着色；
所有颜色都从 `get_workbench_colors()` 取 token，`scale_px` 提供尺寸档。落点
`lib/core/render/visuals/workbench_chrome.py` 正是既有的「浮窗外壳 QSS 与主题判定」家
（同一文件里已经有 `floating_window_stylesheet` 与 `is_workbench_theme_change`），
新函数与它们并列而不是另起一个模块。

面板侧 `floating_stylesheet()` 保留同名方法、改为一行转发；`voice_package_installer.py`
不再 import 面板内的 `_color()` 助手路径（只有这一个方法用它，转发后该函数在本文件
已无调用点，随 ruff 的未使用检测一并清理）。

### 46.1 验收（第 34.3 节「纯移动轮」）

- **逐字符零差异**：在深色与浅色两个主题下，`HEAD` 的 `floating_stylesheet()` 与
  改后的 `voice_installer_stylesheet()` 输出**长度与内容全等**（各 6033 字符）。
  主题用真实事件载荷（`CONFIG_UPDATED` + `workbench_light_theme`）驱动，测完还原原值。
- `tests/test_workbench_floating_windows.py` 的
  `test_floating_stylesheet_follows_the_live_workbench_palette` 继续通过（它断言明暗两版
  互不相等、且各自含自己的 `surface` 色），另有 `test_voice_package_installer_ui.py`、
  `test_render_layout_algorithms.py`（该文件把 `voice_package_installer.py` 列进扫描名单）
  与两条边界测试，共 110 项通过。
- 全量 `2259` 项测试通过（`10` skipped）；`ruff check .` 干净；`compileall` 通过；
  `git diff --check` 干净。
- 冻结清单 `frozen_ui_qt_importers` 仍为 42（`voice_package_installer.py` 的控件树还在）；
  `ui -> 产品包` 耦合清单不变。

## 47. 批次 2 收尾盘点：A 类已抽尽，下一轮转 C 类（方案变更，非迁移）

更新时间：2026-10-09

批次 2 从第 39 节走到第 46 节，八轮全是 A 类「纯能力抽离」。本节是一次**方案盘点**，
不含迁移动作，只回答一个问题：还能不能再抽出 A 类？

### 47.1 盘点方法

对 `frozen_ui_qt_importers` 全部 42 个文件做两轮 AST 扫描：

1. **顶层纯函数/类**：`tree.body` 里不引用任何 `PyQt5` 名字的 `def` / `class`；
2. **纯静态方法**：类体内既不引用 `PyQt5` 名字、也不读任何 `self.<attr>` 的方法。

第 2 条是关键——**只要读了一个实例属性，它就不是纯移动**，抽出去必须连状态一起搬，
那是 C 类的「视图 + 控制器」切分，不是本批次定义的 A 类。

### 47.2 结果：剩余可抽物已不足一轮

| 文件 | 行数 | 顶层纯 | 纯静态 |
| --- | ---: | ---: | ---: |
| `ai_settings_panel.py` | 3889 | 29 | 152 |
| `forum_board.py` | 1150 | 0 | 23 |
| `voice_package_installer.py` | 998 | 10 | 6 |
| `bug_tracker_window.py` | 983 | 0 | 36 |
| `workbench_window.py` | 982 | 3 | 40 |
| `forum_window.py` | 976 | 27 | 0 |
| `playlist_panel.py` | 966 | 18 | 6 |
| `office_page.py` | 924 | 31 | 0 |
| `cmd_window.py` | 802 | 17 | 0 |

逐一看过之后，剩下的都落进三类里，没有一项够得上一轮：

- **单例与生命周期打包**：`open_*_window` / `get_*` / `cleanup_*` / `init_*`
  （`forum_window` 27 行、`office_page` 31 行、`cmd_window` 17 行、`playlist_panel` 18 行）。
  它们靠模块级全局变量串起来，抽出去只会把全局从 A 文件挪到 B 文件，耦合不减反增。
- **`ai_settings_panel` 的 `_collect_values` / `_set_values_to_form`**（128 + 45 行）：
  字段集重合，但控件访问器有 30 多种、要读 `_DEFAULT_VALUES`、还必须保留取值顺序
  （先普通字段、最后 `_force_mode` + `_update_reply_mode_sections`）。做成表驱动要先写一份
  30+ 成员的控件适配协议，等价性对照也只能逐个属性打桩——投入产出比不如直接按 C 类切。
- **`ai_settings_panel` 的 152 行纯静态**：单个都是 2~11 行的小助手（`_gpu_mode_from_num_gpu`、
  `_num_gpu_from_mode`、`_strip_ansi` 一类），抽出去是一次几个符号的碎步提交，收益有限。

`bug_tracker_window.py` / `workbench_window.py` / `tray_icon.py` 的纯静态行数看着可观，
但那些方法都是 `_sync_theme_toggle`、`_control_panel_page_metadata` 一类**紧贴本窗口状态机**的
小函数，同样属于 C 类。

### 47.3 结论与下一轮起点

- 批次 2（A 类/B 类下沉）到此为止：A 类抽尽，B 类在批次 2 首轮已下沉
  `forum_images.py`（第 39 节）。
- 下一轮转入 **批次 3：C 类「视图 + 控制器 + 描述」切分**，并按第 34.2 节要求与去 Qt 同轮交付。
  建议起点是 `ai_settings_panel.py`：它最大（3889 行）、已经有批次 1 抽出的四个无 Qt 模块做地基
  （`ai_settings_contributions` / `config_schema` / `labels` / `validation` + `descriptions` /
  `config_parse`），`_collect_values` / `_set_values_to_form` 正好是「控制器」那一半的入口。
- 每个 tab 一轮、每轮沿用第 34.3 节的门槛；`frozen_ui_qt_importers` 只减不增。

## 48. 批次 3 首轮：设置面板「支持作者 / 贡献者」两页切分（C 类，视图 + 控制器 + 描述）

更新时间：2026-10-09

第 47 节把下一轮起点定为 `ai_settings_panel.py` 的 C 类切分，并建议从「内聚且自洽的一族」
起步。本轮取的是两类只读页：**支持作者**与**贡献者**。它们没有配置字段、不参与
`_collect_values` / `_set_values_to_form` 的取值顺序，也没有后台任务与状态机，因此是
面板里边界最清楚的一块：切走之后面板只留四行薄壳转发与两条 QSS 插值。

**范围（`lib/script/ui/ai_settings_about.py`，414 行）**

- **视图**：`build_sponsor_author_panel()` / `build_contribution_list_panel()`，以及两页共用的
  `_ContributionCardButton`（含 `bind_watermark_label` / `_layout_aware_size_hint` /
  `_apply_watermark` 与两个悬停事件）。
- **控制器**：`open_sponsor_author_link()` / `open_contribution_link()` /
  `set_sponsor_author_image()`。三者原先都是面板方法，只是读 `_show_info_message` 与
  `_project_root()`；现在提示文案经 `show_info` 回调注入、文档树位置经 `root_dir` 入参传入，
  模块因此既不认识面板类型也不认识 `_project_root`，不需要 import 面板。
- **描述**：`sponsor_author_stylesheet()` / `contribution_list_stylesheet()`，两页专属 QSS
  片段（`#sponsorAuthorCard` / `#ContributionCardButton` 一族），只读工作台主题色与像素档。

**面板侧（`ai_settings_panel.py`：3889 → 3607 行）**

- `_build_sponsor_author_panel` / `_build_contribution_list_panel` 改为按原签名转发，
  `_config_tab_meta`、`_project_root()` 与 `self._show_info_message` 由面板显式传入；
  `_set_sponsor_author_image` / `_open_sponsor_author_link` / `_open_contribution_link`
  同样转发。调用点、`_config_tab_meta` 的形状与两页的按钮清单逐项不变。
- `_ContributionCardButton` 由本模块再导出（面板顶部 `from ... import (... as _ContributionCardButton)`），
  `refresh_workbench_theme()` 与 `tests/test_workbench_settings_layout.py` 的导入路径不变。
- `_apply_style()` 原地的 62 行 QSS 换成
  `{_about.sponsor_author_stylesheet()}{_about.contribution_list_stylesheet()}`；
  两个函数各自返回「以换行开头」的片段，贡献者片段末尾补一个换行。拼出的整段样式表
  与切分前**逐字符相同（9450 字符，深色主题）**。
- 顺带清理搬走后不再使用的符号：`QSize` / `QPixmap` 两个控件导入与
  `get_workbench_colors` 导入（`webbrowser` 仍被 `_open_quark_manual_update` 等使用，保留）。

**验证（2026-10-09）**

- 纯移动对照：两个页面函数与卡片控件与 `HEAD` 版本逐行等价，差异只有签名
  （`self` 换成显式入参 `tab_meta` / `root_dir` / `show_info`，`def` 去掉一级缩进），
  差异行数 10 / 10 / 0。
- 样式表 oracle：用 `HEAD` 版本与切分后版本各构造一次面板，`styleSheet()` 字符串全等
  （9450 字符）。中途曾出现「前置空行 +1」「末尾规则与 `QScrollArea` 同行」两处拼接差异，
  定位到片段边界的换行归属后归零。
- 控件树 oracle（离屏）：`sponsor_author` / `contribution_list` 两页在 1000×760 宿主里
  逐控件类型 / 对象名 / 几何 / 可见性 / 文本 / 字体 / 内联样式摘要全等，`grab()` 位图
  SHA-256 相等（差异像素 0）。
- 回归：`tests/test_workbench_settings_layout.py`（贡献卡片两行贴合与宿主样式）、
  `tests/test_update_uninstall_button.py`（更新页按钮与元数据）、
  `tests/test_ai_settings_contributions.py`、`tests/test_offline_distribution.py`
  共 53 条通过。
- 全量 `unittest` 2259 通过 / 10 跳过；`ruff check .` 归零；`compileall` 通过；
  `git diff --check` 干净。

**清单变化**

- `frozen_ui_qt_importers`：42 → **43**。这是批次 3 的正常形态——拆分出的新页仍 import
  `PyQt5`，按第 34.2 节规则显式登记 `lib/script/ui/ai_settings_about.py`；
  C 类的目标是在后续轮次里让这些文件逐个去掉 Qt、再把条目删回。
- `ui -> 产品包` 耦合清单不变（两页只用 `ai_settings_contributions` 与主题表格，不碰产品包）。

**本轮没动的部分**

- 面板的配置页族（`_build_config_category_panel` 338 行、`_collect_values` / `_set_values_to_form`
  控制器入口、六个编辑器工厂）与 `desktop_pet_update` 页仍在面板里，按 tab 逐轮推进。
- `_show_info_message` 留在面板：它同时服务更新页与卸载确认，属于面板级共用设施。

## 49. 批次 3 第二轮：设置面板「桌宠更新」页切分（C 类，视图 + 控制器）

更新时间：2026-10-09

第 48 节切走两个只读页之后，面板里剩下的三个非配置页只有一个：**桌宠更新**。
它与前两页同属「没有配置字段、不参与取值顺序」，差别在于带五个会反馈到面板的动作，
因此这一轮同时把「控制器」那一半切成模块函数。

**范围（`lib/script/ui/ai_settings_update.py`，274 行）**

- **视图**：`build_desktop_pet_update_panel()` 装配四个分区（稳定版本 / 开发版本 / 手动获取 /
  卸载）与五个按钮（`checkUpdateButton` / `syncDevButton` / `quarkManualUpdateButton` /
  `qqGroupUpdateButton` / `uninstallPetButton`），文案、对象名、间距、`_config_tab_meta`
  的形状与按钮顺序逐项不变。按钮回调改指向 `actions` 上的五个可调用项。
- **控制器**：`ensure_update_dialog` / `open_update_dialog` / `on_check_updates` /
  `on_sync_dev_build` / `ensure_qq_group_dialog` / `open_quark_manual_update` /
  `show_qq_group_qrcode` / `uninstall_pet`。面板状态（淡出、信息气泡、事件中心、文档树、
  对话框实例）经 `PetUpdateActions` 数据类显式注入，模块不读 `self`。
- **描述**：本页没有专属 QSS（按钮走面板共享样式表），因此只导出两个布局事实
  `UPDATE_BUTTON_ROW_GAP` 与 `QUARK_UPDATE_URL`。

**面板侧（`ai_settings_panel.py`：3607 → 3474 行）**

- `_build_desktop_pet_update_panel` 改为按原签名转发；八个动作方法改成 `_run_pet_update_action()`
  的薄壳，动作跑完把两个对话框实例写回面板字段——**面板字段仍是唯一状态**，
  `_ensure_update_dialog()` / `_ensure_qq_group_dialog()` 的返回值与惰性缓存语义不变。
- 清理搬走后不再使用的导入：`get_shared_root_dir`、`launch_uninstaller` /
  `resolve_uninstaller`、`ask_confirmation`，以及两个已下沉的常量。

**验证（2026-10-09）**

- 纯移动对照：视图函数与 `HEAD` 版本逐行等价，差异仅签名（`self` → 显式入参
  `tab_meta` / `actions`，`def` 去掉一级缩进）。
- 面板样式表 oracle：与切分前逐字符全等（9450 字符）。
- 三页控件树 oracle（离屏 1000×760）：`sponsor_author` / `contribution_list` /
  `desktop_pet_update` 逐控件类型 / 对象名 / 几何 / 可见性 / 文本 / 字体 / 内联样式摘要全等，
  `grab()` 位图 SHA-256 相等（差异像素 0），`_config_tab_meta` 键集与按钮对象名列表亦相等。
- 回归：`tests/test_update_uninstall_button.py`（四条卸载路径，patch 目标改到真正的所有者
  `ai_settings_update`）、`test_workbench_settings_layout.py`、`test_workbench_lazy_settings.py`、
  `test_ai_settings_reply_mode_sections.py`、`test_offline_distribution.py` 共 74 条通过。
- 边界测试 `tests/test_qt_dependency_boundaries.py` / `tests/test_code_structure_boundaries.py`
  52 条通过。

**清单变化**

- `frozen_ui_qt_importers`：43 → **44**（新增 `lib/script/ui/ai_settings_update.py`）。
- `ui -> 产品包` 耦合清单不变：本模块只依赖 `lib.script.app.uninstall_entry`（非产品包）。

**本轮没动的部分**

- 面板的配置页族（`_build_config_category_panel` 与六个编辑器工厂、`_collect_values` /
  `_set_values_to_form` 控制器入口）仍在面板里，按 tab 逐轮推进。下一轮的天然起点是
  「配置页骨架 + 编辑器工厂族」——它们彼此耦合最紧（表单构建协议共用一套谓词），
  需要先定下视图/控制器的边界再动。

## 50. 批次 3 第三轮：设置面板整段 QSS 下沉到 `visuals/`（A 类，逐字符零差异）

更新时间：2026-10-09

第 48、49 节把三个非配置页切走后，面板自己还留着一份 243 行的 `_apply_style()`：
控件 id 选择器、三态配色、像素档、滚动条与下拉框尺寸全写在一个 `QWidget` 子类里，
取值经 `qt_color_name()` 与 `ui_font().family()` 走 Qt 侧。这一轮把这层**描述**搬到
后端中立的 `visuals/`，与浮窗外壳 QSS（`workbench_chrome.py`，第 46 节）同一落点。

**新文件（`lib/core/render/visuals/ai_settings_panel_visuals.py`，364 行）**

- `ai_settings_panel_stylesheet(mode=None)`：整段面板样式表（配置区/表单标签一族 +
  `QMenu` / `QScrollArea` / `QComboBox` / `QSpinBox` / `QSlider` 各态 + 滚动条）。
- `sponsor_author_fragment(c)` / `contribution_list_fragment(c)`：「支持作者 / 贡献者」
  两页的内联 QSS 片段。它们在第 48 节随两页一起落到 `ai_settings_about.py`，但同一张样式表的
  内联块分散在两个模块会让「整段 QSS 的事实源」出现两个所有者，因此本轮一并上移；
  `ai_settings_about` 改为按原名转发，对外面不变。
- `CONFIG_FONT_SIZE` / `DROPDOWN_ITEM_FONT_SIZE` / `COMBO_DROP_WIDTH` 三个像素档常量。
- 只依赖 `config.font_config.get_ui_font_family()`、`config.scale.scale_px`、`visuals.palette` 的
  `COLORS` / `UI_THEME` 与 `visuals.workbench_tokens`——无 Qt、无 `lib.script`，**不在**
  `frozen_ui_qt_importers` 的扫描面内（仍为 44 项），由
  `test_shared_visual_modules_are_backend_neutral` 校验。

### 50.1 两处只能靠「顺序」复刻的事实

- **调色板查表顺序**：`qt_color_name()` 是「先查 `COLORS` 再查 `UI_THEME`」。两个色板都有
  `text` 键且取值不同（`COLORS` 为 `#333333`、`UI_THEME` 为 `#000000`），按
  `dict(COLORS) | dict(UI_THEME)` 合并会静默换掉正文色。实现取
  `main_colors = {UI_THEME}` 再 `update({COLORS})`，复刻同一优先级。
- **字体族**：原实现取 `ui_font(size=...).family()` 再去引号，本层改取 `get_ui_font_family()`——
  两者在同一进程里返回同一族名，且不再需要构造 `QFont`。

### 50.2 面板侧（`ai_settings_panel.py`：3473 → 3234 行）

- `_apply_style()` 缩成 `self.setStyleSheet(ai_settings_panel_stylesheet())`；
  删除搬走后不再使用的 `qt_color_name` 导入。
- `ai_settings_about.py` 的 `sponsor_author_stylesheet` / `contribution_list_stylesheet`
  改为转发本层片段，名字、签名与返回值不变。

### 50.3 验收（第 34.3 节「纯移动轮」）

- 逐行等价：`HEAD` 的 `_apply_style()` 整段（243 行，含注释与像素档）移入新模块，
  调用点只多一行转发；面板差异 243 删除 / 4 新增（一个 `import` 加三行方法体）。
- 样式表 oracle：`HEAD` 版本与切分后版本各构造一次面板，`styleSheet()` **逐字符全等
  （9450 字符，SHA-256 相等）**。
- 三页控件树 oracle（离屏 1000×760）：`sponsor_author` / `contribution_list` /
  `desktop_pet_update` 逐控件类型 / 对象名 / 几何 / 可见性 / 文本 / 字体 / 内联样式摘要全等，
  `grab()` 位图 SHA-256 相等（差异像素 0），`_config_tab_meta` 键集与按钮对象名列表亦相等。
- 回归：新增 `tests/test_ai_settings_panel_visuals.py` 5 条；`tests/test_workbench_settings_layout.py`
  增 `test_panel_stylesheet_matches_the_shared_visual_module`。全量 `unittest` 2265 通过 / 10 跳过；
  `ruff check .` 归零；`compileall` 通过；`git diff --check` 干净；边界测试 52 条通过。

**清单变化**

- `frozen_ui_qt_importers`：44 项**不变**（新模块在 `lib/core/render/visuals/` 下且无 Qt）。
- `ui -> 产品包` 耦合清单不变。

**本轮没动的部分**

- 面板的配置页族（`_build_config_category_panel` 与六个编辑器工厂、`_collect_values` /
  `_set_values_to_form`）仍在面板里，按 tab 逐轮推进。

## 51. 批次 3 第四轮：设置面板「配置编辑器控件族」切分（C 类，视图控件 + 工厂）

更新时间：2026-10-09

前三轮把面板里三个只读页与整段 QSS 都移走了，剩下的 `AISettingsPanel` 仍拖着约 330 行
「配置编辑器」代码：三个自定义控件类（`_NoWheelSlider` / `_DecimalSliderField` /
`_AnimationDurationSliderField`）、六个编辑器工厂、一组字段谓词、音量百分比换算，以及
两个「浏览目录 / 打开文件」对话框助手。它们虽然长在 `QWidget` 子类里，却只读像素档与
schema 表，不读面板状态（不看 `_force_mode`、不碰 `_config_tab_meta`），因此是本轮切分目标。

**范围（`lib/script/ui/ai_settings_editors.py`，548 行）**

- **视图控件类**：`_NoWheelSlider` / `_DecimalSliderField` / `_AnimationDurationSliderField`
  原样搬到模块级，逐行未动。
- **`ConfigEditorMixin`**：`_create_compact_pair_editor` / `_create_config_line_edit` /
  `_create_config_choice_editor` / `_create_form_label` / `_create_volume_slider_editor` /
  `_create_animation_folder_duration_editor` / `_create_path_editor_with_open_button` /
  `_create_sequence_editor` / `_wrap_field_widget` / `_set_config_editor_value` /
  `_set_sequence_editor_values`、六个字段谓词、`_volume_percent_from_value`、
  `_get_choice_field_options` / `_get_decimal_slider_spec`，以及三个「浏览 / 打开」助手。
  **方法体逐行未动（缩进也未变）**，只拆掉三处对面板类的硬引用：
  `AISettingsPanel._folder_options` → 模块级 `_folder_options()`（纯表查询）、
  `AISettingsPanel._volume_percent_from_value` → `self._volume_percent_from_value`、
  `_set_config_editor_value` 由 `@staticmethod` 恢复成读 `self` 的普通方法（它原本就在读面板类）。
- **`AISettingsPanel` 改为继承 `ConfigEditorMixin`**，并按原名再导出
  `_DecimalSliderField` / `_AnimationDurationSliderField` / `_GENERAL_DECIMAL_SLIDER_SPECS`，
  既有导入面（`tests/test_workbench_settings_layout.py` 等）零改动。

### 51.1 顺带修掉的一处真实缺陷（设置面板音量滑条无法保存）

`ai_settings_config_parse.parse_editor_value(field, widget=...)` 的协议名是
**`volume_value_from_percent`**（不带下划线），而面板历史上只定义了
`_volume_value_from_percent`。于是 `kind == "volume_slider"` 的字段在
`_collect_config_category_values` 里会走 `widget.volume_value_from_percent(...)` 失败，
被包成 `ValueError: SOUND.master_volume 格式错误: type object 'AISettingsPanel' has no
attribute 'volume_value_from_percent'`。

**这是 `HEAD` 上已存在的缺陷，不是本轮引入**——用 `HEAD` 版本复现：对
`audio_music` 页调 `_collect_config_category_values("audio_music")` 必然抛错，也就是
「音频 / 音乐」页的音量滑条（`SOUND.master_volume` 等 5 个字段）一直存不进去。
本轮把编辑器族下沉时，顺手补上协议别名 `volume_value_from_percent = _volume_value_from_percent`
（同一实现、两个名字），缺陷随之消失，并加断言钉住「音量子页必须能读回 `volume_slider` 字段」。

### 51.2 验收

- 纯移动对照：控件类与工厂方法体与 `HEAD` 逐行等价；差异仅三处引用改写与
  面板侧的 import / 继承 / 再导出。
- 八页控件树 oracle（离屏 1000×760）：`ui_anim` / `behavior_physics` / `audio_music` /
  `scene_objects` / `system_dispatch` / `contribution_list` / `sponsor_author` /
  `desktop_pet_update` 逐控件类型 / 对象名 / 几何 / 可见性 / 文本 / 字体 / 内联样式 /
  `workbenchEmbedded` 全等，`grab()` 位图 SHA-256 相等（差异像素 0），`_config_tab_meta`
  键集与按钮对象名列表亦相等；面板 `styleSheet()` 逐字符全等（9450 字符）。
- 回归：`tests/test_workbench_settings_layout.py`（新增编辑器族归属与音量保存两条断言）、
  `tests/test_workbench_lazy_settings.py`、`tests/test_ai_settings_reply_mode_sections.py`、
  `tests/test_ai_settings_panel_visuals.py` 全绿；全量 `unittest` 2267 通过 / 10 跳过；
  `ruff check .` 归零；`compileall` 通过；`git diff --check` 干净；边界测试 52 条通过。

**清单变化**

- `frozen_ui_qt_importers`：44 → **45**（新增 `lib/script/ui/ai_settings_editors.py`；
  按第 34.2 节 C 类规则登记，条目再随该族去 Qt 删除）。
- `ui -> 产品包` 耦合清单不变：新模块不 import `chat` / `office` / `music` / `gsvmove`。

**本轮没动的部分**

- 面板的配置页骨架（`_build_config_category_panel`）与取值/回填控制器
  （`_collect_values` / `_set_values_to_form`）仍在面板里，按 tab 逐轮推进。

## 52. 批次 3 第五轮：设置面板「配置分类页 + 外部配置字段族」切分（C 类）

更新时间：2026-10-09

第四轮切走编辑器族后，面板里最大的单个方法变成 338 行的
`_build_config_category_panel`——它把每个配置分类页从分区、表单到 `_config_tab_meta`
元数据表一并装配出来（成对 / 序列 / 滑条 / 路径 / 布尔各分支都在这一个方法里）。
旁边还散着两组「外部配置字段」：开机启动（走系统托盘逻辑并订阅
`AUTOSTART_STATUS_CHANGE`）与公告永久抑制（走公告偏好文件）。这一轮把它们整体切出。

**范围（`lib/script/ui/ai_settings_config_page.py`，538 行）**

- **`ConfigPageMixin._build_config_category_panel(category)`**：页骨架装配，逐行搬出。
  仍读面板的 `_config_tab_meta`，仍调用编辑器族的 `_create_*` / `_set_config_editor_value`
  与三个只读页的 `_build_*_panel`、两个保存动作 `_on_restore_config_category` /
  `_on_save_config_category`——即「视图装配」这一半，依赖关系不变。
- **外部配置字段族**：`_get/_set_autostart_enabled`、`_subscribe/_unsubscribe_autostart_events`、
  `_set_autostart_checkbox_checked`、`_on_autostart_status_change`、`_append_autostart_field`、
  `_get/_set_announcement_forever_suppressed`、`_append_announcement_suppression_field`，
  以及 `_apply_external_category_fields`（保存/恢复时把开关状态落到系统）。
- **`AISettingsPanel` 改为继承 `ConfigPageMixin`**，`ui/ai_settings_tabs.py` 的
  `panel._build_config_category_panel(category)` 调用点零改动。

### 52.1 面板侧（`ai_settings_panel.py`：2778 → 2291 行）

- 删除搬走的方法，并清掉随之不再使用的导入：`ANIMATION`、`CATEGORY_KEY_ALLOWLIST`、
  `category_section_entries`、`friendly_range_name`、`range_pair_signature`、
  `friendly_key_name` / `friendly_section_name` / `section_help_text`、
  `load_announcement_preferences` / `set_announcement_forever_suppressed`、
  `resolve_animation_folder_path` / `scan_animation_frame_files`
  （`hardcoded_general_default` 仍有调用，保留）。
- `tests/test_workbench_lazy_settings.py` 的两处 patch 目标改到真正的所有者
  `ai_settings_config_page`（`load_announcement_preferences` /
  `set_announcement_forever_suppressed`），与第 49 节同一处理方式。

### 52.2 验收

- 纯移动对照：`ConfigPageMixin` 各方法与 `HEAD` 逐行等价（差异仅面板侧的 import / 继承）。
- 八页 oracle（离屏 1000×760）：控件树摘要 / 位图 SHA-256 全等（差异像素 0），
  且额外比对了 `_config_tab_meta` 的 `fields`（`kind`+`dict_name`+`key` 序列）、
  `defaults`（逐项默认值）与按钮对象名——**配置页元数据零差异**；`styleSheet()` 逐字符全等。
- 回归：`tests/test_workbench_lazy_settings.py`、`tests/test_workbench_settings_layout.py`、
  `tests/test_ai_settings_reply_mode_sections.py`、`tests/test_ai_settings_panel_visuals.py`、
  `tests/test_autostart.py` 全绿；全量 `unittest` 2267 通过 / 10 跳过；`ruff check .` 归零；
  `compileall` 通过；`git diff --check` 干净；边界测试 52 条通过。

**清单变化**

- `frozen_ui_qt_importers`：45 → **46**（新增 `lib/script/ui/ai_settings_config_page.py`）。
- `ui -> 产品包` 耦合清单不变：新模块不 import `chat` / `office` / `music` / `gsvmove`
  （开机启动走 `lib.script.app.*`，非产品包）。

**本轮没动的部分**

- 面板的取值/回填控制器（`_collect_values` / `_set_values_to_form`）、默认值完整性检查、
  保存任务调度与 `_build_ui`（AI 主面板）仍在 `ai_settings_panel.py`，按轮推进。

## 53. 批次 3 第六轮：设置面板「AI 主页面」切分（C 类，视图 + 取值/回填闭环）

更新时间：2026-10-09

第五轮切走配置分类页后，面板里最大的单个方法变成 508 行的 `_build_ui`：它把 AI 主页面的每一段
（回复模式、手动 API、本地 Ollama、语音与 GSV、记忆、动作栏）从布局到 `_config_tab_meta` 之外的
面板状态一并装配出来。旁边还散着**只有这一页在用**的取值与回填闭环——128 行的 `_collect_values`、
Ollama 与手动 API 模型刷新、`_update_*_visibility` 一族，以及页面动作
（恢复默认 / 保存 / 保存并重启）。这一轮把它们整体切出。

**范围（`lib/script/ui/ai_settings_page.py`，1179 行）**

- **`AISettingsPageMixin._build_ui`**：AI 主页面装配，逐行搬出。它读面板的像素档
  （`_border` / `_layer` / `_PANEL_SCALE`）、订阅本页控件信号，并写面板的 `_tab_pages` /
  `_ai_scaffold` / `_config_tab_meta`，最后按懒加载开关决定调 `attach_ai_settings_tabs`
  还是 `_ensure_config_defaults_integrity`——依赖关系不变。
- **取值与回填闭环**：`_collect_values`、`_update_reply_mode_sections` /
  `_welfare_interval_locked` / `_update_auto_companion_interval_row` /
  `_update_gsv_settings_visibility` / `_update_gsv_advanced_visibility`、
  `_refresh_ollama_model_*`、`_normalize_manual_api_base_url*` / `_sync_manual_api_provider_selection` /
  `_on_manual_api_provider_changed` / `_manual_api_models_url` / `_parse_manual_api_models` /
  `_probe_manual_api_models` / `_refresh_manual_api_model_choices` / `_on_probe_manual_api_models`、
  `_run_on_ui_thread`、`_set_widget_description` / `_set_form_row_description`、
  `_parse_editor_value` / `_validate_ai_values` / `_validate_general_config_values` /
  `_collect_all_general_config_values` / `_apply_all_external_config_fields`。
- **页面动作**：`_on_restore_ai_defaults` / `_on_save_ai_action` / `_on_save_and_restart`，
  以及「打开人格文件 / Ollama 下载页 / GSV 缓存目录」与语音包安装/删除的四个回调。
- **`AISettingsPanel` 改为多继承 `AISettingsPageMixin`**（在 `ConfigPageMixin` /
  `ConfigEditorMixin` 之前），`__init__` 的 `self._build_ui()` 与 `ai_settings_tabs.py` 的
  调用点零改动。

### 53.1 三处对面板类的硬引用改写（沿用第四、五轮先例）

- `AISettingsPanel._get_choice_field_options` → `self._get_choice_field_options`
  （方法本身属编辑器族，同类族调用改走 self）。
- `AISettingsPanel._normalize_manual_api_base_url(...)` → `self._normalize_manual_api_base_url(...)`
  （`_collect_values` 里的同类族调用）。
- `_ensure_config_defaults_integrity()`：`_build_ui` 原本写的就是 `self.` 限定，未改。

**定义随调用点走**：`_num_gpu_from_mode` / `_GPU_MODE_*` /
`WELFARE_AUTO_COMPANION_INTERVAL_MINUTES` / `_AI_HINT_TEXT` / `_logger` 只有本页在用，
随本模块一起搬走；`_gpu_mode_from_num_gpu` 仍被面板 `_set_values_to_form` 使用，留在面板并从
本模块导入（只有一份实现）。项目根解析本页改用可注入的 `_page_project_root`（面板覆盖为
`_project_root`），沿用第 51 节 `_editor_project_root` 的做法，让既有
`patch.object(ai_settings_panel, "_project_root", ...)` 继续生效。

### 53.2 面板侧（`ai_settings_panel.py`：2291 → 1237 行）

- 删除 37 个已搬方法（含 `_build_ui`）与两个只服务本页的手动 API 模型助手。
- 清掉随之下沉不再使用的导入：`os` / `subprocess` / `sys` / `threading` / `webbrowser` /
  `QTimer` / `QListView` / `QPushButton` / `QHBoxLayout` / `QLineEdit` /
  `QVBoxLayout` / `QFormLayout` / `QSizePolicy` / `Callable` / `Path` 之外的本页控件、
  `AI_VOICE_MAX_CHARS_{MIN,MAX}`、`validate_ai_values`、`ai_settings_config_parse`、
  `_DecimalSliderField` / `_ApiKeyLineEdit` / `_MANUAL_API_PROVIDER_PRESETS` /
  `_WatermarkComboBox` / `describe_form_row` / `set_widget_description` / `_normalize_api_base_url` /
  `_fetch_api_models`、`get_available_model_names` / `get_model_list_error` / `ensure_user_persona_file`、
  `WELFARE_AUTO_COMPANION_INTERVAL_MS`、`attach_ai_settings_tabs` / `SmoothScrollArea` /
  `create_settings_form`、`VoicePackageInstallBanner` / `VoicePackageManagementBar`。
- 面板仍 `import PyQt5`（自绘 `paintEvent`、动画、鼠标拖拽、生命周期都在），条目保留。

### 53.3 验收

- 纯移动对照：`AISettingsPageMixin` 的 37 个方法与 `HEAD` 逐行等价（差异仅三处 self 改写与
  常量搬家）；面板保留的 79 个方法体与 `HEAD` 逐字节相同。
- 八页 oracle（离屏 1000×760）：`ui_anim` / `behavior_physics` / `audio_music` / `scene_objects` /
  `system_dispatch` / `contribution_list` / `sponsor_author` / `desktop_pet_update` 的控件树
  （类型 / 对象名 / 几何 / 可见性 / 文本 / 字体 / 内联样式）与 `grab()` 位图 SHA-256 全等，
  面板 `styleSheet()` 逐字符全等（9450 字符），`_config_tab_meta` 的 `fields` / `defaults` /
  按钮对象名亦全等——**改动前 / 改动后差异为 0**（对拍在独立 worktree 的 `HEAD` 版本上跑，
  并同步了被 `.gitignore` 忽略的 `config/music/volume.json`，否则音量页会因本地音量偏好不同而假报差异）。
- 回归：`tests/test_workbench_settings_layout.py`（新增 AI 主页面归属断言）、
  `tests/test_ai_settings_reply_mode_sections.py`、`tests/test_persona_storage.py`、
  `tests/test_workbench_lazy_settings.py`、`tests/test_update_uninstall_button.py` 全绿；
  全量 `unittest` 2269 条通过（10 跳过）；`ruff check .` 归零；`compileall` 通过；
  `git diff --check` 干净；边界测试 52 条通过。

**清单变化**

- `frozen_ui_qt_importers`：46 → **47**（新增 `lib/script/ui/ai_settings_page.py`）。
- `ui -> 产品包` 耦合清单：`ai_settings_panel.py` 的 chat / persona / gsvmove 三条随代码搬到
  `ai_settings_page.py`（`handler_auto_companion` / `ollama_registry` / `persona_storage` / `gsvmove`），
  面板侧只留 `lib.script.gsvmove`（`get_voice_package_status` 仍被 `_refresh_voice_package_ui` 使用）——
  按第 33 节的「new file 必须显式登记」规则登记，总数不变。
- 测试补丁目标随之改到真正的所有者：`tests/test_persona_storage.py` 的
  `ensure_user_persona_file`、`tests/test_ai_settings_reply_mode_sections.py` 的
  `get_voice_package_status` / `load_ai_values` / `save_ai_values` / `apply_ai_runtime`
  全部改打到 `ai_settings_page`（同第 52 节的处理）。

**本轮没动的部分**

- 面板级设施（动画 / 淡出 / 悬浮标签页 / `_emit_info` / `_refresh_voice_package_ui` /
  `_collect_config_category_values` / `_ensure_config_defaults_integrity` / `_on_save` /
  `_submit_save_task` / `_set_values_to_form`）、项目字体与水印、`show_centered`、
  `create_workbench_page`、鼠标与生命周期事件仍在 `ai_settings_panel.py`，后续候选见第 34 节的滚动清单。

## 54. 批次 3 第七轮：设置面板「配置取值 / 回填 / 恢复 / 异步保存」控制器切分（C 类）

更新时间：2026-10-09

第六轮切走 AI 主页面后，面板里剩下的是**与视图无关的那一半**：按 `_config_tab_meta` 取值
（`_collect_config_category_values`）与回填（`_set_values_to_form` / `_load_config_tab_values`）、
默认值兜底与逐分类恢复（`_ensure_config_defaults_integrity` / `_on_restore_config_category` /
`_on_restore_defaults`）、通用配置取值校验（`_validate_general_*` 一族），以及异步保存任务调度
（`_submit_save_task`）与三个保存入口（`_on_save` / `_on_save_config_category` 及各自的
“并退出”变体）。这一轮把这 15 个方法与只服务它们的两个模块级助手整体切出。

**范围（`lib/script/ui/ai_settings_config_store.py`，436 行）**

- **`ConfigStoreMixin`**：上述 15 个方法逐行搬出。它们只读 `_config_tab_meta` /
  `_DEFAULT_VALUES` 与事件中心，不持有任何本页控件；对面板设施的依赖（`_emit_info`、
  `_run_on_ui_thread`、`_apply_external_category_fields`、编辑器族 `_set_*`）经 `self` 解析——
  那些方法分属 `AISettingsPageMixin` / `ConfigPageMixin` / `ConfigEditorMixin`，同一宿主上皆可用。
- **模块级保存助手**：`_save_general_config` / `_apply_general_runtime` 随保存路径一起搬走
  （只有这组方法在调用），`_logger` 随之新建一份。
- **项目根解析**本页改用可注入的 `_store_project_root`（面板覆盖为 `_project_root`），沿用第
  51 / 53 节的做法，让既有 `patch.object(ai_settings_panel, "_project_root", ...)` 继续生效。
- **`AISettingsPanel` 改为多继承 `ConfigStoreMixin`**（在 `ConfigPageMixin` 之前），
  `ai_settings_tabs.py` / `ai_settings_config_page.py` / `ai_settings_page.py` 的调用点零改动。

### 54.1 面板侧（`ai_settings_panel.py`：1237 → 858 行）

- 删除 15 个已搬方法与两个模块级保存助手；余下的 62 个方法（自绘 / 动画 / 悬浮标签页 /
  生命周期 / 只读三页 / 更新页 / 描述转发 / 编辑器族导出面）**方法体逐字节与 `HEAD` 相同**。
- 清掉随之下沉的导入（`copy`、`save_general_values`、`ai_settings_validation`、
  `hardcoded_general_default`、`AI_VOICE_MAX_CHARS_DEFAULT` 等由 ruff 指出）。
- 面板仍 `import PyQt5`（自绘 `paintEvent`、动画、鼠标拖拽、生命周期），条目保留。
- **面板不再直接耦合产品包**：最后一处 `lib.script.music`（藏在 `_apply_general_runtime` 里）
  随该函数搬到 `ai_settings_config_store`（见下），面板只剩 `lib.script.gsvmove`。

### 54.2 验收

- 纯移动对照：`ConfigStoreMixin` 的 15 个方法与 `HEAD` 逐行等价（差异仅一处
  `AISettingsPanel._get_choice_field_options` → `self._get_choice_field_options` 与
  `_project_root()` → `_store_project_root()`）；面板保留的 62 个方法体与 `HEAD` 逐字节相同。
- 八页 oracle（离屏 1000×760）：控件树（类型 / 对象名 / 几何 / 可见性 / 文本 / 字体 / 内联样式）、
  `grab()` 位图 SHA-256、`_config_tab_meta` 的 `fields` / `defaults` 与面板 `styleSheet()`
  （9450 字符）**全部零差异**（对拍在独立 worktree 的 `HEAD` 版本上跑，
  并同步了被 `.gitignore` 忽略的 `config/music/volume.json`）。
- 回归：`tests/test_workbench_settings_layout.py`（新增配置存取协作断言）、
  `tests/test_workbench_lazy_settings.py`、`tests/test_ai_settings_reply_mode_sections.py`、
  `tests/test_persona_storage.py`、`tests/test_update_uninstall_button.py`、全量 `unittest`；
  `ruff check .` 归零；`compileall` 通过；`git diff --check` 干净；边界测试 52 条通过。

**清单变化**

- `frozen_ui_qt_importers`：47 → **48**（新增 `lib/script/ui/ai_settings_config_store.py`）。
- `ui -> 产品包` 耦合清单：面板侧 5 条（chat ×3 + gsvmove + music）→ 1 条，
  新模块登记 2 条（`lib.script.gsvmove` 的语音包状态查询与 `lib.script.music`
  的延迟导入），净减 2——**这是面板第一次真正摘掉产品包耦合**。面板只剩
  `lib.script.gsvmove`（`_refresh_voice_package_ui` 的语音包状态查询）。
- 测试补丁目标改到真正的所有者：`tests/test_workbench_lazy_settings.py` 的
  `_save_general_config` / `_apply_general_runtime`、`tests/test_ai_settings_reply_mode_sections.py`
  的 `save_ai_values` / `apply_ai_runtime` / `_save_general_config` / `_apply_general_runtime` /
  `get_compute_hub` 全部改打到 `ai_settings_config_store`。

**本轮没动的部分**

- 面板级设施（动画 / 淡出 / 悬浮标签页 / `_emit_info` / `_refresh_voice_package_ui` /
  `_apply_project_fonts` / `show_centered` / `create_workbench_page` / 鼠标与生命周期事件 /
  `_install_line_edit_context_menus` / 只读三页 / 更新页）仍在 `ai_settings_panel.py`。
  面板下一次瘦身需要改形态（把「面板外壳」整体做成 mixin 或独立的窗口基类），
  或转向批次 2 的档位 D 宿主方向，详见第 34 节的滚动清单。

## 55. 批次 3 第八轮：设置面板「面板外壳」切分（C 类，窗口生命周期 + 自绘边框）

更新时间：2026-10-09

前七轮把设置面板的业务面按 tab / 按职责切完后（第 48~54 节），`ai_settings_panel.py` 里
剩下的基本就是**外壳本身**：`__init__` 的窗口装配（窗口标志、图层注册、透明度动画、
`_build_ui` 调用链）、自绘 `paintEvent`、边框粒子效果的 `TICK` 订阅与随机取点、鼠标拖拽 /
上下文菜单 / 项目字体、悬浮标签栏与配置面板的布局转发、`show_centered` / `fade_out` 的显隐
动画，以及工作台挂载与四个只读页（AI / 更新 / 赞助作者 / 贡献列表）的装配。这一轮把这 51 个
方法整体切出，面板文件只剩组合 + 更新页转发。

**范围（`lib/script/ui/ai_settings_shell.py`，702 行）**

- **`AISettingsShellMixin`**：上述 51 个方法逐行搬出。外壳读写的面板状态（`_anim` /
  `_opacity` / `_tick_subscribed` / `_tab_floating` / `_stable_window_size` 等）仍由 `__init__`
  建立，因此混入的方法与 `AISettingsPanel` 实例状态完全同源，语义未变。
- **`_ui_thread_call = pyqtSignal(object)`** 随 `__init__`（信号连接点）一起搬到本模块——它只
  服务面板自身的 UI 线程调度。
- **窗口档位常量**`_WATERMARK_TEXT` / `_PANEL_SCALE` / `_TITLE_FONT_SIZE` / `_CONFIG_FONT_SIZE` /
  `_HINT_FONT_SIZE` / `_DROPDOWN_ITEM_FONT_SIZE` 随外壳搬走。
- **项目根解析**本页改用可注入的 `_shell_project_root`（面板覆盖为 `_project_root`），
  沿用第 51 / 53 / 54 节的做法。
- **`AISettingsPanel` 改为多继承 `AISettingsShellMixin`**（置首），
  `ai_settings_tabs.py` / `ai_settings_config_page.py` / `ai_settings_page.py` /
  `ai_settings_config_store.py` 的调用点零改动。

### 55.1 面板侧（`ai_settings_panel.py`：858 → 153 行）

- 删除 51 个已搬方法、随之孤立的注释与常量（`_WATERMARK_TEXT` / `_TITLE_FONT_SIZE` /
  `_CONFIG_FONT_SIZE` / `_DROPDOWN_ITEM_FONT_SIZE` / `_PANEL_SCALE` / `_GENERAL_HINT_TEXT` /
  `_HINT_FONT_SIZE`），并清掉 ruff 指出的 40 个失效导入。
- 面板现在只剩四件事：五个 mixin 的多继承、四个 `*_project_root` 钩子、更新页的九个转发方法
  （`_pet_update_actions` 一族）与 `_refresh_voice_package_ui`、以及贡献名单的三个模块级委托。
- **面板仍 `import PyQt5`**（`AISettingsPanel` 是 `QWidget` 子类本身），条目保留。

### 55.2 验收

- 纯移动对照：`AISettingsShellMixin` 的 51 个方法与 `HEAD` 逐行等价（差异仅
  `_project_root()` → `_shell_project_root()`）；面板保留的 11 个方法体与 `HEAD` 逐字节相同。
- 八页 oracle（离屏 1000×760）：控件树（类型 / 对象名 / 几何 / 可见性 / 文本 / 字体 / 内联样式）、
  `grab()` 位图 SHA-256、`_config_tab_meta` 的 `fields` / `defaults` 与面板 `styleSheet()`
  （9450 字符）**全部零差异**（对拍在独立 worktree 的 `HEAD` 版本上跑，并同步了被 `.gitignore`
  忽略的 `config/music/volume.json`）。
- 回归：`tests/test_workbench_settings_layout.py`、`tests/test_workbench_lazy_settings.py`、
  `tests/test_ai_settings_reply_mode_sections.py`、`tests/test_persona_storage.py`、
  `tests/test_update_uninstall_button.py`、`tests/test_workbench_embedded_pages.py`、全量
  `unittest` 2269 条通过（10 跳过）；`ruff check .` 归零；`compileall` 通过；
  `git diff --check` 干净；边界测试 52 条通过。

**清单变化**

- `frozen_ui_qt_importers`：48 → **49**（新增 `lib/script/ui/ai_settings_shell.py`）。
- `ui -> 产品包` 耦合清单不变：新模块不 import `chat` / `office` / `music` / `gsvmove`；
  面板的 `lib.script.gsvmove`（`_refresh_voice_package_ui` 的语音包状态查询）保留。
- `tests/test_visual_presenters.py` 的 `test_qt_panel_hosts_only_execute_shared_visuals`
  自绘宿主清单补上 `ai_settings_shell.py`——`paintEvent` 是自绘事实源的新落点。
- 测试侧改掉的三处导入：`tests/test_workbench_lazy_settings.py` 不再用
  `panel_module.QComboBox/QLabel/QLineEdit`（改直接 import 控件类），
  `tests/test_ai_settings_reply_mode_sections.py` 改用 `ai_settings_defaults.AI_DEFAULT_VALUES`。

**本轮之后的状态**

`ai_settings_panel.py` 从批次 3 开始时的 5439 行降到 153 行，六个模块（面板 + 外壳 + 主页面 +
配置存取 + 配置页 + 编辑器）各自职责单一。面板本体仍是 `QWidget` 子类，**去 Qt 的下一步不是
继续拆文件**（外壳的每个方法都要读写 `self` 上的窗口状态），而是把窗口壳层收进档位 D 的共享
宿主（`backends/qt/widgets/`）并让面板只声明式描述自己；或按第 34 节滚动清单转去批次 2 的
其他大文件（`forum_board.py` 1723 行、`voice_package_installer.py` 998 行等）。## 56. 批次 2 续：主论坛页「发帖屏」切到 `ui/forum_composer.py`（C 类，输入表单域）

更新时间：2026-10-09

第 47 节盘点把 `forum_board.py`（1726 行）归入 C 类：它不含 A 类可抽物，但页面本体是三个并列的屏（列表 / 详情 / 发帖）加上一套
服务回调与调度。第九轮先切输入面最重、与另两屏耦合最少的**发帖屏**：标题 / 正文 / 标签 / 图片的装配，正文编辑区的
一整套行内与段落操作（行内标记 / 字号 / 对齐 / 文字色 / 描边色 / 插入文本与插图），以及发布与字数计数器的联动。

**范围（`lib/script/ui/forum_composer.py`，642 行）**

- **`ForumComposerMixin`**：27 个方法逐行搬出。属于发帖屏的装配族（`_build_composer` 与 `_build_compose_tools` /
  `_build_compose_layout` / `_build_compose_colors`）、正文编辑操作族（`_body_text` / `_body_caret` / `_body_selection` /
  `_set_body_text` / `_on_body_changed` / `_toggle_body_format` / `_apply_body_size` / `_apply_body_align` / `_write_body_layout` /
  `_sync_layout_controls` / `_paragraph_layout_at` / `_toggle_body_color` / `_reapply_body_color` / `_sync_format_buttons` /
  `_sync_color_host` / `_insert_body_text`）、图片回收站（`_on_add_image` / `_remove_compose_image` / `_sync_compose_images`）与
  发布回调（`_on_publish_thread` / `_compose_error` / `_sync_thread_counter` / `_sync_composer`）。
- **`FORUM_SIZE_STEPS`** 随发帖屏搬走；`ForumColorControl` 在本模块以 `_ForumColorControl` 别名导入后再导出。
- **与看板的边界**：发帖屏只向外调 `show_list`（留在看板）；看板侧调本簇内的是 `_build_ui` / `_build_detail` / `open_composer` /
  `on_thread_posted` / `on_image_uploaded` / `on_image_error` / `_sync_session`，多继承后均零改动。发帖屏依赖的看板状态
  （`_compose_images` / `_thumb_data` / `_state` 等）仍由 `ForumBoardPage.__init__` 建立，混入方法与之同源。
- **`ForumBoardPage` 改为多继承 `ForumComposerMixin`**（置首），`forum_window.py` / `forum_account.py` 与测试的调用点零改动。

### 56.1 看板侧（`forum_board.py`：1726 → 1150 行）

- 删除 27 个已搬方法与随之孤立的小节注释；看板只留列表 / 详情 / 会话 / 服务回调与 `_dispatch` 一族。
- 清掉随之下沉的 20 个失效导入（`Path`、`QComboBox` / `QFileDialog` / `QPlainTextEdit`、`apply_color_tokens`、
  `forum_layout` 一族、`FORMAT_BY_KEY` / `FORUM_MARKUP_FORMATS` / `span_at_cursor` / `toggle` 等，由 ruff 指出）；
  `QTextCursor` / `QEvent` / `QSize` / `QPixmap` / `QSizePolicy` 仍作为既有导入面保留。
- `FORUM_SIZE_STEPS` 改为从新模块 import 并**按原名重新导出**（`tests/test_forum_layout_ui.py`、
  `tests/test_forum_board_ui.py` 依赖 `forum_board.FORUM_SIZE_STEPS`）。
- 看板仍 `import PyQt5`（`ForumBoardPage` 是 `QWidget` 子类），条目保留。

### 56.2 验收

- 纯移动对照：`ForumComposerMixin` 的 27 个方法与 `HEAD` **逐字节相同**（缩进也未变）；看板保留的 61 个方法也逐字节相同，
  差异只有类声明的多继承、`FORUM_SIZE_STEPS` 的导入 / 重新导出与清理掉的无用导入。
- 页面 oracle（离屏 1000×760，五个阶段：列表加载中 / 列表 / 详情 / 发帖空白 / 发帖已填写）：控件树（类型 / 对象名 / 几何 /
  可见性 / 文本 / 字体 / 内联样式，每阶段 109–160 个节点）与 `grab()` 位图 SHA-256 **全部零差异**
  （对拍在独立 worktree 的 `HEAD` 版本上跑，并同步了被 `.gitignore` 忽略的 `config/music/volume.json`）。
- 回归：`tests/test_forum_board_ui.py`（文件选择框 patch 改到真正的所有者 `forum_composer`）、`tests/test_forum_layout_ui.py`、
  `tests/test_forum_community_window.py` 全绿；全量 `unittest` 2269 条通过（10 跳过）；`ruff check .` 归零；`compileall` 通过；
  `git diff --check` 干净；边界测试 52 条通过。

**清单变化**

- `frozen_ui_qt_importers`：49 → **50**（新增 `lib/script/ui/forum_composer.py`，按第 34.2 节 C 类规则登记）。
- `ui -> 产品包` 耦合清单不变：新模块不 import `chat` / `office` / `music` / `gsvmove`。
- `tests/test_forum_board_ui.py` 的 `forum_board.QFileDialog` patch 改打到 `forum_composer`（文件选择框的真正所有者），
  与第 49 / 52 / 53 / 54 / 55 节同一处理方式。

**本轮之后的状态**

`forum_board.py` 从 1726 行降到 1150 行：列表 / 详情 / 发帖三屏中，发帖屏已独立成模块，剩下两屏与服务回调仍在看板。
下一轮可继续按屏拆（列表屏或详情屏），也可按第 34 节滚动清单转向其他大文件（`voice_package_installer.py` 999 行、
`bug_tracker_window.py` 984 行、`workbench_window.py` 983 行等）。

## 57. 批次 3 后续：主论坛「账号页区块」切到 `ui/forum_account_sections.py`（C 类）

记录时间：2026-10-09

第 56 节把发帖屏切走后，主论坛只剩「列表 / 详情 / 账号」三块。本轮先处理账号页：它的 8 个
装配方法只被 `ForumAccountPage` 一处使用，属 §34.2 的 C 类切分。

**范围：**

- `lib/script/ui/forum_account_sections.py`（327 行）；`forum_account.py`：613 → 334 行。

- **`ForumAccountSectionsMixin`**：`_build_ui` / `_card` / `_field` / `_build_login_card` / `_build_profile_card` /
  `_build_activity_card` / `_build_data_card` / `_build_server_card` 共 8 个方法，以及 `_font` 与
  `_FORM_HINT_LOGIN` / `_FORM_HINT_REGISTER` 字段提示常量。

- 方法体与前一份**逐字节相同**；`forum_account.py` 的 `ForumAccountPage` 改为
  `ForumAccountPage(ForumAccountSectionsMixin, QWidget)`，只留账号页回调，既有导入面不变。

### 57.1 验证

- 纯移动对照：8 个方法与 `_font` 对 `HEAD` 逐字节相同，字段提示常量值一致。

- 边界套件 `tests.test_qt_dependency_boundaries tests.test_code_structure_boundaries` = 52 项通过。

**清单变化**

- `frozen_ui_qt_importers`：50 → **51**（新增 `lib/script/ui/forum_account_sections.py`）。

**本轮之后的状态**

`forum_account.py` 只剩账号页回调，装配区块已外移。

## 58. 批次 3 后续：主论坛「详情屏」切到 `ui/forum_detail.py`（C 类）

记录时间：2026-10-09

接第 57 节。详情屏 `_build_detail`（回复列表 + 正文 + 楼主信息 + 回复框 + 回复区）是一整段骨架，
只被看板一处调用，同样按 C 类切出。

**范围：**

- `lib/script/ui/forum_detail.py`（187 行）。

- **`ForumDetailMixin._build_detail`**：整个方法体随类一起搬（该 mixin 只有这一个方法）。

- 方法体与前一份**逐字节相同**；`forum_board.py` 用 `from ... import ForumDetailMixin as _ForumDetailMixin`
  保持原别名导入面，`ForumBoardPage` 只是多继承本 mixin。

### 58.1 验证

- 纯移动对照：`_build_detail` 对 `HEAD` 逐字节相同。

- 回归：`tests/test_forum_board_ui.py` 新增 `test_the_detail_screen_lives_in_its_own_module`（断言
  `ForumBoardPage` 多继承 `ForumDetailMixin` 且 `_build_detail` 同源），原「列表 + 详情」用例改为只覆盖列表与调度。

**清单变化**

- `frozen_ui_qt_importers`：51 → **52**（新增 `lib/script/ui/forum_detail.py`）。

- `tests/test_forum_board_ui.py` 新增该拆分的同源断言（沿用第 56 节做法）。

**本轮之后的状态**

`forum_board.py` 的详情屏已外移，剩余列表 / 工具栏留在本体。

## 59. 批次 3 后续：主论坛窗口「整页外壳」切到 `ui/forum_wall.py` + `ui/forum_wall_layout.py`（C 类）

记录时间：2026-10-09

这一步的宿主是 `forum_window.py`（第 58 节后 976 行）：它既是 `ForumWallMixin` 的宿主，也是长直播
浮窗的重导出汇合点。把整页外壳切走后，为避免 `forum_wall` 与 `forum_window` 互相 import 常量，
把纯规格常量再下沉一个叶子模块。

**范围：**

- `lib/script/ui/forum_wall.py`（427 行）；`lib/script/ui/forum_wall_layout.py`（无 Qt，71 行）；
`forum_window.py`：976 → 593 行。

- **`ForumWallMixin`**（12 个方法）：`_build_ui` / `_build_header` / `_build_nav` / `_build_wall` /
  `_build_composer` / `_sync_input_colors` 六个装配方法，以及 `page` / `set_page` / `_refresh_page` /
  `_sync_subtitle` / `_open_post_in_board` / `_on_session_changed` 六个导航与回调。

- **`forum_wall_layout.py`** 只放 `COLUMN_COUNT` / `COLUMN_SPACING` / `WALL_MARGIN` / `SCROLL_GUTTER` /
  `SCROLL_GAP` / `LOAD_OLDER_THRESHOLD_PX` 等 15 个布局与文案常量，不 `import PyQt5`。

- `ForumWallMixin` 方法体与前一份**逐字节相同**；`forum_window.py` 以 `from ... import ForumWallMixin`
  与常量再导出保持导入面。

### 59.1 验证

- 纯移动对照：`ForumWallMixin` 的 12 个方法对 `HEAD` 逐字节相同，常量值一致。

- 边界套件 52 项通过；`forum_wall_layout` 无 Qt，按 §34.2 规则**不登记** `frozen_ui_qt_importers`。

**清单变化**

- `frozen_ui_qt_importers`：52 → **53**（新增 `lib/script/ui/forum_wall.py`；`forum_wall_layout.py` 因无 Qt 不入册）。

- `ui -> 产品包` 耦合清单不变。

**本轮之后的状态**

`forum_window.py`：976 → 593 行，只剩窗口生命周期、卡片与控制器；页面外壳已外移。

## 60. 批次 3 后续：`bug_tracker_window.py` 的查询 / 渲染模型切到 `ui/bug_tracker_data.py`（C 类）

记录时间：2026-10-09

`BugTrackerWindow` 里有一组「只读 `self._records` / `self._instance_filter` 等窗口状态、不碰 Qt 控件」
的筛选与文本渲染方法，是典型的可切控制器层。取色改走 `_level_color(name)` 钩子，避免复制主题全局。

**范围：**

- `lib/script/ui/bug_tracker_data.py`（109 行）。

- **`BugTrackerDataMixin`**：`_filtered_records` / `_record_matches_level_filters` / `_module_key` /
  `_format_when` / `_level_name` / `_render_detail` / `_selected_record` 等 9 个查询与渲染方法。

- `_color_for_level` 不再直接引用模块级主题全局，改为 `self._level_color("_DANGER")` 钩子；钩子由留在
  窗口本体的 `_level_color` 实现。`HEAD` 写法是 `QColor(_DANGER)`（拷贝一份），现在返回全局同一实例；
  两者取值等价（都是主题色），仅对象身份从「拷贝」变为「同一」。

- `bug_tracker_window.py` 反向 import 该 mixin，导入面不变。

### 60.1 验证

- 纯移动对照：9 个方法对 `HEAD` 逐字节相同（仅 `_color_for_level` 一处按上文改写为钩子调用）。

- 回归：`tests.test_workbench_embedded_pages` / `test_workbench_page_registry` / `test_workbench_window` /
  `test_lifecycle_contracts` 全绿；边界套件 52 项通过。

**清单变化**

- `frozen_ui_qt_importers`：53 → **54**（新增 `lib/script/ui/bug_tracker_data.py`）。

**本轮之后的状态**

`bug_tracker_window.py`：983 → 839 行，只剩窗口骨架、自绘与回调。

## 61. 批次 3 后续：语音包安装器「按钮 / 横幅控件」切到 `ui/voice_package_widgets.py`（C 类）

记录时间：2026-10-09

`voice_package_installer.py` 里两个自成一体的小控件（驱动下拉框、安装横幅）及其尺寸 / 配色常量、
字节格式化助手，是干净的控件族。

**范围：**

- `lib/script/ui/voice_package_widgets.py`（206 行）。

- **`_VoiceDriveComboBox`** / **`VoicePackageInstallBanner`** 两个控件类，及 `_WIDTH` / `_HEIGHT` /
  `_LAYER` / `_BORDER` 常量与 `_color` / `_format_bytes` 助手。

- 类体与助手对 `HEAD` **逐字节相同**；`voice_package_installer.py` 反向 import 保留导入面。

### 61.1 验证

- 纯移动对照：两个控件类与助手对 `HEAD` 逐字节相同。

- 边界套件 52 项通过。

**清单变化**

- `frozen_ui_qt_importers`：54 → **55**（新增 `lib/script/ui/voice_package_widgets.py`）。

- `ui -> 产品包` 耦合清单**新增** `(lib/script/ui/voice_package_widgets.py, lib.script.gsvmove.package_manager)`：
  新模块随控件带走了对语音包管理器的 import。

**本轮之后的状态**

`voice_package_installer.py`：998 → 853 行。

## 62. 批次 3 后续：工作台「自绘控件族」切到 `ui/workbench_widgets.py`（C 类）

记录时间：2026-10-09

`workbench_window.py` 中两个独立自绘控件与窗口主体无耦合，是标准 C 类切分。

**范围：**

- `lib/script/ui/workbench_widgets.py`（97 行）。

- **`_WorkbenchFadeOverlay`** / **`_WorkbenchThemeToggle`** 两个控件类整体外移，类体逐字节相同；
  `workbench_window.py` 反向 import 保留导入面。

### 62.1 验证

- 纯移动对照：两个类体对 `HEAD` 逐字节相同。

- 回归：`tests.test_workbench_window` / `test_workbench_embedded_pages` 全绿；边界套件 52 项通过。

**清单变化**

- `frozen_ui_qt_importers`：55 → **56**（新增 `lib/script/ui/workbench_widgets.py`）。

**本轮之后的状态**

`workbench_window.py`：982 → 917 行。

## 63. 批次 3 后续：播放列表「队列按钮」切到 `ui/playlist_queue_buttons.py`（C 类）

记录时间：2026-10-09

`playlist_panel.py` 有两个队列专用按钮与一段布局常量，和面板主体无耦合。

**范围：**

- `lib/script/ui/playlist_queue_buttons.py`（173 行）。

- **`_QueueRemoveButton`** / **`_QueuePlayNowButton`** 及 `_WIDTH` / `_HEIGHT` / `_GAP` / `_PAD_X` /
  `_LAYER` / `_BORDER` 等布局常量整体外移，类体逐字节相同；面板反向 import 保留导入面。

### 63.1 验证

- 纯移动对照：两个按钮类对 `HEAD` 逐字节相同。

- 边界套件 52 项通过。

**清单变化**

- `frozen_ui_qt_importers`：56 → **57**（新增 `lib/script/ui/playlist_queue_buttons.py`）。

**本轮之后的状态**

`playlist_panel.py`：966 → 849 行。

## 64. 批次 3 后续：CMD 窗口「无 Qt 助手 + 标题栏按钮」切到 `ui/cmd_window_widgets.py`（C 类）

记录时间：2026-10-09

`cmd_window.py` 顶层有一组纯函数（十六进制、ANSI 剥离、边缘命中测试）与两个标题栏按钮控件，
不依赖窗口主体。

**范围：**

- `lib/script/ui/cmd_window_widgets.py`（170 行）。

- **`_hex` / `_strip_ansi` / `_hit_edge`** 与 `_EDGE_CURSORS`、事件载体 `_StreamLineEvent` /
  `_StreamDoneEvent`、控件 `_TitleButton` / `_CloseButton` 一并外移，逐字节相同。

- `ruff.toml` 为新模块加一条 per-file ignore `E701`（延续 `cmd_window.py` 的紧凑条件写法）。

### 64.1 验证

- 纯移动对照：上述函数与类对 `HEAD` 逐字节相同。

- 边界套件 52 项通过。

**清单变化**

- `frozen_ui_qt_importers`：57 → **58**（新增 `lib/script/ui/cmd_window_widgets.py`）。

- `ruff.toml` 新增 `lib/script/ui/cmd_window_widgets.py` 的 `E701` per-file ignore。

**本轮之后的状态**

`cmd_window.py`：802 → 687 行。

## 65. 批次 3 后续：主论坛「正文渲染块」切到 `ui/forum_body.py`（C 类）

记录时间：2026-10-09

详情屏切出后（第 58 节），正文富文本渲染仍留在 `forum_board.py`。这一族只读帖子正文与缩略图数据。

**范围：**

- `lib/script/ui/forum_body.py`（283 行）。

- **`ForumBodyMixin`**（7 个方法）：`_render_body` / `_add_body_source` / `_add_body_image` / `_add_body_label` /
  `_block_needs_rich` / `_add_body_rich` / `_add_body_images`，及 `_BLOCK_FONT_DEFAULT` /
  `_HEADING_FONT_SIZES` 常量。

- 方法体对 `HEAD` **逐字节相同**；`forum_board.py` 以 mixin 别名再导出。

### 65.1 验证

- 纯移动对照：7 个方法与两个常量对 `HEAD` 逐字节相同。

- 回归：`tests/test_forum_board_ui.py` 全绿；边界套件 52 项通过。

**清单变化**

- `frozen_ui_qt_importers`：58 → **59**（新增 `lib/script/ui/forum_body.py`）。

- `ui -> 产品包` 耦合清单不变。

**本轮之后的状态**

`forum_board.py` 只剩列表 / 工具栏，正文渲染已外移。

## 66. 批次 3 后续：游戏管理窗口「卡片控件」切到 `ui/game_manager_widgets.py`（C 类）

记录时间：2026-10-09

`game_manager_window.py` 的 `_GameCardWidget` 是独立卡片控件，切出后管理窗口本体只留布局与回调。

**范围：**

- `lib/script/ui/game_manager_widgets.py`（85 行）。

- **`_GameCardWidget`** 整体外移，类体逐字节相同；`game_manager_window.py` 反向 import 保留导入面。

### 66.1 验证

- 纯移动对照：`_GameCardWidget` 对 `HEAD` 逐字节相同。

- 边界套件 52 项通过。

**清单变化**

- `frozen_ui_qt_importers`：59 → **60**（新增 `lib/script/ui/game_manager_widgets.py`）。

**本轮之后的状态**

`game_manager_window.py`：755 → 697 行。

## 67. 批次 3 后续（本次续作第 1 轮）：扬声器「按钮基类 + 无 Qt 助手」切到 `ui/speaker_button_shell.py`

记录时间：2026-10-10

`speaker_control_buttons.py` 有 15 个按钮类 + 尺寸常量 + 9 个无 Qt 助手。档位 3 迁移后的按钮基类
`SpeakerControlButton`（共享悬停 / 按下 / 淡入淡出 / 指针命中状态机）与其依赖的助手是一整族，先切这层。
注意：本模块**不** `import PyQt5`，按 §34.2 规则不登记 `frozen_ui_qt_importers`。

**范围：**

- `lib/script/ui/speaker_button_shell.py`（346 行）；`speaker_control_buttons.py`：1048 → 746 行。

- **`SpeakerControlButton`**（35 个方法）与 `_font_spec` / `_safe_music_service` / `_music_is_playing` /
  `_music_login_snapshot` / `_music_provider_mode_label` / `_music_play_mode` / `_music_volume_percent` /
  `_publish_volume_bubble` / `_anchor_point_of` 共 9 个助手。

- 尺寸常量 `_BTN_WIDTH` / `_BTN_HEIGHT` / `_BTN_PLAYLIST_W` / `_SEARCH_DIALOG_W` / `_SEARCH_DIALOG_H` /
  `_VOLUME_SLIDER_GAP` / `_BAND_SLIDER_GAP` 与 `_BUTTON_LAYER` / `_FADE_MS` / `_UI_OPACITY_SCALE`
  随基类下沉；由 `speaker_control_buttons.py` 以 `from ... import ...  # noqa: F401` 再导出，
  `playlist_panel.py` 的 `_BTN_*` 导入面与 `lib/script/ui/__init__.py` 的 `SpeakerControlButton`
  懒导出均不变（后者仍指向本模块）。

- `tests/test_control_layer_descriptions.py` 的两处「文件 → 类」登记改指新模块。

### 67.1 验证

- 纯移动对照：`SpeakerControlButton` 的 35 个方法与 9 个助手对 `HEAD` 逐字节相同；常量文本一致。

- 回归：`tests.test_control_layer_descriptions` / `test_render_layout_algorithms` / `test_speaker_band_ui` /
  `test_speaker_volume_slider` 全绿；边界套件 52 项通过。

**清单变化**

- `frozen_ui_qt_importers` 计数**不变**（本模块无 `import PyQt5`，按 §34.2 不入册）。

- `ui -> 产品包` 耦合清单**新增** `(lib/script/ui/speaker_button_shell.py, lib.script.music)`。

- `tests/test_control_layer_descriptions.py`：`_MIGRATED_UI_CONTROLS` 与 `migrated` 两处 `SpeakerControlButton`
  改指 `speaker_button_shell.py`。

**本轮之后的状态**

`speaker_control_buttons.py` 只剩 14 个具体按钮类与 `SpeakerControlButtons` 装配器（746 行）。

## 68. 批次 3 后续（本次续作第 2 轮）：Bug tracker「水印覆盖层」切到 `ui/bug_tracker_widgets.py`（C 类）

记录时间：2026-10-10

接第 60 节，`bug_tracker_window.py` 剩下的自绘水印覆盖层是独立子控件（只读宿主 `_watermark_*` 文本并绘制，
不接事件），适合整类外移。

**范围：**

- `lib/script/ui/bug_tracker_widgets.py`（99 行）；`bug_tracker_window.py`：983 → 839 行。

- **`_BugTrackerWatermarkOverlay`** 整体外移，类体逐字节相同。

- `bug_tracker_window.py` 在既有 import 块中反向 import 该类，`self._watermark_overlay` 挂载点不变。

### 68.1 验证

- 纯移动对照：`_BugTrackerWatermarkOverlay` 类体对 `HEAD` 逐字节相同（两版均 77 行）。

- 回归：`tests.test_workbench_embedded_pages` / `test_workbench_page_registry` / `test_workbench_window` /
  `test_lifecycle_contracts` 全绿；边界套件 52 项通过。

**清单变化**

- `frozen_ui_qt_importers`：60 → **61**（新增 `lib/script/ui/bug_tracker_widgets.py`）。

- `ui -> 产品包` 耦合清单不变。

**本轮之后的状态**

`bug_tracker_window.py` 只剩窗口骨架、自绘面板与回调。

## 69. 批次 3 后续（本次续作第 3 轮）：游戏运行时「承载窗口 + 几何助手」切到 `ui/game_runtime_panel.py`（C 类）

记录时间：2026-10-10

`game_runtime.py` 由「可缩放等比承载窗口 `GameRuntimePanel` + 控制器 `GameRuntime`」组成。承载窗口与
两个纯几何函数（`centered_aspect_rect` / `aspect_resize_geometry`）自成一体，先切这层。

**范围：**

- `lib/script/ui/game_runtime_panel.py`（407 行）；`game_runtime.py`：704 → 323 行。

- **`GameRuntimePanel`**（可缩放、等比、自绘边框的宿主窗口）与 `centered_aspect_rect` /
  `aspect_resize_geometry` 一并外移。

- `game_runtime.py` 反向 import 这三个名字，`tests/test_game_runtime_geometry.py` 的既有导入面不变；
  `GameRuntime` 控制器本体未改（逐字节相同）。

### 69.1 验证

- 纯移动对照：`GameRuntimePanel` 类体与两个几何函数对 `HEAD` 逐字节相同；`GameRuntime` 控制器逐字节相同。

- 回归：`tests.test_game_runtime_geometry`（7 项）、`tests.test_control_layer_descriptions`（43 项）全绿；
  边界套件 52 项通过。

- 全量：`py -3 -m unittest discover -s tests -p "test_*.py" -q` = 2277 项通过（skipped=67）；`ruff check .` 全过。

**清单变化**

- `frozen_ui_qt_importers`：61 → **62**（新增 `lib/script/ui/game_runtime_panel.py`）。

- `ui -> 产品包` 耦合清单**不变**：`game_runtime.py` 仍列 `lib.script.music.service`，新模块不 import 产品包。

- `tests/test_qt_dependency_boundaries.py` 的 frozen 清单同步 +1。

**本轮之后的状态**

`game_runtime.py` 只剩 `GameRuntime` 控制器与 `get_game_runtime` / `cleanup_game_runtime` 单例入口。
  下一步候选：`ai_settings_page.py`（1179 行、单个 `_build_ui` 508 行）属**非纯移动**，须作独立方案变更轮处理，
  不在本续作范围内。

## 70. 批次 3 后续（本次续作第 4 轮）：AI 设置页「区块装配」切到 `ui/ai_settings_page_sections.py`（C 类，非纯移动）

记录时间：2026-10-10

第 69 节末尾把 `ai_settings_page.py` 列为「非纯移动、须独立方案变更轮」的下一步候选，本节即该轮。
`ai_settings_page.py` 的 `_build_ui` 是一个 508 行的顺序装配脚本：它按设置区块依次创建控件、
挂 `scaffold`、写 `self._xxx` 引用、并在区块间插入联动调用。这种脚本无法整体外移，只能**按区块切**。

**范围：**

- 新增 `lib/script/ui/ai_settings_page_sections.py`（531 行），装 `AISettingsPageSectionsMixin`。
- `lib/script/ui/ai_settings_page.py`：1179 → 728 行；`AISettingsPageMixin` 改为继承 `AISettingsPageSectionsMixin`。
- `_build_ui`：508 → 45 条语句（仅做 root/center_row/content_panel/scaffold/语音包横幅装配，
  然后按固定顺序调用 8 个区块方法，最后 `scaffold.finish()` + 动作条 + 标签页接入）。
- 外移的 8 个方法：`_build_reply_mode_section`、`_build_welfare_section`、`_build_manual_api_section`、
  `_build_ollama_section`、`_build_generation_section`、`_build_reply_mode_sync`、`_build_voice_section`、
  `_build_memory_section`。
- **`_build_reply_mode_sync` 是唯一新增的「非区块」方法**：它只是原 `_build_ui` 中夹在「生成参数」区块末尾与
  「语音合成」区块之间的两行联动（`self._force_mode.currentIndexChanged.connect(self._update_reply_mode_sections)`
  ＋首次同步调用）。把它单列成方法，是为了让切分后 `_build_ui` 的调用序列仍能**逐语句覆盖原脚本**，
  不因「区块方法之间夹着散装语句」而破坏顺序等价——该调用必须保持夹在 `_build_generation_section`
  与 `_build_voice_section` 之间。
- 区块方法只接收 `scaffold`，其余状态照旧写回 `self`（仍属「控件层」）；它们不改任何
  取值/保存逻辑，本轮不做去 Qt 化，只把「一个巨型方法」变成「可按区块独立迁移的 8 个方法」。

### 70.1 验证

- **语句序列 oracle（非纯移动的等价判据）**：把 `HEAD` 版本的 `_build_ui` 与新版本
  「`_build_ui` 主体 + 8 个区块方法体」按调用点内联展开，做 AST 扁平化后逐条比对，
  236 条语句**顺序与内容完全一致**（两条独立实现各跑一次，均 `mismatches: 0`）。
  比对时只剔除 8 个方法各自新增的 docstring，其余语句零差异。
- **结构性页面 oracle**：`AISettingsPanel(lazy_workbench_pages=True)`（stub 掉 `get_voice_package_status`
  与 `_refresh_hardware_watermark_async`）下，按 objectName 分组的 `QLabel`/`QCheckBox`/`QLineEdit`/
  `QPushButton`/`QComboBox` 计数、`SettingsSectionTitle`/`SettingsSectionDescription`（7）、
  `ConfigFormLabel`（33）、`VoicePackageBannerDetail` 等关键控件**改前改后逐一相同**。
- **回归**：`tests.test_qt_dependency_boundaries`、`tests.test_code_structure_boundaries`、
  `tests.test_workbench_settings_layout`、`tests.test_ai_settings_reply_mode_sections`、
  `tests.test_workbench_lazy_settings`、`tests.test_persona_storage`、`tests.test_update_uninstall_button`
  共 110 项通过。
- 全量：`py -3 -m unittest discover -s tests -p "test_*.py" -q` = 2277 项通过（skipped=67）；
  `ruff check .` 全过。

**清单变化**

- `frozen_ui_qt_importers`：62 → **63**（新增 `lib/script/ui/ai_settings_page_sections.py`，直接 import PyQt5 控件）。
- `ui -> 产品包` 耦合清单**不变**：sections 模块不 import `lib.script.{chat,office,music,gsvmove}` 产品包；
  页面对产品包的既有耦合仍留在 `ai_settings_page.py`。
- `tests/test_workbench_settings_layout.py` 的 AI 页面用例补「8 个区块方法与 sections mixin 同源」断言。

**本轮之后的状态**

`ai_settings_page.py` 只剩页面骨架、取值/校验/保存动作与产品包交互；装配脚本已按区块下沉，
后续可**逐区块**继续迁移（每个区块方法已是独立可搬单元）。仍未去 Qt：sections 与页面本体都直接
构造 PyQt5 控件，去 Qt 化须等控件层替换方案（第 34 节四个批次）继续推进。
