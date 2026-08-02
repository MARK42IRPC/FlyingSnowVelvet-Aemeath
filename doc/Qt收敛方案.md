# Qt 依赖收敛方案

更新时间：2026-08-03

本文档是 Qt 收敛工作的当前实施依据。目标是让业务模块和核心算法不再显式依赖 PyQt5，同时保留现有 Qt 界面作为第一个后端，未来可以替换为自研窗口和绘制后端。

## 1. 目标与边界

最终依赖方向：

```text
lib/script 与业务对象
        ↓
lib/core 的纯数据、算法和协议
        ↓
lib/core/qt_bridge
        ↓
PyQt5 / Windows
```

“不显式依赖 Qt”指业务模块和纯核心模块不能导入 `PyQt5`，也不能在公开数据、事件 payload、类型标注中暴露 `QPoint`、`QRect`、`QImage`、`QPixmap`、`QPainter`、`QTimer` 等 Qt 类型。Qt 后端内部可以使用这些对象。

工作台和复杂 QWidget 控件属于 UI toolkit 适配范围，不在第一轮复制一套控件抽象。第一轮优先处理桌宠运行时、几何、绘制、输入、调度和窗口宿主。

## 2. 目录职责

```text
lib/core/graphics/
  types.py       # Point、Size、Rect、Color、FontSpec 等值类型
  commands.py    # DrawRequest、RenderRequest、RenderItem
  scene.py       # 资源帧、当前帧、活跃请求和排序状态
  backend.py     # 绘制后端协议
  capture.py     # 屏幕截图后端协议

lib/core/timing/
  scheduler.py   # 周期计时器与调度后端协议

lib/core/application_runtime.py # 应用事件循环、退出和一次性调度协议
lib/core/pet_host.py            # 主宠窗口后端回调协议
lib/core/pet_movement_runtime.py # 主宠移动队列、插值、拖拽和状态协作

lib/core/qt_bridge/
  application_runtime.py # QApplication/QEvent/QTimer 生命周期适配
  draw_backend.py  # QImage/QPixmap/QPainter 缓存和绘制
  gif_loader.py    # GIF 帧到 QImage 的加载、缩放和翻转
  particle_system.py # Qt 粒子覆盖窗口和空间索引
  effect_system.py # Qt 特效覆盖窗口和图片缓存
  entity_widget.py # BaseEntity 的 QWidget 宿主与混合元类
  pet_widget.py    # 主宠 QWidget 原生事件与核心输入转换
  window_setup.py  # PetWindow 的 QWidget 初始化与首屏定位
  tray_icon.py     # QSystemTrayIcon 与托盘菜单宿主
  scheduler.py     # QTimer 调度适配
  event_pump.py    # Qt 跨线程事件泵适配
  screen_capture.py # 主屏幕截图与 PNG 编码
  widget_anchors.py # QWidget/QPoint 锚点兼容适配
  workbench_page.py # 可独立显示或嵌入工作台的 Qt 工具页共享宿主
```

`lib/core/graphics` 不得导入 Qt。`lib/core/qt_bridge` 是唯一允许集中导入 Qt 的核心适配包。旧模块可以短期保留兼容门面，但新增代码必须从 `graphics` 或明确的适配接口获取能力。

## 3. 稳定接口

### 3.1 几何值

使用不可变的 `Point`、`Size`、`Rect`、`Color` 和 `FontSpec`。坐标采用桌面坐标约定：x 向右为正，y 向下为正；颜色使用 8-bit RGBA；文字绘制只声明字体族、像素字号和粗体状态。边界转换和字体度量只发生在后端适配器中。

### 3.2 绘制场景

`DrawScene` 只管理：

- 资源 ID 到帧序列的映射；
- 当前帧和循环切换；
- 活跃 `DrawRequest`；
- layer、z、生成顺序；
- 请求的透明度、位置、缩放和翻转状态。

`DrawScene` 不负责图片格式转换、缩放、翻转或实际绘制。

### 3.3 绘制后端

`DrawBackend.render(scene, target, target_rect)` 消费场景，不反向修改业务状态。后端可以拥有资源转换缓存，但必须提供幂等 `cleanup()`。

Qt 后端负责：

- `QImage -> QPixmap` 转换；
- 缩放和水平翻转；
- `QPainter` 状态保存/恢复；
- Qt 几何对象转换。

未来自研后端只需实现同一协议，不应修改 `DrawScene` 或业务对象。

