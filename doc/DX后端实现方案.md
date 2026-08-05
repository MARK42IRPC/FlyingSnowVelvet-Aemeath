# DirectX 后端实现方案

更新时间：2026-08-04

本文档定义 Windows DirectX 桌面后端的技术路线、迁移边界和验收条件。目标不是只实现一个 DX 绘制器，而是让普通桌宠运行进程最终不导入 PyQt5、不加载 Qt DLL，同时保留现有 Qt 后端作为迁移期回退和独立工作台实现。

当前 Qt 边界和后端无关契约以 `doc/Qt收敛方案.md`、`lib/core/backend_router.py`、`lib/core/desktop_backend.py` 及源码测试为准。本文档中的目录和接口草案属于后续实施约束，尚未落地的内容不得当作现有能力。

## 1. 目标与非目标

### 1.1 目标

- Windows 普通桌宠启动、渲染、输入、调度、托盘、截图和退出不依赖 Qt。
- 使用 `directx` 稳定后端 ID 接入现有 `BackendRouter`，配置入口和回退语义保持不变。
- 以 Direct3D 11 和 Windows 系统图形组件完成透明桌面窗口、2D 图片、文字、粒子和特效绘制。
- Python 继续承载业务状态和编排；高频绘制、窗口消息和 GPU 资源生命周期放入原生模块。
- Qt 后端在迁移期间始终可运行，用户选择未完成的 DX 后端时继续明确回退 Qt。

### 1.2 非目标

- 首版不采用 Direct3D 12。桌宠是 2D 合成负载，D3D12 的显式同步和资源管理成本没有直接收益。
- 不在首版重写工作台、设置页、游戏窗口等全部复杂 QWidget 控件。
- 不同时实现 DirectX、OpenGL 和 Vulkan；先用 DirectX 验证后端契约是否完整。
- 不在 Python 中逐条调用 COM API，也不让渲染线程持有或回调 Python 对象。
- 不因 DX 原型可显示一张图片就移除 Qt 依赖或将后端标记为可用。

## 2. 当前基线与阻塞点

现有路由已经提供 `qt`、`directx`、`opengl`、`vulkan` 稳定 ID，控制面板通过 `UI.render_backend` 保存选择。当前只有 `qt` 的 `BackendDescriptor.available=True`，未实现后端会记录原因并回退 Qt。

核心算法和多数业务载荷已经改用 `Point`、`Rect`、`Color`、`FontSpec`、`MouseInput` 和 `KeyboardInput` 等纯数据类型。主宠绘制链已经使用纯 RGBA 资源和不可变命令批，但以下边界仍阻止普通运行进程移除 Qt：

- `lib/script/main.py` 已只从 `DesktopBackendBundle` 获取应用运行时、调度、截图、主宠、覆盖层和托盘工厂；这些工厂返回的对象仍暴露部分 Qt 生命周期行为，尚未收敛为完整后端无关宿主协议。
- `DrawScene` 已只保存 `ImageResource/RasterFrame`，并生成不可变 `DrawBatch/SpriteCommand`；当前命令集只覆盖主宠 sprite，尚未覆盖文字、形状、粒子和特效。
- `DrawBackend.render(batch, target, viewport)` 的 target 仍由后端宿主持有；当前唯一实现使用 `QPainter`，DX target 尚未实现。
- 跨后端 `RenderRequest/RenderItem/PaintCallback` 已删除；两个明确的 Qt 游戏控件使用 `qt_bridge.render_core.QtRenderCore` 本地回调，尚未迁移为声明式命令。
- `WorldObjectBackend` 使用不透明图片和实例句柄，当前唯一实现仍创建 QWidget 世界对象。
- `LayerManager` 已只依赖最小 `LayerWindowHost`，Qt 可见性、前置、原生句柄和 `SetWindowPos` 已迁入 `qt_bridge.window_host`；窗口创建、几何、输入和场景提交等完整宿主能力仍待抽象。
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
| 文字 | DirectWrite | 字体回退、度量、抗锯齿和布局 |
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

### 5.2 声明式绘制命令

跨后端 `PaintCallback` 已删除，Qt 独占控件回调已移入 `qt_bridge`。当前 `SpriteCommand/DrawBatch` 已提供已解析资源帧、透明度、翻转、缩放和 `layer/z/order`；后续按同一批次扩展：

