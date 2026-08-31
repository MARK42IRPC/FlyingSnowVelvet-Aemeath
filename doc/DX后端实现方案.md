# DirectX 后端实现方案

更新时间：2026-08-31

本文档定义 Windows DirectX 桌面后端的技术路线、迁移边界和验收条件。目标不是只实现一个 DX 绘制器，而是让普通桌宠运行进程最终不导入 PyQt5、不加载 Qt DLL，同时保留现有 Qt 后端作为迁移期回退和独立工作台实现。

当前 Qt 边界和后端无关契约以 `doc/Qt边界契约.md`、`doc/视觉表现契约.md`、`lib/core/backend_router.py`、`lib/core/desktop_backend.py` 及源码测试为准。阶段 B 至阶段 E 的诊断链已落地到 `native/dx_backend/`、`lib/core/dx_bridge/` 和 `tests/dx/`：ABI v9 覆盖完整声明式命令批、DirectWrite 字形测量、Win32 + DirectComposition 窗口、尺寸/DPI 目标重建、事件轮询、保持句柄稳定的设备恢复、`WM_POINTER*`/Unicode/IME 输入以及通用托盘命令；`DxDesktopBackend` 使用一个共享 loop 组合运行时、调度、事件泵、屏幕、截图、主宠、托盘、粒子/特效、七类世界对象和原生命令/提示/二维码/公告/音响 UI，阻断 PyQt 的 `ApplicationState` 启停链已经通过。应用二维码主体及 action button、命令提示、DX 基础通知、八按钮附属面板、气泡、公告、音响搜索/播放列表和七类世界对象视觉已迁入共享 presenter，相关 bridge 只负责窗口、输入、低级音频采样、生命周期和批次执行；八按钮动作已通过后端中立分发接通。DX 音乐组合不再导入或实例化 QtMultimedia，主宠漫游也只读取核心游戏障碍 `Rect` provider，不再为查询几何构造 Qt 游戏窗口。DirectX 已开放为实验性可选后端；世界对象专属音效、共享设备资源和真实硬件/DPI/多屏验收仍未完成。控制面板入口已通过 `TrayCommand.OPEN_SETTINGS` 启动隔离 Qt workbench helper；helper 已使用版本 1 原子页面请求并复用存活进程，办公任务和权限请求通过独立版本 1 文件 IPC 直达 `office` 页，稳定后端标记继续受闸门约束。

## 1. 目标与非目标

### 1.1 目标

- Windows 普通桌宠启动、渲染、输入、调度、托盘、截图和退出不依赖 Qt。
- 使用 `directx` 稳定后端 ID 接入现有 `BackendRouter`，配置入口和回退语义保持不变。
- 以 Direct3D 11 和 Windows 系统图形组件完成透明桌面窗口、2D 图片、文字、粒子和特效绘制。
- Python 继续承载业务状态和编排；高频绘制、窗口消息和 GPU 资源生命周期放入原生模块。
- 当前 Qt 输出作为视觉迁移基准；同一业务状态由共享视觉层生成一份描述，DX 只负责等价绘制。
- Qt 后端在迁移期间始终可运行；用户选择 DirectX 时允许进入实验性验证，初始化失败仍明确回退 Qt。

### 1.2 非目标

- 首版不采用 Direct3D 12。桌宠是 2D 合成负载，D3D12 的显式同步和资源管理成本没有直接收益。
- 不在首版重写工作台、设置页、游戏窗口等全部复杂 QWidget 控件。
- 不同时实现 DirectX、OpenGL 和 Vulkan；先用 DirectX 验证后端契约是否完整。
- 不在 Python 中逐条调用 COM API，也不让渲染线程持有或回调 Python 对象。
- 不因 DX 原型可显示一张图片就移除 Qt 依赖或将后端标记为稳定可用；实验性状态与稳定性闸门分开维护。

## 2. 当前基线与阻塞点

现有路由已经提供 `qt`、`directx`、`opengl`、`vulkan` 稳定 ID，控制面板通过 `UI.render_backend` 保存选择。Qt 与 DirectX 的 `BackendDescriptor.available=True`，其中 DirectX 另标记 `experimental=True`；OpenGL/Vulkan 仍未接入。未注册、未实现或初始化失败的后端会记录原因并回退 Qt。

核心算法和多数业务载荷已经改用 `Point`、`Rect`、`Color`、`FontSpec`、`MouseInput` 和 `KeyboardInput` 等纯数据类型。主宠绘制链已经使用纯 RGBA 资源和不可变命令批，但以下边界仍阻止普通运行进程移除 Qt：

