# Qt 依赖收敛方案

更新时间：2026-08-17

本文档是 Qt 收敛工作的当前实施依据。目标是让业务模块和核心算法不再显式依赖 PyQt5，同时保留现有 Qt 界面作为第一个后端，未来可以替换为自研窗口和绘制后端。视觉表现的权威边界见 `doc/视觉表现契约.md`：当前 Qt 输出是迁移基准，但 Qt 后端最终只负责执行共享视觉描述，不拥有产品样式。

## 1. 目标与边界

最终依赖方向：

```text
后端无关业务/控制器 -> 后端无关视觉层 -> lib/core 图形、窗口与服务契约
应用组合入口       -> lib/core/qt_bridge -> PyQt5 / Windows
Qt UI 实现         -> 共享视觉描述 -> lib/core/qt_bridge
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
  visuals.py     # 主宠/命令/粒子/特效/世界对象共享 presenter 与布局
  application_visuals.py # 二维码和通知面板共享视觉描述

lib/core/timing/
  scheduler.py   # 周期计时器与调度后端协议

lib/core/application_runtime.py # 应用事件循环、退出和一次性调度协议
lib/core/application_ui.py      # 应用级 UI 生命周期协议
lib/core/backend_router.py      # 后端目录、注册、启动选择与回退结果
lib/core/desktop_backend.py     # 当前桌面服务的不可变 bundle 与注册
lib/core/window_host.py         # 后端无关 WindowHost v1 与层级窗口宿主协议
lib/core/pet_host.py            # 主宠窗口后端回调与清理协议
lib/core/tray_host.py           # 后端无关托盘生命周期、命令和菜单状态协议
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
  effect_system.py # Qt 特效覆盖窗口和共享批次执行
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
  world_object_backend.py # 稳定对象 ID 到 Qt 窗口类型的注册
  world_object_factory.py # 世界对象 QWidget 类型解析、坐标转换和实例化
  world_objects/   # 摩托、闹钟、沙发、雪堆、雪球、雪豹和音响窗口
```

`config/` 与 `lib/core/qt_bridge` 之外的 `lib/core` 不得导入 Qt 或 `qt_bridge`。核心不再自动懒加载 Qt；桌面后端未配置时使用无窗口回退或明确报错。具体后端配置器由启动组合入口按用户选择注册，`lib/script/main.py` 只消费已经选定的 `DesktopBackendBundle`。

## 3. 稳定接口

### 3.1 几何值

使用不可变的 `Point`、`Size`、`Rect`、`Color` 和 `FontSpec`。坐标采用桌面坐标约定：x 向右为正，y 向下为正；颜色使用 8-bit RGBA。目标契约中桌面窗口几何使用物理像素，窗口内容和绘制命令使用 96 DPI 逻辑像素，后端只在绘制目标边界换算一次；尚未完成该分层的现有路径必须作为视觉迁移项处理。文字的字体、字号、字重、换行、截断、对齐和基线由共享视觉层决定；后端只执行低级度量、字形栅格化和显式排版结果。

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

### 3.3 视觉表现层

颜色、字体、布局、状态样式、动画、粒子、特效、资源选择、采样、alpha 分配和命令组合均属于后端无关视觉逻辑。当前 Qt 控件、`config/config_ui.py` 和 `lib/script/workbench/theme.py` 中的既有表现需要逐步抽成纯数据主题快照及场景 presenter/composer；先让 Qt 消费共享结果并保持原有输出，再让 DX 消费同一结果。

Qt 的现有表现是迁移参照，不等于允许共享层依赖 `QStyle`、stylesheet、`QFontMetrics` 或 `QPainter` 默认值。现有命令不足以唯一表达基准时，先扩展 `graphics` 的纯数据命令，再分别实现执行器。`qt_bridge` 与 `dx_bridge` 不得保存产品主题色、尺寸、圆角、阴影、bloom、羽化或业务状态画法。

### 3.4 绘制后端

