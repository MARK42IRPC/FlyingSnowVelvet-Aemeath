"""????????"""

from __future__ import annotations

CHAT = {
    # 人格文件路径已移至 ollama_config.py 的 PERSONA_FILE
}
VOICE = {
    # 语音音量系数（0.0-1.0）
    'voice_volume': 1.0,
    # 拉海洛方块技能释放语音音量系数（0.0-1.0）
    'lahai_skill_release_volume': 0.7,
    # Push-to-talk hotkey for microphone STT; leave empty to disable (e.g. "Ctrl+Shift+V")
    'microphone_push_to_talk_key': 'V',
    # Vosk 模型列表：默认同时加载中英小模型，支持混合识别
    'microphone_model_paths': [
        'resc/models/vosk-model-small-cn-0.22',
        'resc/models/vosk-model-small-en-us-0.15',
    ],
    # 自动语聊静音超时：持续未说话达到该秒数后自动结束本轮识别
    'microphone_silence_timeout_secs': 3.0,
    # 自动语聊说话判定阈值：麦克风音频 RMS 高于该值视为正在说话
    'microphone_speech_rms_threshold': 550,
    # 轻量自适应降噪：对低于噪声门的 PCM16 样本衰减，避免引入大型音频依赖
    'microphone_denoise_enabled': True,
    'microphone_denoise_strength': 0.65,
    'microphone_noise_gate_threshold': 180,
}