- `lib/script/main.py` 已只从 `DesktopBackendBundle` 获取应用运行时、调度、截图、主宠、覆盖层和托盘工厂；普通运行时的宿主生命周期已由明确协议承接，工作台、设置对话框、游戏窗口和媒体播放器仍是 Qt UI/helper 边界。
- `DrawScene` 已只保存 `ImageResource/RasterFrame`，并生成不可变 `DrawBatch/SpriteCommand`；`DrawBatch` 契约已扩展文字、线段、矩形、椭圆、裁剪和变换命令，DX/WARP ABI v9 已消费全部命令类型并可提交到诊断窗口。
- `DrawBackend.render(batch, target, viewport)` 的 target 由后端宿主持有；Qt 使用 `QPainter`，DX `DxWindowHost` 持有 DComp 交换链并消费同一 `DrawBatch`。产品尺寸必须来自命令的显式 `target_size`，不能由 viewport 或 swap-chain 反推。
- 跨后端 `RenderRequest/RenderItem/PaintCallback` 已删除；两个明确的 Qt 游戏控件使用 `qt_bridge.render_core.QtRenderCore` 本地回调，尚未迁移为声明式命令。
- `WorldObjectBackend` 使用纯 `ImageResource` 和整数实例句柄；Qt 与 DX 均已实现七类世界对象，sprite、动画帧、透明度、翻转、中心缩放、闹钟倒计时、摩托抖动和音响 EMA/指数缩放均由 `graphics.visuals` 与 `world_objects` 的共享函数决定，专属音效和部分独有交互仍需继续收敛。
- `LayerManager` 已只依赖最小 `LayerWindowHost`，Qt 可见性、前置、原生句柄和 `SetWindowPos` 已迁入 `qt_bridge.window_host`；后端无关 `WindowHost v1` 协议、passive/Qt/DX 实现、`DxPetWindow` 组合和 DX factory 注册均已落地。
- `DxLoopContext`、`DxScheduler`、`DxEventPump` 和 `DxApplicationRuntime` 已在完整 DX bundle 中由 owner 线程驱动定时任务、后台事件投递、注册窗口轮询和退出确认；当前使用 `threading.Event` 唤醒加短间隔原生轮询，尚未替代完整 Win32 消息等待。
- `DxScreenProvider` 每次查询重新枚举 Win32 monitors，虚拟桌面和按点选屏不缓存旧拓扑；`DxScreenCapture` 用 GDI 读取主屏 BGRA 并只向业务返回 PNG bytes。`DxPetWindow` 已把 `PetWindow` 纯控制器接到 DX host，覆盖初始几何、锚点、移动、穿透、重绘和关闭清理，但仍是诊断组合。
- 工作台、设置对话框、游戏窗口和媒体播放器仍是 Qt UI。

因此实施顺序必须先补齐跨后端契约，再接 DX 窗口和运行时。只新增一个 `DxDrawBackend` 无法实现“摆脱 Qt”。

## 3. 技术选型

### 3.1 图形与窗口栈

首版固定使用：

| 能力 | 组件 | 用途 |
| --- | --- | --- |
| GPU 设备 | Direct3D 11 | 纹理、渲染目标、设备丢失恢复 |
| 显示与交换链 | DXGI 1.2+ | `CreateSwapChainForComposition`、帧提交 |
| 透明桌面合成 | DirectComposition | 每像素透明、多窗口视觉树和提交 |
| 2D 绘制 | Direct2D 1.1 | 位图、变换、裁剪、透明度和基础形状 |
| 文字 | DirectWrite | 低级度量、字形栅格化和抗锯齿 |
| 图片解码 | WIC | PNG、静态图片和 GIF 帧解码 |
| 窗口与输入 | Win32 | HWND、消息循环、鼠标、键盘、DPI、多屏和托盘 |

交换链使用 BGRA 8-bit 格式和 premultiplied alpha，Direct2D 位图上下文与交换链保持同一 alpha 约定。透明像素的 RGB 必须预乘，避免角色边缘出现黑边或白边。

默认创建硬件 D3D11 设备。WARP 仅用于诊断和自动化环境，不作为用户静默性能回退；硬件设备初始化失败时应让路由回退 Qt 并记录 HRESULT、适配器和驱动信息。

### 3.2 原生边界

新增原生 C++ 动态库，建议目录：

```text
native/dx_backend/          # C++17、CMake、D3D11/DirectComposition 实现
lib/core/dx_bridge/         # Python ctypes 适配和后端组合入口
tests/dx/                   # Windows/DX 集成与像素基线测试
```

原生库导出版本化的稳定 C ABI，Python 使用标准库 `ctypes` 调用。选择 C ABI 而不是 pybind11，可减少 Python ABI 耦合和额外运行时依赖，并允许后续用独立诊断程序直接驱动后端。

构建使用 MSVC、Windows SDK 和 CMake，Release 默认 `/MT`，避免额外分发 VC Runtime。动态库仍需在 CI 和目标 Windows 版本上验证；不能仅凭 `/MT` 假设所有系统组件都可用。

## 4. 目标架构

```text
业务状态与控制器
  -> 纯数据 SceneSnapshot / WindowCommand / HostEvent
  -> lib/core 后端协议与服务门面
  -> lib/core/dx_bridge（ctypes、错误转换、生命周期编排）
  -> flying_snow_dx.dll（Win32 + D3D11 + D2D + DComp）
  -> Windows DWM / 输入 / 屏幕 / 托盘
```

原生层拥有 HWND、COM 对象、D3D 设备、交换链、纹理、字体布局和消息循环。Python 只持有整数句柄和不可变数据，不得接触 COM 指针，不得将 Python `object` 地址作为长期资源句柄。

一个 DX UI 主线程统一拥有窗口、设备、交换链和 DirectComposition visual。资源文件读取和 WIC 解码可在受控 worker 中进行，但 GPU 资源创建、替换和释放必须投递回 DX 主线程。

## 5. 契约改造

### 5.1 资源描述

主宠绘制已将 `DrawScene.register_resource(resource: ImageResource)` 收敛为后端无关资源描述。当前 `RasterFrame` 使用紧密排列的 RGBA8888 `bytes`，包含尺寸和帧时长；Qt 后端只在渲染边界复制成 `QImage/QPixmap`。

后续统一资源仓库允许的资源来源只包括：

