# Qt 边界契约

更新时间：2026-08-30

本文档描述当前有效的 Qt 依赖边界。历史迁移阶段和已完成清单已删除；实现状态以源码、`tests/test_qt_dependency_boundaries.py` 和 `tests/test_code_structure_boundaries.py` 为准。跨后端视觉语义见 [视觉表现契约](视觉表现契约.md)。

## 1. 依赖方向

```text
后端无关业务/控制器 -> lib/core 纯数据与服务协议
Qt 产品 UI          -> lib/core/qt_bridge -> PyQt5
Qt 应用组合入口      -> Qt 产品 UI + Qt bridge
```

后端无关代码不得：

- 导入 `PyQt5` 或 `lib.core.qt_bridge`；
- 在公开签名、事件载荷、配置或持久化数据中暴露 `QPoint`、`QRect`、`QImage`、`QPixmap`、`QPainter`、`QTimer` 等 Qt 类型；
- 通过 `Any`、`object` 或动态属性把 Qt 对象藏进核心协议；
- 调用 `QApplication.instance()`、`QTimer.singleShot()` 或 QWidget 方法；
- 为修正后端差异在 bridge 内私自保存产品颜色、布局、动画或业务状态。

跨边界几何、颜色、字体、图片和输入分别使用 `Point/Size/Rect/Color/FontSpec`、`RasterFrame/ImageResource` 与核心输入类型。事件载荷只传纯 Python 标量、容器、路径或这些稳定值对象。

## 2. 允许 Qt 的位置

以下目录是明确的 toolkit 边界：

- `lib/core/qt_bridge/`：QApplication、窗口、绘制、字体、屏幕、调度、事件泵和 QtMultimedia 适配；
- `lib/script/ui/`：产品 QWidget、工作台、对话框、动画播放器、游戏窗口和世界对象 UI；
- `lib/script/app/qt_backend_bootstrap.py`：Qt 桌面 bundle 组合；
- `lib/script/app/qt_application_ui.py`：Qt 产品 UI 生命周期组合；
- `lib/script/app/workbench_helper_entry.py`：隔离工作台进程入口；
- `lib/script/bug_tracker/__main__.py`：隔离故障跟踪进程入口。

官方游戏包 v1 仍允许 `widget.py` 与 `render.py` 使用 Qt，因为当前游戏扩展契约直接创建 QWidget。其 `constants.py`、`model.py` 和 `skills.py` 必须保持纯 Python，不能因渲染需要导入 QColor 或其它 toolkit 类型。

## 3. 必须保持无 Qt 的位置

- `config/`；
- `lib/core/qt_bridge/` 之外的 `lib/core/`；
- `lib/script/chat/`、`office/`、`music/`、`gsvmove/`、`mainpet/`、`microphone_stt/`、`tool_dispatcher/`；
- `lib/script/workbench/` 的页面元数据、注册表、设置 schema 和主题数据；
- `lib/script/cloudmusic/` 的音乐业务实现；
- `lib/script/SEanima/` 的动画剪辑、解码、效果和进程调度；
- `lib/script/gemes/MAIN/` 的游戏包服务及惰性运行时门面；
- `lib/script/bug_tracker/` 中除独立 `__main__.py` 外的服务与存储。

这些包应能在阻断 PyQt 导入的进程中加载。需要显示界面时，通过组合入口、惰性页面工厂或注入的协议进入 `lib/script/ui`。

## 4. 当前组合边界

`DesktopBackendBundle` 原子提供应用运行时、应用 UI、调度、截图、主宠、托盘、覆盖层、屏幕和窗口宿主。`ApplicationState` 只消费 bundle，不直接导入具体 toolkit。

Qt 后端由 `lib/script/app/qt_backend_bootstrap.py` 注册。Qt 产品 UI 位于 `lib/script/ui`；`lib/core/qt_bridge` 只完成原生对象转换、低级绘制和生命周期适配，不拥有产品页面。

工作台的元数据和 schema 位于 `lib/script/workbench`，QWidget 布局位于 `lib/script/ui/workbench_components.py` 与 `workbench_settings_layout.py`。游戏的公开入口 `lib/script/gemes/MAIN/runtime.py` 是无 Qt 惰性门面，真正的 QWidget 运行时位于 `lib/script/ui/game_runtime.py`。音乐 Qt 播放器位于 `lib/core/qt_bridge/music_player.py`，业务管理器只依赖注入的播放器协议。

DirectX 主进程不得加载 PyQt。需要控制面板时只启动隔离工作台 helper；未迁移的复杂 Qt UI 不应被包装成伪跨后端控件。

## 5. 审计与验证

静态测试必须覆盖：

- `config` 和非 bridge 核心零 Qt 导入；
- 后端中立业务包零 Qt/qt_bridge 导入；
- `lib/core` 不反向导入 `lib.script`，启动入口除外；
- 世界对象管理器、事件协议和图形契约不暴露 toolkit 类型；
- 阻断 PyQt 后核心与 DirectX 交互路径仍可导入运行。

验证命令：

```powershell
py -3 -m compileall -q config lib scripts install_deps.py install_deps
py -3 -m unittest tests.test_qt_dependency_boundaries tests.test_code_structure_boundaries -v
py -3 -m unittest discover -s tests -p "test_*.py" -q
git diff --check
```

新增 Qt 例外时，必须说明它为何属于组合入口或 toolkit 实现。不得只把路径加入白名单来绕过目录归属问题。
