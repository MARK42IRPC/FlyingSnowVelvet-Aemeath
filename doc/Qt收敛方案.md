# Qt 依赖收敛方案

更新时间：2026-08-04

本文档是 Qt 收敛工作的当前实施依据。目标是让业务模块和核心算法不再显式依赖 PyQt5，同时保留现有 Qt 界面作为第一个后端，未来可以替换为自研窗口和绘制后端。

## 1. 目标与边界

最终依赖方向：

```text
后端无关业务/控制器 -> lib/core 的纯数据、算法、协议与服务门面
应用组合入口       -> lib/core/qt_bridge -> PyQt5 / Windows
Qt UI 实现         -> lib/core/qt_bridge + lib/core 公共契约
```

“不显式依赖 Qt”指后端无关业务模块和纯核心模块不能导入 `PyQt5` 或 `lib.core.qt_bridge`，也不能在公开数据、事件 payload、类型标注中暴露 `QPoint`、`QRect`、`QImage`、`QPixmap`、`QPainter`、`QTimer` 等 Qt 类型。应用组合入口和明确的 Qt UI 实现可以选择 Qt 后端。

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
lib/core/backend_router.py      # 后端目录、注册、启动选择与回退结果
lib/core/desktop_backend.py     # 绘制、事件泵、延迟、屏幕与截图服务注册
lib/core/pet_host.py            # 主宠窗口后端回调协议
lib/core/pet_movement_runtime.py # 主宠移动队列、插值、拖拽和状态协作
lib/core/world_objects.py       # 世界对象资源与创建后端门面

lib/core/qt_bridge/
  application_runtime.py # QApplication/QEvent/QTimer 生命周期适配
  desktop_backend.py # Qt 桌面服务的一次性组合注册
  colors.py        # 核心 Color 到 QColor 的主题适配
  draw_backend.py  # QImage/QPixmap/QPainter 缓存和绘制
  font.py          # 字体注册、QFont/QFontMetrics 与混排绘制
  gif_loader.py    # GIF 帧到 QImage 的加载、缩放和翻转
  particle_system.py # Qt 粒子覆盖窗口和空间索引
  effect_system.py # Qt 特效覆盖窗口和图片缓存
  entity_widget.py # BaseEntity 的 QWidget 宿主与混合元类
  pet_widget.py    # 主宠 QWidget 原生事件与核心输入转换
  pet_window.py    # 组合纯 PetWindow 控制器与 Qt QWidget 宿主
  pet_window_ui.py # 主宠拥有的 Qt 控件生命周期
  window_setup.py  # Qt 主宠窗口初始化与首屏定位
  tray_icon.py     # QSystemTrayIcon 与托盘菜单宿主
  scheduler.py     # QTimer 调度适配
  event_pump.py    # Qt 跨线程事件泵适配
  screen_capture.py # 主屏幕截图与 PNG 编码
  widget_anchors.py # QWidget/QPoint 锚点兼容适配
  workbench_page.py # 可独立显示或嵌入工作台的 Qt 工具页共享宿主
  world_object_assets.py # 世界对象图片/GIF 加载、缩放和翻转
  world_object_backend.py # 稳定对象 ID 到 Qt 窗口类型的注册
  world_object_factory.py # 世界对象 QWidget 类型解析、坐标转换和实例化
  world_objects/   # 摩托、闹钟、沙发、雪堆、雪球、雪豹和音响窗口