- 规范化文件路径；
- 拥有明确格式和尺寸的不可变 `bytes`；
- 已由后端资源仓库返回的稳定整数资源 ID。

GIF 帧时长和逻辑尺寸已经进入纯帧数据；循环方式和缩放策略仍需补齐为显式元数据。当前由 Python/Pillow 解码，DX 原型可以直接上传 RGBA 帧；后续再按内存与启动性能决定是否让 WIC 接管路径/编码字节解码。缓存键至少包含资源 revision、帧号、目标尺寸和翻转状态，资源注销后能够确定性释放。

世界对象已迁移到同一资源契约：manager 使用 `ImageResource`，由 `WorldObjectRequest` 提交对象类型、资源、位置、尺寸和纯构造选项；后端只返回稳定整数实例 ID，业务持有 `WorldObjectInstance`。翻转不再作为资源副本返回，而是由后端在渲染边界从 `RasterFrame` 派生。状态、核心几何和雪球运动快照通过 `WorldObjectState`、`WorldObjectMotion` 交接，不能泄露 QWidget、QPixmap 或 PhysicsBody。

### 5.2 共享视觉描述与声明式绘制命令

跨后端 `PaintCallback` 已删除，Qt 独占控件回调已移入 `qt_bridge`。当前 `SpriteCommand/DrawBatch` 已提供已解析资源帧、透明度、翻转、缩放和 `layer/z/order`；Qt 已为下列声明式命令建立行为基准，DX/WARP 的 ABI v9 已完成对应映射：

- `SpriteCommand`：资源、源帧、目标矩形、透明度和翻转；缩放采样模式仍需补为显式字段；
- `TextCommand`：文本、`FontSpec`、颜色、布局矩形和对齐；
- `RectCommand/EllipseCommand`：填充、描边和线宽；
- `ClipPush/ClipPop` 与 `TransformPush/TransformPop`；
- 粒子和特效使用上述基础命令或专用批命令。

每帧由 Python 生成一份连续命令批，一次跨 C ABI 提交。禁止每个 sprite、粒子或文字进行一次 Python 到 DLL 调用。ABI v9 保留 v3 的固定 104 字节异构命令结构，并增加不改变批次布局的 `fsdx_measure_text` 低级测量接口及 `fsdx_register_font_file` 私有字体注册接口；命令包含 `abi_version`、结构体大小、命令类型、flags、`layer/z/order`、资源句柄、几何参数、透明度、线宽、RGBA 颜色、六元素变换和帧 payload 区间。文字正文与字体族通过同一次 `fsdx_submit_frame` 或 `fsdx_submit_window_frame` 调用携带的只读 UTF-8 payload 交接，原生层只在调用期间消费。绘制命令在相邻状态边界内按 `layer/z/order` 稳定排序，裁剪/变换 push-pop 保持原始批次顺序并校验类型配对；未知命令、越界 payload 和不平衡状态栈必须返回可诊断错误。

跨后端排序继续使用 `layer/z/order`。仅 Qt 独占 UI 可暂时使用 `QtRenderCore` 本地 painter 回调，不能把该路径注册为 DX 场景内容；需要迁移到 DX 的视觉内容必须产出命令。

颜色、字号、布局、状态样式、文字换行与基线、资源选择、采样、混合、动画、粒子和特效算法必须在后端无关视觉层中解析。DX native 和 `dx_bridge` 只消费已确定的窗口描述、资源和 `DrawBatch`；不能读取主题配置、识别业务组件或用 Direct2D/DirectWrite 默认值补全视觉选择。粒子、特效、命令输入框外壳和七类世界对象视觉批次由 `lib/core/graphics/visuals.py` 生成；二维码主体、action button 状态及 DX 基础通知由 `lib/core/graphics/application_visuals.py` 生成，Qt `BaseQrDialog` 与 DX application UI 消费同一 `ApplicationPanelVisual`。整个 `dx_bridge` 不得导入 PyQt，也不得构造产品颜色、字体或具体绘制命令。

现有命令无法唯一复现 Qt 基准时，先扩展共享纯数据契约，再同时更新 Qt 和 DX 执行器。sprite 采样、线帽/连接、文字排版、渐变、阴影、混合和羽化等语义不得在 C++ 层硬编码为产品效果。原生层可以保留透明清屏色、像素格式、缓存和设备恢复等技术参数。

### 5.3 窗口宿主

第一步已落地后端无关 `LayerWindowHost`：稳定 identity 用于注册和注销，`is_alive/is_visible` 用于过滤窗口，`stack_window` 返回后端原生整数 token，原生堆叠不可用时通过 `raise_window` 回退。`LayerManager` 只负责 `layer/z/order` 排序和触发时机，不再识别 QWidget、HWND 或 `SetWindowPos`。Qt 适配器弱持有 QWidget，桌面后端通过工厂注册；未配置后端时使用无副作用宿主保证核心可独立运行。

后端无关 `WindowHost v1` 已在 `lib/core/window_host.py` 定义并由 `DesktopBackendBundle.window_host_factory` 暴露，至少覆盖：

- 创建、显示、隐藏、关闭透明无边框窗口；
- 读取和设置 `Point/Rect`、DPI 和所属屏幕；
- 点击穿透、是否激活、光标和捕获状态；
- layer/z 排序、前置请求和原生整数句柄；
- 请求重绘及提交场景；
- 幂等 `cleanup()`。

