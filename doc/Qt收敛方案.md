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
  resources.py   # RasterFrame、ImageResource 纯 RGBA 资源
  commands.py    # sprite、文字、形状、裁剪/变换命令与 DrawBatch
  scene.py       # 资源 revision、当前帧、活跃请求和批次生成
  ordering.py    # layer、z、生成顺序排序
  backend.py     # 绘制后端协议
  capture.py     # 屏幕截图后端协议
  image_loader.py # 静态图/GIF 解码、纯资源缩放
  gif_loader.py  # GIF 到纯 RGBA ImageResource 的解码

lib/core/timing/
  scheduler.py   # 周期计时器与调度后端协议

lib/core/application_runtime.py # 应用事件循环、退出和一次性调度协议
lib/core/application_ui.py      # 应用级 UI 生命周期协议
lib/core/backend_router.py      # 后端目录、注册、启动选择与回退结果
lib/core/desktop_backend.py     # 当前桌面服务的不可变 bundle 与注册
lib/core/window_host.py         # 后端无关 WindowHost v1 与层级窗口宿主协议
lib/core/pet_host.py            # 主宠窗口后端回调与清理协议
lib/core/tray_host.py           # 后端无关托盘生命周期协议
lib/core/overlay_host.py        # 粒子/特效覆盖层生命周期协议
lib/core/pet_movement_runtime.py # 主宠移动队列、插值、拖拽和状态协作
lib/core/world_objects.py       # 世界对象资源与创建后端门面

lib/core/qt_bridge/
  application_runtime.py # QApplication/QEvent/QTimer 生命周期适配
  application_ui.py # Qt 字体、公告、预加载、CMD、提示和退出动画组合
  desktop_backend.py # Qt 桌面服务的一次性组合注册
  window_host.py   # QWidget/Win32 层级适配
  colors.py        # 核心 Color 到 QColor 的主题适配
  draw_backend.py  # QImage/QPixmap/QPainter 缓存和绘制
  render_core.py # 仅供 Qt 控件内部使用的 painter 回调队列
  font.py          # 字体注册、QFont/QFontMetrics 与混排绘制
  gif_loader.py    # RasterFrame 到 QImage 及 Qt 图片变换
  particle_system.py # Qt 粒子覆盖窗口和空间索引
  effect_system.py # Qt 特效覆盖窗口和图片缓存
  entity_widget.py # BaseEntity 的 QWidget 宿主与混合元类
  pet_widget.py    # 主宠 QWidget 原生事件与核心输入转换
  pet_window.py    # 组合纯 PetWindow 控制器与 Qt QWidget 宿主
  pet_window_ui.py # 主宠拥有的 Qt 控件生命周期
  window_setup.py  # Qt 主宠窗口初始化与首屏定位
  tray_host.py     # TrayHost 到 Qt signal/单例生命周期适配
  tray_icon.py     # QSystemTrayIcon 与托盘菜单宿主
  scheduler.py     # QTimer 调度适配
  event_pump.py    # Qt 跨线程事件泵适配
  screen_capture.py # 主屏幕截图与 PNG 编码
  widget_anchors.py # QWidget/QPoint 锚点兼容适配
  workbench_page.py # 可独立显示或嵌入工作台的 Qt 工具页共享宿主
  world_object_assets.py # ImageResource 到 Qt 图片的边界转换
  world_object_backend.py # 稳定对象 ID 到 Qt 窗口类型的注册
  world_object_factory.py # 世界对象 QWidget 类型解析、坐标转换和实例化
  world_objects/   # 摩托、闹钟、沙发、雪堆、雪球、雪豹和音响窗口