- `SpriteCommand`：资源、源帧、目标矩形、透明度、翻转和插值模式；
- `TextCommand`：文本、`FontSpec`、颜色、布局矩形、对齐和裁剪；
- `RectCommand/EllipseCommand`：填充、描边和线宽；
- `ClipPush/ClipPop` 与 `TransformPush/TransformPop`；
- 粒子和特效使用上述基础命令或专用批命令。

每帧由 Python 生成一份连续命令批，一次跨 C ABI 提交。禁止每个 sprite、粒子或文字进行一次 Python 到 DLL 调用。命令结构包含 `abi_version`、结构体大小和帧序号，未知命令必须返回可诊断错误，不能越界解析。

跨后端排序继续使用 `layer/z/order`。仅 Qt 独占 UI 可暂时使用 `QtRenderCore` 本地 painter 回调，不能把该路径注册为 DX 场景内容；需要迁移到 DX 的视觉内容必须产出命令。

### 5.3 窗口宿主

第一步已落地后端无关 `LayerWindowHost`：稳定 identity 用于注册和注销，`is_alive/is_visible` 用于过滤窗口，`stack_window` 返回后端原生整数 token，原生堆叠不可用时通过 `raise_window` 回退。`LayerManager` 只负责 `layer/z/order` 排序和触发时机，不再识别 QWidget、HWND 或 `SetWindowPos`。Qt 适配器弱持有 QWidget，桌面后端通过工厂注册；未配置后端时使用无副作用宿主保证核心可独立运行。

后续在该最小协议之上补齐后端无关 `WindowHost`/`WindowManager`，至少覆盖：

- 创建、显示、隐藏、关闭透明无边框窗口；
- 读取和设置 `Point/Rect`、DPI 和所属屏幕；
- 点击穿透、是否激活、光标和捕获状态；
- layer/z 排序、前置请求和原生整数句柄；
- 请求重绘及提交场景；
- 幂等 `cleanup()`。

DX 层级实现直接使用 HWND；完整 Qt 窗口实现继续用适配对象包装 QWidget。窗口层级只在注册、显示、交互或层级变化时重申，不持续抢占前台或按帧强制 `HWND_TOPMOST`。需要第三方输入法的可编辑窗口必须允许正常激活和 IME z-order，装饰窗口才使用 `WS_EX_NOACTIVATE`。

主宠 Win32 窗口将 `WM_MOUSE*`、`WM_POINTER*`、键盘、移动、DPI 和关闭消息转换为现有 `PetHostCallbacks` 纯数据调用。拖拽期间使用鼠标捕获，点击穿透通过窗口扩展样式和 `WM_NCHITTEST` 切换，不模拟 Qt 事件。

### 5.4 桌面运行时能力

DX 组合入口必须一次性注册一组完整服务：

- `ApplicationRuntime`：Win32 消息循环、调度、退出确认和残留窗口关闭；
- `EventPump`：线程安全队列加主线程唤醒消息；
- `Scheduler` 和 deferred call：基于等待计时器或统一调度队列；
- 屏幕能力：虚拟桌面、按点选屏、DPI 和 PNG 截图；
- 主宠、粒子、特效和世界对象窗口宿主；
- 托盘图标、菜单命令和资源图标加载；
- 绘制资源仓库和 DX 场景提交。

`desktop_backend.py` 已把当前绘制、`ApplicationRuntime`、`ApplicationUiHost`、`Scheduler`、`ScreenCapture`、主宠窗口、`OverlayHost` 工厂、`TrayHostFactory`、事件泵、延迟、屏幕、截图和层级窗口宿主工厂收进单个不可变 `DesktopBackendBundle`，但仍只覆盖上述完整能力的一部分。新增能力应继续扩展明确协议和组合对象，不能恢复彼此无关的模块全局变量，也不能让 `lib/script/main.py` 为每个后端分别导入一串具体实现。

最终组合入口只负责：读取配置、注册可用后端配置器、执行路由、创建所选 `DesktopBackendBundle`。`ApplicationState` 已从 bundle 获取运行时、调度、截图、主宠、覆盖层和托盘服务，并只通过 `PetWindowHost.shutdown_host()`、`OverlayHost.cleanup()` 和 `TrayHost.cleanup()` 执行退出；完整窗口创建、几何、输入和重绘服务仍需继续迁移，最终不得直接导入 `qt_bridge` 或 `dx_bridge`。