`DrawBackend.render(batch, target, viewport)` 消费不可变 `DrawBatch`，不读取或修改活动场景。`viewport` 使用核心 `Rect`，target 由具体后端宿主持有。后端可以按资源 ID、revision、帧号、尺寸和翻转状态缓存转换结果，但必须在 revision 变化时淘汰旧资源，并提供幂等 `cleanup()`。

Qt 后端负责：

- `QImage -> QPixmap` 转换；
- 缩放和水平翻转；
- `QPainter` 绘制 sprite、文字、线段、矩形和椭圆，并保存/恢复裁剪与变换状态；
- Qt 几何对象转换。

这些操作必须严格执行命令中已确定的尺寸、采样、透明度、排版、裁剪和变换语义，不得调用 Qt 默认样式补全视觉决策。未来自研后端只需实现同一批次协议，不应读取 `DrawScene` 私有状态、主题配置或业务对象。

### 3.5 应用与主宠宿主

`ApplicationRuntime` 定义桌面应用创建、一次性调度、事件处理、退出请求、退出确认和事件循环；应用编排不得直接调用具体 toolkit 的静态计时器或事件类型。

`ApplicationUiHost` 是应用级 UI 的粗粒度生命周期边界，按 prepare/start/begin-shutdown/stop/cleanup/finalize 阶段管理字体、公告、预加载器、CMD、提示面板、登录窗口初始化和退出动画。不得为每个 Qt 控件向 `ApplicationState` 增加一个工厂或 cleanup 回调，也不得从主编排器直接导入 `lib.script.ui`。

`PetHostCallbacks` 定义主宠后端向业务控制层提交的渲染准备、鼠标、键盘、窗口移动和关闭回调。Qt 后端由 `QtPetWidget` 实现 QWidget 原生事件方法并转换为 `MouseInput`、`KeyboardInput` 和 `Point`；未来自研窗口后端应调用同一组回调，不在业务控制器中模拟 Qt 事件对象。

`PetWindowHost.shutdown_host()` 是应用编排关闭主宠的唯一入口；`TrayHost` 封装退出/公告回调、初始化、预关闭和清理；`OverlayHost` 封装立即清空和完整清理。`ApplicationState` 不得访问 Qt signal、主宠 `_timing_manager` 或调用 `close()/deleteLater()`。Qt 侧由 `QtPetWindow`、`QtTrayHost`、`ParticleOverlay` 和 `EffectOverlay` 完成这些原生生命周期行为。

### 3.6 桌面服务与世界对象

`backend_router.py` 保存稳定后端 ID `qt`、`directx`、`opengl`、`vulkan`，并以纯数据 `BackendSelection` 返回请求后端、实际后端、是否回退、成熟度和原因。路由只调用组合入口注册的配置器，不导入任何具体后端。DirectX 可启动但标记为实验性，OpenGL/Vulkan 尚未接入；未注册、未实现或初始化失败的候选后端必须回退 Qt。Qt 自身初始化失败时直接终止启动，不能伪装成功或重复重试同一个配置器。

`desktop_backend.py` 通过不可变 `DesktopBackendBundle` 原子注册绘制后端、`ApplicationRuntime`、`ApplicationUiHost`、`Scheduler`、`ScreenCapture`、主宠窗口、`OverlayHost` 工厂、`TrayHostFactory`、事件泵、一次性延迟、虚拟屏幕、按点屏幕、截图、`LayerWindowHostFactory`、可选的 `WindowHostFactory` 和后端级幂等 cleanup。核心调用方只读取当前 bundle 的服务，不导入具体后端；事件循环返回后由 `ApplicationState` 调用 cleanup 兜底释放后端所有资源。

