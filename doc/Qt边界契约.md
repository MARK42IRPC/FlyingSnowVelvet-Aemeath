# Qt 边界契约

更新时间：2026-10-09

本文档描述当前有效的 Qt 依赖边界。历史迁移阶段和已完成清单已删除；实现状态以源码、`tests/test_qt_dependency_boundaries.py` 和 `tests/test_code_structure_boundaries.py` 为准。跨后端视觉语义见 [视觉表现契约](视觉表现契约.md)。

本文只描述**已经成立**的边界，不描述待办。

> **迁移次序修订（2026-10-07）：** `frozen_ui_qt_importers` 里剩余的大文件（`ai_settings_panel.py`、
> `forum_board.py` 等）改为「先按模块拆分瘦身、再逐个收敛 Qt」。拆分只做纯移动、不得让冻结清单增长；
> 拆分出的新文件若仍 import Qt，必须显式登记而不是放宽断言。批次划分与每轮验收门槛见
> [Render 层边界契约](render层边界契约.md) 第 34 节，执行记录见第 35–56 节。
>
> 冻结清单当前为 50 项：第九轮把主论坛页的发帖屏切到
> `lib/script/ui/forum_composer.py`（第 56 节），`forum_board.py` 1726 → 1150 行；
> 此前批次 3 第八轮把面板外壳（窗口生命周期 + 自绘边框 + 悬浮标签栏）切到
> `lib/script/ui/ai_settings_shell.py`（第 55 节），面板本体自此只剩 153 行（多继承 + 更新页转发）；
> 第七轮把「配置取值 / 回填 / 恢复 / 异步保存」控制器切到
> `lib/script/ui/ai_settings_config_store.py`（第 54 节）；第六轮把面板的 AI 主页面
> （`_build_ui` + 取值/回填闭环）切到 `lib/script/ui/ai_settings_page.py`（第 53 节）；第五轮把「配置分类页 + 外部配置字段族」切到
> `lib/script/ui/ai_settings_config_page.py`（第 52 节）；
> 第四轮把面板的「配置编辑器控件族」切到
> `lib/script/ui/ai_settings_editors.py`（视图控件 + 工厂，第 51 节）——该页仍 import Qt，
> 按 34.2 节规则新增登记；
> 第三轮把面板整段 QSS 下沉到
> `lib/core/render/visuals/ai_settings_panel_visuals.py`（面板 3473 → 3234 行，第 50 节）——该文件在
> `lib/core/render/visuals/` 下且不 import Qt，不在本清单的扫描面内，故条目数不变；
> 第二轮把「桌宠更新」页切到 `ai_settings_update.py`
> （面板 3607 → 3473 行，第 49 节）；此前首轮把「支持作者 / 贡献者」两页切到
> `ai_settings_about.py`，该文件仍 import Qt 因此按规则新增登记（面板同时瘦身
> 3889 → 3607 行，第 48 节）；C 类的目标是后续轮次逐个去 Qt 再把条目删回去。
> 第三十三轮移出 `announcement_dialog.py` / `update_dialog.py` /
> `help_window.py` / `workbench_floating.py`，批次 1 与批次 2 各轮均未新增条目——批次 1 拆出的
> `ai_settings_contributions.py` 不含 Qt；批次 2 首轮的下沉目标是档位 D 的
> `backends/qt/widgets/forum_images.py`（不在该清单的扫描面内），`forum_board.py` 只减不加；
> 后续各轮的落点是 `lib/core/render/visuals/`、`lib/core/services/` 与
> `lib/script/ui/ai_settings_validation.py`、`ai_settings_descriptions.py`、
> `ai_settings_config_parse.py`（均无 Qt）等无 Qt 模块，同样不在扫描面内。

## 1. 依赖方向

```text
后端无关业务/控制器 -> lib/core 纯数据与服务协议
Qt 产品 UI          -> lib/core/render/backends/qt -> PyQt5
Qt 应用组合入口      -> Qt 产品 UI + Qt bridge
```

后端无关代码不得：

- 导入 `PyQt5` 或 `lib.core.render.backends.qt`；
- 在公开签名、事件载荷、配置或持久化数据中暴露 `QPoint`、`QRect`、`QImage`、`QPixmap`、`QPainter`、`QTimer` 等 Qt 类型；
- 通过 `Any`、`object` 或动态属性把 Qt 对象藏进核心协议；
- 调用 `QApplication.instance()`、`QTimer.singleShot()` 或 QWidget 方法；
- 为修正后端差异在 bridge 内私自保存产品颜色、布局、动画或业务状态。

跨边界几何、颜色、字体、图片和输入分别使用 `Point/Size/Rect/Color/FontSpec`、`RasterFrame/ImageResource` 与核心输入类型。事件载荷只传纯 Python 标量、容器、路径或这些稳定值对象。

## 2. 允许 Qt 的位置

以下目录是明确的 toolkit 边界：