DX 层级实现直接使用 HWND；完整 Qt 窗口实现继续用适配对象包装 QWidget。窗口层级只在注册、显示、交互或层级变化时重申，不持续抢占前台或按帧强制 `HWND_TOPMOST`。需要第三方输入法的可编辑窗口必须允许正常激活和 IME z-order，装饰窗口才使用 `WS_EX_NOACTIVATE`。

主宠 Win32 窗口将 `WM_MOUSE*`、`WM_POINTER*`、键盘、移动、DPI 和关闭消息转换为现有 `PetHostCallbacks` 纯数据调用。拖拽期间使用鼠标捕获，点击穿透通过窗口扩展样式和 `WM_NCHITTEST` 切换，不模拟 Qt 事件。

ABI v9 和 `dx_bridge.window_host.DxWindowHost` 已实现这一边界的诊断版本：每个 runtime 拥有一个 HWND、DComp visual 和预乘 alpha composition swap chain，覆盖显示/隐藏、物理几何、屏幕/DPI 快照、穿透、激活、捕获、层级、重绘、可见帧提交、尺寸/DPI 目标重建、设备恢复和幂等销毁。native 只写事件队列，Python 轮询后转换为 `MouseInput`、`KeyboardInput`、Unicode 文本、IME 预编辑和 `Point`，并在重绘事件上提交 `DrawBatch`。`WM_POINTER*` 会过滤触控/笔提升的重复鼠标消息；物理按键不再调用 `ToUnicode`，`WM_CHAR`/`WM_UNICHAR` 与 `WM_IME_COMPOSITION` 分别交付最终文本和预编辑文本，候选窗位置由 `fsdx_set_window_ime_position` 设到输入区域；托盘命令通过 `FSDX_EVENT_TRAY_COMMAND` 传递整数命令 ID 和勾选标志。当前仍限制一个 runtime 一个窗口；屏幕拓扑变化后的窗口重排属于 Stage C 后续工作。

### 5.4 桌面运行时能力

DX 组合入口必须一次性注册一组完整服务：

- `ApplicationRuntime`：Win32 消息循环、调度、退出确认和残留窗口关闭；
- `EventPump`：线程安全队列加主线程唤醒消息；
- `Scheduler` 和 deferred call：基于等待计时器或统一调度队列；
- 屏幕能力：虚拟桌面、按点选屏、DPI 和 PNG 截图；
- 主宠、粒子、特效和世界对象窗口宿主；
- 托盘图标、菜单命令和资源图标加载；
- 绘制资源仓库和 DX 场景提交。

`dx_bridge.desktop_backend.DxDesktopBackend` 已把绘制、`ApplicationRuntime`、`ApplicationUiHost`、`Scheduler`、`ScreenCapture`、主宠窗口、`OverlayHost` 工厂、`TrayHostFactory`、`WindowHost` factory、事件泵、延迟、屏幕、截图、层级窗口宿主和世界对象收进一个共享上下文，并通过不可变 `DesktopBackendBundle` 原子注册。bundle 的 backend cleanup 在事件循环返回后兜底释放 scheduler、event pump、世界对象及残留 native host；新增能力应继续扩展明确协议和组合对象，不能恢复彼此无关的模块全局变量，也不能让 `lib/script/main.py` 为每个后端分别导入一串具体实现。

诊断实现已用一个 `DxLoopContext` 统一承载线程安全回调队列、单调时钟任务和已注册宿主的 `poll_events()`。`DxScheduler` 的迟到周期只合并触发一次并从回调完成时重新计时；`DxEventPump.emit()` 可由 worker 调用，重复唤醒合并后只在 owner 线程执行；`DxApplicationRuntime` 提供一次性任务、事件处理、带退出码的退出确认和残留窗口关闭。`DxScreenProvider` 不保留显示器缓存，显示器热插拔后的下一次查询即可得到新拓扑；`DxScreenCapture` 将 GDI 资源正确解除选入后才调用 `GetDIBits`，避免截图句柄状态泄漏。`DxTrayHost` 使用 `Shell_NotifyIconW` 创建通知区图标，将 `TrayCommand` 命令写入 ABI v9 的 `FSDX_EVENT_TRAY_COMMAND` 事件，并通过 `fsdx_set_tray_menu_state` 同步游戏模式、鼠标穿透和开机启动勾选；菜单还提供 CMD、清理桌面/缓存/历史和作者主页入口。初始化失败可由共享循环有限重试，隐藏、销毁和 cleanup 保持幂等。Qt 和 DX 均由 `ApplicationState` 路由到 `lib/script/app/tray_actions.py`，文件与系统 I/O 使用 `ComputeHub.submit_interactive_io()`。`DxApplicationUiHost` 原生承接命令输入、信息提示、元宝/音乐二维码和自动/手动公告；公告使用共享 core 服务和原生窗口，不打开浏览器。单个业务回调异常会交给循环异常处理器且不丢弃同轮后续回调。当前仍以 `threading.Event` 和默认 8ms 有界轮询驱动 native 队列；后续应评估 `MsgWaitForMultipleObjectsEx` 或原生 wake handle，避免空闲轮询成为最终架构。

最终组合入口只负责：读取配置、注册可用后端配置器、执行路由、创建所选 `DesktopBackendBundle`。`ApplicationState` 已从 bundle 获取运行时、调度、截图、主宠、覆盖层和托盘服务，并只通过 `PetWindowHost.shutdown_host()`、`OverlayHost.cleanup()` 和 `TrayHost.cleanup()` 执行退出；完整窗口创建、几何、输入和重绘服务仍需继续迁移，最终不得直接导入 `qt_bridge` 或 `dx_bridge`。