```

`config/` 与 `lib/core/qt_bridge` 之外的 `lib/core` 不得导入 Qt 或 `qt_bridge`。核心不再自动懒加载 Qt；桌面后端未配置时使用无窗口回退或明确报错。具体后端配置器由启动组合入口按用户选择注册，`lib/script/main.py` 只消费已经选定的 `DesktopBackendBundle`。

## 3. 稳定接口

### 3.1 几何值

使用不可变的 `Point`、`Size`、`Rect`、`Color` 和 `FontSpec`。坐标采用桌面坐标约定：x 向右为正，y 向下为正；颜色使用 8-bit RGBA；文字绘制只声明字体族、像素字号和粗体状态。边界转换和字体度量只发生在后端适配器中。

### 3.2 绘制场景

`DrawScene` 只管理：

- 资源 ID 到不可变 `ImageResource` 的映射及注册 revision；
- 当前帧和循环切换；
- 活跃 `DrawRequest`；
- layer、z、生成顺序；
- 请求的透明度、位置、缩放和翻转状态；
- 已解析帧、按顺序排列的不可变 `DrawBatch`；
- 当前注册资源的 `ResourceRevision` 快照，供后端确定性淘汰缓存。

`RasterFrame` 固定使用紧密排列的 RGBA8888 `bytes`，不保存 `QImage/QPixmap`。`DrawScene` 不负责 toolkit 图片转换、缩放、翻转或实际绘制。

`DrawBatch` 还可携带 `TextCommand`、`LineCommand`、`RectCommand`、`EllipseCommand`、`ClipPush/ClipPop` 和 `TransformPush/TransformPop`；这些命令只使用核心颜色、字体、矩形、点和六元素二维变换数据。

### 3.3 绘制后端

`DrawBackend.render(batch, target, viewport)` 消费不可变 `DrawBatch`，不读取或修改活动场景。`viewport` 使用核心 `Rect`，target 由具体后端宿主持有。后端可以按资源 ID、revision、帧号、尺寸和翻转状态缓存转换结果，但必须在 revision 变化时淘汰旧资源，并提供幂等 `cleanup()`。

Qt 后端负责：

- `QImage -> QPixmap` 转换；
- 缩放和水平翻转；
- `QPainter` 绘制 sprite、文字、线段、矩形和椭圆，并保存/恢复裁剪与变换状态；
- Qt 几何对象转换。

未来自研后端只需实现同一批次协议，不应读取 `DrawScene` 私有状态或修改业务对象。

### 3.4 应用与主宠宿主

`ApplicationRuntime` 定义桌面应用创建、一次性调度、事件处理、退出请求、退出确认和事件循环；应用编排不得直接调用具体 toolkit 的静态计时器或事件类型。

`ApplicationUiHost` 是应用级 UI 的粗粒度生命周期边界，按 prepare/start/begin-shutdown/stop/cleanup/finalize 阶段管理字体、公告、预加载器、CMD、提示面板、登录窗口初始化和退出动画。不得为每个 Qt 控件向 `ApplicationState` 增加一个工厂或 cleanup 回调，也不得从主编排器直接导入 `lib.script.ui`。

`PetHostCallbacks` 定义主宠后端向业务控制层提交的渲染准备、鼠标、键盘、窗口移动和关闭回调。Qt 后端由 `QtPetWidget` 实现 QWidget 原生事件方法并转换为 `MouseInput`、`KeyboardInput` 和 `Point`；未来自研窗口后端应调用同一组回调，不在业务控制器中模拟 Qt 事件对象。

`PetWindowHost.shutdown_host()` 是应用编排关闭主宠的唯一入口；`TrayHost` 封装退出/公告回调、初始化、预关闭和清理；`OverlayHost` 封装立即清空和完整清理。`ApplicationState` 不得访问 Qt signal、主宠 `_timing_manager` 或调用 `close()/deleteLater()`。Qt 侧由 `QtPetWindow`、`QtTrayHost`、`ParticleOverlay` 和 `EffectOverlay` 完成这些原生生命周期行为。

### 3.5 桌面服务与世界对象

`backend_router.py` 保存稳定后端 ID `qt`、`directx`、`opengl`、`vulkan`，并以纯数据 `BackendSelection` 返回请求后端、实际后端、是否回退和原因。路由只调用组合入口注册的配置器，不导入任何具体后端。未注册、未实现或初始化失败的候选后端必须回退 Qt；Qt 自身初始化失败时直接终止启动，不能伪装成功或重复重试同一个配置器。

`desktop_backend.py` 通过不可变 `DesktopBackendBundle` 原子注册绘制后端、`ApplicationRuntime`、`ApplicationUiHost`、`Scheduler`、`ScreenCapture`、主宠窗口、`OverlayHost` 工厂、`TrayHostFactory`、事件泵、一次性延迟、虚拟屏幕、按点屏幕、截图、`LayerWindowHostFactory` 和可选的 `WindowHostFactory`。核心调用方只读取当前 bundle 的服务，不导入具体后端；主宠接入和完整场景提交仍待继续迁移。

`window_host.LayerWindowHost` 是窗口排序所需的最小协议，稳定 identity 只用于注册和注销，原生堆叠 token 由 `stack_window()` 单独返回。`WindowHost` v1 在此基础上补齐显示、隐藏、关闭、`Point/Rect` 几何、DPI、屏幕、点击穿透、激活、鼠标捕获、重绘和幂等清理。`LayerManager` 只处理 `layer/z/order`、存活过滤和调度，不调用 QWidget 或 Win32 API。Qt 的弱引用、可见性、`raise_()`、`winId()` 和 `SetWindowPos` 均封装在 `qt_bridge.window_host`；桌面后端未配置时使用不执行窗口副作用的被动宿主。

`graphics.image_loader` 将静态图和 GIF 解码为 `ImageResource`，缩放也在后端无关层完成。`world_objects.py` 使用不可变 `WorldObjectRequest`、整数实例 ID 和 `WorldObjectInstance` 包装承接对象创建、状态、动作与核心几何查询；对象管理器只提交 `ImageResource`、`Point`、尺寸和纯构造选项。Qt 后端负责将 `motor`、`clock`、`sofa`、`snow_pile`、`snowball`、`snow_leopard`、`speaker` 解析为具体 QWidget 类型，以及在适配边界转换图片和窗口原生状态。

`config/font_config.py` 只保存字号、字体路径、已注册字体族和文本分段；`qt_bridge.font` 才能创建 `QFont`、执行度量、裁剪、换行和绘制。

## 4. 分阶段计划

### 阶段一：数据契约

已完成。新增 `graphics/types.py`、`resources.py` 和 `commands.py`。跨后端图形契约不再包含 `RenderItem/RenderRequest/PaintCallback`；仅供现有 Qt 游戏控件使用的回调队列已迁入 `qt_bridge.render_core`，旧 `render_layer.py` 已删除。

### 阶段二：绘制状态与 Qt 后端拆分

已完成。`DrawScene` 只接收 `ImageResource/RasterFrame`，每次绘制生成不可变 `DrawBatch/SpriteCommand`；GIF 解码不再创建 Qt 图片。`DrawBackend` 和 `qt_bridge.draw_backend` 通过批次交接，`DrawCore` 从 `desktop_backend` 获取已注册工厂；未配置桌面后端时使用无窗口绘制回退，不反向导入 Qt。

验收：`draw_core.py`、`graphics/` 和业务请求模块不显式导入 Qt，也不保存 toolkit 对象或 painter 回调；现有 GIF 帧切换、排序、缩放、翻转和透明度行为不变。

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
- 主宠、托盘和覆盖层生命周期已收敛到 `PetWindowHost`、`TrayHost` 和 `OverlayHost`；`ApplicationState` 不再连接 pyqtSignal、读取主宠私有 timing manager 或直接关闭/延迟销毁 QWidget。
- 世界对象管理器只依赖 `world_objects` 和核心几何，使用稳定 ID 创建对象；七个 QWidget 窗口已移入 `qt_bridge/world_objects/`。
- 世界对象 manager 只持有 `ImageResource` 和 `WorldObjectInstance`，不保存 QWidget、QPixmap 或 PhysicsBody；中心、矩形、状态和雪球运动快照在 Qt 适配层转换为核心 `Point/Rect`/纯数据。音响管理器不再拥有搜索和登录对话框生命周期。
- `effects/` 特效脚本和拉海洛技能规则只保存纯状态；图片加载、文字栅格化和技能头像缓存留在 Qt 窗口后端，`EFFECT_REQUEST` 会递归拒绝不透明后端对象。
- 无生产者的 `DRAW_RENDER` 事件及其 painter/target_rect 载荷已删除，主宠 Qt 宿主直接从核心绘制场景渲染。
- 原 `RenderCore` painter 回调不再属于跨后端契约；两个明确的 Qt 游戏控件改用 `QtRenderCore` 本地队列，不能向 DX 场景注册该回调。
- `LayerManager` 只持有 `LayerWindowHost`，不再识别 QWidget 或调用 Win32；它只在注册、注销或改层级后提交一次待处理排序，不再每帧强制调用 `SetWindowPos(HWND_TOPMOST)`。
- 工作台、对话框、游戏窗口和 Qt 媒体播放器仍是明确的 toolkit 实现；它们可以导入 Qt，但不得把 Qt 类型泄漏回核心协议和业务事件。
- 后端路由基础已完成；控制面板的 `UI.render_backend` 提供 Qt、DirectX、OpenGL 和 Vulkan 选择，设置仅在下次启动时生效。当前只有 Qt 标记为可用，其余候选保留用于接入验证，未注册时会记录原因并回退 Qt。

后续顺序固定为：

1. 已完成：以单个 `ApplicationUiHost` 移除 `ApplicationState` 对 Qt/UI 模块的直接依赖，并用阻断 PyQt 的 Fake UI host 测试保护。
2. 已完成：将 Qt 配置器注册移出 `main.py`，由 `lib/script/app/qt_backend_bootstrap.py` 按用户选择惰性导入后端。
3. 已完成：落地 `WindowHost v1` 协议、passive 实现、Qt 适配器和 `DesktopBackendBundle.window_host_factory`；主宠接入、IME 策略和 DX 原生实现仍待继续。
4. 已完成：扩展 `DrawBatch` 的文字、线段、矩形、椭圆、裁剪与变换命令，并由 Qt 后端建立行为基准；`DrawScene` 仍只从业务请求生成 sprite，迁移业务绘制时再提交其它命令。
5. 当前阶段：把声明式命令映射到 DX/WARP 离屏原型，补齐命令 ABI 和像素基线后再实现可见窗口。

不得跳过前三项直接编写 DX 透明窗口，否则会把现有 Qt UI 和 painter 假设复制进新后端。

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
rg -n "PyQt5|QPoint|QRect|QImage|QPixmap|QPainter|QTimer|pyqtSignal" lib/core/graphics lib/core/draw_core.py
rg -n --glob "*.py" --glob "!lib/core/qt_bridge/**" "from PyQt5|import PyQt5|lib\.core\.qt_bridge" config lib/core
```

交接时说明：已完成阶段、稳定接口、验证命令、未迁移 Qt 边界、未提交的其他工作树改动。