- `lib/core/render/backends/qt/`：QApplication、窗口、绘制、字体、屏幕、调度、事件泵和 QtMultimedia 适配；其中 `application.py` 是 `QApplication` 实例的唯一取用点，`font.py` / `screen.py` / `screen_capture.py` 不得再直接调 `QApplication.instance()`；
- `lib/script/ui/`：产品 QWidget、工作台、对话框、动画播放器、游戏窗口和世界对象 UI；
- `lib/script/app/qt_backend_bootstrap.py`：Qt 桌面 bundle 组合；
- `lib/script/app/qt_application_ui.py`：Qt 产品 UI 生命周期组合；
- `lib/script/app/workbench_helper_entry.py`：隔离工作台进程入口；
- `lib/script/bug_tracker/__main__.py`：隔离故障跟踪进程入口。

自绘只允许发生在上面列出的 toolkit 边界里：`QPainter`、`QPainterPath`、`QRegion`、
`drawEllipse` / `drawPixmap` / `fillRect` 与 `paintEvent` 在 `lib/core/render/backends/qt/`、`lib/script/ui/` 和官方游戏包之外一律不出现，
其余模块只能构造 `DrawBatch`。

官方游戏包 v1 仍允许 `widget.py` 与 `render.py` 使用 Qt，因为当前游戏扩展契约直接创建 QWidget。其 `constants.py`、`model.py` 和 `skills.py` 必须保持纯 Python，不能因渲染需要导入 QColor 或其它 toolkit 类型。

## 3. 必须保持无 Qt 的位置

- `config/`；
- `lib/core/render/backends/qt/` 之外的 `lib/core/`；
- `lib/core/services/`：后端中立的业务服务，解析产品数值（世界对象物理、淡入时长、覆盖层滞留）并定义跨后端协议（`music_playback.MusicPlayerProtocol`），必须能在阻断 PyQt 的进程中加载，且不得导入 `lib/script`；
- `lib/script/chat/`、`office/`、`music/`、`gsvmove/`、`mainpet/`、`microphone_stt/`、`tool_dispatcher/`；
- `lib/script/workbench/` 的页面元数据、注册表、设置 schema 和主题数据；
- `lib/script/cloudmusic/` 的音乐业务实现；
- `lib/script/SEanima/` 的动画剪辑、解码、效果和进程调度；
- `lib/script/gemes/MAIN/` 的游戏包服务及惰性运行时门面；
- `lib/script/bug_tracker/` 中除独立 `__main__.py` 外的服务与存储。

这些包应能在阻断 PyQt 导入的进程中加载。需要显示界面时，通过组合入口、惰性页面工厂或注入的协议进入 `lib/script/ui`。

## 4. 当前组合边界

`DesktopBackendBundle` 原子提供应用运行时、应用 UI、调度、截图、主宠、托盘、覆盖层、屏幕和窗口宿主。`ApplicationState` 只消费 bundle，不直接导入具体 toolkit。

Qt 后端由 `lib/script/app/qt_backend_bootstrap.py` 注册。Qt 产品 UI 位于 `lib/script/ui`；`lib/core/render/backends/qt` 只完成原生对象转换、低级绘制和生命周期适配，不拥有产品页面。

工作台的元数据和 schema 位于 `lib/script/workbench`，QWidget 布局位于 `lib/script/ui/workbench_components.py` 与 `workbench_settings_layout.py`。游戏的公开入口 `lib/script/gemes/MAIN/runtime.py` 是无 Qt 惰性门面，真正的 QWidget 运行时位于 `lib/script/ui/game_runtime.py`。音乐播放器的后端中立协议在 `lib/core/services/music_playback.py` 的 `MusicPlayerProtocol`：命令是普通方法调用，结果走 `set_callbacks()` 注册的回调，不得再用 Qt 信号表达。Qt 实现在 `lib/core/render/backends/qt/runtime/music_player.py`，无 Qt 实现在 `lib/script/cloudmusic/_player.py` 的 `MciMusicPlayer`；两个后端的组合入口各自注入一个，`CloudMusicManager` 只驱动注入的那一个。

DirectX 主进程不得加载 PyQt。需要控制面板时只启动隔离工作台 helper；未迁移的复杂 Qt UI 不应被包装成伪跨后端控件。

一个进程只允许一个绘制后端生效，`lib/core/render/registry.py` 是唯一的安装点。bundle 只安装一次，安装者用**身份**（identity，不是字段相等）认领它：

- 同一个 owner 重复安装是空操作，保留首次安装的 bundle —— DX 的 `configure_dx_desktop_backend` 依赖这条路径重入；
- 第二个不同的后端拿到 `BackendAlreadyConfiguredError`，而不是在已生效的 bundle 上再覆盖一层；
- 安装中途失败由安装者自己撤回（`uninstall_desktop_backend_bundle`）。撤回按 owner 收口，没装过的后端无法借它顶掉正在生效的后端；半成品安装必须什么都不留下，否则路由的 Qt 回退会撞上一个已经被拆掉的后端从而拒绝启动。