### 3.4 应用与主宠宿主

`ApplicationRuntime` 定义桌面应用创建、一次性调度、事件处理、退出请求、退出确认和事件循环；应用编排不得直接调用具体 toolkit 的静态计时器或事件类型。

`PetHostCallbacks` 定义主宠后端向业务控制层提交的渲染准备、鼠标、键盘、窗口移动和关闭回调。Qt 后端由 `QtPetWidget` 实现 QWidget 原生事件方法并转换为 `MouseInput`、`KeyboardInput` 和 `Point`；未来自研窗口后端应调用同一组回调，不在业务控制器中模拟 Qt 事件对象。

## 4. 分阶段计划

### 阶段一：数据契约

已完成。新增 `graphics/types.py` 和 `graphics/commands.py`，`RenderCore` 已不再导入 Qt，`render_layer.py` 只保留兼容导入。

### 阶段二：绘制状态与 Qt 后端拆分

已完成。新增 `DrawScene`、`DrawBackend` 和 `qt_bridge.draw_backend`。`DrawCore` 保留原公开方法，改为纯场景门面并懒加载 Qt 后端，确保既有调用方不需要一次性迁移。

验收：`draw_core.py`、`graphics/` 和业务请求模块不显式导入 Qt；现有 GIF 帧切换、排序、缩放、翻转和透明度行为不变。

### 阶段三：几何和输入

进行中。`movement_controller.py`、`pet_movement_queue.py`、物理边界提供器、锚点纯计算、鼠标/键盘核心事件、屏幕裁剪与线段碰撞算法、实体核心位置接口和主宠状态机已迁移到 `Point/Rect` 与纯输入载荷；键盘事件使用核心 `Key/KeyModifier`，Qt 原生键值只在 `qt_bridge.input` 转换，麦克风快捷键解析不再导入 Qt。命令框和关闭按钮的事件处理器也已改用核心 `MouseButton/Point`，不再直接导入 PyQt；需要保留 QPoint 状态的 QWidget 在事件接收边界统一调用 `qt_bridge.window.coerce_qpoint()`。鼠标穿透的共享状态也改为纯进程布尔状态，不再借用 `QApplication.property`。QWidget/QPoint 锚点方法已搬到 `qt_bridge.widget_anchors`，QApplication 屏幕查询已搬到 `qt_bridge.screen`；`anchor_utils` 和 `screen_utils` 只保留旧调用方的懒加载兼容门面。旧的 QPoint-like 输入仍由 `graphics.types.coerce_point()` 兼容解析。原 `get_position()/get_geometry()` Qt 兼容接口暂时保留，剩余 UI/对象调用将在下一步收敛到 `qt_bridge`。

验收：移动、拖拽、锚点、碰撞和游戏运行时几何测试覆盖纯数据路径。

### 阶段四：调度和事件泵

已完成。`EventPump` 已从 `EventCenter` 中抽出，Qt `QObject/pyqtSignal` 位于 `qt_bridge.event_pump`，事件中心支持注入 FakePump。事件处理器异常会记录事件类型、回调模块和限定名及完整堆栈，便于定位跨后端载荷边界错误。后台命令结果直接发布到线程安全的 `EventCenter`，Ollama 状态与流式回调通过纯 `CallbackDispatcher` 复用同一 EventPump 契约，不再自建 Qt 信号。`CallbackDispatcher` 必须在创建者线程绑定 EventPump，不得由首个后台任务延迟创建；否则 Qt 对象会归属无事件循环的 worker，导致后续完成回调丢失和请求状态永久 busy。`TimingManager`、Ollama 周期 ping、聊天流式刷新和自动陪伴不再直接依赖 `QTimer`，只依赖 `timing.scheduler.Scheduler` 创建可取消周期计时器；`ApplicationState` 和桌宠窗口在 Qt 桌面组合边界注入独占的 `qt_bridge.scheduler.QtScheduler`。聊天侧的 40ms 流式刷新与自动陪伴计时器在回调入口先停止，以保持 single-shot 语义。`LayerManager`、主宠保护检查和工具回忆重派发使用可注入的一次性调度函数，默认 Qt 实现同样位于 `qt_bridge.scheduler`。停止退出时通过幂等 `cleanup()` 退订事件并释放调度后端。

验收：FakeScheduler 覆盖启动、停止、改频、任务触发和暂停引用计数；独立子进程阻断 PyQt 导入后仍可导入并运行核心调度，Qt 后端另有真实事件循环行为测试。