两个启动组合边界已经完成：`ApplicationUiHost` 统一承接字体、公告、预加载、CMD、提示面板、登录 UI 和退出动画；启动入口按后端选择惰性导入配置器，`main.py` 不注册或导入 Qt。`WindowHost v1` 协议和 Qt factory 已落地，随后扩展声明式绘制命令。DX 第一份可执行成果必须是 WARP 离屏渲染与像素测试，不是用户可见透明窗口。

## 6. C ABI 与事件模型

ABI 只传固定宽度整数、浮点数、UTF-8 字节块、带长度数组和不透明整数句柄。所有导出函数返回状态码，线程局部错误文本通过独立函数读取。禁止跨边界抛出 C++ 异常。

建议最小能力组：

```text
fsdx_get_abi_version
fsdx_create_runtime / fsdx_destroy_runtime
fsdx_recover_device / fsdx_get_device_generation
fsdx_create_window / fsdx_destroy_window
fsdx_set_window_state
fsdx_register_resource / fsdx_release_resource
fsdx_submit_frame
fsdx_poll_events
fsdx_request_exit
fsdx_get_last_error
```

当前 ABI v9 已实现 `fsdx_get_abi_version`、runtime 创建/销毁、RGBA 资源注册/释放、异构绘制批次提交、DirectWrite 字形测量、私有字体注册和 RGBA readback，并覆盖窗口创建/销毁、状态读取、显示、几何、穿透、捕获、激活、IME 候选窗定位、层级、重绘、窗口帧提交及 `fsdx_poll_events`。事件队列将物理按键、最终 Unicode 文本、IME 预编辑/结束和通用托盘命令分成独立事件；托盘菜单状态通过 `fsdx_set_tray_menu_state` 交接，托盘 HWND、HICON 和通知区注册均由 native 层拥有。`fsdx_recover_device` 在窗口 owner 线程原地重建设备，`fsdx_get_device_generation` 提供单调 generation；恢复成功产生 `DEVICE_RECOVERED` 事件。批次支持 sprite、DirectWrite 文字、线段、矩形、椭圆、嵌套裁剪和二维仿射变换，`fsdx_measure_text` 使用同一 runtime 的 `IDWriteFactory` 返回实际字形布局尺寸。资源、命令和文字 payload 在 C ABI 调用期间被 native 层复制或完整消费；Python 只持有整数句柄。WARP 通过 `FSDX_RUNTIME_FLAG_WARP` 显式选择，默认硬件路径尚未接入桌面路由。readback 返回紧密排列的预乘 RGBA8888。

DX runtime 初始化时必须注册仓库 `resc/FRONTS` 中的 HarmonyOS Sans SC 和 WuWa Lahai-Roi 字体；任一字体文件缺失、路径无效或注册失败都视为初始化失败，由 `BackendRouter` 记录原因并回退 Qt，不得静默使用系统回退字体。设备恢复会按已注册路径重建私有字体集合。

原生层不得从窗口过程、渲染线程或 worker 任意回调 Python。窗口输入、设备状态和托盘命令写入有界事件队列，Python 在事件泵边界批量 `poll`。队列必须合并可丢弃的高频移动/重绘事件，但不得丢弃按键、按钮、关闭、设备丢失和资源错误事件。

提交帧时原生层复制或在调用期间完整消费命令字节；不得在函数返回后继续引用 Python 缓冲区。资源上传同样要明确复制所有权，避免 GC 后悬挂指针。

## 7. 线程、设备与生命周期

启动顺序：

1. 校验 DLL ABI、Windows 版本和配置。
2. 创建 DX UI 主线程及消息窗口。
3. 创建 D3D11/DXGI/D2D/DWrite/WIC/DComp 设备。
4. 注册桌面服务并创建业务运行时。
5. 创建主宠窗口、资源和首帧。
6. 首帧成功提交后才报告后端初始化成功。

退出顺序反向执行：停止接收业务命令，关闭调度和输入，销毁各窗口 visual/target/swapchain，释放缓存和设备，退出消息循环，最后卸载 DLL。所有 destroy/cleanup 操作必须幂等，部分初始化失败也走同一清理路径。

检测到 `DXGI_ERROR_DEVICE_REMOVED` 或 `DXGI_ERROR_DEVICE_RESET` 时：

1. 停止提交当前帧并记录 `GetDeviceRemovedReason()`。
2. 释放所有设备相关资源，保留可重建的 CPU 资源描述。
3. 在同一 DX 主线程重建设备、窗口 target 和资源。
4. 完整重绘所有可见窗口。
5. 连续重建失败时请求受控退出并提示下次启动回退 Qt，不在损坏设备上死循环。

ABI v9 保留 ABI v5 已落实的第 1 至第 4 项：资源注册时保留预乘 CPU 像素，恢复时保持资源整数句柄和 HWND 不变，重建 D3D11、D2D、DWrite、DComp、交换链、窗口 visual 及全部位图；ctypes 对单次 GPU 操作最多恢复并重试一次。第二次仍失败会直接返回诊断错误，不在 bridge 内循环；正式应用运行时接入后再把该错误转换为受控退出和下次启动回退。