`window_host.LayerWindowHost` 是窗口排序所需的最小协议，稳定 identity 只用于注册和注销，原生堆叠 token 由 `stack_window()` 单独返回。`WindowHost` v1 在此基础上补齐显示、隐藏、关闭、`Point/Rect` 几何、DPI、屏幕、点击穿透、激活、鼠标捕获、重绘和幂等清理。`LayerManager` 只处理 `layer/z/order`、存活过滤和调度，不调用 QWidget 或 Win32 API。Qt 的弱引用、可见性、`raise_()`、`winId()` 和 `SetWindowPos` 均封装在 `qt_bridge.window_host`；桌面后端未配置时使用不执行窗口副作用的被动宿主。

`graphics.image_loader` 将静态图和 GIF 解码为 `ImageResource`，缩放也在后端无关层完成。`world_objects.py` 使用不可变 `WorldObjectRequest`、整数实例 ID 和 `WorldObjectInstance` 包装承接对象创建、状态、动作与核心几何查询；对象管理器只提交 `ImageResource`、`Point`、尺寸和纯构造选项。Qt 后端负责将 `motor`、`clock`、`sofa`、`snow_pile`、`snowball`、`snow_leopard`、`speaker` 解析为具体 QWidget 类型，以及在适配边界转换图片和窗口原生状态。

`config/font_config.py` 只保存字号、字体路径、已注册字体族和文本分段；`qt_bridge.font` 可以创建 `QFont`、执行低级度量和字形绘制。裁剪、换行、截断、回退字体选择和基线等会改变布局的决定必须逐步迁入共享视觉层，不得让 Qt 与 DX 各自选择。

## 4. 分阶段计划

### 阶段一：数据契约

已完成。新增 `graphics/types.py`、`resources.py` 和 `commands.py`。跨后端图形契约不再包含 `RenderItem/RenderRequest/PaintCallback`；仅供现有 Qt 游戏控件使用的回调队列已迁入 `qt_bridge.render_core`，旧 `render_layer.py` 已删除。

### 阶段二：绘制状态与 Qt 后端拆分

已完成。`DrawScene` 只接收 `ImageResource/RasterFrame`，每次绘制生成不可变 `DrawBatch/SpriteCommand`；GIF 解码不再创建 Qt 图片。`DrawBackend` 和 `qt_bridge.draw_backend` 通过批次交接，`DrawCore` 从 `desktop_backend` 获取已注册工厂；未配置桌面后端时使用无窗口绘制回退，不反向导入 Qt。

验收：`draw_core.py`、`graphics/` 和业务请求模块不显式导入 Qt，也不保存 toolkit 对象或 painter 回调；现有 GIF 帧切换、排序、缩放、翻转和透明度行为不变。

### 阶段三：几何和输入

进行中。`movement_controller.py`、`pet_movement_queue.py`、物理边界提供器、锚点纯计算、鼠标/键盘核心事件、屏幕裁剪与线段碰撞算法、实体核心位置接口和主宠状态机已迁移到 `Point/Rect` 与纯输入载荷；键盘事件使用核心 `Key/KeyModifier`，Qt 原生键值只在 `qt_bridge.input` 转换，麦克风快捷键解析不再导入 Qt。命令框和关闭按钮的事件处理器也已改用核心 `MouseButton/Point`。鼠标穿透共享状态改为纯进程布尔状态。QWidget/QPoint 锚点方法位于 `qt_bridge.widget_anchors`，QApplication 屏幕查询位于 `qt_bridge.screen`；`anchor_utils` 和 `screen_utils` 通过已注册桌面服务工作，不再懒加载 Qt。旧的 QPoint-like 输入仍由 `graphics.types.coerce_point()` 兼容解析。原 `get_position()/get_geometry()` Qt 兼容接口只由 Qt 宿主提供，核心生产调用统一使用 `get_core_position()/get_core_geometry()`。

游戏运行时公开的拉海洛中部区域也已改为核心 `Rect`，并由 Qt 游戏宿主通过 `core.game_obstacles` 注册 provider；主宠状态机不再导入 `lib.script.gemes`、惰性构造 `GameRuntimePanel(QWidget)` 或兼容接收 `QRect`。验收：移动、拖拽、锚点、碰撞、DX 阻断 PyQt 和游戏运行时几何测试覆盖纯数据路径。

