# Changelog

本文件记录飞行雪绒 LTS 系列的公开版本变更。版本标签与发布包名称保持一致，例如 `LTS1.0.7pre1`。

## [未发布]

目标版本：`LTS1.0.7pre7`（`config/version_info.py` 与 README 已抬版）。

### Added
- 控件层「描述 + 后端渲染」新增 UI 线程调度宿主：`lib/core/render/backends/qt/widgets/ui_dispatch.py`
  的 `UiDispatcher` 把「保证回调在 UI 线程执行」收成一处（跨线程走 `QueuedConnection`、本线程
  就地执行），`render_bridge.create_ui_dispatcher()` 是控件侧唯一落点。此前公告 / 论坛 / 帮助 /
  办公控制器各自声明 `pyqtSignal(object)` 仅为回到 UI 线程，因而被迫继承 `QObject` 并 import
  `PyQt5`；控制器现在只持有调度宿主，不再声明信号。
- 窗口描述层补齐浮窗要件：`visuals/window_spec.py` 新增 `RichTextSpec`（`QTextBrowser` 只读 HTML）
  与 `ProgressBarSpec`（`QProgressBar`），并为既有词汇补 `stretch` / `min_height` / `default_font` /
  `collapse_when_empty` / `border_mid` / `border_fill`；`visuals/window_specs.py` 新增
  `announcement_window_spec()` 与 `update_window_spec()`。宿主 `spec_host.py` 承接富文本、进度条、
  嵌套布局、空行自动收起与三层描边外壳；`render_bridge` 新增 `create_floating_window_base()` /
  `create_window_button()` / `floating_window_classes()` / `window_button_icons()` 四个转发落点。
- 设置面板贡献名单逻辑抽成后端中立模块：新增 `lib/script/ui/ai_settings_contributions.py`
  （不 import `PyQt5`），`ai_settings_panel.py` 的贡献名单解析/加载、路径解析、编码兜底读取、
  手工条目常量全部下沉到此，面板只留三个委托；`ai_settings_panel.py` 5439 → 5261 行。
  行为与旧实现逐条相等（真实 `开发贡献.txt`：解析 15 条、加载 14 条全等），
  `frozen_ui_qt_importers` 不变。新增 `tests/test_ai_settings_contributions.py` 12 条断言。
- 设置面板配置 schema 与取值格式化抽成后端中立模块：新增
  `lib/script/ui/ai_settings_config_schema.py`（不 import `PyQt5`），把
  `_CATEGORY_KEY_ALLOWLIST` 等十张 schema 表与 `_category_section_entries` /
  `_hardcoded_general_default` / `_format_config_editor_value` 等七个取值函数下沉，
  共 531 行、17 个符号；`ai_settings_panel.py` 4862 → 4318 行。逐符号比对与旧实现相等，
  冻结清单不变。新增 `tests/test_ai_settings_config_schema.py` 11 条断言。
- 设置面板文案/名称表抽成后端中立模块：新增 `lib/script/ui/ai_settings_labels.py`
  （不 import `PyQt5`），`_SECTION_HELP_TEXTS` / `_DICT_FRIENDLY_NAME` / `_KEY_FRIENDLY_NAME`
  三张表与 `section_help_text` / `friendly_*` / `animation_folder_display_name` 全部下沉，
  面板只从新模块导入；`ai_settings_panel.py` 5261 → 4862 行。行为逐项相等，冻结清单不变。
  新增 `tests/test_ai_settings_labels.py` 11 条断言。
- 拆分登记机制固化为可回归断言：`tests/test_code_structure_boundaries.py` 新增
  `test_split_ui_modules_are_registered_not_silently_allowed`，钉住「拆出的新文件无论嵌套多深
  都会被扫描、两个清单都按 `lib/script/ui/` 前缀接受子包路径」。

- 论坛图片视图族与列表两行下沉到档位 D 宿主：新增
  `lib/core/render/backends/qt/widgets/forum_images.py`（619 行），承接 `ForumImageView` /
  `ForumImageThumb` / `ForumDetailImage` 与 `ForumPostRow` / `ForumReplyRow`，以及它们共用的
  排版辅助（`_font` / `_block_alignment` / `_apply_like_state` / `_clear_layout` /
  `_color_span_at`）与常量（`LIST_THUMB_SIZE` / `COMPOSE_THUMB_SIZE` / `BODY_WIDTH_HINT` /
  `FORUM_IMAGE_MAX_PIXELS` 等）。为此把两处事实源上移到后端中立层：图片描边宽度
  `FORUM_IMAGE_FRAME` 落到 `lib/core/render/visuals/forum_visuals.py`（`forum_style.py` 按原
  名字重新导出），UI 字体入口改为可注入（默认走档位 B 的字体服务，`forum_board.py` 显式注入
  产品面同一入口）。`lib/script/ui/forum_board.py` 2236 → 1723 行，只留 `ForumBoardPage` 并按
  原语义重新导出上述名字，既有导入面（`forum_account.py`、测试）零改动；
  `frozen_ui_qt_importers` 不变。改前 / 改后三屏（列表 / 详情 / 发帖页，760×620）逐像素比对
  差异为 **0**。执行记录见 `doc/render层边界契约.md` 第 39 节。

- 论坛卡片底纹的规格层抽成后端中立模块：新增
  `lib/core/render/visuals/forum_texture_visuals.py`，承接花纹候选与取值范围、`CardTexture`、
  `texture_seed()` / `card_texture()` 与绘制期几何助手；`lib/script/ui/forum_texture.py`
  696 → 508 行，只留 `paint_card_texture()` / 纹理层缓存与各 `_paint_*`，并按原名字重新导出，
  既有导入面不变。改前 / 改后底纹位图（四张固定卡片 × 深色主题，240×180）逐像素比差异 **0**。
  执行记录见 `doc/render层边界契约.md` 第 40 节。

- 办公聊天气泡的 Markdown→富文本转换抽成后端中立模块：新增
  `lib/core/render/visuals/office_chat_rich.py`（`md_to_rich` 与正则表），
  `lib/script/ui/office_chat_view.py` 342 → 284 行，只留控件树、气泡宽度与主题取色，
  颜色仍由产品面传参。`HEAD` 与改后对同一组 10 个用例输出逐字符相等；既有导入面不变。
  执行记录见 `doc/render层边界契约.md` 第 41 节。

- 办公 / 手动 API 的纯解析逻辑抽成后端中立模块：新增 `lib/core/services/api_endpoints.py`，
  承接 `MANUAL_API_PROVIDER_PRESETS`、`normalize_api_base_url` / `manual_api_models_url` /
  `parse_api_models` / `fetch_api_models`；`lib/script/ui/office_mode_settings.py` 按原名再导出，
  `ai_settings_panel.py` 的导入面零改动，`re` / `requests` 从该 UI 文件顶层消失。为此把
  `API_TIMEOUT_SECS` / `API_RETRY_COUNT` / `API_TOTAL_ATTEMPTS` 从
  `lib/script/chat/network_policy.py` 下沉到 `lib/core/services/network_policy.py`（原路径改成
  重导出垫片），修掉一处 `lib/core` → `lib/script` 的反向依赖。本机 DSH 探测因需产品包仍留在
  `office_mode_settings.py`，`ui -> 产品包` 清单由两项减到一项。逐行与逐字符比对、15 组地址 +
  7 组载荷行为等价。执行记录见 `doc/render层边界契约.md` 第 42 节。

- 设置面板「通用配置」取值校验抽成无 Qt 模块：新增
  `lib/script/ui/ai_settings_validation.py`，承接逐键 / 跨键 / 整段三个校验入口与
  `get_choice_field_options` / `raise_config_value_error` / `validate_general_numeric`；
  动画目录选项与工程根目录由面板以参数注入，新模块不 import `PyQt5` 也不 import `SEanima`，
  失败抛 `ConfigValueError`（`ValueError` 子类）。`ai_settings_panel.py` 4158 → 3991 行，
  六个面板方法改为转发，异常类型与消息逐字符不变。验收：把 `HEAD` 的六个方法原样摘出成
  独立类做对照，30 组单键取值 + 5 组整段入口的异常类型与消息差异为 0。执行记录见
  `doc/render层边界契约.md` 第 43 节。

- 设置面板字段说明文本抽成无 Qt 模块：新增 `lib/script/ui/ai_settings_descriptions.py`，
  承接 `build_config_single_description` / `build_config_range_description` /
  `description_value_type` / `description_preview_value` 四个纯函数（含动画倍速与
  `ui_cache_preload` 两处文案特例）。只读 `ai_settings_config_schema` 与 `ai_settings_labels`，
  面板四个方法改为转发、调用点与既有断言零改动；`ai_settings_panel.py` 3991 → 3942 行。
  验收：HEAD 四方法摘出成独立类对照，40 组输入差异 0。执行记录见
  `doc/render层边界契约.md` 第 44 节。

- 设置面板配置编辑器取值解析抽成无 Qt 模块：新增 `lib/script/ui/ai_settings_config_parse.py`，
  承接 `parse_text_by_template`（文本 / 布尔 / 整数 / 小数，其余 `ast.literal_eval`）与
  `parse_editor_value`（范围对 / 数组 / 音量滑块 / 小数滑块 / 单值按 `kind` 分派）。
  五个控件类型判定与音量百分比换算改由面板以 `widget=` 注入（新增五个静态谓词），
  新模块既不 import `PyQt5`、也不认识控件类；面板两个方法改为转发，
  `ai_settings_panel.py` 3942 → 3889 行。验收：HEAD 两方法用同名替身控件对照 36 组，差异 0。
  执行记录见 `doc/render层边界契约.md` 第 45 节。

- 语音包安装器浮窗的 QSS 并入后端中立视觉层：`visuals/workbench_chrome.py` 新增
  `voice_installer_stylesheet(mode=None)`（共享外壳 + 安装器的下拉框尺寸档、
  两条进度条 chunk 配色与三个按钮 id 着色），与既有 `floating_window_stylesheet()` 并列；
  `voice_package_installer.py` 的同名方法改为一行转发，文件内不再需要 `_color()` 助手。
  验收：深 / 浅两个主题下与拆分前输出逐字符全等（各 6033 字符）。执行记录见
  `doc/render层边界契约.md` 第 46 节。

- 设置面板「支持作者 / 贡献者」两页切到新模块：新增 `lib/script/ui/ai_settings_about.py`
  （414 行，仍在 `lib/script/ui/` 下并 import Qt），承接两类只读页的**视图**
  （`build_sponsor_author_panel` / `build_contribution_list_panel` 与共用的
  `_ContributionCardButton`）、**控制器**（`open_sponsor_author_link` / `open_contribution_link` /
  `set_sponsor_author_image`，提示文案经 `show_info` 回调注入）与**描述**
  （`sponsor_author_stylesheet` / `contribution_list_stylesheet` 两段页面 QSS）。
  `ai_settings_panel.py` 3889 → 3607 行，四个页面/控制器方法改为按原签名转发，
  `_ContributionCardButton` 按原名再导出（`refresh_workbench_theme` 与既有测试零改动）。
  验收：两页与 `HEAD` 逐行等价（差异仅签名 10 / 10 / 0 行）；面板 `styleSheet()`
  与切分前逐字符全等（9450 字符）；离屏 1000×760 宿主下两页控件树摘要全等、位图
  SHA-256 相等（差异像素 0）。`frozen_ui_qt_importers` 42 → 43（按第 34.2 节显式登记
  `ai_settings_about.py`，C 类目标是后续轮次逐个去 Qt 再删回）。
  执行记录见 `doc/render层边界契约.md` 第 48 节。

- 设置面板「桌宠更新」页切到新模块：新增 `lib/script/ui/ai_settings_update.py`（274 行），
  承接**视图**（`build_desktop_pet_update_panel`：四个分区与五个按钮）与**控制器**
  （`ensure_update_dialog` / `open_update_dialog` / `on_check_updates` / `on_sync_dev_build` /
  `ensure_qq_group_dialog` / `open_quark_manual_update` / `show_qq_group_qrcode` / `uninstall_pet`），
  面板状态经 `PetUpdateActions` 数据类显式注入。`ai_settings_panel.py` 3607 → 3474 行，
  八个动作方法改成 `_run_pet_update_action()` 薄壳并把对话框实例写回面板字段（状态唯一），
  同时清掉四个搬走后不再使用的导入与两个已下沉常量。
  验收：视图与 `HEAD` 逐行等价（差异仅签名）；`styleSheet()` 9450 字符全等；
  离屏 1000×760 三页控件树摘要全等、位图 SHA-256 相等（差异像素 0）、
  `_config_tab_meta` 键集与按钮对象名相等；`test_update_uninstall_button.py` 的 patch 目标
  改到真正的所有者 `ai_settings_update`。`frozen_ui_qt_importers` 43 → 44。
  执行记录见 `doc/render层边界契约.md` 第 49 节。

- 设置面板整段 QSS 下沉到后端中立视觉层：新增
  `lib/core/render/visuals/ai_settings_panel_visuals.py`（364 行），承接 `_apply_style()` 原地
  拼的 243 行样式表（配置区 / 表单标签一族、`QMenu` / `QScrollArea` / `QComboBox` / `QSpinBox` /
  `QSlider` 各态与滚动条），以及「支持作者 / 贡献者」两页的内联 QSS 片段与三个像素档常量；
  `ai_settings_panel._apply_style()` 改为一行转发，`ai_settings_about` 的两个样式函数改为按原名
  转发。新模块只读 `COLORS` / `UI_THEME` 与工作台 token，不 import `PyQt5`、不在
  `frozen_ui_qt_importers` 扫描面内（仍为 44 项）。`ai_settings_panel.py` 3473 → 3234 行。
  验收：面板 `styleSheet()` 与拆前逐字符全等（9450 字符，SHA-256 相等）；离屏 1000×760 三页
  控件树摘要全等、位图 SHA-256 相等（差异像素 0）；新增
  `tests/test_ai_settings_panel_visuals.py` 5 条。执行记录见 `doc/render层边界契约.md` 第 50 节。

- 设置面板「配置编辑器控件族」切到新模块：新增 `lib/script/ui/ai_settings_editors.py`（548 行），
  承接三个自定义控件类（`_NoWheelSlider` / `_DecimalSliderField` / `_AnimationDurationSliderField`，
  逐行原样搬出）与 `ConfigEditorMixin`（六个编辑器工厂、字段谓词、音量百分比换算、序列/成对
  编辑与两个「浏览 / 打开」助手）。`AISettingsPanel` 改为继承该 mixin 并按原名再导出
  `_DecimalSliderField` / `_AnimationDurationSliderField` / `_GENERAL_DECIMAL_SLIDER_SPECS`，
  面板 3234 → 2778 行。顺手修掉一处 `HEAD` 上已存在的真实缺陷：协议名
  `volume_value_from_percent`（不带下划线）在面板上从未定义，`kind == "volume_slider"` 的字段
  保存时抛「格式错误」，导致「音频 / 音乐」页的音量滑条（`SOUND.master_volume` 等 5 项）一直存不进去；
  本轮补上协议别名并加断言钉住。验收：八页控件树 / 位图 / `_config_tab_meta` oracle 零差异，
  `styleSheet()` 9450 字符逐字符全等；`frozen_ui_qt_importers` 44 → 45。
  执行记录见 `doc/render层边界契约.md` 第 51 节。

- 设置面板「配置分类页 + 外部配置字段族」切到新模块：新增
  `lib/script/ui/ai_settings_config_page.py`（538 行），承接 338 行的
  `_build_config_category_panel`（分区、表单、成对/序列/滑条/路径/布尔各分支装配与
  `_config_tab_meta` 写入）与开机启动 / 公告永久抑制两组外部配置字段
  （含 `_apply_external_category_fields`）。`AISettingsPanel` 改为继承 `ConfigPageMixin`，
  `ai_settings_tabs.py` 的调用点零改动，面板 2778 → 2291 行；清掉随之下沉使用的十二个导入。
  `tests/test_workbench_lazy_settings.py` 的两处 patch 目标改到真正的所有者
  `ai_settings_config_page`。验收：八页控件树 / 位图零差异（差异像素 0），并额外比对
  `_config_tab_meta` 的 `fields` / `defaults` / 按钮清单——配置页元数据零差异；
  `styleSheet()` 9450 字符逐字符全等。`frozen_ui_qt_importers` 45 → 46。
  执行记录见 `doc/render层边界契约.md` 第 52 节。

- 设置面板 AI 主页面切到 `lib/script/ui/ai_settings_page.py`（`AISettingsPageMixin`，1179 行）：
  508 行的 `_build_ui` 与本页专属的取值/回填闭环（`_collect_values`、Ollama 与手动 API 模型刷新、
  `_update_*_visibility` 一族）以及页面动作（恢复默认 / 保存 / 保存并重启）整体搬出。
  `AISettingsPanel` 改为多继承本 mixin，`ai_settings_panel.py` 2291 → 1237 行；
  `frozen_ui_qt_importers` 46 → 47（第 53 节）。八页控件树 / 位图 oracle 差异为 **0**，
  `styleSheet()` 9450 字符逐字符全等，全量 2269 条通过（10 跳过），`ruff`、`compileall`、
  `git diff --check`、边界测试 52 条均干净。执行记录见 `doc/render层边界契约.md` 第 53 节。

- 设置面板「配置取值 / 回填 / 恢复 / 异步保存」控制器切到
  `lib/script/ui/ai_settings_config_store.py`（`ConfigStoreMixin`，436 行）：按 `_config_tab_meta`
  取值与回填、默认值兜底与逐分类恢复、通用配置取值校验、异步保存任务调度与三个保存入口整体搬出，
  模块级 `_save_general_config` / `_apply_general_runtime` 同行。`AISettingsPanel` 改为多继承本
  mixin，`ai_settings_panel.py` 1237 → 858 行，并**摘掉面板对产品包的最后一处 `lib.script.music`
  耦合**（延迟导入随函数搬走）；`frozen_ui_qt_importers` 47 → 48（第 54 节）。八页控件树 / 位图
  与 `_config_tab_meta` oracle 差异为 **0**，`styleSheet()` 9450 字符逐字符全等。
  执行记录见 `doc/render层边界契约.md` 第 54 节。