目标坐标契约中，桌面窗口位置、显示器边界和 Win32 输入几何使用物理桌面像素，窗口内容、字号和 `DrawBatch` 使用 96 DPI 逻辑像素。DX 目标边界按 `dpi / 96` 换算一次，DirectWrite 字号、D2D 几何、资源目标尺寸和 readback 必须遵守同一缩放，禁止多阶段重复换算。当前代码与自动化测试已覆盖该分层、负坐标及 100%/125%/150% DPI；真实硬件上的多屏热插拔和第三方输入法仍需手工验收。进程声明 Per-Monitor V2 DPI awareness，处理 `WM_DPICHANGED` 推荐矩形。

## 8. 分阶段实施

### 阶段 A：契约补齐

- 已完成主宠和世界对象统一资源描述、纯 RGBA 帧、缩放、不可变 sprite 批次、Qt 后端 revision 缓存及整数世界对象实例句柄。
- 已从跨后端契约删除 `PaintCallback` 并保留 Qt 本地适配；文字、形状、裁剪和变换命令已补齐并完成 DX/WARP 映射，粒子和特效已转换为基础命令和 sprite 批次。
- 已新增最小 `LayerWindowHost`、`WindowHost v1` 和 Qt/DX 窗口适配器，移除 `LayerManager` 对 QWidget/Win32 方法的直接调用；`DxPetWindow` 已通过同一宿主协议接入主宠控制器。
- 已将应用运行时、调度器、截图服务以及主宠、覆盖层、托盘工厂和当前桌面能力收进 `DesktopBackendBundle`；DX bundle 共享单个 loop 并提供后端级 cleanup，主宠、覆盖层和托盘对象行为已收敛为 `PetWindowHost`、`OverlayHost` 和 `TrayHost`。
- 已完成 `ApplicationUiHost` 和启动组合入口拆分：`ApplicationState` 不再导入 Qt/UI 模块或注册后端，Qt/DX 配置器由 bootstrap 惰性安装；`WindowHost v1`、完整声明式命令批、DX 原生窗口和主宠组合已落地。

退出条件：核心和跨后端业务数据中不存在 QImage/QPixmap/QPainter/QWidget；阻断 PyQt 导入的子进程测试可实例化核心场景、层级和应用编排。

### 阶段 B：DX 离屏绘制原型

- 已建立 CMake/MSVC 工程和 `ctypes` bridge；ABI v3 完成统一的 104 字节命令与只读 UTF-8 帧 payload，当前 ABI v9 保持该布局并扩展窗口、设备恢复、原生输入、托盘命令和 DirectWrite 测量契约。
- 当前已在 WARP 上验证 sprite、DirectWrite 文字、线段、矩形、椭圆、裁剪、变换、透明度、翻转、缩放、状态边界内异构排序、资源 revision 缓存、未知命令错误、不平衡状态栈和预乘 RGBA readback；硬件路径接入桌面路由和 PNG 诊断仍待后续完成。
- 阶段 B 原型只创建离屏 target；后续阶段已补齐用户可见窗口和完整 bundle，当前由路由以实验性 `directx` 后端提供尝试入口。

退出条件：像素基线、透明边缘、资源释放、错误码和 ABI 不匹配测试通过；阶段 B 单独的离屏原型不代表稳定后端，最终成熟度由第 9 节闸门控制。

### 阶段 C：主宠透明窗口

- 已完成第一条诊断链路：Win32 + DirectComposition 透明窗口、可见帧提交、`WindowHost v1` 操作、`PetHostCallbacks` 事件转换、负坐标/屏幕/DPI 快照、点击穿透、捕获和窗口层级。
- 窗口几何和 `WM_DPICHANGED` 路径已同步重建交换链、离屏 render target 与 readback staging texture；设备恢复已保持 CPU 资源、整数句柄、HWND 与可见性，并对每次提交设置一次重试上限。
- 已新增 Qt-free 诊断运行时：共享 owner-thread 循环驱动 `DxScheduler`、合并式 `DxEventPump`、一次性任务、注册窗口事件轮询、退出确认和残留窗口关闭；阻断 PyQt 导入的独立进程测试已覆盖该链路。
- 已新增屏幕与主宠诊断组合：动态 Win32 monitor provider、GDI 主屏 PNG capture、`DxPetWindow` 纯控制器宿主和 DX 层级适配器均有独立注入测试；主宠关闭会先清理核心状态，再发布 `APP_QUIT`，native host/context 注销保持幂等。
- 已新增完整 DX 原生日常托盘菜单：`Shell_NotifyIconW`、任务栏重建恢复、公告、控制面板、CMD、游戏模式、鼠标穿透、开机启动、桌面/缓存/历史清理、作者主页和退出均通过统一命令事件交给 `ApplicationState`；菜单勾选状态由 ABI v9 状态接口同步，控制面板通过隔离 Qt workbench helper 打开。helper 复用存活进程并消费版本 1 原子页面请求，办公权限请求会直达 `office` 页。
- `WM_POINTER*`、`WM_CHAR`/`WM_UNICHAR`、IME 预编辑/提交/结束、候选窗定位和命令面板组合文本显示已经接通；当前下一步补齐屏幕热插拔和可注入的真实 device-removed 失败测试。
- 无 Qt 粒子、特效、应用 UI 宿主和 DX bundle 已接入诊断组合；当前继续补齐真实第三方输入法、设备丢失和多屏验收矩阵。

退出条件：主宠可独立运行，但粒子、特效、托盘或退出链任一能力仍依赖 Qt 时，DirectX 只能保持实验性状态，不得标记为稳定后端。

### 阶段 D：视觉与世界对象