### 阶段四：调度和事件泵

已完成。`EventPump` 已从 `EventCenter` 中抽出，Qt `QObject/pyqtSignal` 位于 `qt_bridge.event_pump`，事件中心支持注入 FakePump。事件处理器异常会记录事件类型、回调模块和限定名及完整堆栈，便于定位跨后端载荷边界错误。后台命令结果直接发布到线程安全的 `EventCenter`，Ollama 状态与流式回调通过纯 `CallbackDispatcher` 复用同一 EventPump 契约，不再自建 Qt 信号。`CallbackDispatcher` 必须在创建者线程绑定 EventPump，不得由首个后台任务延迟创建；否则 Qt 对象会归属无事件循环的 worker，导致后续完成回调丢失和请求状态永久 busy。`TimingManager`、Ollama 周期 ping、聊天流式刷新和自动陪伴不再直接依赖 `QTimer`，只依赖 `timing.scheduler.Scheduler` 创建可取消周期计时器；`ApplicationState` 和桌宠窗口在 Qt 桌面组合边界注入独占的 `qt_bridge.scheduler.QtScheduler`。聊天侧的 40ms 流式刷新与自动陪伴计时器在回调入口先停止，以保持 single-shot 语义。`LayerManager`、主宠保护检查和工具回忆重派发使用可注入的一次性调度函数，默认 Qt 实现同样位于 `qt_bridge.scheduler`。停止退出时通过幂等 `cleanup()` 退订事件并释放调度后端。

验收：FakeScheduler 覆盖启动、停止、改频、任务触发和暂停引用计数；独立子进程阻断 PyQt 导入后仍可导入并运行核心调度，Qt 后端另有真实事件循环行为测试。

### 阶段五：窗口和 UI

进行中，核心运行时的 Qt 反向依赖已清零。

- `BaseEntity` 和 `PetWindow` 是无 Qt 控制器；`QtPetWindow`、`QtPetWidget`、主宠控件创建、窗口移动、穿透和绘制位于 `qt_bridge`。
- 托盘、粒子、特效、GIF、屏幕截图、颜色和字体实现均位于 `qt_bridge`，旧核心兼容壳和 `script.app.qt_runtime` 已删除。
- 主宠、托盘和覆盖层生命周期已收敛到 `PetWindowHost`、`TrayHost` 和 `OverlayHost`；`ApplicationState` 不再连接 pyqtSignal、读取主宠私有 timing manager 或直接关闭/延迟销毁 QWidget。
- 世界对象管理器只依赖 `world_objects` 和核心几何，使用稳定 ID 创建对象；七个产品 QWidget 窗口位于 `lib/script/ui/world_objects/`，由 Qt 组合根注入后端。
- 世界对象 manager 只持有 `ImageResource` 和 `WorldObjectInstance`，不保存 QWidget、QPixmap 或 PhysicsBody；中心、矩形、状态和雪球运动快照在 Qt 适配层转换为核心 `Point/Rect`/纯数据。音响管理器不再拥有搜索和登录对话框生命周期。
- Qt `BaseQrDialog` 与 DX application UI 已消费同一个 `ApplicationPanelVisual`，二维码主体的 Qt 基准尺寸、主题、布局、PNG 解码和资源目标尺寸不再由两个后端分别维护。
- DX 基础通知和七类世界对象视觉批次已迁到共享 presenter；相关 DX bridge 只管理窗口、事件、低级音频采样、对象状态和批次提交。
- `effects/` 特效脚本和拉海洛技能规则只保存纯状态；图片解码/缩放/羽化和文字效果命令由共享视觉层完成，Qt 特效窗口只执行 `DrawBatch`，后端仅保留资源上传缓存和字形栅格化。`EFFECT_REQUEST` 会递归拒绝不透明后端对象。
- 无生产者的 `DRAW_RENDER` 事件及其 painter/target_rect 载荷已删除，主宠 Qt 宿主直接从核心绘制场景渲染。
- 原 `RenderCore` painter 回调不再属于跨后端契约；两个明确的 Qt 游戏控件改用 `QtRenderCore` 本地队列，不能向 DX 场景注册该回调。
- `LayerManager` 只持有 `LayerWindowHost`，不再识别 QWidget 或调用 Win32；它只在注册、注销或改层级后提交一次待处理排序，不再每帧强制调用 `SetWindowPos(HWND_TOPMOST)`。
- 工作台、对话框、游戏窗口和 Qt 媒体播放器仍是明确的 toolkit 实现；它们可以导入 Qt，但不得把 Qt 类型泄漏回核心协议和业务事件。办公任务页与权限许可窗通过 `lib/script/ui/office_style.py` 共享同一工作台主题和桌宠粉青视觉，不在两个 QWidget 中复制样式常量。
- 后端路由基础已完成；控制面板的 `UI.render_backend` 提供 Qt、DirectX、OpenGL 和 Vulkan 选择，设置仅在下次启动时生效。Qt 标记为当前可用，DirectX 标记为实验性功能并允许实际启动验证，OpenGL/Vulkan 仍显示尚未接入；未注册或初始化失败时记录原因并回退 Qt。

