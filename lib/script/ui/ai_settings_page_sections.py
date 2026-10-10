"""AI 设置「AI 主页面」区块装配方法。

本模块把 `ai_settings_page.AISettingsPageMixin._build_ui` 里 508 行的线性装配
分成七个「区块装配」方法，每个方法只读入参 `scaffold`（`SettingsPageScaffold`）与
`self` 上已有的属性 / 工厂。这是「描述 + 后端渲染」收敛的铺垫：区块边界在此
被固定下来，之后每个区块可以逐个改成「声明描述 + 宿主渲染」而不影响其他区块。

行级等价：每个方法的语句与原 `_build_ui` 中对应段落逐字节相同；调用时作用于同一 `self`。

本模块 `import PyQt5`（区块直接构造 Qt 控件），按 34.2 节登记 `frozen_ui_qt_importers`。
"""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLineEdit,
    QListView,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config.ollama_config import (
    AI_VOICE_MAX_CHARS_MAX,
    AI_VOICE_MAX_CHARS_MIN,
)
from config.scale import scale_px
from lib.script.ui.ai_settings_defaults import AI_DEFAULT_VALUES as _DEFAULT_VALUES
from lib.script.ui.ai_settings_editors import _DecimalSliderField
from lib.script.ui.office_mode_settings import (
    ApiKeyLineEdit as _ApiKeyLineEdit,
    MANUAL_API_PROVIDER_PRESETS as _MANUAL_API_PROVIDER_PRESETS,
    WatermarkComboBox as _WatermarkComboBox,
)
from lib.script.ui.workbench_settings_layout import create_settings_form


#: 回复源路由：与 `ai_settings_page` 同源（原页反向导出）。
_GPU_MODE_CPU = "cpu"
_GPU_MODE_GPU = "gpu"
_GPU_MODE_AUTO = "auto"