- 设置面板「面板外壳」切到 `lib/script/ui/ai_settings_shell.py`（`AISettingsShellMixin`，702 行）：
  `__init__` 的窗口装配、自绘 `paintEvent`、边框粒子 `TICK` 订阅、鼠标拖拽与上下文菜单、
  项目字体、悬浮标签栏与配置面板布局转发、`show_centered` / `fade_out` 显隐动画、工作台挂载与
  四个只读页装配整体搬出（51 个方法），窗口档位常量与 `_ui_thread_call` 信号同行。
  `AISettingsPanel` 改为多继承本 mixin，`ai_settings_panel.py` 858 → **153 行**，
  只剩多继承、项目根钩子、更新页转发与贡献名单委托；`frozen_ui_qt_importers` 48 → 49（第 55 节）。
  八页控件树 / 位图 oracle 差异为 **0**，`styleSheet()` 9450 字符逐字符全等。
  执行记录见 `doc/render层边界契约.md` 第 55 节。

- 论坛发帖屏切到 `lib/script/ui/forum_composer.py`（`ForumComposerMixin`，642 行）：
  发帖屏的装配（`_build_composer` 与字号 / 对齐 / 颜色三个控件族助手）、正文编辑区的一整套行内操作
  （行内标记 / 字号 / 对齐 / 文字色 / 描边色 / 插入文本与插图）、发布与字数计数器联动整体搬出，共 27 个方法；
  `FORUM_SIZE_STEPS` 与 `ForumColorControl` 别名同行。`ForumBoardPage` 改为多继承本 mixin（置首），
  `forum_board.py` 1726 → 1150 行，只留列表 / 详情 / 会话与调度，并按原名重新导出 `FORUM_SIZE_STEPS`；
  `frozen_ui_qt_importers` 49 → 50（第 56 节）。改前 / 改后列表 / 详情 / 发帖（空白与填写）五个阶段
  的控件树与 `grab()` 位图 SHA-256 差异均为 **0**。执行记录见 `doc/render层边界契约.md` 第 56 节。

### Changed
- 删除 `ai_settings_panel.py` 里已死的 N 卡能力探测链路：
  `_refresh_nvidia_acceleration_capability_async()` 已无任何调用点（两个调用位在某次重构中
  消失），`_nvidia_gpu_present` 因此恒为 `False`，`_update_gsv_settings_visibility()` 里
  `and nvidia_present` 的第二项恒假、N 卡开关实际上从不显示。清理掉该方法、
  `has_nvidia_gpu` 导入与三个属性，`visible` 判断与采集只保留语音包可用性一项——
  对外表现逐项不变，并新增断言钉住"开关只由语音包决定"。
- 删除 `ai_settings_panel.py` 里已死的硬件/水印一族（`_query_hardware_watermark_lines` /
  `_gpu_pick_score` / `_format_gb_text` / `_MEMORYSTATUSEX` 等十个符号及 `ctypes` / `json`
  两个死导入）：该族全仓无调用点，面板的硬件水印走 `startup_probe.load_saved_watermark_payload()`。
  `_gpu_mode_from_num_gpu` / `_num_gpu_from_mode` 与三个 `_GPU_MODE_*` 有真实调用，保留。
  `ai_settings_panel.py` 4318 → 4159 行。
- 公告浮窗（`announcement_dialog.py`）与更新浮窗（`update_dialog.py`）改为「窗口描述 + 后端宿主」：
  两个 `DesktopPet*Dialog` 不再是 `WorkbenchFloatingWindow` 子类、不再 import `PyQt5`，
  `AnnouncementController` 不再继承 `QObject`，`pyqtSignal` 用后端中立替身 `_AnnouncementSignal` /
  `_UpdateSignal`（只保留 `connect` / `emit` / `disconnect`）；`workbench_floating.py` 改为纯再导出
  垫片，基类与窗眉按钮工厂经 `render_bridge` 转发。`frozen_ui_qt_importers` 46 → **42**
  （移出 `announcement_dialog.py` / `update_dialog.py` / `help_window.py` / `workbench_floating.py`，
  无新增）。验收门槛与执行记录见 `doc/render层边界契约.md` 第 33 节。
- 办公模式设置块的构造期探测改为走模块全局：`OfficeModeSettings` 原写作 `probe or probe_local_dsh`，
  把函数对象在导入时就捕获进实例，测试与宿主事后替换模块属性对已建实例无效
  （`tests/test_office_mode_page.py` 原有的 patch 因此形同虚设）。现改为 `probe or (lambda: probe_local_dsh())`，
  测试改为 patch 真正的所有者，本机 DSH 的「探测到 / 未探测到」分支第一次被真正驱动。
- 版本基线抬到 `LTS1.0.7pre7`（`config/version_info.py`、README、安装器内置资源包 URL 同步）。
- 控件层收敛推进次序修订：剩余 `lib/script/ui` 大文件（`ai_settings_panel.py` 5439 行、
  `forum_board.py` 2236 行等）改为**先按模块拆分瘦身、再逐个收敛 Qt**。理由是一个改动同时承担
  拆分与去 Qt 时，像素 oracle 报出的差异无法归因，冻结清单也会在同一文件上连续两轮被触碰。
  新次序分四批：批次 0 先让冻结清单与 `ui -> 产品包` 耦合清单能登记拆分出的新文件；批次 1 抽离
  纯能力（贡献名单解析、硬件探测、配置格式化等无 Qt 逻辑）；批次 2 把需跨文件共享的 Qt 宿主
  下沉到 `lib/core/render/backends/qt/widgets/`（档位 D）；批次 3 才按 tab/页切产品页本体并与
  去 Qt 同轮交付。拆分轮必须是"纯移动"（`git diff -M --find-copies` 逐行等价、零像素差异），
  收敛轮沿用逐像素 oracle；`frozen_ui_qt_importers` 只减不增。次序、验收门槛与风险记于
  `doc/render层边界契约.md` 第 34 节，执行检查表见 `doc/维护手册.md`。

### Fixed
- 修复公告浮窗重新打开后按钮全部失效的问题：`QtSpecWindow` 的“已给出决定”标志
  从未在 `show_window()` 里重置，而修改后的公告 / 更新浮窗只收起窗口（不结束生命周期），
  因此首次点任意关闭按钮（标题栏× / 最小化 / 今日不再显示 / 永远不再显示 / 关闭）后，
  再打开时 `resolve_with()` 会直接 return，所有按钮都静默失效。现在重新显示视为新一次交互，
  重置该标志；两轮打开 / 关闭的回归测试已针对此固定。
- 修复启动预热（“启动期预绘制缓存”）在 `progress_panel` 上报
  `AttributeError: 'ProgressPanel' object has no attribute 'render'` 的问题：控件层迁出后
  `ProgressPanel` 不再是 `QWidget`，离屏预热应画它的描述宿主（`_host`）而非自身；
  `_warm_paint` 现会在窗口描述控件上自动选择可画目标，实在无法画时静默跳过。

## [LTS1.0.7pre6] - 2026-09-22

### Added
- 音乐工具支持一次点多首：`play_music` 新增 `queries` 数组参数，模型可以把「放《纸飞机》《逆潮》
  《碎花》」一次传完；只支持文本协议的回退网关则用顿号（或换行）连写多个歌名。调度器逐个
  搜索，第一首照常 `MUSIC_PLAY_TOP` 立即播放，其余按顺序 `MUSIC_ENQUEUE` 追加队列末尾，并给
  一条「正在播放 N 首歌曲」汇总气泡；批量入队带 `silent=true`，不再逐首弹一串「已加入播放队列」。
  办公模式的桌宠工具 `play_music` 走同一条路径，工具记录与回包给出「已让音响依次播放 N 首：…」。
  单首点歌行为不变。

### Changed
- 控制面板「系统调度」页新增「图层顺序」分区，把 `config/config_layer.py` 的
  `LAYER_VALUES`（背景 / 世界物体 / 桌宠本体 / 粒子 / 特效 / 面板 / 对话框 / 提示框等 11 项）
  搬进设置界面，按「数值越大越靠前」给出中文说明，保存走稀疏用户配置、与 GUI 源值共享同一份默认，
  和其它设置一样重启后完整生效。原先只能用 `#图层` 命令查看图层快照，现在直接在这里调。
### Changed
- 控件层「描述 + 后端渲染」新增模态宿主：`lib/core/render/backends/qt/widgets/message_box_host.py`
  的 `QtMessageBoxHost` 持有真实 `QMessageBox` 与 `exec_()` 模态循环，图标/按钮组合/文字/
  默认键/逃逸键/层级注册都由调用方以后端中立参数声明；`render_bridge` 新增
  `create_message_box_host()` 作为控件侧唯一落点。共享确认/提示框（`confirm_dialog.py`）
  据此迁出 Qt、删除 `PyQt5` 依赖，`frozen_ui_qt_importers` 57 → 56；`ask_confirmation` /
  `show_message` 调用面不变。这也补上了 `exec_()` 类对话框迁移的"宿主支持模态"前置项。
- 渲染层继续下沉浮窗外壳：`lib/script/ui/workbench_floating.py` 的样式生成、主题变更判定
  与拖拽/窗口按钮策略，可拆的部分抽到后端中立的 `lib/core/render/visuals/workbench_chrome.py`
  （不 import Qt、不 import `lib.script`），真实 `QWidget` 宿主与窗口按钮工厂落到
  `lib/core/render/backends/qt/widgets/floating_window.py` 与 `window_buttons.py`；`workbench_floating.py`
  358 → 39 行，改为保留历史导入名的再导出垫片，二维码登录/更新/下载/公告/帮助浮窗与主
  工作台窗口共用同一份外壳实现。浮窗 QSS 与收敛前逐字符一致（新增测试
  `tests/test_workbench_chrome.py` 钉死内联按钮样式与工作台 token 的一致性）。
- 办公线性图标抽成后端中立规格：九段 SVG 与逻辑尺寸落到
  `lib/core/render/visuals/office_icons.py`，Qt 的 `QSvgRenderer` 渲染落到
  `lib/core/render/backends/qt/widgets/office_icons.py`，`render_bridge` 新增
  `render_office_icon()` / `render_office_icon_pixmap()` 落点。`lib/script/ui/office_icons.py`
  改为门面垫片、不再 `import PyQt5`，`frozen_ui_qt_importers` 56 → 55；办公页与审批
  对话框的图标调用面不变。新增 `tests/test_office_icons.py` 逐字符钉死每个图标的成品 SVG。
- 点击粒子辅助去 Qt：`_particle_helper.py` 不再 import `PyQt5`，按钮名改经
  `render_bridge.pointer_button_name()` 翻译，粒子 ID 用 `visuals/controls.py` 的
  `BUTTON_PARTICLES` 共享映射；`frozen_ui_qt_importers` 55 → 54，调用面不变。新增
  `tests/test_particle_helper.py` 覆盖左右键映射与事件载荷。
- 论坛样式去 Qt：`forum_style.py` 的底纹混色 `forum_texture_color()` 改为纯十六进制
  整数混色（与 `QColor` 逐通道 `round()` 结果逐字节一致），删除 `QColor` 依赖，
  `frozen_ui_qt_importers` 54 → 53；新增 `tests/test_forum_style.py` 钉死 14 组混色输出。
- 二维码登录浮窗宿主能力下沉：`BaseQrDialog` 提供单次自动收起 `QTimer`、`hide_dialog()`
  停表与 `set_clickthrough()`；`yuanbao_login_dialog.py` 改为复用、不再 import `PyQt5`，
  `frozen_ui_qt_importers` 53 → 52。新增 `tests/test_yuanbao_login_dialog.py`。
- 网易云二维码登录面板去 Qt：`BaseQrDialog` 的 `window_flags` 新增 `"login"` 预设
  （不进任务栏/不抢焦点）并提供 `restore_soon()` 自愈；`cloudmusic_login_dialog.py`
  改用共享的窗口标志、自愈与 `set_clickthrough()`，不再 import `PyQt5`，
  `frozen_ui_qt_importers` 52 → 51。
- 翻页按钮（上一页 / 下一页）去 Qt：`page_turn_buttons.py` 的 `_PageTurnButton` 改为
  「描述 + 宿主装配」，共享绘制新增 `application_visuals.build_page_turn_button_visual()`、
  共享状态新增 `visuals/controls.py` 的 `PageTurnButtonControl`；`QtControlHost` 新增
  `accepts_focus=False`，附属控件不再抢键盘焦点。删除 `PyQt5` 依赖，
  `frozen_ui_qt_importers` 51 → 50。
- 音响菜单控制按钮族去 Qt：`speaker_control_buttons.py`（暂停/播放、下一曲、登录音乐、
  平台模式、播放模式、搜索优先级、播放列表、一键历史/清空/本地/喜欢、音量加减共 12 个按钮
  与组管理器）改为「描述 + 宿主装配」。共享绘制新增
  `application_visuals.build_speaker_action_button_visual()`（面板底壳复用
  `panel_visuals.action_button_commands()`；几何图标与文字居中），共享状态新增
  `visuals/controls.py` 的 `SpeakerActionButtonControl`；`render_bridge` 新增
  `local_anchor_point()` / `core_point()` 两个后端中立落位助手。删除 `PyQt5` 依赖，
  `frozen_ui_qt_importers` 50 → 49。新增 `SpeakerControlButtonBehaviorTests` 与
  `SpeakerActionButtonVisualTests`（含逐像素核对几何图标中心取自 Qt `QRect.center()` 的
  `x + (w - 1) // 2`）。同时修掉翻页按钮族第二十七轮遗留的两处偏差：共享三角形的
  图标中心曾按 `x + w / 2` 取值（差一个像素），且上一页/下一页箭头方向与迁移前 Qt
  绘制相反。
- 音响菜单族样式去 Qt：`speaker_menu_style.py` 的 `_C_*` 常量从 `QColor` 改成核心
  `Color`（共享色板唯一事实源），面板壳与动作按钮的配方下沉到
  `application_visuals.build_speaker_panel_visual()` /
  `build_speaker_action_button_visual()`，新增描述层的 `SpeakerPanelSpec` /
  `SpeakerActionButtonSpec`。控件是 `QWidget` 子类、`paintEvent` 必须自己起
  `QPainter`，因此新增档位 D 宿主
  `backends/qt/widgets/control_painter_host.py`（`QtPainterHost`）与
  `render_bridge.create_painter_host()` / `painter_color()`：控件只交一份批次、
  由注入的绘制实现落像素。删除 `PyQt5` 依赖，`frozen_ui_qt_importers` 49 → 48；
  `SpeakerActionButtonMixin` 不再设置窗口标志/尺寸/光标/图层（那些由使用它的
  `QWidget` 自己调）。新增 `SpeakerMenuStyleFacadeTests`：30 组面板/按钮状态与
  迁移前 Qt 绘制**逐字节相等**，队列删除/立即播放按钮与工作台「关于」按钮三只真实
  控件改前/改后像素也逐字节相等。本轮同时真实修掉 `painter_color` 把 `#rrggbb`
  主题令牌当通道元组拆开的回归（工作台「关于」按钮曾在 `int('#', 10)` 上炸掉），
  并钉死该边界对核心 `Color` / 通道元组 / 十六进制文本三种输入的取值。
- 修复进度条迁移遗留的运行期崩溃：`progress_panel` 从 `QWidget` 改成"描述 + 宿主"
  后只保留了 `width/height/isVisible` 三个转发，同族的 `playlist_panel` 仍在调
  `progress_panel.x()` / `.y()`（迁移前那是 `QWidget.x()`），于是
  `show_for() -> _show_progress_panel() -> _update_progress_panel_position() ->
  _update_control_buttons_position() -> progress_panel.x()` 抛 `AttributeError`。
  异常发生在 `_set_control_buttons_visible(True)` **之前**，整族控制按钮（含搜索
  按钮）跟着一起不出现，症状看起来像"搜索按钮消失了"。`ProgressPanel` 补回
  `x()` / `y()` 转发，与 `page_turn_buttons` / `speaker_control_buttons` 的对外
  视图面一致。新增 `MigratedControlHostViewTests` 三条断言：视图方法齐全、
  `_update_control_buttons_position()` 整条链在真实控件上跑通并真的摆了九个按钮、
  以及同族模块不得对已迁出 `QWidget` 的控件调用未转发的几何方法（静态回归面）。
- 修复音响搜索框「搜索歌曲」按钮落位错误：`speaker_menu_style` 的门面在第三轮收敛时
  把 `QRect` 交给描述层，却只传了宽高、丢掉了原点（构建器硬编码 `Rect(0, 0, w, h)`）。
  搜索框在同一个 painter 上并排画输入区（`x = 0`）与按钮（`x = _INPUT_W`），于是按钮
  被画到 `0.._BTN_W`、整块压在输入区上——看起来就是"搜索按钮消失了"。`SpeakerPanelSpec` /
  `SpeakerActionButtonSpec` 与 `build_speaker_panel_visual` / `build_speaker_action_button_visual`
  新增可选 `origin`（默认原点，单控件宿主调用面不变），门面把真实原点传下去。新增
  `SpeakerMenuStyleFacadeTests` 的非零原点逐字节对照（含面板壳，修前必红）与
  `test_search_button_paints_to_the_right_of_the_input_box` 整窗像素断言（修前必红）。