- 已将 GIF、粒子、特效、基础信息提示及七类世界对象迁移到统一资源和 scene/window 协议。
- 粒子和特效运行时已由 `lib/core/graphics/visuals.py` 共享 presenter 生成同一 `DrawBatch`；Qt 与 DX 覆盖层均只执行批次，图片缩放和边缘羽化也使用同一纯 RGBA 资源处理。Qt 基准的 `no_fade`、文字 alpha override 和八方向 bloom 预算已固化为结构测试。
- 命令输入框的黑/青/粉三层外壳已由 Qt 与 DX 共用 composer；DX 使用 Qt 配置的 `240x36` 逻辑尺寸、白色输入区、字体、占位文本和紧凑 IME 坐标，并有 Qt/DX-WARP 像素对比及真实窗口 readback 测试。
- 主宠 sprite 的目标尺寸已由共享 `SpriteCommand.target_size` 显式传递，Qt paint viewport 与 DX 重绘区域不再各自决定缩放；命令输入框相对主宠的右侧锚点、边缘翻转和屏幕夹取也由 Qt/DX 共用纯几何解析器。
- 二维码主体及底部 action button 已迁入 `graphics/application_visuals.py`：Qt `BaseQrDialog` 与 DX application UI 共用 `320x430` Qt 基准尺寸、主题、布局、PNG 解码、资源目标尺寸、状态文字和 action button 状态批次；Qt `QPushButton` 仅作为透明输入适配器，DX 根据同一 `action_rect` 处理 hover、pressed、release 和登录面板关闭/取消事件。
- DX 基础通知面板已由共享 notice presenter 生成。Qt 聊天气泡的换行、自适应尺寸、三层背景、混合字体分段、锚点和屏幕夹取抽成 `BubbleVisualDescription`，DX `_DxBubbleWindow` 已消费同一描述并接入 `INFORMATION`/`TICK`/`UI_BUBBLE_HIDE`；真实 DX host 使用 ABI v9 DirectWrite 度量，无测量能力的测试宿主才回退 portable 度量。
- 七类世界对象的 sprite、动画帧、透明度、翻转、中心缩放、闹钟倒计时、摩托抖动和音响 EMA/指数缩放已由 `graphics.visuals` 与 `world_objects` 共享函数统一生成，DX world object backend 只管理对象状态、低级音频采样、原生窗口和批次提交。
- 命令提示框的背景、尺寸、默认/哈希行、选中态、分隔线、混合字体、页码、默认文案和命中矩形已迁入 `CommandHintVisualDescription`，Qt 与 DX 均只执行该描述；`DxCommandHintWindow` 已接入命令框跟随、筛选、导航、补全、翻页和点击执行，并在真实 DX host 创建后切换 ABI v9 DirectWrite 度量。鼠标穿透、放大、缩小、关闭、启动鸣潮、聊天模式、交互模式和更多功能八个附属按钮已由 `build_command_action_panel_visual()` 生成共享三行布局与状态批次，“更多功能”位于“启动鸣潮”正上方，并由后端中立动作分发统一执行。音响本体视觉、搜索结果和播放列表均已有 DX 原生宿主；搜索、播放/暂停、队列、进度、音量、模式、喜欢和本地音乐操作不依赖 Qt 控件。
- 补齐采样、文字排版、混合、羽化和 DPI 等显式命令语义；DX 后端只执行批次，不保存产品色值、布局或效果算法。
- 世界对象已支持原生窗口、GIF、几何、物理运动、拖拽、翻转、点击穿透、淡出、共享倒计时、摩托连续加减速/二段跳、闹钟重复弹跳和雪豹定时转向；对象专属音效仍待补齐。
- 合并能共享交换链的覆盖窗口，保留确需独立输入区域的窗口。
- 迁移文字栅格化、字体回退和动画帧时序。

退出条件：普通桌宠视觉能力不创建 QWidget，不把 Qt 图片对象传回业务层；同一状态只产生一份后端无关视觉描述，Qt 与 DX 不再维护重复主题、布局或效果算法。

### 阶段 E：完整桌面运行时

- 已实现不依赖 Qt 的 `ApplicationRuntime`、EventPump、Scheduler、动态屏幕 provider、GDI PNG 截图、主宠、基础托盘、粒子/特效、世界对象、应用 UI 和完整 bundle；配置器在启动入口惰性注册。
- 阻断 PyQt 的独立子进程已覆盖 `ApplicationState.start()`、`APP_MAIN`、分阶段退出、运行时确认、backend cleanup 和初始化失败释放单实例锁。
- 仍需验证真实语音、聊天、更新、重启和单实例全链不依赖 Qt，并接入正式 Win32 消息等待。workbench helper 已使用版本 1 页面请求、进程复用和办公文件 IPC；DX 主进程只负责拉起 helper，不在自身进程构造 Qt 控件。音乐托盘清理已走 `cloudmusic.user_data` 无 Qt 数据路径；播放器 factory 由桌面组合入口注入，DX 明确注入 `None` 并使用 MCI，不导入或实例化 Qt 播放器。

退出条件：选择 DX 时，从进程启动到退出均不导入 `PyQt5`，自动化检查 `sys.modules` 和已加载 DLL 均无 Qt。

### 阶段 F：Qt 工作台隔离与依赖拆分