后续顺序固定为：

1. 已完成：以单个 `ApplicationUiHost` 移除 `ApplicationState` 对 Qt/UI 模块的直接依赖，并用阻断 PyQt 的 Fake UI host 测试保护。
2. 已完成：将 Qt 配置器注册移出 `main.py`，由 `lib/script/app/qt_backend_bootstrap.py` 按用户选择惰性导入后端。
3. 已完成：落地 `WindowHost v1` 协议、passive/Qt 适配器和 `DesktopBackendBundle.window_host_factory`；ABI v7 的诊断 `DxWindowHost` 已接通 `WM_POINTER*`、Unicode 文本、IME 预编辑/提交/结束和候选窗定位。
4. 已完成：扩展 `DrawBatch` 的文字、线段、矩形、椭圆、裁剪与变换命令，并由 Qt 后端建立行为基准；`DrawScene` 仍只从业务请求生成 sprite，迁移业务绘制时再提交其它命令。
5. 已完成：DX/WARP ABI v3 已映射 sprite、文字、线段、矩形、椭圆、裁剪与变换；ABI v7 保持固定 104 字节命令和同帧 UTF-8 payload，并由真实 WARP 像素测试保护透明度、排序及状态栈。
6. 当前阶段：ABI v7 的 `DxWindowHost`、共享 `DxLoopContext`、调度、事件泵、应用运行时、动态屏幕、PNG capture、主宠、完整原生日常托盘菜单、粒子/特效、世界对象和原生命令/提示/二维码 UI 已由 `DxDesktopBackend` 组成完整 bundle；阻断 PyQt 的 `ApplicationState` 启动、`APP_MAIN`、分阶段退出及 backend cleanup 组合已通过。DirectX 现以实验性功能开放实际启动验证；控制面板入口由 `ApplicationUiHost.open_settings()` 启动隔离 Qt workbench helper，helper 已使用版本 1 原子页面请求、复用存活进程并支持直达办公页，办公任务另用版本 1 文件 IPC 交换状态和命令。下一步补自动公告、共享设备资源和真实硬件/多屏/第三方输入法验收，完成后再移除实验性标记。
7. 当前视觉收敛：`graphics/visuals.py` 已承接粒子、特效、命令输入框外壳、主宠/命令框几何以及七类世界对象的 sprite、动画帧、透明度、翻转、中心缩放、闹钟倒计时、摩托抖动和音响 EMA/指数缩放；`graphics/application_visuals.py` 已承接二维码主体、action button 状态和 DX 基础通知。Qt 世界对象只执行共享 `DrawBatch`；Qt `BaseQrDialog` 与 DX application UI 共用 `ApplicationPanelVisual`，Qt/DX-WARP 二维码像素采样和整个 DX bridge 的 AST 边界已有测试保护。二维码按钮的 Qt 原生控件只负责输入和点击适配，不再通过 stylesheet 绘制产品像素。
8. 当前气泡收敛：Qt `Bubble` 的换行、自适应尺寸、三层背景、混合字体分段位置、相对主宠锚点和屏幕夹取已迁入 `BubbleVisualDescription`；Qt 只提供 `QFontMetrics` 低级度量并通过 `QtDrawBackend` 执行，迁移前后单行、多行、左对齐和硬换行像素完全一致。DX `_DxBubbleWindow` 已接入同一描述并承接 `INFORMATION` 队列、`TICK` 生命周期和主宠锚点；DirectWrite 度量适配仍待接入。
9. 后续视觉收敛：命令提示框视觉和命中矩形已统一为 `CommandHintVisualDescription` 并接入 DX 原生交互宿主，鼠标穿透、放大、缩小、关闭、启动鸣潮、聊天模式、交互模式和更多功能八个矩形附属按钮已统一为 `build_rect_action_button_visual()`，二维码 action button 已统一为 `build_qr_panel_visual()` 的共享状态批次并接入 DX 命中交互；DX 单个原生窗口承载同一三行批次和 hover/pressed 命中，“更多功能”位于“启动鸣潮”正上方，交互模式动作已接入 `INTERACTION_MODE_SET`，其余 toolkit-neutral 动作仍待补齐。下一步补齐 DirectWrite 低级文字度量、缩放/启动/更多功能动作和音响搜索 UI 宿主，并统一世界对象专属音效和部分独有交互。每项都先让 Qt 消费共享结果并保持基准，再接 DX 执行器。
10. 视觉验收：继续补 100%、125%、150% DPI 下的跨后端截图、明确文字边界/基线/换行语义；达到代表性场景基准后才可移除 DX 实验性标记。