- 控件层「描述 + 后端渲染」补上窗口级描述：前几轮收敛的是单个叶控件（`visuals/controls.py`），带子控件树的对话框一直缺一级描述。本轮新增`lib/core/render/visuals/window_spec.py`（窗口描述的词汇：文本/图标/accent bar/输入区/按钮/空隙 + 行列容器 + `WindowSpec`）与 `window_specs.py`（按窗口语义组装的产品共享装配件），以及档位 D 的后端宿主 `lib/core/render/backends/qt/widgets/spec_host.py`（`QtSpecWindow` 按描述装配真实控件树，并把按钮点击、关闭按钮、Esc/窗口管理器关窗收敛成同一个决定）。屏幕归属与绘制实现由 `render_bridge.create_spec_window()` 注入，宿主不静态引用档位 A。办公审批弹窗（`office_approval_dialog.py`）据此迁出 Qt、不再继承 `QDialog`，只收审批状态、产出窗口描述、翻译 `reject`/`allow`/`allow_task` 语义，`frozen_ui_qt_importers` 47 → 46，调用面（`approval_id` / `decision_made` / `dismiss_without_decision()` / `findChild()` / `destroyed`）不变。顺手修掉`office_approval_controller` 用闭包连 `destroyed` 导致弹窗销毁时抛 `NameError: cannot access free variable 'self'` 的既有缺陷（改为连绑定方法）。新增 `tests/test_window_spec.py` 十条断言：描述层在 PyQt5 被屏蔽的进程里独立工作、中立模块不 import Qt/`lib.script`/任一后端、窗口宿主不越档、审批弹窗的控件树与语义逐项由描述产出。
- 帮助浮窗去 Qt，窗口描述宿主补齐工具窗能力：`lib/script/ui/help_window.py` 不再 import
  `PyQt5` 的窗口/控件符号、也不再是 `QWidget` 子类（同文件的 `HelpWindowController` 仍是
  `QObject`，与并列的 `office_approval_controller.py` 同理，故该文件仍在
  `frozen_ui_qt_importers` 里并注明理由）。控件树、窗口标志（无边框 `Qt.Tool` + 置顶 + 半透明）、
  固定尺寸、`LayerManager` 的 `DIALOG` 层级注册、`windowOpacity` 淡入淡出与 ``paintEvent``
  描边外壳全部由 `visuals/window_specs.py` 的 `help_window_spec()` 描述、
  `backends/qt/widgets/spec_host.py` 装配；滚动正文用新下沉的
  `backends/qt/widgets/smooth_scroll.py`（原 `workbench_settings_layout.SmoothScrollArea`，
  设置页 / 论坛 / 帮助共用的同一份手感）。描述层新增 `kind` / `layer` / `fade` /
  `hide_semantics` / `border_frame` / `fixed_size` 等窗口字段与 `LayoutSpec.scroll`，
  「关闭」这类只收起窗口的语义不会结束窗口生命周期。迁移 oracle：迁移后的壳层与收敛前
  `HEAD` 版本**逐像素相等**（两种主题各 440×380 全图比对），并顺手修掉过程中暴露的
  关闭按钮垂直居中（应为贴顶）偏差。`tests/test_window_spec.py` 17 → **25** 条断言；
  `MIGRATED_LEAF_CONTROLS` 移出 `help_window.py`（它已不再自己落位，改由窗口描述宿主负责）。
- 办公面样式去 Qt：`office_style.py` 整份文件曾因为两个控件树辅助函数而 import `PyQt5`，
  但那 600 行里约 500 行是把工作台 token 拼成 QSS 的纯字符串逻辑。本轮拆开：整份 QSS、
  五档推理强度档位色、气泡内边距与设置页字号档下沉到后端中立的
  `lib/core/render/visuals/office_chrome.py`（工作台 token 走 `visuals/workbench_tokens.py`；
  原先经 `lib.script.workbench.theme` 再进 `lib.core.render.visuals` 的绕行也一并去掉）；
  `apply_office_fonts()` / `create_office_accent_bar()` 这类必须真的遍历 / 构造 `QWidget`
  的辅助落到 `lib/core/render/backends/qt/widgets/office_widgets.py`，由 `render_bridge`
  的 `apply_settings_page_fonts()` / `apply_office_widget_fonts()` / `create_office_accent_bar()`
  转发（与 `create_painter_host` / `render_office_icon` 同属解析/转发层）。`office_style.py`
  改为保留历史导入名的薄门面，`frozen_ui_qt_importers` 48 → 47，调用面不变。两条不反向
  依赖的约束决定了签名：档位数量 `effort_steps` 与设置页字号档都由产品侧门面喂进来，
  因为 `lib/core/render` 不得 import `lib.script`。新增 `tests/test_office_style.py`：
  15 组（三种模式 × standalone × 两个页名）QSS 与收敛前**逐字节相等**、档位色按模式取
  权威 token 对照、字号档镜像与产品面逐值一致、门面与中立模块都不再 import `PyQt5`。
### Changed
- 自动更新包覆盖提速：覆盖阶段不再对每个文件读两遍内容，只比同名文件的大小（字节正确性
  已由资源包的 SHA-256 在下载时兜住），拷贝并发发起。真实 733 MiB / 17,195 文件的资源包
  实测从 690 秒降到 29 秒，同一份包重跑 8 秒。待补装（`apply_pending_overlay`）仍然比对
  真实字节——那里「判同」等于丢弃备份。
- 福利 API 档的自动陪伴间隔固定为 6 分钟一次，设置面板不再提供这条滑条，改由一行说明
  交代原因。这一档的优先级高于游戏模式覆盖：公共接口有限流，间隔越短越容易被限流。
- 渲染层目录收敛到 `lib/core/render/` 的三层：根层是后端中立的解析/转发/路由（`router.py` 选后端、
  `registry.py` 装服务、`backends/base.py` 定义 `DesktopBackendBundle`），`visuals/` 是共享视觉事实源，
  `backends/qt/` 再分 `drawing/`（命令到 QPainter 的执行）与 `runtime/`（窗口、输入、调度、字体、屏幕、
  托盘、播放器等平台能力）。`lib/core/backend_router.py` 与 `lib/core/desktop_backend.py` 迁入根层，
  旧路径不保留兼容壳。业务层与 `lib/script/ui` 对后端的引用面按「冻结基线，只减不增」的测试清单锁定，
  新增直接引用会失败。DX 仍是 `available=False` 的实验实现，暂不切分 `drawing/`、`runtime/`。
- 图层能力收敛到 `lib/core/render/layers/`：`lib/core/layer.py`（绘制层枚举与排序）、
  `lib/core/layer_manager.py`（顶层窗口 z-order）、`lib/core/window_host.py`（后端中立窗口宿主协议）
  与 `lib/core/render/visuals/ordering.py` 合并成一个包，旧路径不保留兼容壳。包内按职责分文件：
  `spec.py`（`Layer`）、`order.py`（排序原语）、`draws.py`（层内 `z` 槽 `BASE`..`OVERLAY_THIRD`）、
  `hosts.py`（宿主协议）、`windows.py`（`WindowLayer` / `WindowsLayerManager`）、`scene.py`。
  同时把「画布绘制层」与「顶层窗口层级」拆成两个枚举：窗口注册统一走 `WindowLayer`，
  绘制批次继续用 `Layer`，两者共享 `config/config_layer.py` 的同一份数值。presenter 里手写的
  `z=1..7` 字面量改为 `layers.draws` 的具名槽，渲染结果逐字节不变
  （`tests/test_unified_draw_order.py` 与像素比对测试为准）。
- 绘制落点收敛：新增 `lib/script/ui/render_bridge.py`，作为控件取绘制实现、主题色与坐标转换的唯一
  UI 侧入口。`lib/script/ui` 里直接 import `lib/core/render/backends/qt/drawing/` 的文件从 31 个降到 0，
  新增引用会被测试叫停。未配置后端时它回退到真实 Qt 实现而不是空实现——空实现会让控件静默画不出
  东西，把像素比对测试的回归伪装成通过（本轮真的踩到过一次）。控件直接 `import PyQt5` 的名单另有一份
  只减不增的冻结清单，避免 UI 的 Qt 面在无人留意时长回来。主题色事实源仍是共享色板，本层只做
  `Color -> QColor` 的边界转换。
- 渲染后端协议化与统一数据类型：新增 `PresentationHost` / `FontProvider` / `TextMetrics`
  三个后端中立协议（`lib/core/render/backends/base.py`），由组合入口注入、经
  `lib/script/ui/render_bridge.py` 取用。屏幕归属、位置夹取、控件全局矩形现在返回核心
  `Rect`/`Point` 而不是 `QRect`/`QPoint`（`Rect` 补了 `center`/`right`/`bottom`）；
  字体与文本度量按后端自己的对象提供。`lib/script/ui` 对具体后端运行时路径的直接引用
  从 51 个文件降到 0，两份冻结清单（`drawing/`、`runtime/`）现在都是空的。
  产品页面共享的 QWidget 基类与锚点助手另立 `backends/qt/widgets/`，`runtime/text_metrics.py`
  迁入 `drawing/`，三条旧路径的转发模块一并删除。
- 控件层改为「描述 + 后端渲染」，气泡框成为首个不再继承 `QWidget` 的产品控件：新增后端中立的
  `lib/core/render/visuals/controls.py`（`BubbleControl` 状态机、`BubbleInfo` / `PointerEvent` /
  `PointerClick` / `AnchorPlacement`）承载气泡的可见性、消息队列、`min/max` tick 判定、锚点解算、
  透明度目标与绘制批次；真实顶层窗口移到 `backends/qt/widgets/control_host.py`，持有窗口标志、
  透明度动画、绘制执行、指针翻译、剪贴板与 z-order。控件窗口宿主不静态引用 `drawing/`，绘制实现与
  呈现几何由 `render_bridge.create_control_host()` 注入，档位 A 的引用清单不变。`bubble.py` 因此
  不再 `import PyQt5`，`frozen_ui_qt_importers` 由 73 项降到 72 项；对外接口
  （`adjust_size_to_text` / `fade_in` / `hide_bubble` / `clear_queue` / `remove_bubbles` /
  `get_text_size` / `get_anchor_point` / `isVisible` / `hide` / `close`）保持不变，像素基准四条断言
  全部通过。其余控件按渲染层边界契约文档第 12 节的滚动清单逐个迁移。

- 控件层「描述 + 后端渲染」滚动迁移继续：音响右键 UI 的音量与频段两条滑条
  （`speaker_volume_slider.py` / `speaker_band_slider.py`）不再继承 `QWidget`、不再 `import PyQt5`，
  改为描述层状态机（`visuals/controls.py` 的 `RectSliderControl` / `BandSliderControl`）+ 后端窗口宿主，
  `frozen_ui_qt_importers` 由 70 项降到 68 项。这是首批**被动拖动**控件：宿主新增
  `on_pointer_move` 移动回调与 `capture_on_press`（`grabMouse`/`releaseMouse`，防止指针移出窄窗口后
  拖动断流），拖动提交（音量 `MUSIC_VOLUME`、频段提示）仍在控件侧。对外接口与
  `speaker_control_buttons.py` 调用面不变。

- 修复音响双滑条迁移后丢条件漏掉的族内几何读取：`speaker_search_dialog._is_mouse_far_from_family()`
  与 `playlist_panel._is_mouse_far_from_family()` 仍在读 `widget.geometry().center()`，而这两族里
  的滑条 / 播放进度条已经迁出 `QWidget`、没有 `geometry()`。点到音响弹出搜索 UI 后，自动隐藏的
  TICK 分支会抛 `AttributeError`，事件中心把回调异常吞成一条日志，表现是"鼠标离远不收起"。
  两处改为 `widget_global_rect(widget).center`（核心 `Rect` 属性），并补两道守卫：静态扫描
  「`lib/script/ui` 里对控件变量取 `.geometry()`」，以及真构造音响搜索 UI、让族内滑块可见后驱动
  `_is_mouse_far_from_family()` 并盯事件中心错误日志的运行期断言。

- 控件层「描述 + 后端渲染」滚动迁移继续：音响搜索结果框（`speaker_search_result_box.py`）
  不再继承 `QWidget`、不再 `import PyQt5`，改为描述层状态机（`visuals/controls.py` 的
  `SearchResultListControl`）+ 后端窗口宿主，`frozen_ui_qt_importers` 由 70 项降到 67 项
  （本轮共移出双滑条与结果框三项）。翻页、选中行、搜索中标记与绘制批次都在描述层，
  悬停/点击命中改用共享 `visual.row_rects` 反查行号（不再手算 `(y-border)//行高`）；
  位置仍由 `SpeakerSearchDialog` 通过 `UI_ANCHOR_RESPONSE` 驱动，`clear_results` /
  `set_results` / `set_searching` / `navigate` / `turn_page` / `fade_in` / `fade_out` 调用面不变。

- 控件层「描述 + 后端渲染」滚动迁移继续：命令提示框（`command_hint_box.py`）不再继承
  `QWidget`、不再 `import PyQt5`，改为描述层状态机（`visuals/controls.py` 的
  `CommandHintControl`）+ 后端窗口宿主，`frozen_ui_qt_importers` 由 70 项降到 66 项
  （本轮共移出双滑条、结果框与提示框四项）。默认三行提示与 `#` 命令列表两种模式、
  循环翻页、选中行与补全都在描述层，命中行改用共享 `visual.row_rects` 反查；
  提示框不再是 `QWidget`，改由自身顶层宿主靠锚点事件贴到命令框左下，
  `update_input` / `get_completion` / `navigate` / `turn_page` / `fade_in` / `fade_out`
  调用面不变。

- 控件层「描述 + 后端渲染」滚动迁移收掉最后一个成规模控件族：右键矩形动作按钮一族八个按钮
  （鼠标穿透 / 放大 / 缩小 / 关闭 / 启动鸣潮 / 聊天模式 / 交互模式 / 更多功能）连同共享基类
  `rect_action_button_style.py` 一并迁出，该文件删除，`frozen_ui_qt_importers` 由 66 项降到
  57 项（一族九个文件一次移出）。八个按钮改为 `lib/script/ui/rect_action_button_runtime.py` 的
  `RectActionButtonRuntime`（描述 + 宿主装配），差异只剩「文字、节点 id、点下去做什么」；
  `ScaleUpButton` / `ScaleDownButton` 现在是两个各自独立的类。右键图层原有的 `adopt()`
  直接收编各按钮宿主，仍然每帧只移动一个原生窗口；`launch_wuwa_button` 的启动动作收口到共享
  `lib/script/app/wuwa_launcher.py`。对外接口不变。

### Fixed
- 修复本地音乐音质明显受损：解码 WAV 的保真口径写错了——`_decode_av` 把目标固定成
  单声道 22.05kHz（48kHz 音源直接对半砍），`_decode_soundfile` 也把所有结果混成单声道，
  于是本地 FLAC / M4A 听起来比在线音源还差。现在两条路径都按源文件保留采样率与声道数，
  只有 MCI 明确不接受的极端参数（超范围采样率、零声道）才退到安全档，多于两声道才混成
  双声道。解码口径版本号进入缓存指纹（`_DECODE_REVISION`），旧目录自然失效，用户不必手动
  清缓存。新增 `LocalAudioFidelityTests`：立体声 FLAC 保持 44.1kHz/2ch、48kHz 单声道不被
  重采样、超范围采样率按边界夹取、口径版本变化必须改变缓存指纹。
- 修复本地音乐播放队列音频无限重叠：MCI 别名与创建它的线程强绑定，只有 `open` 该别名的
  线程才能对它发 `play` / `status` / `setaudio` / `close`；在别的线程上执行会返回错误码
  263（“指定的设备未打开，或不被 MCI 所识别”）并给出空串。`MciMusicPlayer` 原先在调用线程
  `open`、却在新建的轮询线程里查 `status mode`，空串被当成“播放完成”立即推进队列，而同一
  线程外的 `close` 也失败，被放弃的音频继续发声——整条队列因此逐首叠加播放，本地音乐越多
  越明显。现在全部 MCI 调用收进一个常驻工作线程：命令投递过去并等待完成，播放状态由它轮询，
  `position_ms` / `duration_ms` / `is_busy` 读它维护的缓存；轮询只在拿到错误码 0 时才依据
  `mode` 判定播完。新增 `MciMusicPlayerThreadAffinityTests` 用按线程登记的别名表复现该约束。
- 修复本地音乐有时打不开：`MciMusicPlayer` 走 Windows MCI，只会尝试 `type MPEGAudio` 和
  `type mpegvideo`，而扫描目录时收录的 FLAC / M4A(AAC) / OGG / Opus / WebM 容器 MCI 都
  打不开（返回 263/277），用户看到的就是“有些本地音乐打不开”。新增
  `lib/script/cloudmusic/_decoder.py` 与 `_constants.local_audio_needs_decode()`：非
  MCI 原生格式先解码成 16bit PCM WAV 再交给 MCI，解码优先用 `soundfile`（libsndfile，
  随发行版打包），M4A/AAC 回退到可选的 `av`（PyAV）；结果按“路径+大小+mtime”哈希缓存在
  用户缓存目录 `music/decoded/` 下，重复播放命中缓存。MCI 打开也改为先 `type mpegvideo`
  再回退“不带 type”，不再依赖 MPEGAudio 别名。失败时给出“解码失败/文件损坏或缺少解码器”
  的明确提示，而不是静默无声。新增 `tests/test_local_music_decode.py`。
- 修复右键动作按钮迁移夹带的两处回归：关闭按钮的鼠标进入/离开原先读 `_button.parent()`，
  按钮迁出 `QWidget` 后没有这个方法，每次进出都抛 `AttributeError` 被事件中心吞成日志，
  关闭按钮不再自动淡入/淡出——改为统一取后端宿主 `_runtime.host`（未迁移控件仍回退
  `parent()`）。八个动作按钮旧实现里的 `_idle_timeout` 是死代码（赋值后从不读取），迁移
  时若接成宿主空闲计时，按钮会在命令框打开约 `idle_close_ms` 后各自消失；现在不再给按钮
  设空闲计时，族内自动隐藏仍只由命令框的鼠标距离守卫负责。
- 修复命令列表高亮不跟随鼠标：提示框与搜索结果框原先在各自 `QWidget` 里
  `setMouseTracking(True)`，迁到共享控件宿主后宿主没打开鼠标跟踪，Qt 只在按住按键时才
  投递 `mouseMoveEvent`，"移动鼠标高亮不动、按下才跟着跳"。宿主现在在传入移动回调时
  自行开启鼠标跟踪，命令提示框、搜索结果框与两条音响滑条一起恢复。三处回归各补一条
  运行期守卫。
- 修复控件层迁移暴露的一处 teardown 崩溃：`CommandDialog` 与 `RightClickUiLayer` 原来
  只订阅、不释放事件（命令框还挂了 `focusChanged` 与输入框事件过滤器），控件销毁后事件
  中心仍持有强引用回调，陈旧事件投回已析构的 Python 包装会抛 `AttributeError`，PyQt 槽内
  未捕获异常直接 `abort()`（单独跑 `test_control_layer_descriptions.py` 会以 `0xC0000409`
  退出）。现在命令框 `closeEvent`、右键图层 `close_layer()` 各自注销订阅，`eventFilter`
  再补一道取 `_entry` 的兜底。