```

`config/` 与 `lib/core/qt_bridge` 之外的 `lib/core` 不得导入 Qt 或 `qt_bridge`。核心不再自动懒加载 Qt；桌面后端未配置时使用无窗口回退或明确报错。`lib/script/main.py` 是当前组合入口，只在这里注册 `configure_qt_desktop_backend()`，再通过 `backend_router.configure_selected_backend()` 应用用户选择。

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

### 3.5 桌面服务与世界对象

`backend_router.py` 保存稳定后端 ID `qt`、`directx`、`opengl`、`vulkan`，并以纯数据 `BackendSelection` 返回请求后端、实际后端、是否回退和原因。路由只调用组合入口注册的配置器，不导入任何具体后端。未注册、未实现或初始化失败的候选后端必须回退 Qt；Qt 自身初始化失败时直接终止启动，不能伪装成功或重复重试同一个配置器。

`desktop_backend.py` 注册绘制后端工厂、事件泵、一次性延迟、虚拟屏幕、按点屏幕和截图能力。核心调用方只读取这些服务，不导入具体后端。

`world_objects.py` 使用稳定对象 ID 和不透明图片句柄承接资源加载、尺寸查询、对象创建与核心几何查询。对象管理器只提交 `Point`、尺寸和构造数据，并通过门面取得 `Point/Rect`；Qt 后端负责将 `motor`、`clock`、`sofa`、`snow_pile`、`snowball`、`snow_leopard`、`speaker` 解析为具体 QWidget 类型，以及在适配边界调用图片和窗口原生几何接口。

`config/font_config.py` 只保存字号、字体路径、已注册字体族和文本分段；`qt_bridge.font` 才能创建 `QFont`、执行度量、裁剪、换行和绘制。

## 4. 分阶段计划

### 阶段一：数据契约

已完成。新增 `graphics/types.py` 和 `graphics/commands.py`，`RenderCore` 已不再导入 Qt，`render_layer.py` 只保留兼容导入。

### 阶段二：绘制状态与 Qt 后端拆分

已完成。新增 `DrawScene`、`DrawBackend` 和 `qt_bridge.draw_backend`。`DrawCore` 保留原公开方法并改为纯场景门面，从 `desktop_backend` 获取已注册工厂；未配置桌面后端时使用无窗口绘制回退，不反向导入 Qt。

验收：`draw_core.py`、`graphics/` 和业务请求模块不显式导入 Qt；现有 GIF 帧切换、排序、缩放、翻转和透明度行为不变。

### 阶段三：几何和输入

进行中。`movement_controller.py`、`pet_movement_queue.py`、物理边界提供器、锚点纯计算、鼠标/键盘核心事件、屏幕裁剪与线段碰撞算法、实体核心位置接口和主宠状态机已迁移到 `Point/Rect` 与纯输入载荷；键盘事件使用核心 `Key/KeyModifier`，Qt 原生键值只在 `qt_bridge.input` 转换，麦克风快捷键解析不再导入 Qt。命令框和关闭按钮的事件处理器也已改用核心 `MouseButton/Point`。鼠标穿透共享状态改为纯进程布尔状态。QWidget/QPoint 锚点方法位于 `qt_bridge.widget_anchors`，QApplication 屏幕查询位于 `qt_bridge.screen`；`anchor_utils` 和 `screen_utils` 通过已注册桌面服务工作，不再懒加载 Qt。旧的 QPoint-like 输入仍由 `graphics.types.coerce_point()` 兼容解析。原 `get_position()/get_geometry()` Qt 兼容接口只由 Qt 宿主提供，核心生产调用统一使用 `get_core_position()/get_core_geometry()`。

游戏运行时公开的拉海洛中部区域也已改为核心 `Rect`，主宠状态机不再兼容接收 `QRect`。验收：移动、拖拽、锚点、碰撞和游戏运行时几何测试覆盖纯数据路径。

### 阶段四：调度和事件泵

已完成。`EventPump` 已从 `EventCenter` 中抽出，Qt `QObject/pyqtSignal` 位于 `qt_bridge.event_pump`，事件中心支持注入 FakePump。事件处理器异常会记录事件类型、回调模块和限定名及完整堆栈，便于定位跨后端载荷边界错误。后台命令结果直接发布到线程安全的 `EventCenter`，Ollama 状态与流式回调通过纯 `CallbackDispatcher` 复用同一 EventPump 契约，不再自建 Qt 信号。`CallbackDispatcher` 必须在创建者线程绑定 EventPump，不得由首个后台任务延迟创建；否则 Qt 对象会归属无事件循环的 worker，导致后续完成回调丢失和请求状态永久 busy。`TimingManager`、Ollama 周期 ping、聊天流式刷新和自动陪伴不再直接依赖 `QTimer`，只依赖 `timing.scheduler.Scheduler` 创建可取消周期计时器；`ApplicationState` 和桌宠窗口在 Qt 桌面组合边界注入独占的 `qt_bridge.scheduler.QtScheduler`。聊天侧的 40ms 流式刷新与自动陪伴计时器在回调入口先停止，以保持 single-shot 语义。`LayerManager`、主宠保护检查和工具回忆重派发使用可注入的一次性调度函数，默认 Qt 实现同样位于 `qt_bridge.scheduler`。停止退出时通过幂等 `cleanup()` 退订事件并释放调度后端。

验收：FakeScheduler 覆盖启动、停止、改频、任务触发和暂停引用计数；独立子进程阻断 PyQt 导入后仍可导入并运行核心调度，Qt 后端另有真实事件循环行为测试。

### 阶段五：窗口和 UI

进行中，核心运行时的 Qt 反向依赖已清零。

- `BaseEntity` 和 `PetWindow` 是无 Qt 控制器；`QtPetWindow`、`QtPetWidget`、主宠控件创建、窗口移动、穿透和绘制位于 `qt_bridge`。
- 托盘、粒子、特效、GIF、屏幕截图、颜色和字体实现均位于 `qt_bridge`，旧核心兼容壳和 `script.app.qt_runtime` 已删除。
- 世界对象管理器只依赖 `world_objects` 和核心几何，使用稳定 ID 创建对象；七个 QWidget 窗口已移入 `qt_bridge/world_objects/`。
- 世界对象门面不再公开 `pixmap` 命名；对象管理器通过不透明图片句柄和显式尺寸工作，中心与矩形查询在 Qt 适配层转换为核心 `Point/Rect`。音响管理器不再拥有搜索和登录对话框生命周期。
- `effects/` 特效脚本和拉海洛技能规则只保存纯状态；图片加载、文字栅格化和技能头像缓存留在 Qt 窗口后端，`EFFECT_REQUEST` 会递归拒绝不透明后端对象。
- 无生产者的 `DRAW_RENDER` 事件及其 painter/target_rect 载荷已删除，主宠 Qt 宿主直接从核心绘制场景渲染。
- `LayerManager` 只在注册、注销或改层级后提交一次待处理排序，不再每帧强制调用 `SetWindowPos(HWND_TOPMOST)`。
- 工作台、对话框、游戏窗口和 Qt 媒体播放器仍是明确的 toolkit 实现；它们可以导入 Qt，但不得把 Qt 类型泄漏回核心协议和业务事件。
- 后端路由基础已完成；控制面板的 `UI.render_backend` 提供 Qt、DirectX、OpenGL 和 Vulkan 选择，设置仅在下次启动时生效。当前只有 Qt 标记为可用，其余候选保留用于接入验证，未注册时会记录原因并回退 Qt。

下一步按 `DX后端实现方案.md` 补齐资源、声明式绘制、窗口和桌面运行时契约，再通过现有路由接入 DirectX 后端；其他候选适配器在该契约经过验证后实施。

验收：启动、退出、层级、透明窗口、托盘、工作台懒加载和设置保存行为保持不变。

## 5. 约束与禁止事项

- 不在 `graphics` 中增加 Qt 兼容类型或 Qt 条件导入。
- 不通过 `Any` 把 Qt 对象藏进公开协议；后端边界使用 `object` 或明确的协议类型。
- 不让纯核心模块调用 `QApplication.instance()`、`QTimer.singleShot()` 或 QWidget 方法。
- 不让纯核心模块导入 `lib.core.qt_bridge`；后端选择只能发生在组合入口。
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
rg -n --glob "*.py" --glob "!lib/core/qt_bridge/**" "from PyQt5|import PyQt5|lib\.core\.qt_bridge" config lib/core
```

交接时说明：已完成阶段、稳定接口、验证命令、未迁移 Qt 边界、未提交的其他工作树改动。