### 阶段五：窗口和 UI

进行中。`BaseEntity` 已改为无 Qt 的纯 ABC，Qt QWidget 与 ABC 的混合宿主位于 `qt_bridge.entity_widget`；托盘、窗口初始化、粒子、特效和 GIF 后端也已搬入 `qt_bridge` 并保留旧路径兼容导出。粒子脚本及官方拉海洛粒子扩展已无显式 PyQt 导入：颜色和文字字体分别使用纯 `graphics.types.Color/FontSpec`，屏幕边界读取返回核心 `Rect`；`qt_bridge.particle_system` 在绘制入口转换 `QColor/QFont`、计算文字数值度量，粒子数据不再持有 Qt 颜色、字体或自定义 `QPainter` 回调。`PetWindow` 源码不再直接导入 PyQt，QPoint 转换、窗口移动、穿透旗标和 QPainter 绘制均委托给 `qt_bridge.window`，内部移动轨迹使用核心 `Point`。聊天截图改为注入纯 `graphics.capture.ScreenCapture`，Qt 主屏幕抓取和 PNG 编码位于 `qt_bridge.screen_capture`，业务事件与模型请求只传递 `bytes`。应用生命周期新增纯 `ApplicationRuntime` 协议，`ApplicationState` 的应用创建、single-shot、事件处理、退出确认、残留窗口关闭和事件循环均通过该协议调用；`QApplication/QEvent/QTimer` 实现集中在 `qt_bridge.application_runtime`，旧 `script.app.qt_runtime` 仅保留兼容导出。主宠 QWidget 原生 `paint/mouse/key/move/close` 事件已搬到 `qt_bridge.pet_widget.QtPetWidget`，`PetWindow` 只实现 `PetHostCallbacks` 的核心输入和渲染准备回调，Qt 几何兼容接口也由宿主提供。主宠移动队列、插值、拖拽和 moving/idle 状态协作已搬到可独立实例化的 `PetMovementRuntime`，事件中心和宿主副作用均通过注入提供；`MovementController` 使用纯 `MovementSettings`，阻断 PyQt 后仍可运行。`UI_ANCHOR_RESPONSE`、`ENTITY_POSITION_RESPONSE` 和实体状态响应中的几何字段已统一为核心 `Point`，Qt UI 只在消费边界转回 `QPoint`；对象管理器和主宠 UI 的生产调用已改用 `get_core_position()/get_core_geometry()`。故障跟踪与游戏包页面的独立窗口/工作台嵌入生命周期已收敛到 `qt_bridge.workbench_page.QtWorkbenchToolPage`，页面注册由 `script.workbench.builtin_pages` 单点维护，工作台只调用统一的 `refresh_workbench_page()`；嵌入页不再重新挂载其它入口持有的独立窗口。下一步继续拆分主宠状态机与绘制状态副作用，并将对象管理器中的图片加载、屏幕查询和 QWidget 创建迁到后端工厂。工作台和对话框仍保留 Qt UI toolkit 作为可替换边界内的一个实现。

验收：启动、退出、层级、透明窗口、托盘、工作台懒加载和设置保存行为保持不变。

## 5. 约束与禁止事项

- 不在 `graphics` 中增加 Qt 兼容类型或 Qt 条件导入。
- 不通过 `Any` 把 Qt 对象藏进公开协议；后端边界使用 `object` 或明确的协议类型。
- 不让纯核心模块调用 `QApplication.instance()`、`QTimer.singleShot()` 或 QWidget 方法。
- 不一次性迁移复杂 UI 控件；每个阶段都保留可运行的 Qt 后端。
- 旧兼容模块必须记录用途，调用方迁移完成后删除兼容导出。
- 新增接口必须有纯数据测试和至少一个 Qt 后端行为测试。

## 6. 验证与交接

每阶段至少运行：

```powershell
py -3 -m compileall -q lib tests
git diff --check
```

绘制阶段还需运行统一绘制排序、图形契约和桌宠窗口相关测试，并扫描：

```powershell
rg -n "PyQt5|QPoint|QRect|QImage|QPixmap|QPainter|QTimer|pyqtSignal" lib/core/graphics lib/core/draw_core.py lib/core/render_core.py
```

交接时说明：已完成阶段、稳定接口、验证命令、未迁移 Qt 边界、未提交的其他工作树改动。