- 修复游戏窗口压住粒子与特效：游戏宿主窗口原先用 `Qt.Tool | Qt.WindowStaysOnTopHint`
  并注册到 `WindowLayer.PANEL`，因此落进置顶窗口带；被点击激活后 Windows 会把它抬到同带顶部，
  反过来盖住 `WA_ShowWithoutActivating` 的粒子/特效覆盖层。现在按工作台窗口处理：
  普通窗口 + 无边框，既不置顶也不进 `LayerManager`，粒子与特效覆盖层始终画在游戏画面之上。
  窗口内的自绘仍按 `Layer.PANEL` 参与各自画布内的排序，视觉结果不变。
- 修复控件层迁移后的一处越界回归：`command_dialog._is_mouse_far_from_family()` 仍在把核心
  `Rect` 当 `QRect` 用（`widget_global_rect(widget).center()`），命令框自动隐藏的 TICK 分支因此抛
  `TypeError: 'Point' object is not callable`，而事件中心会把回调异常吞成一条日志——所以它既不崩界面
  也不报错，只是自动隐藏静默失效。已改为属性写法 `.center`，并补两道守卫：静态扫描「核心几何不得
  按 Qt 方法写法取用」（含别名链 `a = producer(); b = a`），以及真实构造命令框、驱动 TICK 并盯事件
  中心错误日志的运行期断言。同一模式在 `lib/script`、`lib/core`、`scripts` 全仓审计后只剩这一处。
- 修复安装器在非空残留目录上必然失败：目标文件改用 `CREATE_ALWAYS`，残留文件/目录冲突
  会被清掉，带安装标记的半成品安装目录允许覆盖修复，开始解压前还会清理已死进程留下的
  `FSV-<pid>-<tid>-<tick>` 暂存目录。此前用户会遇到
  `当文件已存在时，无法创建该文件。`（183/80）并卡在解压阶段装不下去。
- 修复单个损坏的游戏扩展会让整条启动链失效：`GamePackageService` 现在逐个加载粒子和特效
  扩展，加载失败的只跳过该扩展并记警告，不再让异常穿 `ApplicationState._on_init_ready`。
  此前一个已安装游戏包引用被删除的模块路径（例如 `lib.core.graphics`）就会抛
  `ModuleNotFoundError`，使托盘图标、托盘菜单和音响搜索 UI 一起消失——它们都在该回调的
  异常点之后。已安装副本与官方源签名不一致时仍会自动重装覆盖。
- 修复「福利 API 配置」分区里的开关比其它分区偏左约 18px：`QFormLayout` 在整张表只有
  无标签行时会整列丢掉标签列（`addRow("", widget)` 不登记 `LabelRole`），现在补偿一个
  可见的零高占位标签把该列留住。
- 卸载桌宠的确认弹窗改用与其它弹窗一致的样式：此前是裸的 `QMessageBox.warning`，浅色
  背景、英文 Yes/No 按钮、正文颜色都不对。新增 `lib/script/ui/confirm_dialog.py` 统一
  确认与提示弹窗（中文按钮、工作台配色、破坏性操作走 danger 色、取消为默认键）。
- 修复应用启动阶段的单点故障：`ApplicationState._on_init_ready` 现在把管理器初始化、
  `APP_MAIN` 发布、托盘图标、运行期 UI 四个阶段各自做异常隔离，任一阶段抛错只记录该阶段
  的堆栈并继续执行后续阶段。此前任意一处异常（例如某个已安装游戏的扩展 import 失败）
  都会连带吞掉托盘图标、托盘菜单和主界面，用户只看到「宠物窗口出来了但右键音响没反应」。
- 修复 `WindowHost v1` 的激活语义与契约文档不一致：`PassiveWindowHost.set_clickthrough(True)`
  现在会撤销已持有的激活态，`show()/activate()` 之后再由点击穿透接手时，`is_active()` 不再
  停留在 `True`。Qt 后端本来就以 `self._clickthrough` 拦截 `activateWindow()`，passive 宿主现在
  与它对齐——「点击穿透的窗口不持有激活」，但仍允许可编辑窗口正常激活和 IME z-order。
  `tests/test_window_host_contract.py` 顺带改为显式断言「激活 → 开穿透后失活」的时序，
  不再依赖一次调用的隐含结果。

## [LTS1.0.7pre5] - 2026-09-16

### Fixed
- 修复帖子正文的多行排版被并成一行：发帖框按**行**写段落排版令牌（`[center]` / `[size=NN]`），
  而渲染侧本来只在空行处分段、连续行按 Markdown 软换行并成一段——于是第 2 行起的令牌被当成
  同一段里的重复令牌洗掉，几行还会挤成一行显示。现在带令牌的行由 `starts_layout_paragraph()`
  判出、在渲染侧独占一段，两边对段落的定义一致。
- 修复右键雪堆必崩：`snow_pile.py` 的右键分支调用了一个从未存在过的 `_spawn_cb`，
  每次右键都抛 `AttributeError`。右键与批次生成现在共用 `_request_leopard_spawn()`，
  只发布 `MANAGER_INTERACTION`，由 `SnowPileManager` 查数量、比上限后再转发生成事件。

## [LTS1.0.7pre4V2] - 2026-09-15

### Fixed
- 修复自动更新容易失败：进入更新流程前先结束镜像位于安装目录内的自有子进程（办公侧车、
  控制面板、语音 worker），安装目录里的文件不再占着目录；路径比较改按写法归一，
  短名 / 目录链接下的安装目录不再被当成两个目录，占用登记与补装也不会丢。
- 修复更新完成后控制面板窗口消失，以及切到工作台「办公模式」配置页时闪出一个空窗口。

### Changed
- 更新包校验只核对一个哈希：发布清单里的 SHA-256 在下载时随流算出，「校验更新包」不再是一次
  全量读盘；安装器结构检查不再逐条解压算 CRC-32。发布清单与安装器归档目录只记路径与真实大小。
- 发行构建不再逐文件算 SHA-256，改为打包前用包内解释器跑一次真实的桌宠启动与功能自检，
  打包时连打两次比对哈希，只有逐字节一致才保留一份。

## [LTS1.0.7pre4] - 2026-09-13

### Added
- 办公模式拆成两个界面：工作台里的「办公模式」页只留配置项（办公后端、办公模式独立 api、
  启动时预热）与「技能管理」「插件管理」两张卡片，任务界面搬进独立办公窗口
  （`lib/script/ui/office_page.py`，工作台式自绘外壳 + 尺寸手柄），由配置页上的「打开办公页面」
  按钮与托盘一级入口「办公页面」打开，复用模块级单例。`office` 工作台页的工厂改为
  `office_mode_page.OfficeModePage`；配置控件复用 `OfficeModeSettings`（与 AI 设置面板同一份
  实现），保存走 `save_office_values()` 只覆盖 `OFFICE_VALUE_KEYS` 里的办公字段，默认值表移到
  `ai_settings_defaults.AI_DEFAULT_VALUES` 由两处共用。独立办公窗口在退出时由
  `lib/script/ui/shutdown.py` 统一隐藏与释放。
- 工作台新增「技能管理」「插件管理」卡片（`lib/script/ui/office_manager_card.py` 共用实现）：
  列表固定露出 5 行，多出的条目在卡片内滚动，卡片高度不随条目数变化；安装选择目录并复制进
  用户根，删除有确认框，内置项不可删除。技能来自 `lib/script/office/skills.py`（内置根
  `resc/agent` 只读 + DSH home 下的用户根，名字取 `SKILL.md` frontmatter，同名时用户根覆盖
  内置），插件来自 `lib/script/office/plugins.py`（包体复制进 profile 的 `node_modules`，包名
  登记在 `<用户根>/user/state/office/plugins.json`，启动 provisioning 时由
  `apply_registered_bundles()` 合并回被覆盖的 profile `package.json`）。
- 托盘菜单新增「雪绒论坛」入口与独立窗口：`lib/core/forum.py` 是不导入任何 GUI 库的留言墙
  客户端，`GET /api/feed` 按 id 倒序分页（默认 60 条，`before` 往前翻页）、`POST /api/messages`
  发帖，响应限 2 MiB，429 译成中文限流说明；请求交给注入的 `submit_io` 线程池、结果经
  `dispatch` 回 UI 线程，`_generation` 丢弃过期结果，`cleanup()` 后所有回调静默丢弃。
  `lib/script/ui/forum_window.py` 用工作台外壳做成独立置顶窗（`forum_style.py` 只引用工作台
  令牌），留言按发布时间倒序填进三列：每条是一张宽度等于列宽、高度随内容自适应的卡片，
  逐条插入当前最矮的列，滚动到距底部 140px 内再请求更早一页并追加；`accent`
  （pink/cyan/blue/snow）只改卡片描边颜色，填充与文字沿用工作台令牌。底部发帖框可选描边色、
  显示字数，按客户端 12 秒冷却锁住发送按钮并倒计时（服务端同 IP 6 秒 1 条，客户端更保守）。
  窗口是模块级单例，退出时由 `lib/script/ui/shutdown.py` 统一隐藏与释放。托盘菜单入口在 Qt
  后端生效；DX 后端的原生托盘菜单仍是无「bug跟踪」的精简集合，暂未列入该入口。
- 办公模式新增桌宠能力工具：音乐、雪豹、沙发、摩托、倒计时、音量与瞬移现在也能由办公代理
  调用。桌宠能力只活在主进程，Node 侧车拿不到音响窗口、动画管理器和音乐服务，所以走「工具
  登记 + 命令回传」：`services/dsh-office-runtime/bridge/index.mjs` 用
  `@deepseek-ai/dsh-tools` 的 `defineTool` 注册与陪伴模式同名的 10 个工具（参数与
  `lib/script/chat/native_tools.py` 的原生工具一致），模型调用时回传 `pet_tool_call`；
  `lib/script/office/pet_tools.py` 复用 `native_tool_to_dispatch()` 把调用翻译成桌宠指令，
  交给 `ToolDispatcher.execute_command()` 执行，结果以 `pet_tool_result` 回包（15 秒超时）
  并作为一条 `pet/tool` 事件写进任务详情页的「工具记录」。刻意不开放 `recall_memory` 与
  `inspect_screen`（都要往陪伴聊天流注入 `INPUT_CHAT`，办公模式没有这条通道）以及
  `open_browser`（会在办公沙箱与审批边界之外打开系统浏览器）。桌宠工具不触发审批：它们不请求
  沙箱提权，也不读写工作区。
- 新增星空划过粒子 `star_streak`（`lib/script/practical/star_streak_particle.py`）：白色 bloom 圆形
  颗粒沿一个方向匀漂、叠加布朗抖动，尾段 `fade_ticks` 内线性淡出。请求参数为 `count_range`（本次
  随机个数）、`color`、`bloom_range`（bloom 半径的随机范围）、`duration_ticks`、`fade_ticks`、
  `direction`，默认依次是 `(0, 1)`、白色、`(2, 4)` 像素、20 tick、其中最后 10 tick 淡出、往左。
  办公页推理滑条被按住时每个逻辑 tick（`EventType.TICK`）从滑块把手位置召唤一批，松开、页面隐藏或
  `cleanup()` 都退订。圆形粒子的 bloom 改由共享视觉层展开：`visuals.CIRCLE_BLOOM_RINGS` 按「半径占
  bloom 半径的比例 / 透明度倍数」由外到内铺同心光圈，`_particle_bounds()` 的保守包围盒取
  `max(size, bloom)`，Qt 与 DX 消费同一份命令批次。
- 独立办公窗口右上角备齐最小化、最大化 / 还原、关闭三个按钮（`create_window_button()` 加
  `theme.window_button_stylesheet()` 的 `WorkbenchWindowButton`，与工作台主窗口同一套）：最大化走
  `showMaximized()` 只铺满工作区，不进全屏、不覆盖任务栏，按钮在「最大化 / 还原」之间换图标，最大化
  时收掉右下角尺寸手柄、关闭先还原尺寸再淡出；内嵌到工作台时不出现这组按钮，窗口交给工作台。
- 雪绒论坛正文新增效果令牌 `[雪豹]`（`lib/script/ui/forum_markup.py` 的 `FORUM_EFFECT_TOKENS`）：
  正文里写令牌表示贴一张动图，渲染时令牌整段洗掉、卡片底部居中贴一只会自己走的雪豹 gif
  （`lib/script/ui/forum_sticker.py`；令牌到资源的对应关系 `FORUM_STICKER_ASSETS` 与令牌表成对，
  测试核对）。洗令牌放在标记渲染之后，`**[雪豹]**` 得到空的 `<b></b>` 而不是留下一对星号
  （卡片上会印出 `****`）；令牌算效果、不算可见字数，不影响正文的字号自适应。贴图高
  `STICKER_HEIGHT`、宽按 gif 比例，缩放前按全部帧可见像素的并集裁掉 gif 自带的透明留白
  （逐帧裁会让各帧对齐基准漂移、动起来发抖）；帧由全局 `GIF_FRAME` 事件推进，与雪豹世界物体
  同一时钟，贴图跟着卡片销毁一起退订；gif 缺失时是零尺寸透明控件，卡片留空而不是整墙渲染失败。

### Changed
- 托盘菜单「桌宠清理」二级项与同级项的文字中心对齐：所有行的条目文字矩形统一由
  `_TrayMenuHintStyle._item_text_rect()` 计算，箭头留位只压缩二级项自己的可写宽度（左右各收一半），
  箭头绘制点也比预留位略靠右，二级项既不偏移也不会顶到箭头；
  `tests/test_tray_menu_style.py` 钉住这三条约束。
- 语音设置只保留四个常用项：采样温度（情绪自然）、重复惩罚（情绪丰富）、ONNX 语速（语速快慢）
  和语音缓存上限（语音保存条数），Top-K、Top-P、长文本分句、片段停顿、随机种子和语音字数限制
  收进“高级设置”折叠区，默认收起，控件不再全部挤在一起。
- 最大解码步数不再作为用户设置：`OnnxVoiceRuntime` 按每段文本长度与 `speed_factor` 自适应计算
  解码上限（64~1200），语速越慢或文本越长预算越高，短文本更快结束；事件请求显式传入
  `max_steps` 时仍以请求值为准，旧的 `gsv_max_steps` 配置项被忽略且不再写回。
- 新装或未保存过语音设置的默认参数调整为：分句方式 `cut0`（不自动分句）、采样温度 1.35、
  重复惩罚 1.6、语速 1.1；已有用户已保存的设置不受影响，运行时在配置缺失时也回退到同一组默认值。
- 办公模式改用 `get_office_active_config()` 解析接口且不回退：开启“办公模式独立 api”时只使用
  独立密钥/地址/模型，缺项直接报错；关闭时若回复模式为福利 API，向办公模式发送信息会被拦截，
  桌宠气泡与办公页同时给出切换「手动 API」的提示，不再创建任务。
- 办公后端新增“本机 DeepSeek Harness（探测）”：只读探测用户机器上全局安装的
  `@deepseek-ai/dsh`（`FSV_OFFICE_LOCAL_DSH` → `PATH` → npm 全局目录 → `%APPDATA%\npm`），
  校验包名、依赖集合与 major.minor 版本同系列后复用包内或系统 Node 启动侧车；未探测到时
  设置项置灰并在描述里说明原因，内置 DSH 仍是默认后端。
- 音响形变改为跟随系统混音的节奏频段：新增 `lib/core/audio_spectrum.py`，在默认输出设备上开
  WASAPI 回环流并做 rFFT，只统计 `SPEAKER_AUDIO.freq_min`–`freq_max`（默认 60–250Hz，
  鼓点/贝斯）的能量，再按 `level_floor_db`–`level_ceil_db` 映射成 0.0–1.0 强度；不再用整体
  峰值的开方值驱动，正常播放时只有真正的鼓点峰值接近满形变，低速段不再“挤成一坨”。
  回环不可用时仍回退到原来的峰值路径；`response_gain` 默认由 4.0 调整为 1.4。
- 右键 UI 合并为一层绘制：命令框、命令提示框、八个附属按钮和两个翻页按钮不再各自创建独立
  置顶窗口，改由 `lib/script/ui/right_click_ui_layer.py` 的单个宿主窗口承载；子控件继续按屏幕
  坐标计算锚点，由 `move_widget_to_global()` 换算成宿主本地坐标，宿主按可见子控件的并集一次
  `setGeometry()` 并用同一并集设置命中掩码（空隙仍点击穿透）。拖动桌宠时每帧只有一次原生
  窗口移动，不再每个控件都裁剪、重绘一次，右键面板打开时的掉帧明显降低。
- 在线资源包改为双读：`install_resource_bundle()` 按归档内容自动选择路径——带
  `.fsv-shard-index.bin` 的 LZMA2 分片包走流式分片解码（1 MiB 一块，逐条核对数量、大小与
  偏移，分片读不满或有多余数据都报错），旧的普通 Deflate 包仍由 `zipfile` 逐条解压；
  本版本发布继续用 Deflate，构建脚本新增 `--resource-sharded` 开关，等在用客户端都升级到
  读得懂分片的版本后再切换。
- 自研 CUDA 推理端的 f32 batched MatMul 改为 4 行 × 4 列的寄存器分块（原来一个线程只负责
  一行 4 列，权重 quad 每行都要重读一次）：`fsv_kernel_bench` 上语音包的主力形状快 1.4×–2.6×
  （`m=212 k=2048 n=512` 814.7→313.5 µs、`m=212 k=512 n=2048` 1126.2→521.9 µs），
  每个输出的规约顺序未变，`fsv_engine_smoke` 的 13 个 matmul 对照用例全部通过，int8 包
  端到端音频逐位一致；受益的是把 `MatMulNBits` 还原成 f32 的 fp32/fp16 包。
- 新增 `native/cuda_voice_runtime/tools/dev_voice_soak.py`：在同一进程里连续合成并逐次打印
  耗时、`nvidia-smi` 显存与采样点差。24 次实测显存 3186→2857 MiB、结果与第一次逐位一致、
  `alloc fail=0 abandon=0`，确认自研后端可以长时间占用显卡。
