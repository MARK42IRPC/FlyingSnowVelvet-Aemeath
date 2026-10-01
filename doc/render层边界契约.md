# Render 层边界契约

更新时间：2026-10-01

本文档定义 `lib/core/render/` 的目标结构与依赖边界。它不是阶段计划，而是结构改建完成后必须成立的规则。

**状态：结构搬迁已按第 8 节执行，`drawing` / `runtime` 切分仍待进行。** 目录与引用规则以本文档为准；改建前的事实源是 [Qt 边界契约](Qt边界契约.md) 与 [跨后端视觉表现契约](视觉表现契约.md)，那两份文档继续负责“哪些内容算视觉逻辑”和“什么算无 Qt”。

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
  contract.py            后端中立协议：窗口宿主、能力声明、字体度量、屏幕与输入请求
  router.py              按配置选择后端并装配；跨后端判定的唯一位置
  registry.py            后端注册表与描述符
  visuals/               现 lib/core/render/visuals 内容，原样迁入并重新导出
  backends/
    base.py              后端共同接口
    qt/
      drawing/           命令到 QPainter 的执行
      runtime/           窗口、输入、调度、字体度量、屏幕、托盘、播放器
    dx/
      drawing/
      runtime/
    vulkan/              占位注册项，不提前建抽象
```

`visuals/` 是共享事实源，不是“Qt 的 visuals”。它必须能被 Qt、DX 和 Vulkan 同等引用，因此不得出现在任何单个后端子树之下。

Vulkan 在本文档生效时只是 `registry.py` 里的一条未启用描述符。没有第二个真实实现之前不为它设计抽象。

## 3. 三档规则

规则的粒度是档位，不是“整个后端不得被外部引用”。

**档位 A：绘制执行（`backends/*/drawing/`）**

- 只允许被 `router.py` 引用；
- 不得导入 `lib/script`；
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

一句话概括：**绘制实现不得共享，能力经协议共享。**

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
- `router.py` 不自行读取配置文件，而是消费 `BackendSelection`；一个进程只有一个后端生效，语义沿用现有 `lib/core/backend_router.py`。
- 用户或环境显式请求某个后端时，不接受静默回退；回退只发生在该后端初始化失败，且必须记录请求后端、实际后端与失败阶段。

## 6. 迁移顺序

顺序不能颠倒，否则规则无法断言。

1. 先切档位：把 `runtime` 从 `drawing` 中分出来，两个 bridge 内部先分目录，路径暂不变。
2. 再建 `contract.py`：把业务层对 `font`、`screen`、`text_metrics`、`window` 的需求收敛成协议，注入点放在组合入口。
3. 然后搬目录：`qt_bridge` / `dx_bridge` 移入 `backends/`，`graphics` 移入 `visuals/`，保持对外重新导出。
4. 最后改断言与文档：测试白名单、`doc/维护手册.md`、`doc/Qt边界契约.md`、本索引同步更新。

第 3 步会同时影响 55 个测试文件中的路径字符串与 `doc/维护手册.md` 的验证命令。搬动时保留一层重新导出会掩盖未完成的迁移，因此只在单次提交内使用，并在同一次提交结束时撤掉。

## 7. 验证

以下断言必须在改建后成立，实现位置随落地进度补充：

- `lib/core/render/backends/*/drawing/` 不得被 `router.py` 之外的模块导入；
- `lib/core/render/` 整体不得导入 `lib.script`，`visuals/` 还不得导入 `PyQt5` 或任一 `backends`；
- `lib/core/render/backends/*/runtime/` 不得被 `lib/script` 以具体路径导入，只允许经 `contract.py`；
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