`#后端` 命令不改运行时：它只写配置再走 `APP_QUIT{restart: True}` 重启，所以「换后端」永远是下一个进程的事。

需要区分「绘制后端」和「绘制执行器」：`DrawCore` 经 `draw_backend_factory` 建的那一个是后端；`lib/script/ui/` 与 `lib/core/render/backends/qt/` 中按控件直接构造的 `QtDrawBackend()` 是绑定 QPainter 的执行器，各自持有自己的 pixmap 缓存，不构成第二个后端。

产品数值只有一份来源：`lib/core/services/` 负责解析（`world_object_physics` 的世界对象物理参数、`ui_presentation` 的 UI 淡入时长与覆盖层滞留），两个 bridge 与 `lib/script/ui` 都只消费解析结果。世界对象的物理参数由 `resolve_world_object_physics()` 统一读 `PHYSICS` / `MORTOR` / `SNOWBALL` / `SNOW_LEOPARD` / `BEHAVIOR`，任何后端不得再自行读这些字典：此前 Qt 的七个世界对象页面与 DX 的 `world_object_backend` 各读一份，副本之间已经出现过可验证的默认值分歧（`ui_fade_duration` 一处回退 180、另一处 200）。技术步长（拖拽采样窗口、雪花特效密度、响铃秒数）不是产品数值，仍留在各自 bridge。本地音乐播放器同样只有一份契约：`MusicPlayerProtocol` 定义在 `lib/core/services/music_playback.py`，Qt 侧 `QtMusicPlayer` 与无 Qt 侧 `MciMusicPlayer` 各自实现它，`CloudMusicManager` 只调接口、不再区分后端。

## 5. 审计与验证

静态测试必须覆盖：

- `config` 和非 bridge 核心零 Qt 导入；
- 后端中立业务包零 Qt/`lib.core.render.backends.qt` 导入；
- `lib/core` 不反向导入 `lib.script`，启动入口除外；
- `lib/core`（含 `lib/core/render/visuals`）不读取 `config/config_ui.py`：产品色板由 `lib/core/render/visuals/palette.py` 唯一拥有，`config/config_ui.py` 只重新导出同一 `COLORS` / `UI_THEME` 对象；
- 已迁移的面板 QWidget 宿主只执行共享 `DrawBatch`，不得直接填充面板或排版文字；尚无命令原语的矢量图标可以保留在宿主；
- `lib/core/render/backends/qt/` 中只有 `application.py` 可以出现 `QApplication.instance()`，其余模块必须经 `get_application()`；
- 世界对象管理器、事件协议和图形契约不暴露 toolkit 类型；
- 世界对象物理参数只在 `lib/core/services/world_object_physics.py` 解析：DX 的 `world_object_backend` 与 `lib/script/ui/world_objects/` 都不得再导入 `PHYSICS` / `MORTOR` / `SNOWBALL` / `SNOW_LEOPARD` / `BEHAVIOR`，两个后端解析出的数值必须逐项相等；
- `lib/core/render/visuals/` 下的 presenter 块不得导入 `PyQt5`、`lib.script`、`lib.core.render.backends.qt`、
  `lib.core.render.backends.dx` 或 `config.config_ui`：它们是两个后端共用的事实源，必须能在任一边加载；
- `ui/` 对 `lib/script/{chat,office,music,gsvmove}` 的导入是一份冻结清单：新增耦合会失败，清单里已不存在的条目
  也会失败；私有子模块一律不允许；
- 阻断 PyQt 后核心与 DirectX 交互路径仍可导入运行；
- 一个进程只生效一个绘制后端：`tests/test_backend_router.py` 的 `SingleRenderBackendTests` 与 `tests/dx/test_dx_desktop_backend.py` 的同批用例断言第二个后端被拒、同 owner 重装保留原 bundle、失败安装可撤回并给回退后端留出位置；
- 缺 PyQt5 时工作台 helper 只给可恢复中文提示（进程内提示文本 + 一个信息框）并以非 0 码退出，不得向用户泄露 `ModuleNotFoundError` 堆栈，也不得把其余崩溃一并吞掉。

验证命令：

```powershell
py -3 -m compileall -q config lib scripts install_deps.py install_deps
py -3 -m unittest tests.test_qt_dependency_boundaries tests.test_code_structure_boundaries -v
py -3 -m unittest tests.test_visual_presenters tests.test_shared_panel_visuals tests.test_visual_backend_parity -q
py -3 -m unittest tests.test_backend_neutral_services tests.test_workbench_helper -q
py -3 -m unittest tests.test_backend_router tests.dx.test_dx_desktop_backend -q
py -3 -m unittest discover -s tests -p "test_*.py" -q
git diff --check
```

新增 Qt 例外时，必须说明它为何属于组合入口或 toolkit 实现。不得只把路径加入白名单来绕过目录归属问题。