- 自研 CUDA 推理端的 broadcast 逐元素内核（`fsv_binary_bcast_f32`）改为每线程 4 个输出：
  语音包里该内核的调用绝大多数是逐通道偏置（`[1,C,1]` 对 `[1,C,T]`），内层维度上一侧步长
  0、一侧步长 1，行内偏移因此是同一个常量的倍数，一次坐标回代就能喂一次 float4 存储，
  步长为 1 且对齐的一侧改读 float4、广播侧只读一次，会跨行的一组退回逐元素回代。
  `FSV_NV_GPU_TIMER` 在真实合成里量到该内核由 0.312 s 降到 0.097 s（两次合计算、653 次
  启动、形状不变，3.2×），`fsv_engine_smoke` 全部通过；`fsv_kernel_bench` 新增
  `binary_bcast batch channels spatial` 用例（该用例在大形状上前后都是访存受限，
  只用来看内核本身，不看端到端）。
- 在线资源包的覆盖安装改成「先跳过、最后回头重试」：原来按顶层条目 `copytree`/`copy2`，
  一个被扫盘、杀毒或索引进程占住的文件就会让整次更新失败。现在按文件逐个替换，被占用的先
  记下来继续替换其它文件，重试 3 次（间隔 0.4 秒）后仍然失败才报错并列出文件名，
  目录结构与空目录不受影响。
- 自研 CUDA 推理端的显存回收池改用 size class 作键：256 KiB 及以下仍按 1 KiB 对齐，
  之上按 1/8 递增（256 KiB–64 MiB 之间约 47 档、最多浪费 12.5%）。解码每步的序列都
  比上一步长，同一族中间张量的字节数几乎不重样，原来 1 KiB 粒度的桶几乎每次都落空，
  每个张量各来一次 `cuMemAlloc`/`cuMemFree`（各自掐断一次上下文）。256 字任务
  （46.46 s 音频）一次合成的真实显存分配由 235719 次降到 115512 次（池命中
  804772 → 924979），同一份工作量下 `alloc/launch` 由 1.02–1.21 降到 0.658–0.682，
  折合一次合成约省 7 s；`launch` 次数、上传与回读完全不变。31 字基准同样下降
  （含载入的一次进程 10326 → 7020 次分配），端到端在机器抖动内。测量口径见
  `doc/自研CUDA极简推理端.md`“长文本与显存池的 size class”。
- 自研 CUDA 推理端的图执行改成“一次解析、每次运行只做槽位寻址”：`prepare()` 期建立
  名字到值槽的映射（含 If 分支、Loop 体会写回外层作用域的名字）、每个节点的操作数与结果
  槽位、以及每个槽位被读的次数（图输出记为永不释放）；运行期把值写进运行时自己的表，
  按槽位取操作数、按槽位写结果、引用减到零时归还显存缓冲，不再每个操作数做一次字符串
  哈希，也不再每步重建整张值表、重新统计一遍引用。31 字基准的同进程 cpu/cuda 比值
  0.791 → 0.739（cuda 1.66 s → 1.56 s，两版 DLL 交替各三次），节点循环的取输入
  0.173 s → 0.081 s、存结果 0.243 s → 0.043 s（`FSV_NATIVE_PROBE`，三次运行加载入的
  累计值，节点数同为 217485）；256 字长任务 64.86 s / 65.96 s，在既有记录 63.6–72.7 s
  的抖动内。If/Loop 子图仍走原来的名字表，语义不变，音频与改动前一致。
- 自研 CUDA 推理端的算子分发改成整数比较：`build_plan()` 顺带把每个节点的算子名折成
  64 位 FNV-1a 哈希（`hash_op_name()`），运行期的 `op == "Add"` 这类比较因此只比一次整数，
  字面量在编译期就折好；If/Loop 体不在计划里，仍按名字现场哈希一次。74 个算子名的哈希
  74/74 不重复，`fsv_engine_smoke` 全部通过，音频不变。
- 自研 CUDA 推理端新增跨图设备缓冲直通：解码器分两半，前一半产出的缓存原来要走一趟
  「回读主机 → 再上传」的完整来回。现在前一半用 `fsv_graph_set_retained()` 声明只留在卡上
  的输出（运行期照常回名字/形状/类型，但载荷为空），后一半用 `fsv_graph_borrow_device()`/
  `fsv_graph_import_device()` 把这块显存绑到自己输入名上、跑完用 `fsv_graph_release_device()`
  交还；借不到（那个值没有显存副本）就退回按字节走。同一张配对表还把解码器每步回灌的
  `y`/`y_emb`（下一半的 `iy`/`iy_emb`）留在卡上——它们的长度随步数增长，是 256 字任务里
  最大的一份来回。同一 DLL、同一份 256 字文本（46.46 s 音频、seed 20260910）各一次：上传
  51522 次 1.82 GB → 50255 次 0.68 GB、回读 6386 次 1.17 GB → 5117 次 0.03 GB，
  `launch` 次数不变（1053882），一次合成少运 2.28 GB，两次输出的 wav SHA256 与既有基准
  逐位一致。真实分配 139598 → 151651（回灌的张量每步都比上一步长，缓冲同样进不了同一个
  size class），分配段 8.30 → 8.47 s、峰值显存 2798.1 → 2796.6 MB，仍在抖动内。31 字基准
  回读 839 → 683 次、上传 12637 → 12484 次。测量口径见 `doc/自研CUDA极简推理端.md`
  “跨图设备缓冲直通”。
- 音响右键 UI 在「搜索框 + 搜索歌曲」正上方新增音量滑条：与整行等宽（240px、高 20px），
  复用共享滑条视觉（黑/青/粉三层面板、深青已播放填充、竖向矩形手柄），带 20 个小刻度，
  拖动吸附到 5% 档形成颗粒手感，松开时气泡提示当前百分比；原控制按钮整体上移一个滑条高度，
  不再与滑条重叠。共享层新增 `build_slider_visual` 与 `slider_handle_commands`，
  DX 音响搜索描述（`lib/core/render/visuals/speaker_visuals.py`）加入同一条音量带并可拖动，
  两个后端的几何、刻度与配色一致。
- 滑条手柄由粉色菱形改成竖向矩形（高宽约 4:3）：共享层新增 `slider_handle_commands`
  统一生成竖矩形手柄并移除已无调用方的 `rotated_square_commands`，播放进度条
  （Qt 播放列表 UI 与 DX 音响播放列表描述）和音响右键 UI 音量滑条因此形状一致，
  更贴合黑/青/粉的面板基调。
- 启动等待（`APP_PRE_START` → `APP_INIT_READY`）新增可选的异步 UI 预绘制缓存：打开
  「系统调度 → 启动 → 启动期预绘制缓存」后，`lib/script/ui/preloader.py` 会在等待的空转里
  逐个构造播放列表、进度条、音响搜索、云音乐登录和元宝登录窗口并离屏预绘制一次，再登记进
  `lib/core/ui_cache.py` 的 30 MiB 专用缓存池。整个过程异步且可中断，没画完也照常启动；
  缓存超预算时按最近最少使用淘汰旧控件，正在显示的窗口受保护不会被淘汰，退出时统一释放。
  开关默认关闭，关闭时保持原有的就绪后分步预加载。启动编排新增
  `ApplicationUiHost.prewarm_runtime_ui()` 边界，DX 后端按需构建面板、不参与抢跑。
- 办公权限许可窗去掉 Windows 原生标题栏（窗眉）：`OfficeApprovalDialog` 改为
  `Qt.FramelessWindowHint` 的工作台式自绘外壳，保留粉青强调条与许可卡片，卡片标题行
  可拖动整窗，右上角补齐工作台同款的关闭按钮（悬停变危险色，等同于“拒绝”并回传决定），
  窗口外框用 `border_strong` 收边并居中于父窗或光标所在屏幕，与工作台、办公页同一套
  明暗主题令牌。

- `ToolDispatcher._accepts_mode_generation()` 明确 `mode_generation is None` 的含义：调用方
  没有陪伴模式代次（办公模式的桌宠工具）时，只要求桌面仍停在办公模式；切回陪伴模式后，办公
  发出但没收尾的异步工作同样作废。此前这条路径一律判为过期，办公模式的音乐、窥屏、浏览器
  指令会被静默丢弃。
- 本机 DeepSeek Harness 的探测改到启动等待期：`main.py` 在 `APP_PRE_START` 把只读探测
  排进 IO 线程池跑一次并缓存（`local_dsh.prime_local_dsh_status()`），设置面板只读缓存，
  不再在打开面板时于 UI 线程现算 `npm prefix -g` 与若干次 `node --version`。办公后端下拉
  改为按探测结果呈现：探测到可用安装才列出「本机 DeepSeek Harness」（可选并可真实路由）；
  没探测到就完全不显示，不再留一个置灰的占位项；只有已保存该后端、当前却探测不到时，
  才保留一个置灰项并在描述里说明原因，避免把用户既有的选择静默改回内置 DSH。

- 办公页「推理强度」由三档下拉改成五档滑条：档位为 极速 / 轻量 / 一般 / 思考 / 沉思，档位
  文字显示在滑条上方，颜色按强度从工作台的淡粉线性过渡到青（`office_style.office_effort_colors()`
  在工作台 pink/cyan 令牌之间插值，明暗两套主题都可用），已达档位刻度用该档颜色、未达档位用
  分隔线色。契约同步扩到五档 `REASONING_EFFORTS = ("off", "low", "high", "max", "ultra")`，
  旧的 off/high/max 仍是合法值、历史任务无需迁移；侧车 `bridge/index.mjs` 为新增的 `low` 与
  `ultra` 各补一条执行策略提示词。推理强度独占一行，工作目录输入框不再被挤压。
- 雪绒论坛窗口按工作台的语言重做一遍：字号与控件间距对齐设置面板（页头 19/11、卡片昵称 14、
  正文 17、日期 11、输入与按钮 14），窗口优先级改成与工作台窗口相同（`Qt.Window |
  Qt.FramelessWindowHint`，不再置顶、不再注册进 `LayerManager`——注册的窗口会被
  `stack_window()` 放进 `HWND_TOPMOST` 链，实测会压在别的应用之上），默认宽度由 1240 收到
  620，卡片最小宽度随三列网格调到 170。卡片重排为左上昵称、中间居中大字正文、右下小字日期，
  并按卡片信息的内容哈希稳定铺一层几何底纹（见下条）。底部发帖框新增昵称输入框，留空即以
  「匿名」发送，昵称默认值随之从「雪绒桌宠」改成与服务端一致的「匿名」。
- 论坛卡片正文按字数自适应字号：`forum_style.forum_card_text_size()` 让 6 字及以内的短句取到
  2 倍字号、24 字及以上回到基准 17（`scale_px(17, min_abs=12)`），中间线性过渡；一句话的
  卡片不再空一大片，长留言仍按基准字号排版。
- 卡片底纹从 6 种单层花纹扩到 15 种并抽到 `lib/script/ui/forum_texture.py`：几何间隙拼贴
  （tiles 圆角瓷砖 / bricks 错缝长砖 / crosses 十字瓷砖，块与块之间留缝）、几何瓷砖平铺
  （diamonds 菱形 / hexes 六边蜂窝 / triangles 三角 / dots 圆点 / rings 圆环）、粗线几何
  （polygons 空心多边形 / solid_polygons 实心多边形 / coarse_rings 粗圆环 / coarse_crosses
  粗十字 / hatch 斜纹：边数、线宽、图形尺寸与斜纹间隔都由种子取值并叠坐标噪波）、艺术渐变
  （radial_dots 点半径与透明度沿到焦点的距离渐变、fade_stripes 斜线透明度渐变）。种子从
  留言 id 换成卡片信息（id / 昵称 / 正文 / 配色 / 时间）的 `blake2b` 内容哈希，正文改一个字
  就会换一套底纹，跨进程仍然一致。底纹比首版更实：透明度 8~16 提到 20~34、线宽以
  `CARD_TEXTURE_STROKE_BASE`（2px）为下限、平铺边长收到 12~22、斜纹间隔收到平铺边长的
  0.45~1.0 倍，颜色也从纯中性色改成「主题中性色按 `FORUM_TEXTURE_TINT_RATIO` 掺上卡片
  accent」（`forum_style.forum_texture_color(accent=...)`），底纹因此带上卡片自己的色调。
  底纹按（花纹 + 卡片尺寸 + 颜色 + 设备像素比）缓存成透明位图再 blit，
  一屏卡片重绘由 5.7 ms 降到 1.9 ms（蜂窝花纹单卡 4.5 ms → 0.23 ms），滚动不再掉帧。
- 托盘菜单收敛一级入口：去掉「bug跟踪」与「CMD终端」，「清理桌面 / 清理缓存 / 清理历史」
  三个清理动作收成「桌宠清理」二级菜单（悬停自动展开），条目改名为「桌面物体 / 音乐缓存 /
  登录数据」，二级菜单用同一个 `TrayContextMenu` 构造并新增指针箭头绘制，因此与一级菜单
  是同一套样式。两个入口的功能没有丢失：故障跟踪仍可从工作台工具页打开，CMD 终端仍可由
  命令对话框的 `/` 前缀与 DX 原生托盘菜单打开，`TrayCommand` 与主协调路由未改动。
- 原生工具的可用函数与参数从代码常量搬到 `resc/toolcall.txt`：人格词是用户文件、升级不覆盖，
  用法写在人格词里就同步不到改过人格词的用户，所以改由
  `native_tools.native_tool_system_note()` 在请求期读文件并拼进 system 消息；文件缺失或读取
  失败时只留 `NATIVE_TOOL_SYSTEM_NOTE` 里的硬性规则，不影响降级路径。
- 雪绒论坛卡片正文支持 `**粗体**`、`*斜体*`、`__下划线__`、`~~删除线~~` 四种行内标记
  （`***粗斜体***` 就是两层叠加），解析放在不依赖 GUI 的 `lib/script/ui/forum_markup.py`：
  原文先转义 `&`、`<`、`>` 与换行，再把成对的标记换成标签，没配对的标记按普通字符显示；
  标记是纯文本、原样存服务端，只有雪绒论坛把它渲染成格式。底部发帖框左侧新增四个复选小按钮
  （按钮字 B / I / U / S 本身就是效果示例）：选中文字点一下把选区包进标记、空输入点亮后接着
  打的字自动落进标记、光标已在标记里时按钮点亮表示「这里已经是这种格式」，再点一下取消。
  正文控件从 QLabel 换成只读富文本控件 `lib/script/ui/forum_text.py` 的 `MarkupText`：UI 字体
  只注册了 Bold 一个字面（`resc/FRONTS/HarmonyOS_Sans_SC_Bold.ttf`），`<b>`、`font-weight`
  与三种 QFont weight 实测墨迹完全相同，QLabel 里看不出加粗、也拿不到 `QTextDocument`；
  `MarkupText` 遍历字符片段给加粗片段叠一层同色描边（笔宽按字号取 `BOLD_OUTLINE_RATIO`），
  斜体 / 下划线 / 删除线交给 Qt 合成与绘制期装饰。量高改用临时文档，避免 `sizeHint()` 把已
  排好版的正文改窄、裁掉最后一行。

- 办公窗口的正文与控件字号铺到整棵控件树并复用设置页口径：`office_style.apply_office_fonts()` 先调
  `workbench_settings_layout.apply_settings_page_fonts()`（页头说明与分区标题 / 说明、输入控件），
  再补设置页没有的工具按钮、文本视图、标签页和 `Office*` 标签；QSS 的 `font-size` 只作用于选择器
  命中的那个控件本身，不铺这一遍子控件仍是应用默认的 12px。
- 推理强度滑条从卡片正文搬进卡片最底部的输入坞 `QFrame#OfficeComposer`（提示词在上、动作条在下），
  排在发送按钮左侧；档位名仍在滑条左侧与「推理强度」标签同一行，已达档位刻度用该档颜色。滑条填色
  段里那层固定种子的星点（`EFFORT_STAR_COUNT` / `EFFORT_STAR_SEED`）随之删掉，星空交给粒子层。
- 办公配置从 AI 设置面板移除：办公后端、独立 API 与启动时预热只长在工作台「办公模式」页，
  写盘归属跟着拆开——`save_ai_values()` 只写 `get_ai_panel_setting_defaults()`（不含 `office_*`），
  `save_office_values()` 只写 `OFFICE_VALUE_KEYS`（`config.ollama_config.OFFICE_SETTING_KEYS`），
  表单没提交的办公键沿用当前生效值，密钥文件改成「调用方没给的密钥沿用磁盘现值」，面板保存
  不再把办公配置或办公密钥清回默认值，`apply_ai_runtime()` 也只在 values 带办公键时才动
  `OFFICE_MODE`。
- 办公模式配置页与 AI 设置页统一手感：内嵌到工作台时不再重复页内大标题（顶栏已经显示页名，
  与 `create_workbench_page()` 对 AI 页的处理一致），滚动换成两边共用的
  `workbench_settings_layout.SmoothScrollArea`（原 `ai_settings_panel._SmoothScrollArea` 搬进
  共用模块），「保存办公配置」从分区正文搬到底部 `SettingsActionBar`（左侧新增常驻状态行
  `SettingsActionStatus`，保存与探测结果都落在这一行），「打开办公页面」改写成设置面板同款的
  控件行（右对齐标签 + 控件列），技能/插件列表项字重跟同页正文一样加粗。
- 雪绒论坛留言墙给卡片与竖排滚动条之间留出 `SCROLL_GAP`（默认 10px，与列间距同一档）：
  滚动条不再贴着卡片，最后一列不会被条子压成「卡片右边框」。最小窗口宽度相应加上
  `SCROLL_GUTTER` = 滚动条宽度 + `SCROLL_GAP`（滚动条宽度提成
  `forum_style.FORUM_SCROLLBAR_WIDTH`，样式表本体与最小宽度推导共用一份），否则滚动条一出现
  视口少 20px，三列会被挤到卡片最小宽度 170 以下。
- 发行构建的输入指纹改为抽样：`--resume` 用的 `.fsv-distribution-state.json` 仍逐文件记录
  相对路径 / 大小 / mtime，内容只按预算抽样（每个文件最多 64 KiB、每棵输入树最多 50 MiB，
  按文件大小从大到小取），解释器自带的 `Lib/site-packages` 不再重复进指纹。发版构建不再把
  十几 GB 的 site-packages / node_modules 整个读一遍，构建时间不再主要花在校验输入上。
- 更新包校验只核对一个哈希：发布清单里的 SHA-256 现在随下载流算出，「校验更新包」不再把几百兆
  重新读一遍；`validate_update_installer` 也不再 `testzip()` 逐条解压算 CRC-32，安装器在一次更新里
  被校验两遍时直接命中缓存。