两个启动组合边界已经完成：`ApplicationUiHost` 统一承接字体、公告、预加载、CMD、提示面板、登录 UI 和退出动画；启动入口按后端选择惰性导入配置器，`main.py` 不注册或导入 Qt。当前实施 `WindowHost v1`，随后扩展声明式绘制命令。DX 第一份可执行成果必须是 WARP 离屏渲染与像素测试，不是用户可见透明窗口。

## 6. C ABI 与事件模型

ABI 只传固定宽度整数、浮点数、UTF-8 字节块、带长度数组和不透明整数句柄。所有导出函数返回状态码，线程局部错误文本通过独立函数读取。禁止跨边界抛出 C++ 异常。

建议最小能力组：

```text
fsdx_get_abi_version
fsdx_create_runtime / fsdx_destroy_runtime
fsdx_create_window / fsdx_destroy_window
fsdx_set_window_state
fsdx_register_resource / fsdx_release_resource
fsdx_submit_frame
fsdx_poll_events
fsdx_request_exit
fsdx_get_last_error
```

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

坐标统一使用物理桌面像素；Win32 边界负责 logical/physical 转换。进程声明 Per-Monitor V2 DPI awareness，处理 `WM_DPICHANGED` 推荐矩形，并覆盖负坐标、多屏不同缩放和屏幕热插拔。

## 8. 分阶段实施

### 阶段 A：契约补齐

- 已完成主宠和世界对象统一资源描述、纯 RGBA 帧、缩放、不可变 sprite 批次、Qt 后端 revision 缓存及整数世界对象实例句柄。
- 已从跨后端契约删除 `PaintCallback` 并保留 Qt 本地适配；文字、形状、粒子和特效命令仍待补齐。
- 已新增最小 `LayerWindowHost` 和 Qt 层级适配器，移除 `LayerManager` 对 QWidget/Win32 方法的直接调用；完整窗口生命周期协议仍待补齐。
- 已将应用运行时、调度器、截图服务以及主宠、覆盖层、托盘工厂和当前桌面能力收进 `DesktopBackendBundle`；主宠、覆盖层和托盘对象行为已收敛为 `PetWindowHost`、`OverlayHost` 和 `TrayHost`，Qt signal、QWidget 销毁及托盘单例释放不再泄漏到 `ApplicationState`。完整窗口创建、几何、输入和重绘契约仍待迁移。
- 已完成 `ApplicationUiHost` 和启动组合入口拆分：`ApplicationState` 不再导入 Qt/UI 模块或注册后端，Qt 配置器由惰性 bootstrap 安装；当前进入 `WindowHost v1`，随后扩展文字/形状/裁剪命令。

退出条件：核心和跨后端业务数据中不存在 QImage/QPixmap/QPainter/QWidget；阻断 PyQt 导入的子进程测试可实例化核心场景、层级和应用编排。

### 阶段 B：DX 离屏绘制原型

- 建立 CMake/MSVC 构建和 C ABI 版本检查。
- 在 WARP 和硬件设备上渲染 sprite、文字、透明度、翻转、裁剪和排序。
- 输出 PNG 供自动化比较，不创建用户可见窗口。

退出条件：像素基线、透明边缘、资源释放、错误码和 ABI 不匹配测试通过。此阶段 `directx.available` 仍为 `False`。

### 阶段 C：主宠透明窗口

- 实现 Win32 + DirectComposition 主宠窗口和帧提交。
- 接入 `PetHostCallbacks`、拖拽、点击穿透、DPI、多屏和窗口层级。
- 覆盖窗口显示/隐藏、输入捕获、屏幕热插拔和设备丢失恢复。

退出条件：主宠可独立运行，但粒子、特效、托盘或退出链任一能力仍依赖 Qt 时，不得标记 DX 可用。

### 阶段 D：视觉与世界对象

- 将 GIF、粒子、特效、气泡及七类世界对象迁移到统一资源和 scene/window 协议。
- 合并能共享交换链的覆盖窗口，保留确需独立输入区域的窗口。
- 迁移文字栅格化、字体回退和动画帧时序。

退出条件：普通桌宠视觉能力不创建 QWidget，不把 Qt 图片对象传回业务层。

### 阶段 E：完整桌面运行时

- 实现 Win32 `ApplicationRuntime`、EventPump、Scheduler、屏幕截图和托盘。
- 将 `main.py` 的具体 Qt 导入移入 Qt 组合模块，按路由惰性导入后端。
- 验证语音、聊天、更新、重启、单实例和优雅退出不依赖 Qt 事件循环。