class AISettingsPageSectionsMixin:
    """AI 主页面的七个「区块装配」方法；由 `AISettingsPageMixin` 多继承。"""

    def _build_reply_mode_section(self, scaffold) -> None:
        """装配「回复模式」区块（回复来源 / 自动陪伴 / 人格文件）。"""

        interface_section = scaffold.add_help_section(
            "回复模式",
            "选择本次保存后固定使用的回复来源。",
            help_text=(
                "决定桌宠聊天时去哪拿回复。选中的来源失败时不会自动换一个，"
                "而是直接把失败报出来，所以你看到问题就知道该改哪一项。\n\n"
                "福利 API 走项目提供的公共接口，不用自己填密钥；手动 API 需要你自己填"
                "密钥和地址；本地 Ollama 全部在本机跑，不联网但吃显卡；"
                "规则回复完全不调用模型，只按关键词给固定回应。"
            ),
        )
        form = create_settings_form()
        self._reply_mode_form = form
        interface_section.body_layout.addLayout(form)

        self._force_mode = _WatermarkComboBox()
        self._force_mode.setView(QListView(self._force_mode))
        self._force_mode.addItem('福利 API', '1')
        self._force_mode.addItem('手动 API', '0')
        self._force_mode.addItem('本地 Ollama', '2')
        self._force_mode.addItem('规则回复', '3')
        form.addRow("回复模式", self._force_mode)
        self._set_form_row_description(
            form,
            self._force_mode,
            "回复只走选中的来源，失败时不会切换到其他来源。",
        )

        self._auto_companion_enabled = QCheckBox("启用自动陪伴")
        self._auto_companion_enabled.setChecked(True)
        form.addRow("", self._auto_companion_enabled)
        self._set_form_row_description(
            form,
            self._auto_companion_enabled,
            "开启后会按设定间隔自动触发陪伴对话。",
        )

        self._auto_companion_interval_minutes = _DecimalSliderField(
            1,
            20,
            1,
            value=_DEFAULT_VALUES["auto_companion_interval_minutes"],
            decimals=0,
            suffix=" 分钟",
        )
        form.addRow("陪伴间隔", self._auto_companion_interval_minutes)
        self._set_form_row_description(
            form,
            self._auto_companion_interval_minutes,
            "自动陪伴两次观察之间的时间，范围 1~20 分钟。",
        )

        self._auto_companion_enabled.toggled.connect(self._update_auto_companion_interval_row)

        persona_row, persona_layout = self._create_field_row_group(spacing=scale_px(8, min_abs=6))
        self._open_persona_file_btn = QPushButton("设置人格词")
        self._open_persona_file_btn.setFixedWidth(scale_px(110, min_abs=92))
        self._open_persona_file_btn.clicked.connect(self._on_open_persona_file)
        persona_layout.addWidget(self._open_persona_file_btn, 0)
        persona_layout.addStretch(1)
        form.addRow("人格配置", persona_row)
        self._set_form_row_description(
            form,
            persona_row,
            "使用系统默认程序打开当前生效的人格 txt，直接编辑系统 prompt。",
        )
        self._set_widget_description(self._open_persona_file_btn, "使用系统默认程序打开当前生效的人格 txt。")


    def _build_welfare_section(self, scaffold) -> None:
        """装配「福利 API 开关」区块。"""

        self._welfare_section = scaffold.add_help_section(
            "福利 API 配置",
            "仅在回复模式选择福利 API 时显示。",
            help_text=(
                "公共接口的开关，只在回复模式选「福利 API」时才有意义。\n\n"
                "「智力提升」打开后会换成能力更强的模型，回复更聪明但更慢，"
                "也更容易触发公共接口的限流。只是日常闲聊的话关着就够用。"
            ),
        )
        form = create_settings_form()
        self._welfare_section.body_layout.addLayout(form)
        self._welfare_intelligence_boost = QCheckBox("智力提升")
        form.addRow("", self._welfare_intelligence_boost)
        self._set_form_row_description(
            form,
            self._welfare_intelligence_boost,
            "关闭时使用 Agnes 2.0 Flash；开启后使用 Agnes 2.5 Flash。",
        )


    def _build_manual_api_section(self, scaffold) -> None:
        """装配「手动 API 开关」区块（密钥 / 提供商 / 地址 / 模型）。"""

        self._manual_api_section = scaffold.add_help_section(
            "手动 API 配置",
            "仅在回复模式选择手动 API 时显示。",
            help_text=(
                "接任意 OpenAI 兼容接口。\n\n"
                "「接口密钥」单独保存，不写进普通配置文件，清理登录数据时会一起清掉。\n\n"
                "「常用提供商」只是帮你把地址填好，选完仍然可以手动改；"
                "用第三方中转或自建服务时，注意地址要以 /v1 结尾。\n\n"
                "「模型名」必须和对方服务里实际存在的名字一致，填错会直接报错而不是自动回退。"
            ),
        )
        form = create_settings_form()
        self._manual_api_section.body_layout.addLayout(form)

        self._api_key = _ApiKeyLineEdit()
        form.addRow("接口密钥", self._api_key)
        self._set_form_row_description(
            form,
            self._api_key,
            "OpenAI 兼容接口密钥，单独保存在用户密钥文件中。",
        )

        self._manual_api_provider = _WatermarkComboBox()
        self._manual_api_provider.setView(QListView(self._manual_api_provider))
        for label, base_url in _MANUAL_API_PROVIDER_PRESETS:
            self._manual_api_provider.addItem(label, base_url)
        self._manual_api_provider.currentIndexChanged.connect(self._on_manual_api_provider_changed)
        form.addRow("常用提供商", self._manual_api_provider)
        self._set_form_row_description(
            form,
            self._manual_api_provider,
            "选择后自动填入该提供商的 OpenAI 兼容接口地址；自定义地址仍可直接填写。",
        )

        self._api_base_url = QLineEdit()
        self._api_base_url.textChanged.connect(self._sync_manual_api_provider_selection)
        self._api_base_url.editingFinished.connect(self._normalize_manual_api_base_url_input)
        form.addRow("接口地址", self._api_base_url)
        self._set_form_row_description(
            form,
            self._api_base_url,
            "外部接口地址，通常填写兼容 OpenAI 的基地址；若直接填写完整的 `/chat/completions` 或 `/v1/chat/completions` 端点也可兼容。",
        )

        api_model_row, api_model_layout = self._create_field_row_group(spacing=scale_px(8, min_abs=6))
        self._api_model = _WatermarkComboBox()
        self._api_model.setView(QListView(self._api_model))
        self._api_model.setEditable(True)
        self._api_model.setInsertPolicy(QComboBox.NoInsert)
        self._api_model.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if self._api_model.lineEdit():
            self._api_model.lineEdit().setPlaceholderText("输入或探测接口模型")
        api_model_layout.addWidget(self._api_model, 1)
        self._probe_manual_api_models_btn = QPushButton("探测模型")
        self._probe_manual_api_models_btn.setFixedWidth(scale_px(100, min_abs=84))
        self._probe_manual_api_models_btn.clicked.connect(self._on_probe_manual_api_models)
        api_model_layout.addWidget(self._probe_manual_api_models_btn, 0)
        form.addRow("接口模型", api_model_row)
        self._set_form_row_description(
            form,
            api_model_row,
            "外部接口模型名，例如 qwen3.5-plus。可探测 OpenAI 兼容接口的 /models 列表，也可直接手动输入。",
        )
        self._set_widget_description(self._probe_manual_api_models_btn, "使用当前填写的接口地址和密钥探测可用模型列表。")


    def _build_ollama_section(self, scaffold) -> None:
        """装配「Ollama 设置」区块（地址 / 模型 / 设备优先 / CPU 线程）。"""

        self._ollama_section = scaffold.add_help_section(
            "Ollama 配置",
            "仅在回复模式选择本地 Ollama 时显示。",
            help_text=(
                "把推理放在本机跑，不联网、不上传内容。\n\n"
                "需要先自己装好 Ollama 并把模型拉下来；地址通常是本机的 11434 端口。\n\n"
                "本地模型的效果和显存直接相关，模型越大越慢；"
                "机器带不动时可以换小模型，而不是反复调这里的参数。"
            ),
        )
        form = create_settings_form()
        self._ollama_section.body_layout.addLayout(form)

        base_row, base_layout = self._create_field_row_group(spacing=scale_px(8, min_abs=6))
        self._ollama_base_url = self._create_config_line_edit(expanding=True)
        base_layout.addWidget(self._ollama_base_url, 1)
        self._open_ollama_app_btn = QPushButton("打开Ollama")
        self._open_ollama_app_btn.setFixedWidth(scale_px(110, min_abs=92))
        self._open_ollama_app_btn.clicked.connect(self._on_open_ollama_app)
        base_layout.addWidget(self._open_ollama_app_btn, 0)
        form.addRow("Ollama地址", base_row)
        self._set_form_row_description(
            form,
            base_row,
            "本地 Ollama 服务地址，默认 http://localhost:11434。",
        )
        self._set_widget_description(self._open_ollama_app_btn, "打开 Ollama 应用或下载页，便于获取/管理模型。")

        self._ollama_model = _WatermarkComboBox()
        self._ollama_model.setView(QListView(self._ollama_model))
        self._ollama_model.setEditable(True)
        self._ollama_model.setInsertPolicy(QComboBox.NoInsert)
        self._ollama_model.set_before_popup_callback(self._refresh_ollama_model_dropdown)
        if self._ollama_model.lineEdit():
            self._ollama_model.lineEdit().setPlaceholderText("自动检测本地模型")
        form.addRow("Ollama模型", self._ollama_model)
        self._set_form_row_description(
            form,
            self._ollama_model,
            "本地 Ollama 使用的模型名，从检测到的模型列表中选择。",
        )

        self._gpu_mode = _WatermarkComboBox()
        self._gpu_mode.setView(QListView(self._gpu_mode))
        self._gpu_mode.addItem("CPU优先", _GPU_MODE_CPU)
        self._gpu_mode.addItem("GPU优先", _GPU_MODE_GPU)
        self._gpu_mode.addItem("自动", _GPU_MODE_AUTO)
        form.addRow("推理模式", self._gpu_mode)
        self._set_form_row_description(
            form,
            self._gpu_mode,
            "控制推理设备偏好；自动模式会按环境能力选择。",
        )

        self._num_thread = QLineEdit()
        form.addRow("CPU线程数", self._num_thread)
        self._set_form_row_description(
            form,
            self._num_thread,
            "CPU 推理线程数，0 表示使用框架默认值。",
        )


    def _build_reply_mode_sync(self) -> None:
        """回复模式下拉框的信号连接与首次同步（原 `_build_ui` 末尾）。"""

        self._force_mode.currentIndexChanged.connect(self._update_reply_mode_sections)
        self._update_reply_mode_sections()


    def _build_generation_section(self, scaffold) -> None:
        """装配「生成参数」区块（温度 / 模型视野）。"""

        generation_section = scaffold.add_help_section(
            "生成参数",
            "调整大模型输出与图片输入。",
            help_text=(
                "控制模型「怎么答」。\n\n"
                "「温度」越高回答越随机、越低越保守；「最大长度」限制一次最多说多长；"
                "「上下文轮数」决定它还记得前面聊过多少。\n\n"
                "上下文调大会同时增加显存占用和每次回复的时间，长对话卡顿时优先动这一项。"
            ),
        )
        form = create_settings_form()
        generation_section.body_layout.addLayout(form)

        self._api_temperature = _DecimalSliderField(0.0, 2.0, 0.05, value=_DEFAULT_VALUES["api_temperature"])
        form.addRow("大模型温度", self._api_temperature)
        self._set_form_row_description(
            form,
            self._api_temperature,
            "大模型采样温度范围 0~2，越高回复越发散。",
        )

        self._model_vision = _DecimalSliderField(0, 100, 1, value=_DEFAULT_VALUES["model_vision"], decimals=0)
        form.addRow("模型视力", self._model_vision)
        self._set_form_row_description(
            form,
            self._model_vision,
            "视力越高，token消耗越高，看图越清晰。100 为不压缩，0 为压缩到 720p。",
        )


    def _build_voice_section(self, scaffold) -> None:
        """装配「语音合成」区块（基础选项 + 高级参数）。"""

        self._gsv_launcher_available = not self._voice_package_status.install_required
        self._voice_section = scaffold.add_help_section(
            "语音合成",
            "控制 ONNX 语音模型、采样、节奏和本地缓存。",
            help_text=(
                "桌宠说话的声音。\n\n"
                "涉及到具体模型、采样率和分段的项建议保持默认：这些值和语音包是配套的，"
                "改错会让声音变调、变快或者直接不出声。\n\n"
                "「缓存」用来复用已经合成过的句子，关掉之后每句话都要重新推理，"
                "反应明显变慢，但不会再往磁盘写文件。"
            ),
        )
        form = create_settings_form()
        self._voice_section.body_layout.addLayout(form)

        self._gsv_auto_start = QCheckBox("自动启用ONNX语音模块")
        self._gsv_auto_start.setChecked(_DEFAULT_VALUES["gsv_auto_start"])
        form.addRow("", self._gsv_auto_start)
        self._set_form_row_description(
            form,
            self._gsv_auto_start,
            "开启后，桌宠启动时会在后台加载并预热 ONNX 语音模型。",
        )

        self._gsv_gpu_hybrid = QCheckBox("通用 GPU 加速（DirectML）")
        self._gsv_gpu_hybrid.setChecked(_DEFAULT_VALUES["gsv_gpu_hybrid"])
        form.addRow("", self._gsv_gpu_hybrid)

        self._gsv_nvidia_cuda_acceleration = QCheckBox("N卡加速")
        self._gsv_nvidia_cuda_acceleration.setChecked(
            _DEFAULT_VALUES["gsv_nvidia_cuda_acceleration"]
        )
        form.addRow("", self._gsv_nvidia_cuda_acceleration)
        self._set_form_row_description(
            form,
            self._gsv_nvidia_cuda_acceleration,
            "使用 NVIDIA 显卡推理，只需已安装显卡驱动；开启后显存占用会增加。",
        )

        self._gsv_temperature = _DecimalSliderField(0.01, 2.0, 0.01, value=_DEFAULT_VALUES["gsv_temperature"])
        form.addRow("采样温度", self._gsv_temperature)
        self._set_form_row_description(
            form,
            self._gsv_temperature,
            "情绪自然：数值越高语调起伏越丰富，过高可能使语调不稳定。",
        )

        self._gsv_repetition_penalty = _DecimalSliderField(
            0.1,
            2.0,
            0.01,
            value=_DEFAULT_VALUES["gsv_repetition_penalty"],
        )
        form.addRow("重复惩罚", self._gsv_repetition_penalty)
        self._set_form_row_description(
            form,
            self._gsv_repetition_penalty,
            "情绪丰富：数值越高越不容易重复同一语气。",
        )

        self._gsv_speed_factor = _DecimalSliderField(0.5, 2.0, 0.05, value=_DEFAULT_VALUES["gsv_speed_factor"])
        form.addRow("ONNX语速", self._gsv_speed_factor)
        self._set_form_row_description(
            form,
            self._gsv_speed_factor,
            "语速快慢：1.0 为原速，数值越大语速越快。",
        )

        self._gsv_cache_max_files = _DecimalSliderField(1, 128, 1, value=_DEFAULT_VALUES["gsv_cache_max_files"], decimals=0)
        form.addRow("语音缓存上限", self._gsv_cache_max_files)
        self._set_form_row_description(
            form,
            self._gsv_cache_max_files,
            "语音保存条数：保留最近生成的语音条数，超出后自动删除旧缓存。",
        )

        gsv_cache_row, gsv_cache_layout = self._create_field_row_group(spacing=scale_px(8, min_abs=6))
        self._open_gsv_cache_dir_btn = QPushButton("打开文件夹")
        self._open_gsv_cache_dir_btn.setFixedWidth(scale_px(132, min_abs=112))
        self._open_gsv_cache_dir_btn.clicked.connect(self._on_open_gsv_cache_dir)
        gsv_cache_layout.addWidget(self._open_gsv_cache_dir_btn, 0)
        gsv_cache_layout.addStretch(1)
        form.addRow("语音缓存", gsv_cache_row)
        self._set_form_row_description(
            form,
            gsv_cache_row,
            "打开 ONNX 语音缓存目录。",
        )
        self._set_widget_description(self._open_gsv_cache_dir_btn, "打开 ONNX 语音缓存目录。")

        self._gsv_advanced_toggle = QCheckBox("高级设置")
        self._gsv_advanced_toggle.toggled.connect(self._update_gsv_advanced_visibility)
        form.addRow("", self._gsv_advanced_toggle)
        self._set_form_row_description(
            form,
            self._gsv_advanced_toggle,
            "展开采样候选、分句、停顿、随机种子和字数限制等进阶参数。",
        )

        # 折叠块整块放进分区 body：QFormLayout 隐藏行仍占行距，会留下一条大空白
        self._gsv_advanced_group = QWidget(self._voice_section)
        advanced_layout = QVBoxLayout(self._gsv_advanced_group)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(0)
        advanced_form = create_settings_form()
        self._gsv_advanced_form = advanced_form
        advanced_layout.addLayout(advanced_form)
        self._voice_section.body_layout.addWidget(self._gsv_advanced_group)

        self._gsv_top_k = _DecimalSliderField(1, 1025, 1, value=_DEFAULT_VALUES["gsv_top_k"], decimals=0)
        advanced_form.addRow("Top-K", self._gsv_top_k)
        self._set_form_row_description(advanced_form, self._gsv_top_k, "每一步保留概率最高的候选数量，默认 15。")

        self._gsv_top_p = _DecimalSliderField(0.01, 1.0, 0.01, value=_DEFAULT_VALUES["gsv_top_p"])
        advanced_form.addRow("Top-P", self._gsv_top_p)
        self._set_form_row_description(advanced_form, self._gsv_top_p, "限制累计概率候选范围，1.0 表示不额外截断。")

        self._gsv_text_split_method = _WatermarkComboBox()
        self._gsv_text_split_method.setView(QListView(self._gsv_text_split_method))
        for label, value in (
            ("按全部标点分句", "cut5"),
            ("不自动分句", "cut0"),
            ("每四句一段", "cut1"),
            ("每约 50 字一段", "cut2"),
            ("按中文句号分句", "cut3"),
            ("按英文句号分句", "cut4"),
        ):
            self._gsv_text_split_method.addItem(label, value)
        advanced_form.addRow("长文本分句", self._gsv_text_split_method)
        self._set_form_row_description(advanced_form, self._gsv_text_split_method, "控制长回复如何拆成多个独立语音片段。")

        self._gsv_fragment_interval = _DecimalSliderField(
            0.0,
            5.0,
            0.05,
            value=_DEFAULT_VALUES["gsv_fragment_interval"],
        )
        advanced_form.addRow("片段停顿(秒)", self._gsv_fragment_interval)
        self._set_form_row_description(advanced_form, self._gsv_fragment_interval, "分句片段之间插入的静音时长，默认 0.3 秒。")

        self._gsv_seed = QLineEdit(str(_DEFAULT_VALUES["gsv_seed"]))
        self._gsv_seed.setPlaceholderText("-1 表示每次随机")
        advanced_form.addRow("随机种子", self._gsv_seed)
        self._set_form_row_description(advanced_form, self._gsv_seed, "-1 为随机；固定非负整数可复现 T2S 采样结果。")

        self._ai_voice_max_chars = _DecimalSliderField(
            AI_VOICE_MAX_CHARS_MIN,
            AI_VOICE_MAX_CHARS_MAX,
            1,
            value=_DEFAULT_VALUES["ai_voice_max_chars"],
        )
        advanced_form.addRow("语音字数限制", self._ai_voice_max_chars)
        self._set_form_row_description(
            advanced_form,
            self._ai_voice_max_chars,
            "ONNX 语音合成最大文本长度，超过此长度的回复不会转为语音。",
        )

        self._gsv_advanced_rows = (
            self._gsv_top_k,
            self._gsv_top_p,
            self._gsv_text_split_method,
            self._gsv_fragment_interval,
            self._gsv_seed,
            self._ai_voice_max_chars,
        )
        self._update_gsv_advanced_visibility()
        self._update_gsv_settings_visibility()


    def _build_memory_section(self, scaffold) -> None:
        """装配「记忆体验」区块（上下文 / 回忆数量 / 思考模式）。"""

        memory_section = scaffold.add_help_section(
            "记忆与陪伴",
            "调整会话记忆规模和自动陪伴行为。",
            help_text=(
                "桌宠记得多少，以及会不会主动找你说话。\n\n"
                "「记忆条数」决定长期记忆里保留多少条内容，调大更连贯但每次请求也更长。\n\n"
                "「自动陪伴」打开后桌宠会按设定间隔自己开口；"
                "间隔按分钟算，调得太小会显得聒噪，也会更快消耗 API 额度。"
            ),
        )
        form = create_settings_form()
        memory_section.body_layout.addLayout(form)

        self._memory_context_limit = _DecimalSliderField(0, 48, 1, value=_DEFAULT_VALUES["memory_context_limit"])
        form.addRow("记忆上下文条数", self._memory_context_limit)
        self._set_form_row_description(
            form,
            self._memory_context_limit,
            "附带给 AI 的 recent memory 条数，0 表示不附带，范围 0~48。",
        )

        self._memory_recall_count = _DecimalSliderField(5, 50, 1, value=_DEFAULT_VALUES["memory_recall_count"])
        form.addRow("回忆提取条数", self._memory_recall_count)
        self._set_form_row_description(
            form,
            self._memory_recall_count,
            "回忆工具单次提取的记忆条数，范围 5~50。",
        )

        self._api_enable_thinking = QCheckBox("启用思考模式(外部接口可用)")
        form.addRow("", self._api_enable_thinking)
        self._set_form_row_description(
            form,
            self._api_enable_thinking,
            "开启后，支持思考模式的外部接口将返回推理链路。",
        )