- 进入更新流程先解除安装目录占用：`lib/script/app/update_locks.py` 结束镜像位于安装目录内的自有
  子进程（办公侧车、控制面板 helper、语音 worker，永远跳过当前进程与它的祖先进程），Qt 字体改为
  从内存注册，`resc/FRONTS/*.ttf` 不再被主进程握到退出。
- 在线资源包覆盖安装先比对内容，逐字节相同的文件直接跳过，不再重写一遍；仍被占用的少数文件不再
  让整次更新失败，而是登记到用户根的 `state/update-pending-overlay/`，由下次启动时在 PyQt5 与
  onnxruntime 导入之前补装，目标文件之后被完整安装器换过时补装项自动作废。
### Fixed
- 修复安装路径带 8.3 短名 / 目录链接时更新会悄悄失效：占用释放先把安装根 `resolve()`，而进程
  镜像路径是原样字符串，按字面比较一个占用进程都挑不出来（覆盖安装又报 13 号错误），补装清单
  同理会把待补装项判成「属于另一个安装目录」整份丢掉、被占用的文件永远补不上。两处改走
  `lib/script/app/update_paths.py` 的写法归一：`realpath` 摊平短名与 junction，再按 Windows
  不区分大小写比较。
- 修复 CUDA 构建在“嵌入 PTX”一步要几十分钟：`cmake/embed_ptx.cmake` 原来按两个十六进制
  字符循环一次（半兆内核对应上百万次），每轮都把整份十六进制串展开进一次
  `string(SUBSTRING ...)`，再把 8 KB 缓冲逐字符 `string(APPEND)`，两次拷贝都是平方级。
  现在一次正则转义全部字节、再按 8192 字符切块（4 的倍数，不会切断一个 `\xNN` 转义），
  同一份 PTX 生成由二十分钟以上降到 2.4 秒，解码回来逐字节一致。
- 修复长文本语音朗读被静默截断：一次合成请求里的整段文本共享同一个语义解码预算
  （`max_steps`），预算用满后解码器停在保护上限，既不触发停止条件也不报错（只在 Worker
  的 stderr 留一行包内警告），表现为多句文本的后面几句没有被念出来，`cut0` 不切分模式
  下最明显。实测每个汉字约 4.5~5.2 个语义 token（`speed_factor` 1.1），默认 500 步只覆盖
  约 100 字，127 字的文本需要 576 步。`OnnxVoiceRuntime` 现在按 `max_steps` 计算每段可容纳
  的字数，先在分句与读点边界把超长文本拆段再逐段合成，段落之间沿用 `fragment_interval`
  停顿；同一段文本的端到端实测由 18.2 秒（截断）恢复到 23.0 秒完整合成。合成结果明显短于
  文本长度时写入警告日志，便于再次出现提前停止时定位。
- 修复桌宠运行时的任务栏闪烁（用户反馈“类似焦点争夺”）：Qt 粒子与特效覆盖层此前是可激活
  窗口，粒子清空/重新出现都会 `hide()`/`show()` 并重申置顶，前台窗口跟着在覆盖层和其它
  窗口之间来回移动。现在两个覆盖层都设置 `WA_ShowWithoutActivating` 并补上
  `WS_EX_NOACTIVATE`（与 DX 后端的 `FSDX_WINDOW_FLAG_NO_ACTIVATE` 对齐），清空后先滞留
  `PARTICLES.overlay_hide_linger_ms`（默认 500ms）再隐藏，滞留期内重新出现粒子/特效即
  取消隐藏，不再反复 `show()` 和 `LayerManager.enforce_burst()`；退出、暂停和清理仍然
  立即隐藏，不受滞留影响。策略集中在 `lib/core/render/backends/qt/overlay_policy.py`。
- 修复退出后 stderr 的 comtypes 崩溃（`OSError: exception: access violation writing ...`
  与 `ValueError: COM method call without VTable`）：`lib/core/audio_meter.py` 与
  `lib/core/audio_spectrum.py` 用 `ctypes.cast` 把 `IMMDevice.Activate()` 的返回值改写成
  `IAudioMeterInformation`/`IAudioClient`，但 `cast` 不增加引用计数，Activate 的临时指针与
  结果指针共享同一个 COM 引用，临时指针先析构就会把仍在使用的接口提前释放，之后任何一次
  `Release()` 都落在已释放的对象上。两处改用 `QueryInterface`（pycaw 自身取接口的方式，
  返回自带引用的指针），`AudioMeter.cleanup()` 同时在 COM 仍可用时放掉 meter 指针，退出期
  不再依赖 GC 与 COM 拆卸的先后；新增回归用例用指针替身记录引用计数，回环客户端与 meter
  各持一次引用（`tests/test_audio_spectrum.py`），真实设备上打开回环、静音期重建设备流、
  重新播放和退出均正常。
- 修复设置面板折叠区收起后仍留一条大空白行：Qt 5.15 的 `QFormLayout` 没有行级显隐，逐行
  隐藏控件后该行仍然占用一条 `verticalSpacing`，实测语音设置「高级设置」6 行、办公模式
  「独立 api」4 行收起后各留 66px / 44px 空白，整页多出 110px。两处折叠改为把折叠内容放进
  独立容器（`_gsv_advanced_group` / `_office_independent_api_group`）后整块 `setVisible()`，
  布局项被完全跳过；离屏渲染改前/改后对照确认收起态空白消失、展开态坐标逐像素一致。
- 退出动画的出厂默认目录改回 `爱弥斯联合_anima`：`lib/script/SEanima/clip.py` 的
  `DEFAULT_EXIT_ANIMATION_FOLDER` 自 LTS1.0.6beta2 起就是它，但
  `config/config_animation.py` 的 `ANIMATION` 字典一直写着 `星炬学院_anima`；带配置的默认值
  会覆盖代码默认值，所以没改过设置的用户退出动画仍是旧序列帧。`tests/test_seanima_clip_config.py`
  新增两例守住一致性（出厂配置必须等于代码常量、两个默认目录都必须在资源树里存在），
  离线安装器的 payload 校验清单同步改成要求 `爱弥斯联合_anima/0001.webp`，包内缺该目录时
  构建会直接失败而不是等到运行时才发现。
- 修复办公模式「启动时预热」被挤到分区最左侧：它单独住在一张 `QFormLayout` 里，而标签列宽度
  取自表内最长标签，整张表一个标签都没有时标签列塌成 0，复选框落到 x=0，比上面「办公模式独立
  api」左移整整一个标签列（渲染实测 32px vs 208px）。`QFormLayout.addRow("", widget)` 根本
  不创建标签项，`SettingsFormLayout._normalize_row()` 对空标签也帮不上忙；改成显式
  `addRow(QLabel("", parent), widget)` 让空标签占住固定宽的标签列，收起/展开两态都与其它字段
  同列（离屏渲染改前/改后对照确认）。
- 修复点歌经常播错歌：`ToolDispatcher._search_music()` 原来先按「作者是不是鸣潮」分档、再拿
  「歌名更短」当次级键，而歌名长短和点歌意图无关，会把接口排在第一位的正确结果挤掉（实测
  点「拉海洛之心」播成同作者的「纸飞机」、点「逆潮」播成「远航星的告别」）。现在先把歌名
  去噪归一（书名号、括号后缀、全角空格与标点都丢掉）再比：完全相同 +2、互相包含 +1，作者含
  「鸣潮」另 +2，平手保持接口原本的热度顺序，不再用歌名长度当次级键；这两次实测错播在
  `tests/test_tool_dispatcher.py` 里各钉了一条回归用例。
- 修复独立办公窗口的窗口底透明：`lib/script/ui/office_page.py` 是自绘外壳、没有宿主替它铺
  表面，`office_stylesheet()` 却一直把根控件设成 `background: transparent`，离屏渲染实测
  根背景 alpha=0，页头文字与卡片留白直接透出桌面。`office_stylesheet(standalone=True)`
  现在自己铺 `canvas` 画布底与 `border_strong` 描边（内嵌到工作台或设置面板时仍保持透明），
  窗口同时订阅 `CONFIG_UPDATED`，换主题后重刷样式，`cleanup()` 里退订。
- 修复技能与插件的安装路径逃逸：安装时把清单里的名字直接拼进技能根 / `node_modules`，名字
  写成 `../..` 这类路径就会把内容复制到根目录之外，而列表与卸载只看根目录，装出来的目录
  既看不见也删不掉。`skills._safe_skill_dir_name()` 与 `plugins._package_dir_parts()`
  现在拒绝路径分隔符、`.`/`..` 以及含 `:` 的段。
- 修复独立办公窗口的尺寸手柄停在左上角：`QSizeGrip` 建出来时还没显示，`resizeEvent` 里的
  `grip.isVisible()` 为假就直接跳过摆位，手柄留在 (0, 0)，页头左上角多出一块方点。显示与缩放现在
  都走 `_sync_size_grip()`，独立窗口渲染预览里方点消失、手柄回到右下角。
- 修复雪绒论坛卡片的粗体把笔画糊在一起：加粗靠的是一层同色描边（UI 字体只有 Bold 一个字面），
  笔宽原按字号 4.5% 取且下限 1px，2 倍字号的短句（33px 上下）拿到 1.48px，`加粗` 这类笔画密的
  字被描边填死、丢掉字怀。现在改成 3% 并收在 0.5px~1px：33px 实测描边 0.99px，墨迹比普通字
  多 27%（旧口径 37%），字怀重新打开。上限是硬要求，不是审美偏好；下限必须大于 0——`QPen`
  宽度 0 会被 Qt 当成 1px 的 cosmetic 笔。
- 修复「插件管理」在没装本机运行时的机器上一个内置插件都不显示：内置 bundle 由源 profile 的
  `package.json` 声明，但列表项要等包体落进 `node_modules` 才出现，干净检出或刚装上还没跑过
  provisioning 的机器（CI 检出可复现）整张卡片是空的。现在声明过的内置 bundle 一律列出并标记
  为内置、不可删除，包体没落地时在条目说明里写明装好本机运行时后即可加载。
- 修复切到工作台「办公模式」页时桌面上闪出一个空窗口：办公配置块的显隐回调会给办公后端、独立 api 开关、启动预热三行补 `setVisible(True)`，而它们在 `_build()` 里建出来时还没有父控件，Qt 按「无父控件的控件就是窗口」把它们当场提成顶层窗口显示；办公页又是切到该页时才构造的，于是闪过一个空的小窗口。这三行本来就不是显隐项（铺进分区后跟着分区显示），去掉这三下渲染逐像素不变，显隐回调只切「独立 api」那一块。

## [LTS1.0.7pre3] - 2026-09-12

### Added
- 自研 CUDA 极简推理端新增显存耗尽的整体退避：连续 8 次分配失败后本轮余下节点全部由主机执行，下一次图执行自动重试设备路径，不再逐节点回退。
- 安装包启动程序改由 `launch_entry.py` 引导，包内只保留启动与卸载两个 EXE，启动时不再闪现 cmd 控制台窗口。
- 卸载器统一为工作台风格，并可选择一并删除语音包与用户数据。
- 聊天工具链改用 OpenAI 兼容 tool call，人格词不再教授 `###指令###` 文本格式。

### Changed
- 面板绘制逻辑改由共享视觉层生成，核心图形不再依赖应用配置。
- 浮窗统一工作台风格、跟随系统亮暗，并支持拖动与最小化。
- 安装进度条改为语音包安装页同款：一青一粉两条 24px 进度条，条内居中显示百分比，下载条完成时显示“已完成”。
- 安装器解压改为自适应线程池：工作线程数取逻辑核数减 2（上限 4）、优先级最低，每 250ms 采样整机与自身 CPU 占用，其他进程占用超过 65% 时向单线程收敛，空闲时也不超过逻辑核的一半，多个核心分摊负载，不再吃满单个核心导致系统卡顿；解压时按未压缩大小预分配文件并声明顺序扫描，降低写入争抢。
- 卸载器加入与安装器同款的一青一粉进度条：先统计待删除文件再执行删除，青色统计条在总数未知时来回扫动，粉色删除条显示百分比，下方显示已删除文件数与字节数。
- 安装器解压阶段的剩余时间改为按最近 2 秒完成的文件数取平均速率估算，并且每 2 秒才刷新一次，进度条上的“预计剩余”不再随单个大文件上下跳动。
- 工作台更新器只探测能放下离线安装器 ZIP 的两个模型仓库（Hugging Face / ModelScope），
  并发读取清单并共享 16 秒总上限：首个源返回后最多再等 2 秒收集更快的镜像，超时后报
  “所有更新源在 16 秒内均不可用”。此前 GitHub PACK 与 Gitee 的 fetcher 虽然存在却从未
  被调用过（两个平台的 release 对单文件有体积上限，装不下安装器 ZIP），探测实际只在
  Hugging Face / ModelScope 之间串行等待（每个源各自 3 次重试、每次最长 10 秒）。
- 离线包依赖再剪枝约 53 MiB：移除从未被导入的纯 Python `jieba`（36 MiB）、`jieba_fast`
  的关键词抽取与 SWIG 源码、只用 Jython 加载的 `*.p` 概率表、`onnx` 的 C++ 源码、
  `numpy` 的链接期 `.lib`、Node 包内的 Yarn 插件，并阻止仓库根目录的测试日志与开发者
  `py.ini` 进入 payload。
- 仓库根目录的设计预览页不再进包：`local_pages/` 改名为不跟踪的 `.localpage/`，
  `.gitignore`、payload 收集与安装器归档都跳过新名和旧名，重复的 HarmonyOS 字体与
  GIF 素材不再随包发布。
- 移除 Qt 的软件 OpenGL 回退 `opengl32sw.dll`（20.0 MiB）：工作台没有任何
  `QOpenGLWidget`/`QSurfaceFormat`/Qt Quick/Qt WebEngine 用法，`opengl` 后端仍未实现，
  实测 `QApplication` 加桌宠式半透明窗口跑完后进程只加载 `d3d11`/`dxgi`，从不请求
  GL 上下文。守卫测试在有人引入 GL 用法或启用 OpenGL 后端时失败，提示把该 DLL 放回
  `QT_BIN_FILES`。
- 安装器内置归档改为 4 路固态 LZMA2 分片：ZIP 仍是结构完整的普通归档，每个 payload
  文件都保留一条带真实路径与真实大小的条目，字节则按目录顺序拼进
  `.fsv-shard-NNN.fsvlzma`，由 `.fsv-shard-index.bin` 记录所属分片、偏移和长度。
  `payload.zip` 从 353,348,712 字节降到 257,549,096 字节，安装器从 354,045,608 字节
  降到 258,262,888 字节（−95.8 MB，−27.1%）。同一台机器、同一份原生解压器实测
  17,124 个文件从 56.1 秒降到 43.7 秒：解压本来就由建文件和落盘主导（纯 inflate 只占
  4.1 秒），分片解码与写盘按分片重叠，且沿用原有的自适应负载门与最低线程优先级，
  并发分片数上限 4，仍然不会吃满任意一核。连同前面几轮剪枝，payload 从 828.9 MiB
  降到 747.8 MiB。

### Fixed
- 修复离线安装器首屏显示在线版文案：首屏在读取 PE 尾记录之前就已绘制，而“在线安装 /
  离线安装”当时由 `archive_size` 推断（此时仍为 0，于是被判成在线版）。现在构建时把模式
  写进 `payload_info.h`（`FSV_ONLINE_BUILD`），首屏文案、安装页副标题与默认错误提示都由
  该编译期常量决定，新增守卫测试钉住“文案不得再依赖 archive_size”。
- 修复工作台更新器的时间戳比对：只有远端发布时间严格晚于本机已安装时间戳（且版本不同或
  revision 改变）才提示更新，本机更晚（开发版）或时间相等（刚装过同一个包）统一提示已经是
  最新包，不再把旧包当成新版本推送；检查阶段文案也随之改为“正在获取更新包”。
- 工作台更新器不再把发布包文件名当作版本号：检查结果和“安装器已准备”都改显示发布 tag
  （安装器分支另起一行标注更新文件名），当前已是最新时也不再出现“当前已为”这类病句。
- 修正文字闪动特效被矩形裁剪的问题。
- CUDA 设备选址改为按显存与算力取舍，PTX 目标降到 `compute_61`，GTX 10xx 等 Pascal 卡不再被排除。
- `ScatterElements`、`ArgMax`、`Pad`、`InstanceNormalization` 设备化，单次合成阻塞回读从 539 次 / 103 MB 降到 442 次 / 61 MB。
- 卸载器复选框改为自绘（`BS_OWNERDRAW`），点击时不再叠加 Windows 原生复选框方块与虚线焦点框；安装器与卸载器的按钮键盘焦点统一改用青色描边环。
- 离线发行包补发 `doc/贡献名单和主播的狗盆/`（贡献名单与赞助图片），安装后的“贡献列表”不再只剩三条内置记录。

## [LTS1.0.7pre1] - 2026-09-06

### Added
- 新增自包含 NVIDIA CUDA 运行时 v2 方案，运行时携带 CPython、ONNX Runtime CUDA、CUDA/cuDNN DLL 和 Worker，只要求兼容的 NVIDIA 显示驱动。
- 新增离线运行时构建、校验、清单哈希和安全解压工具，并为安装器页面增加原生 DPI/状态视觉验收。

### Changed
- Windows 离线安装器和 Qt N 卡安装窗口统一为公告面板风格的亮色向导，并内嵌 HarmonyOS Sans SC 字体。
- 离线安装器更新链路改为校验并发布单文件 EXE 外层 ZIP，更新器从 Hugging Face 与 ModelScope 的固定清单选择同一发布包。

## [LTS1.0.7beta2] - 2026-08-30

### Added
- 办公模式设置新增后端选择入口，并以完整名称展示 DeepSeek Harness；安装依赖时可由用户决定是否安装该可选办公后端。

### Changed
- Node 下载源改为并发测速国内镜像与官方源，按实测延迟排序并在失败时依次回退。
- 依赖安装器拆分为职责明确的 `install_deps/` 模块，根目录脚本继续作为兼容入口。
- GitHub Actions 的测试环境固定为项目支持的 Python 3.11。
- 清理已退役的游戏统计、Qt 图片适配、本地服务骨架、自动语聊按钮，以及 PyNCM CLI 与其专用工具模块。
- Qt 产品控件、动画、游戏窗口和媒体适配归入明确 toolkit 边界；后端中立包新增静态与阻断导入审计，并清理已完成的历史计划文档。