退出条件：选择 DX 时，从进程启动到退出均不导入 `PyQt5`，自动化检查 `sys.modules` 和已加载 DLL 均无 Qt。

### 阶段 F：Qt 工作台隔离与依赖拆分

- 将工作台和暂未迁移的复杂 Qt 工具页作为可选独立 helper 进程启动。
- 主进程与 helper 只通过版本化 IPC 交换设置快照和命令，不共享 QWidget 或业务单例。
- 把 PyQt5 从普通桌宠核心依赖移到可选 Qt 后端/工作台依赖和对应发行包。
- 保留含 Qt 的兼容发行方式，直到 DX 后端经过稳定发布周期。

退出条件：不安装 PyQt5 时 DX 普通版可以安装、启动、运行、更新和退出；打开未安装的 Qt 工作台时给出明确可恢复提示。

## 9. 后端可用性闸门

只有同时满足以下条件，才允许把 `BACKEND_DESCRIPTORS` 中 `directx.available` 改为 `True`：

- 配置器注册完整 bundle，不依赖先执行 Qt 配置器产生的全局状态。
- 主宠、GIF、粒子、特效、世界对象、输入、屏幕、托盘和退出链均可运行。
- DX 进程启动期间未导入 PyQt5，也未加载 Qt DLL。
- 硬件设备初始化、首帧提交和透明窗口验收成功。
- 设备丢失、部分初始化失败和重复 cleanup 不泄漏窗口、线程或 COM 资源。
- 自动回退只发生在进入 DX 主运行时之前；运行中失败走受控退出/下次回退，不在同一进程混装 Qt 和 DX 事件循环。
- 全量核心测试、DX 集成测试和至少一轮真实多屏/DPI 手工验证通过。

后端配置器失败时继续沿用现有 `BackendSelection` 语义。日志必须同时记录请求后端、实际后端、失败阶段、HRESULT 和适配器信息，不向用户伪装为已启用 DirectX。

## 10. 验证矩阵

### 10.1 自动化

- C ABI 版本、结构体大小、非法参数、重复释放和错误文本测试。
- 离屏截图像素测试：透明、alpha 预乘、排序、缩放、翻转、裁剪和字体。
- 透明窗口像素与窗口样式测试，确认画面非空且背景透明。
- 鼠标点击穿透、捕获拖拽、键盘焦点和第三方输入法前景关系测试。
- 多屏负坐标、100%/125%/150% 混合 DPI 和 `WM_DPICHANGED` 测试。
- 模拟 device removed，验证资源重建和失败上限。
- 阻断 PyQt 导入的独立子进程启动、事件、调度、截图和退出测试。
- 反复创建/销毁窗口和后端，检查线程、句柄、显存及 COM 引用泄漏。

### 10.2 CI 与发布

- Windows CI 安装 Windows SDK 和 CMake，分别构建 Debug/Release 原生库。
- Release 包 dry-run 校验 DLL、架构和 ABI 文件，DX 包不得意外携带 Qt DLL。
- 对不具备 GPU 的 CI 使用 WARP 跑离屏测试；透明窗口和硬件设备测试在专用 Windows runner 执行。
- x64 作为首个受支持架构；Python 位数、DLL 位数和发布包架构不一致时启动前明确失败。

### 10.3 手工验收

- 主宠闲置、拖拽、穿透、聊天输入、托盘、气泡、粒子、特效和世界对象。
- 第三方输入法候选窗不被桌宠持续置顶遮挡，普通桌宠装饰窗口不抢输入焦点。
- 睡眠唤醒、锁屏解锁、显示器拔插、显卡驱动重启和远程桌面切换。
- 普通退出、异常退出、更新后重启及连续多次启动无残留进程。

## 11. 交付边界

每阶段提交必须同时包含契约测试、对应后端测试和文档更新。不得在一个提交中同时改变 C ABI 又不更新 ABI 版本；不得提交本地生成的 DLL、PDB、Release ZIP 或公告文件到源码仓库。

第一阶段的成功标准是完成后端无关契约，而不是减少 `requirements.txt` 一行。最终完成标准是 DX 普通桌宠在没有 PyQt5 的干净环境中通过完整启动和退出验收，并且 Qt 工作台作为可选组件不反向污染主进程。