后续迁移不得在 DX bridge 中先仿制 Qt 画法；共享命令无法表达 Qt 基准时，先扩展纯数据契约并让 Qt 执行器消费，再接 DX。

验收：启动、退出、层级、透明窗口、托盘、工作台懒加载和设置保存行为保持不变。

## 5. 约束与禁止事项

- 不在 `graphics` 中增加 Qt 兼容类型或 Qt 条件导入。
- 不通过 `Any` 把 Qt 对象藏进公开协议；后端边界使用 `object` 或明确的协议类型。
- 不让纯核心模块调用 `QApplication.instance()`、`QTimer.singleShot()` 或 QWidget 方法。
- 不让纯核心模块导入 `lib.core.qt_bridge`；后端选择只能发生在组合入口。
- 不在 `qt_bridge` 或任何其它后端中保存产品主题、布局、动画和特效算法；后端只执行共享视觉描述。
- 不用后端私有偏移、缩放、颜色或效果补丁修正差异；缺少语义时先扩展纯数据契约。
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

视觉阶段至少追加：

```powershell
$env:FLYING_SNOW_DX_DLL = (Resolve-Path 'native/dx_backend/build/cmake/Release/flying_snow_dx.dll')
py -3 -m unittest tests.test_visual_presenters tests.test_bubble_visual tests.test_qr_panel_visual tests.test_visual_backend_parity
```

`tests.test_visual_presenters` 的 AST 审计禁止 `dx_bridge/application_ui.py` 和 `world_object_backend.py` 实例化 `Color`、`FontSpec` 或产品绘制命令。还需保存 Qt 迁移前后基线，并验证同一业务状态不因后端选择生成不同命令；像素对比覆盖 100%、125% 和 150% DPI。文字可容忍有限栅格边缘差异，但边界、基线、换行、资源、颜色和透明度必须一致。

交接时说明：已完成阶段、稳定接口、验证命令、未迁移 Qt 边界、未提交的其他工作树改动。