### Fixed
- 修复办公模式长上下文和流式输出期间频繁滚动导致的页面卡顿，并在任务结束前禁用重复发送。
- 工具调用增加规范化与模糊别名恢复，降低模型输出轻微拼写偏差导致的调用失败。
- 修复办公运行时预热调用错误接口导致启动预热失败，并使用固定 I/O 槽位避免重复预热。
- 修复关闭桌宠时 Ollama 清理方法缺失导致的异常；退出清理现可隔离组件故障、重试失败项，并避免 Ollama 启动与退出竞态残留子进程。

## [LTS1.0.7beta1] - 2026-08-29

### Added
- 新增办公模式工作台、DSH 侧车、任务恢复、权限审批和办公状态 IPC。
- 办公输入框、工作目录和只读文本区域增加中文复制、粘贴、剪切右键菜单。

### Changed
- 网页登录统一使用系统 Microsoft Edge，安装器和绿色包不再下载或携带 Chromium 运行时。
- 安装器镜像测速改为并发执行，GUI 安装器获得逐次刷新的 UTF-8 进度记录。
- 发行包携带 DSH 源码与固定锁文件，不再生成已退役的元宝本地中转服务 bundle。

### Fixed
- 修复 OpenAI 兼容聊天客户端仍调用已删除元宝运行时接口、导致普通 API 请求潜在导入失败的问题。
- 修复办公服务在应用启动前注册定时器造成 IPC 轮询不稳定的问题。

## [LTS1.0.7test0822] - 2026-08-11

### Fixed
- 修复赞助列表开发者卡片尺寸计算不完整导致内容被裁切的问题。
- 修复窥屏、回忆等工具调用先播报中间答复、随后又播报最终答复造成的重复语音。
- 修复游戏包管理器文件选择窗口强制使用非原生样式的问题，恢复 Windows 默认文件对话框。
- 修复人格词在程序更新后被默认模板覆盖的问题；用户编辑后保存到用户目录并优先读取。

### Changed
- 工作台打开不再锁定 30 FPS，不再强制置顶或注册全局层级，改为普通任务栏窗口，并使用项目图标显示任务栏图标。
- 更新公告草稿，覆盖最近的工作台、语音、游戏包和人格词变更；本版本只推送源码，不发布程序包或公告。

## [LTS1.0.6pre7v2] - 2026-08-06

### Changed
- 新增实验性故障恢复命令 `#后端 dx` / `#后端 qt`：无需控制面板即可持久化切换绘制后端，写盘成功后通过统一退出链重启，失败时保留当前实例。
- DX 托盘新增“控制面板”命令，并将托盘双击行为对齐 Qt；设置由隔离 Qt workbench helper 承载，DX 主进程保持 Qt-free。
- DX 运行时补齐音乐边界：托盘清理历史/登录数据不再创建播放管理器，播放器 factory 改由桌面组合入口注入，DX 组合不再导入或实例化 `QtMusicPlayer`/QtMultimedia，并使用 MCI fallback；未修改现有登录态处理逻辑。
- DirectX 原生托盘升级到 ABI v7：菜单覆盖 CMD、游戏模式、鼠标穿透、开机启动、桌面/缓存/历史清理、作者主页和退出，命令通过统一 `FSDX_EVENT_TRAY_COMMAND` 事件交接，勾选状态由 `fsdx_set_tray_menu_state` 同步；Qt 与 DX 共用无 Qt 托盘动作路由，DirectX 现以实验性后端开放验证。
- DirectX/WARP 离屏原型升级到 ABI v3，单批支持 DirectWrite 文字、嵌套裁剪和二维变换，并通过同帧 UTF-8 payload 保持明确的跨语言内存所有权。
- DirectX 原型升级到 ABI v4，新增 Win32 + DirectComposition 透明窗口、窗口状态与生命周期、可见帧提交和有界事件轮询；`DxWindowHost` 已接入 `WindowHost v1` 与 `PetHostCallbacks` 纯数据转换，完整稳定性验收前保持实验性后端。
- DirectX 原型升级到 ABI v5，CPU 侧保留可重建的预乘资源数据，设备恢复时保持资源句柄和 HWND 稳定并重建 D3D/D2D/DComp 对象；提交路径仅对设备丢失执行一次恢复重试，并公开 generation 与恢复事件用于诊断。
- DirectX 输入边界升级到 ABI v6，物理按键与 Unicode 文本提交分离，原生窗口支持 `WM_POINTER*`、`WM_CHAR`/`WM_UNICHAR`、IME 预编辑/提交/结束和候选窗定位；DX 命令输入可显示中文组合文本且不会因 `ToUnicode` 重复写入字符。
- DirectX 诊断层新增 Qt-free owner-thread 循环、合并式周期调度和跨线程事件泵，并由 `DxApplicationRuntime` 统一驱动一次性任务、原生窗口轮询、退出确认及残留窗口关闭；完整稳定性验收前保持实验性后端。
- DX 诊断组合新增动态 Win32 显示器查询、GDI 主屏 PNG 截图、`DxPetWindow` 纯控制器宿主和层级窗口适配；资源句柄、窗口/context 注销及主宠关闭路径均有注入测试保护，现允许以实验性后端实际验证。
- DX 诊断组合新增原生 `Shell_NotifyIconW` 托盘宿主，公告/退出命令通过同一有界事件队列派发，并覆盖任务栏重建恢复、有限初始化重试、隐藏与幂等销毁；完整稳定性验收前仍保持实验性状态。
- DX 诊断组合新增 Qt-free 粒子和特效覆盖层，将文字、线段、矩形、圆形、图片缩放/旋转/羽化统一转换为不可变 `DrawBatch`；七类世界对象也已具备原生窗口、GIF、核心几何、拖拽、翻转、物理运动、点击穿透和淡出基础能力。
- Qt 与 DX 命令输入框收敛到共享视觉 composer：统一黑/青/粉外壳、白色输入区、`240x36` 配置尺寸、字体/占位文本和 IME 几何，并通过 Qt/DX-WARP 像素基线验证。
- 主宠 sprite 新增显式目标尺寸，Qt paint viewport 与 DX 重绘区域不再决定资源缩放；命令框相对主宠的锚点、屏幕边缘翻转和夹取也迁入共享纯几何解析器，避免窗口裁切和后端位置分歧。
- 二维码主体、action button 状态、DX 基础通知和七类世界对象视觉迁入共享 presenter；世界对象的 sprite、动画帧、透明度、翻转、中心缩放、闹钟倒计时、摩托抖动和音响 EMA/指数缩放由 Qt/DX 共用，Qt 世界对象只执行共享批次；Qt `BaseQrDialog` 与 DX application UI 共用 Qt 基准主题、布局、资源尺寸、按钮命中矩形和命令批次，Qt 原生按钮仅保留透明输入适配，整个 DX bridge 不再导入 PyQt 或构造产品颜色、字体和绘制命令。
- Qt 聊天气泡迁入共享 `BubbleVisualDescription`：换行、自适应尺寸、三层背景、混合字体分段、主宠锚点和屏幕夹取不再由 QWidget 绘制路径决定；Qt 只提供低级字体度量并执行共享批次，单行、多行、左对齐和硬换行保持迁移前逐像素一致。
- Qt 命令提示框迁入共享 `CommandHintVisualDescription`：三层背景、自适应尺寸、默认/哈希行、选中态、分隔线、混合字体、页码、默认文案和行命中矩形不再由 QWidget 私有 painter 路径决定；Qt 只提供低级字体度量并执行共享批次。DX 新增原生命令提示宿主，消费同一描述并支持跟随、筛选、导航、补全、翻页和点击执行，不导入或构造 Qt 控件。
- `INFORMATION` 气泡已接入 DX 原生 `_DxBubbleWindow`，与 Qt `Bubble` 共用 `BubbleVisualDescription` 的换行、尺寸、锚点和批次；DX 仍使用 Qt-free portable metrics。右键七个附属按钮已由共享两行布局和批次驱动，并由一个 DX 原生窗口承载完整控件组；DirectWrite 度量、完整动作分发和音响搜索 UI 宿主继续作为后续迁移项。
- 关闭、自动聊天、恢复、穿透、放大/缩小、启动鸣潮、聊天模式和更多功能八类矩形按钮迁入共享 presenter，Qt 控件不再私有维护背景层、内容区、字体和居中文字画法。
- 修复 DirectX 运行约十秒后主宠首次漫游可能通过游戏几何查询隐式构造 `GameRuntimePanel(QWidget)` 并触发“必须先构造 QApplication”的致命错误；游戏避让改用核心 `Rect` provider，核心键盘处理器也不再直接导入 Qt 播放列表，UI 包入口改为惰性兼容导出。
- 新增共享单个 `DxLoopContext` 的完整 DX `DesktopBackendBundle` 和 `DxApplicationUiHost`，原生命令输入、信息提示及元宝/音乐二维码不再依赖 Qt；阻断 PyQt 的 `ApplicationState` 启动、`APP_MAIN`、分阶段退出和 backend owner 最终清理组合已通过。DirectX 现以实验性后端开放，完整托盘菜单、自动公告、工作台隔离、共享设备资源和硬件验收完成后再转为稳定状态。
- `DesktopBackendBundle.cleanup` 成为后端级幂等收尾边界；应用事件循环结束后统一清理残留 scheduler、event pump、世界对象和 native host，应用状态构造失败也会释放单实例锁并执行后端清理。

### Fixed
- 修复全新 Python 环境运行安装器时，安装器提前导入应用配置和 Pillow，导致依赖尚未安装就退出的问题。
- 修复元宝预启动与手动登录并发时误切端口、旧监控阻塞重试和损坏 Chromium 候选阻断登录的问题；本地服务改为身份校验、串行生命周期和严格进程所有权清理。

## [LTS1.0.6pre7] - 2026-08-05

### Changed
- 应用版本号提升为 `LTS1.0.6pre7`，同步版本信息、README、发布手册和当前维护文档页头。
- 桌面后端改为统一路由与服务 bundle，应用生命周期、托盘、覆盖层、窗口宿主和主宠宿主通过明确协议组合；未实现或初始化失败的候选后端会记录原因并安全回退 Qt。
- 绘制资源、GIF 解码、批次排序和世界对象素材进一步移出 Qt 业务层，后端缓存按资源 revision 自动失效，为后续 DirectX/WARP 接入保留稳定边界。
- 应用级字体、公告、预加载、提示、登录和退出动画由统一 UI 宿主管理，退出时对称释放托盘、覆盖层、窗口和调度资源。
- 麦克风识别改为 WebRTC VAD 端点检测、整句 PCM 收集和 NumPy 频谱软降噪，保留现有 Vosk 模型与聊天事件出口，避免逐样本噪声门误伤语音。
- 手动语聊恢复静音端点自动结算，降噪结果为空时回退原始 PCM，并补充整句时长、RMS 与结算原因日志。

### Fixed
- 修复 Windows 用户名或安装目录包含空格、`&`、`!`、`%`、单引号等字符时，桌面快捷方式无法创建或普通启动、调试、安装依赖、更新重启链路解析路径失败的问题。

## [LTS1.0.6pre6] - 2026-08-03

### Changed
- 桌宠 Qt 后端进一步集中到核心宿主和桥接层，新增后端无关的应用运行时、图形/输入契约、桌宠宿主与移动运行时，并收敛调度、绘制、粒子、托盘和窗口适配边界。
- 工作台主题与设置页面布局、字号和外部页面主题刷新得到统一；多模态请求增加 5 秒共享启动冷却，避免连续图片请求挤占上游接口。
- 麦克风降噪改为基于短帧噪声底估计、响度变化率和渐变门限，降低整段 RMS 被语音污染后截断词尾的问题；自动语聊过滤连续的 `huh/嗯/啊` 等无意义识别结果，避免首次说完后重复触发。
- ONNX 语音包运行时修订到 6，中文前端在整句 G2PW 后按词优先应用 GPT-SoVITS 多音字规则，并保持三档包的既有量化层级。

## [LTS1.0.6pre5] - 2026-08-02

### Added
- AI 工具调用新增原生 Function Calling 通道：OpenAI 兼容接口与 Ollama 优先传递结构化工具调用，不支持 tools 的目标自动回退旧文本协议。
- 安装依赖脚本新增版本化的 Python 3.11 DirectML venv；控制面板语音设置新增“使用gpu混合推理（可能会提高显存占用）”开关。
- ONNX 语音新增隔离 Worker 混合后端：迭代式 `t2s_stage` 使用 CPU，其余图优先 DirectML；关闭开关会终止 Worker 释放显存，启动或推理失败时自动回退 CPU。
- CPU ONNX 后端同步迁移到低优先级隔离 Worker，并将自动预热延后到桌宠主界面就绪，避免预热阻塞 Qt 主线程。
- 中英混合语音改为单次语义推理内拼接双语前端特征，修复英文单词和盘符字母脱离上下文后发音异常的问题。
- 麦克风识别新增轻量 PCM16 自适应降噪、降噪开关、降噪强度和噪声门阈值配置，自动语聊的说话判定使用降噪后的音频。
- 回复模式配置新增自动陪伴开关与 1~20 分钟固定间隔滑块；保存后立即重排定时器，游戏模式仍优先使用固定 5 分钟间隔。

### Fixed
- 修复 Windows 重解析目录下删除 ONNX 语音包后，已匹配的安装状态文件可能未被清理的问题。
- 修复工作台切换页面和主题时缺少过渡、配置控件滚轮误改、主题开关焦点描边异常及页面动态创建闪烁的问题；工作台显示期间增加临时 30 FPS 限制，退出后恢复用户帧率。
- 修复更新器显示 release tag 而不是程序包名的问题；更新完成后提供普通重启和环境重启，分别调用 `启动程序.bat` 与 `安装依赖.bat`。
- 修复更新窗口、语音安装器、工作台和提示面板的层级注册、拖动、后台任务恢复及悬浮提示自动隐藏问题。
- 修复粒子覆盖层按整屏重绘、重定位插值坐标不同步造成的额外 GPU 消耗和视觉跳动；现在使用固定虚拟桌面和 128px 矩形分块，增量缓存包围盒、分块归属与绘制顺序，只刷新命中的合并脏区。
- 修复配置保存、语音包解压、麦克风启动和更新流程在 Qt 主线程执行长耗时 I/O 的问题；保存失败时不再提前关闭面板。

## [LTS1.0.6pre4] - 2026-07-31

### Changed
- 安装器先从 `resc.net.txt` 的 Gitee/GitHub `RESC` 镜像下载并校验 `jieba-fast 0.53` 的 Python 3.11 Windows x64 预编译 wheel，再安装 `genie-tts`，不再要求用户准备 C++ 编译环境。
- Python 解释器选择固定优先使用 64 位 Python 3.11，以匹配发布的本地扩展 wheel。

### Fixed
- 修复 Python 3.11.6 自带 pip 23.2.1 不支持 `--progress-bar raw`，导致全部依赖在下载前失败的问题。
- 修复依赖安装失败时吞掉 pip 输出、只显示失败包名的问题；现在保留具体错误原因，并继续尝试安装后续依赖。

## [LTS1.0.6pre3] - 2026-07-30

### Added
- 启动后异步获取桌宠公告，支持滚动正文、当日或永久抑制自动显示，以及托盘菜单手动打开。
- 控制面板“界面与动画”页新增“不显示公告”复选框，可查看和取消永久抑制状态。
- 新增爱弥斯 `aimisiV2` ONNX 语音包安装与本地推理，兼容中英文文本、参考音频/文案、角色模型和公共模型；旧 GSVmove 或缺失语音包时在控制面板顶部提示安装。
- 控制面板新增语音包删除入口，有效包或损坏包均可在确认后后台删除并立即重新安装。
- 桌宠随包提供固定版本的官方 UnRAR 与许可证，用于单文件 RAR5 语音包的安全检查和解压，不依赖用户环境或额外下载解压器。
- 发布 `aimisiV2` 完全包、默认中等包与节约包三档，并同步到 ModelScope 与 Hugging Face；包内包含中文 RoBERTa 与 tokenizer。

### Changed
- 托盘每次打开“桌宠公告”都会重新获取远端内容；连续点击只展示最后一次请求结果，网络失败时回退到最近一次成功缓存。
- 语音包安装界面新增完全包、中等包、节约包下拉选择，显示每档真实下载、解压与暂存空间需求。
- 语音包安装界面将磁盘空间说明明确为安装过程所需、下载包大小和安装后占用空间三项；节约包标注为“强烈推荐”。
- 语音包安装窗口新增“后台安装”：隐藏窗口而不中断任务，重新打开安装入口可恢复进度；桌宠仅在下载完成、激活、完成及异常等关键节点提醒。
- 语音包改为 64 MiB 字典的单文件 RAR5 solid archive，优先 ModelScope 下载、失败后完整回退 Hugging Face，不再使用 Gitee/GitHub 七分卷。
- 依赖安装输出收敛为已有/未安装依赖摘要，以及 pip 风格的青粉双色当前依赖和整体进度条；不支持终端颜色时自动使用等宽单色样式。
- 依赖安装前的可用性检查新增当前包、检查数、百分比和进度条，长时间扫描时不再只显示静态提示。
- 语音合成不再拉起端口 9880 的 GSVmove HTTP 服务；新包激活成功后安全清理旧外部运行时。
- 语音包安装完成后默认开启并立即预热 ONNX 语音模块，无需再次手动启用。
- ONNX 语音包升级到格式版本 2，除固定参考音频路径外兼容 GPT-SoVITS API v2 请求字段；采样、提示文案、文本切分、停顿、种子和语速参数接入真实推理链路。
- 格式版本 2 增加独立运行时修订号；缺失修订号或低于当前修订的旧包会显示“安装最新语音包”，不再继续使用含已知推理错误的模型图。
- 控制面板开放 ONNX 采样温度、Top-K、Top-P、重复惩罚、语速、长文本分句、片段停顿、随机种子和最大解码步数，并作为桌宠语音请求的运行时默认值。