- 工作台和暂未迁移的复杂 Qt 工具页可作为独立 helper 进程启动；helper 已复用存活进程，并可按版本 1 请求切换目标页面。
- 办公页已通过版本 1 原子文件 IPC 交换任务快照、命令与权限决定，不共享 QWidget 或业务单例；其它设置页继续按阶段迁移到同一边界。
- 把 PyQt5 从普通桌宠核心依赖移到可选 Qt 后端/工作台依赖和对应发行包。
- 保留含 Qt 的兼容发行方式，直到 DX 后端经过稳定发布周期。

退出条件：不安装 PyQt5 时 DX 普通版可以安装、启动、运行、更新和退出；打开未安装的 Qt 工作台时给出明确可恢复提示。

## 9. 后端稳定性闸门

DirectX 已通过 `available=True, experimental=True` 开放给用户验证。只有同时满足以下条件，才允许把 `BACKEND_DESCRIPTORS` 中 `directx.experimental` 改为 `False`，将其视为稳定后端：

- 配置器注册完整 bundle，不依赖先执行 Qt 配置器产生的全局状态。
- 主宠、GIF、粒子、特效、世界对象、输入、屏幕、托盘和退出链均可运行。
- DX 进程启动期间未导入 PyQt5，也未加载 Qt DLL。
- 代表性 Qt 基准场景已迁入共享视觉层；同一状态生成相同命令，Qt 与 DX 在规定像素容差内一致。
- 硬件设备初始化、首帧提交和透明窗口验收成功。
- 设备丢失、部分初始化失败和重复 cleanup 不泄漏窗口、线程或 COM 资源。
- 自动回退只发生在进入 DX 主运行时之前；运行中失败走受控退出/下次回退，不在同一进程混装 Qt 和 DX 事件循环。
- 全量核心测试、DX 集成测试和至少一轮真实多屏/DPI 手工验证通过。

后端配置器失败时继续沿用现有 `BackendSelection` 语义。日志必须同时记录请求后端、实际后端、失败阶段、HRESULT 和适配器信息；实验性后端成功启动还要记录兼容性警告，失败时不得阻止 Qt 回退。

## 10. 验证矩阵

### 10.1 自动化

共享应用视觉和 bridge 边界的当前可执行入口为：

```powershell
$env:FLYING_SNOW_DX_DLL = (Resolve-Path 'native/dx_backend/build/cmake/Release/flying_snow_dx.dll')
py -3 -m unittest tests.test_visual_presenters tests.test_bubble_visual tests.test_qr_panel_visual tests.test_visual_backend_parity
```

- C ABI 版本、结构体大小、非法参数、重复释放和错误文本测试。
- 离屏截图像素测试：透明、alpha 预乘、排序、缩放、翻转、裁剪和字体。
- 视觉描述结构测试：选择 Qt、DX 或 Fake 后端时，同一状态和主题快照产生完全相同的布局与命令。
- Qt 基准保持测试和 Qt/DX-WARP 跨后端截图对比；几何、颜色、alpha 和资源必须一致，文字只允许有限的栅格边缘差异。
- 透明窗口像素与窗口样式测试，确认画面非空且背景透明。
- 鼠标点击穿透、捕获拖拽、键盘焦点和第三方输入法前景关系测试。
- 多屏负坐标、100%/125%/150% 混合 DPI 和 `WM_DPICHANGED` 测试。
- 强制设备重建已验证资源/窗口句柄、像素、可见性、generation、owner 线程和单次重试；仍需在专用 runner 注入真实 device removed，覆盖驱动返回路径。
- 阻断 PyQt 导入的独立子进程启动、事件、调度、截图和退出测试。
- 反复创建/销毁窗口和后端，检查线程、句柄、显存及 COM 引用泄漏。

### 10.2 CI 与发布

- Windows CI 安装 Windows SDK 和 CMake，分别构建 Debug/Release 原生库。
- Release 包 dry-run 校验 DLL、架构和 ABI 文件，DX 包不得意外携带 Qt DLL。
- 对不具备 GPU 的 CI 使用 WARP 跑离屏测试；透明窗口和硬件设备测试在专用 Windows runner 执行。
- x64 作为首个受支持架构；Python 位数、DLL 位数和发布包架构不一致时启动前明确失败。

### 10.3 手工验收

- 主宠闲置、拖拽、穿透、聊天输入、托盘、气泡、粒子、特效和世界对象。
- Qt 与 DX 在同机、同字体、同资源、同状态及 100%/125%/150% DPI 下并排对比视觉表现。
- 第三方输入法候选窗不被桌宠持续置顶遮挡，普通桌宠装饰窗口不抢输入焦点。
- 睡眠唤醒、锁屏解锁、显示器拔插、显卡驱动重启和远程桌面切换。
- 普通退出、异常退出、更新后重启及连续多次启动无残留进程。

## 11. 交付边界

每阶段提交必须同时包含契约测试、对应后端测试和文档更新。不得在一个提交中同时改变 C ABI 又不更新 ABI 版本；不得提交本地生成的 DLL、PDB、Release ZIP 或公告文件到源码仓库。

原生 C/C++ 源码属于源码仓库和开发者源码包；普通用户包不携带 `native/dx_backend` 源码，只在 DirectX 完整可用并通过发布验收后携带对应架构的 `flying_snow_dx.dll` 及版本/ABI 元数据。当前离屏原型 DLL 仅用于本地测试，不进入用户包。

第一阶段的成功标准是完成后端无关契约，而不是减少 `requirements.txt` 一行。最终完成标准是 DX 普通桌宠在没有 PyQt5 的干净环境中通过完整启动和退出验收，并且 Qt 工作台作为可选组件不反向污染主进程。