### Fixed
- 修复元宝本地中转自动切换备用端口后，聊天仍请求旧端口而提示无法连接服务的问题。
- 修复语音包磁盘下拉弹层样式不一致且被置顶安装浮窗遮挡、RAR 后端与网络等待状态混淆，以及主安装按钮文字对比度不足的问题。
- 修复 ONNX 语速通过最终波形重采样实现而导致音高和音色变化的问题；默认语速改为 `1.0`，变速改由 VITS 模型内部完成。
- 修复共享 I/O 队列繁忙时语音包安装任务无法启动、弹窗持续停在“正在准备下载”且没有网络活动的问题。
- 修复 `genie-tts` 顶层导入受缺失 `GenieData` 和终端编码影响，导致安装依赖脚本每次启动都误判缺失并重复下载的问题。
- 修复语音包安装前递归搜索旧 GSVmove 导致“检查磁盘空间”阶段耗时过长的问题；容量检查通过后立即下载，旧运行时改为激活后按路径记录清理。
- 修复语音包安装进度在解压完成时提前显示 `100%`、校验阶段又退回处理中；解压、SHA-256 校验、依赖检查、激活和清理现在使用连续且不回退的总进度。
- 修复大型 RAR 解压时高频递归统计输出目录，与 UnRAR 争用磁盘元数据 I/O 而使界面看似卡死的问题；解压阶段改为动态进度，完整性校验继续显示精确进度。
- 修复非整数 ONNX 语速下 VITS 特征与外部噪声时间轴取整不一致，导致 `/vq_model/Mul_1` 出现 `99 by 100` 广播异常；`0.8/1.0/1.1/1.2` 四档 CPU 回归均可生成有效音频。
- 修复 ONNX 参考 HuBERT 提示语义未追加原生链路尾静音，以及语义 token 按循环下标截取可能少一帧的问题。
- 修复中文 RoBERTa 缺失或优化图在 CPU 不支持时静默退回零特征的问题；新包使用可执行的 RoBERTa ONNX 并在加载失败时明确报错。
- 修复英文聊天文本未携带多语言推理提示、以及 OpenAI/严格模式错误文本被误送入 ONNX 播报的问题；错误仅显示气泡，正常中英文回复保留原文并传递语言提示。

## [LTS1.0.6pre2] - 2026-07-29

本条目记录 `pre2` 版本的当前公开基线。

### Changed
- 版本基线更新为 `LTS1.0.6pre2`，并同步版本信息、README、发布说明与文档索引。
- GSV 语音合成设置仅在启动时检测到共享根目录的 `start_gsvmove.bat` 入口后显示。
- 手动 API 配置增加常用 OpenAI 兼容提供商地址预设，同时保留自定义地址和模型输入。
- 阿里云百炼改为使用统一的 OpenAI 兼容请求路径，移除 DashScope 专用请求、多模态与错误处理分支。

## [LTS1.0.6pre1] - 2026-07-20

本条目记录版本号系列由 `beta` 切换为 `pre` 后的公开基线。

### Changed
- 版本号系列由 `beta` 切换为 `pre`，当前基线更新为 `LTS1.0.6pre1`，并同步版本信息、README、发布说明与文档索引。

## [Unreleased]

### Changed
- AI 回复模式改为单一路由，选择福利 API、手动 API、本地 Ollama、规则回复或元宝后不再跨来源回退；模式专属配置改为按选择显隐。
- 福利 API 改为测速读取 GitHub/Gitee 发布配置源中的密钥与地址；默认使用内置 Agnes 2.0 Flash 模型标识，新增“智力提升”开关切换 Agnes 2.5 Flash，不再要求服务端实现 `/models`。配置下载超时固定 10 秒，失败后额外重试 3 次。
- 修复手动 API 已保存密钥仍被运行时旧快照判定为缺失，以及“保存并重启”在退出前同步执行网络热重载导致界面长时间未响应的问题。
- 手动 API 支持异步探测 OpenAI 兼容 `/models` 列表并从下拉框选择模型，仍可直接输入自定义模型；未填写协议的接口地址会自动补全 HTTP/HTTPS。
- 修复 AI 回忆工具读取旧目录、裸主题无法解析和可能递归调用的问题；补齐模型可见工具清单，并收紧浏览器协议与对象生成数量边界。
- 程序更新改为并发探测 GitHub `PACK` 与 Gitee“最新包”固定发布槽；主进程只下载和校验，退出后由独立更新进程覆盖安装并自动重新启动桌宠。

### Fixed
- 修复手动 API 密钥在其他进程写入或程序重启后仍使用模块导入期旧值的问题。
- 修复桌宠更新器把 `RESC` 安装资源 release 当作程序新版本的问题；程序更新不再扫描 release 列表。

## [LTS1.0.6beta5] - 2026-06-30

本条目记录拉海洛方块特效系统、右侧说明区重排和技能反馈补齐后的公开快照。

### Added
- 新增拉海洛方块特效系统，支持图片展示、时序动画和淡入淡出。
- 新增技能释放图像资源展示、技能语音与失败语音链路。
- 新增技能释放前置判定，失败时不进入暂停播片流程。
- 新增计分板漂字聚合逻辑，防止同位置同时间的加减分文本堆叠。
- 新增 `resc/tips.txt` tips 文案轮换，按行随机且优先未读。

### Changed
- 版本号更新为 `LTS1.0.6beta5`，并同步版本信息、README、更新日志与发布说明。
- 右侧说明区重新排版，`CONTROL`、`最高分` 与 `TIPS` 分区显示，避免标题与正文叠压。
- `CONTROL`、`TIPS` 标题改用拉海洛字体，tips 正文使用鸿蒙字体。
- `B` 键说明与逻辑统一为音乐暂停/继续事件。
- 拉海洛方块技能暂停时长固定为 2 秒，避免跟随特效变化。
- 技能资源改为使用对应 webp 展示图，缩放、羽化和抗锯齿逻辑同步整理。

### Fixed
- 修复右侧说明区 `CONTROL` 字样与内容叠压的问题。
- 修复 `最高分` 与 `TIPS` 区块过高的问题。
- 修复部分技能预判失效时仍进入暂停播片的逻辑漏洞。
- 修复千咲技能在填充率满格时仍可能误触发播片的问题。
- 修复技能语音链路中部分音效无法正常触发的问题。

## [LTS1.0.6beta3V2] - 2026-06-28

本条目按 `2026-06-28` 当前一轮性能与系统结构收敛整理，重点覆盖音频链、主宠移动、`tick/frame` 调度、异步计算搬运，以及粒子系统整体时序修正。

### Added
- 新增 `lib/core/compute_hub.py`，统一承接 IO / 向量 / CPU 三类后台任务执行器。
- 新增 `lib/core/pet_movement_queue.py`，为主宠提供事件驱动的移动步骤队列。
- 粒子系统新增更细粒度的点击粒子、方块小游戏粒子和运行期避让逻辑的配套接口。
- 拉海洛方块 runtime 新增游戏区几何查询接口，供主宠漫游避让使用。

### Changed
- 版本号更新为 `LTS1.0.6beta3V2`，并同步版本信息、README、更新日志与 changelog。
- 音频链路统一收敛：语音 / 特效音改为同一事件入口，核心按 `主音量 * 类型音量` 结算；音乐保持 `主音量 * 音乐音量` 独立链路。
- 主宠移动逻辑从直接控制重构为事件驱动队列，漫游、沙发锁定、音响靠近、雪豹追踪统一改为入队执行。
- `TimingManager` 统一发布 `TICK` / `FRAME`，物理、移动和粒子改为 `tick` 更新，`frame` 插值渲染。
- 多个重运算与阻塞请求已迁移到后台计算/IO 执行器，减轻主线程压力。
- practical 粒子脚本全部切到明确的 `tick/秒` 语义，不再依赖 `frame_fps` 运行时缩放。
- 右键 UI 淡出粒子、音乐音符粒子、点击粒子等效果做了新一轮密度/速度/寿命微调。

### Fixed
- 修复粒子系统在高并发申请下可能丢事件的问题。
- 修复 `tick/frame` 切换后粒子、物理、拖拽物体速度失真与静止抖动问题。
- 修复粒子覆盖层层级偏低导致被窗口遮挡的问题。
- 修复粒子渲染整数取整导致的慢速粒子抖动，并修正异步回填引发的轨迹回弹抽搐。
- 修复桌宠随机漫游可能停在或横穿拉海洛方块核心下落区的问题。
- 修复若干下载 / HTTP / 音乐播放启动等场景仍会卡主线程的问题。

## [LTS1.0.6beta2] - 2026-06-27

本条目按 `2026-06-12` 至 `2026-06-27` 最近一轮推送整理，涵盖 4 个功能批次：
`25a6da3`、`d91ef63`、`e4f3fd4`，以及当前工作区内对外部 API 配置脱敏补丁。

### Added
- `SEanima` 模块拆分为 `clip.py`、`decoder.py`、`effects.py`，将动画定义、帧解码计划与退出阴影特效分层解耦，便于后续扩展个性化序列帧资源。
- AI 设置面板新增启动/退出序列帧目录下拉配置，可直接从 `resc/GIF` 下的 `*_anima` 文件夹中选择动画资源。
- 新增测试 `tests/test_seanima_clip_config.py`，覆盖序列帧目录默认值与兼容回退逻辑。
- 新增测试 `tests/test_ollama_config_shared_secret_fallback.py`，覆盖共享 secrets 回退读取链路。

### Changed
- 版本号更新为 `LTS1.0.6beta2`，并同步 README、文档索引与发布说明入口。
- 启动动画默认目录调整为 `耶比_anima`，退出动画默认目录调整为 `爱弥斯联合_anima`。
- 旧的 `start-anim-compressed` / `exit-anim-compressed` 资源目录迁移为 `*_anima` 命名，播放器内部保留兼容回退解析，不改变外部启动/退出动画通信逻辑。
- 安装脚本对 Windows 下 Python 探测与调用链路进一步收口，减少多 Python 环境下的误判与拉起失败。
- 本地默认人格与部分本地默认配置同步调整，使新装环境的默认对话与对象行为更贴近当前版本设定。

### Fixed
- 外部 API 的 `API Key`、`API_BASE_URL`、`API_MODEL` 现在全部写入 `resc/user/ai/ollama_secrets.json`，不再明文落入受 Git 跟踪的 `config/ollama_config.py`。
- AI secrets 在项目目录与共享目录之间的读取/写入回退逻辑进一步补强，安装或迁移场景下更不容易丢失本地配置。
- 安装流程对本机 Python 解释器的识别更稳定，并继续保留本地 AI secrets，避免重新安装后覆盖用户已有敏感配置。

## [LTS1.0.6beta1] - 2026-06-12

本条目按 `2026-06-12` 最近一轮推送整理，涵盖 3 个提交：
`752344c`、`20c2971`、`200ced6`。

### Added
- 退出动画新增阴影强度、模糊半径、偏移方向配置项，并补充对应测试 `test_exit_animation_shadow_config`。
- AI 本地敏感配置独立存放到 `resc/user/ai/ollama_secrets.json`，新增保存链路测试 `test_ai_settings_storage_local_secrets`。

### Changed
- 元宝网页登录与本地中转改为复用系统已安装的 Microsoft Edge / Google Chrome，不再打包或部署内置 Chromium 运行时。
- 绿色包与安装脚本移除 Playwright Chromium 资源部署逻辑，减小浏览器运行时负担，避免继续携带过时内核。
- AI 设置面板补充退出动画阴影相关编辑项，并同步整理部分默认值与通用配置回填逻辑。
- 退出动画阴影渲染细节调整，关闭流程相关清理链路进一步收口。
- GSVmove 与 Yuanbao-Free-API 在 Windows 下的探测、拉起与清理命令改为隐藏控制台执行，减少闪窗。

### Fixed
- 元宝认证头改为每次从当前登录会话现场抓取，不再持久化缓存认证头文件；仅保留 `storage_state` 登录态，降低失效缓存导致的登录异常。
- API Key、`hy_user`、`x_uskey` 不再写入受 Git 跟踪的配置文件，避免误提交本机敏感数据。

## [LTS1.0.5] - 2026-05-10

本条目按 `2026-04-15` 至 `2026-05-10` 的提交整理，涵盖 11 个提交。

### Added
- 控制面板新增 **桌宠更新** 标签页，提供版本检测、增量/全量更新、进度展示与自动重启。
- 托盘菜单 **鼠标穿透** 改为可勾选开关，并新增全局穿透状态模块 `clickthrough_state.py`。
- 新增 **QR 对话框统一基类** `qr_dialog_base.py`，网易云 / 元宝 / QQ 群登录共用。
- 控制面板新增 **QQ 群二维码展示** 对话框。
- YuanBao 认证头持久化缓存 `storage_state_headers.json`，页面级 request 监听器被动捕获。
- 记忆系统重构：单文件 `memory.txt` → `memory/memory_YYYY-MM-DD.txt` 按日分片，启动自动迁移旧数据。
- 回忆工具 **加权评分算法**：匹配质量(精确3/子串2/内容1) × 时间衰减(1.0/0.8/0.5/0.3)。
- 控制面板新增 **回忆提取条数** UI 滑块 (5~50)，替换硬编码常量。
- 元宝附加参数硬编码：chat_id 恒空，should_remove_conversation/upload_images 恒 True。
- 新增语音提醒音频资源与 `ams_clickthrough_reminder` 模块。
- 仓库内置 `pyncm` 源码（因 PyPI 已下架该包）。
- 新增测试用例：`test_ai_settings_panel_yuanbao_hidden`、`test_gsvmove_root_resolution`、`test_yuanbao_free_api_stream`。

### Changed
- AI 设置面板移除 chat_id / should_remove_conversation / upload_images 复选框与输入框，防止误操作覆盖。
- QR 登录对话框重构减少约 500 行重复代码。
- HTTP 会话引入缓存池复用，减少 TLS 握手开销；流式读取 chunk_size 从 1 调整为 128。
- GSVmove 启动等待从 45s 延长到 90s，超时时输出日志尾部便于诊断。
- 回忆匹配逻辑从"取最新 N 条"改为加权评分，跳过 tool_recall 触发的 AI 回复写入记忆。
- tool_recall 消息不再附带 [默认记忆] 块，避免双路记忆混淆。
- 版本号从 `LTS1.0.5pre3` 发布为 `LTS1.0.5`。

### Fixed
- 修复应用关闭时退出动画被跳过的问题。
- 元宝二维码登录入口识别稳定性提升（扩展按钮选择器覆盖范围）。
- 元宝流式响应兼容 SSE 多行 data/event 协议，text 类型提取覆盖 msg/text/content/value 等字段。
- GSVmove 根目录解析增加空值保护。
- pyncm 从 pip 依赖改为项目内置源码，解决 PyPI 下架导致 CI 构建失败的问题。
- 元宝退出登录时清理认证头缓存，避免残留登录态。

## [LTS1.0.5pre2] - 2026-04-14

本条目按 `2026-03-19` 至 `2026-04-15` 的提交补充整理。

### Added
- 仓库内置 `YuanBao-Free-API` 服务源码，并补齐桌宠侧本地中转服务、二维码登录面板与登录状态联动。
- 控制面板补充 **启动时创建快捷方式** 开关。
- 新增绿色资源包打包脚本，用于携带模型与浏览器资源离线分发。
- 控制面板新增 **桌宠更新** 标签页，提供 **检查新版本** 与 **同步开发版** 按钮入口。
- 新增独立 **CMD 终端窗口**，支持历史记录、加载动画、ANSI 清理与 `/命令` 路由执行。
- 新增 **雪球** 对象系统、管理器、粒子、音效与控制面板配置项。
- 控制面板新增 **自动启用 GSV 语音模块** 开关，可直接控制桌宠启动时是否启用 GSV。
- 控制面板新增 **GSV 缓存上限** 滑块，范围 `1~128`，默认 `20`。
- 控制面板新增 **打开缓存文件夹** 按钮，方便直接查看最近生成的语音文件。
- GSV 语音生成结果现在会归档到 `resc/user/temp/gsv_voice/`。

### Changed
- 音乐模块继续向统一多源结构收口，QQ / 网易云 / 酷狗的搜索、播放、登录与路由逻辑进一步整理。
- 元宝登录链路改为由本地服务、浏览器自动化与二维码登录面板协同完成。
- 元宝登录状态持久化与退出链路继续整理，关闭流程更稳定。
- 控制面板标签栏边框绘制、高亮样式与更新页布局已重构。
- 关闭 GSV 开关后，既不会预热服务，也不会再处理按需 AI 文本语音请求。
- GSV 缓存改为统一目录直存文件，不再为每条语音创建单独文件夹。
- 语音缓存超过上限时会按时间自动清理旧文件。
- 打包脚本改为统一读取 `config/version_info.py` 中的版本号。
- 普通包与绿色包都会排除运行时状态，并对敏感配置进行脱敏。
- 仓库文档、发布说明、资料舱门户统一按 `pre2` 现状重写。

### Fixed
- 稳定元宝控制面板、浏览器登录、二维码登录与登录态持久化相关问题。
- 收紧退出流程，减少关闭时的服务残留与窗口残留。
- 改善绿色包构建与发布包内容边界，避免携带本机运行时状态与敏感参数。
- 修正文档中遗留的旧目录引用与旧版本号引用。
- 修正资料舱生成脚本对贡献/赞助文档目录的旧路径依赖。
- 将 `config/music/volume.json` 与 `config/user_scale.json` 从 Git 跟踪中移除，避免继续提交运行时状态。

## [LTS1.0.5pre1] - 2026-03-18

### Added
- 接入 GSVmove 文本转语音桥接，并补齐基础控制面板参数。
- 加入本地 Vosk 语音识别与 Push-to-Talk 能力。
- Ollama 模型检测与后台拉取流程进一步完善。
- 控制面板补充 YuanBao-Free-API 登录与配置入口。

### Changed
- AI、音乐、UI、安装脚本、事件与插件结构持续整理。
- 仓库默认配置不再内置 API Key 与登录态信息。

### Fixed
- 修复多项音乐播放、平台兼容、自适应缩放与 UI 显示问题。

### Disclaimer
- YuanBao-Free-API、登录态抓取与相关兼容能力仅用于学习、研究和个人测试。
- 使用第三方平台接口请自行评估风险并遵守其服务条款。
