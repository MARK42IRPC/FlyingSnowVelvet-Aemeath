"""Static dependency and resource catalog for the installer."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_LINKS_FILE = PROJECT_ROOT / "resc.net.txt"
RESOURCE_SOURCE_HOSTS = {
    "gitee.com": "Gitee",
    "github.com": "GitHub",
}
RESOURCE_PING_ATTEMPTS = 3
RESOURCE_PING_TIMEOUT_SECONDS = 5.0

MIN_VERSION = (3, 7, 0)
TARGET_PYTHON = (3, 11)

PYPI_MIRRORS = [
    {"name": "Tsinghua", "url": "https://pypi.tuna.tsinghua.edu.cn/simple", "host": "pypi.tuna.tsinghua.edu.cn"},
    {"name": "Aliyun", "url": "https://mirrors.aliyun.com/pypi/simple", "host": "mirrors.aliyun.com"},
    {"name": "Tencent", "url": "https://mirrors.cloud.tencent.com/pypi/simple", "host": "mirrors.cloud.tencent.com"},
    {"name": "Douban", "url": "https://pypi.douban.com/simple", "host": "pypi.douban.com"},
    {"name": "Huawei", "url": "https://repo.huaweicloud.com/repository/pypi/simple", "host": "repo.huaweicloud.com"},
    {"name": "USTC", "url": "https://pypi.mirrors.ustc.edu.cn/simple", "host": "pypi.mirrors.ustc.edu.cn"},
    {"name": "PyPI", "url": "https://pypi.org/simple", "host": "pypi.org"},
]

NPM_REGISTRIES = [
    {"name": "npmmirror", "url": "https://registry.npmmirror.com", "host": "registry.npmmirror.com"},
    {"name": "Tencent", "url": "https://mirrors.cloud.tencent.com/npm", "host": "mirrors.cloud.tencent.com"},
    {"name": "npmjs", "url": "https://registry.npmjs.org", "host": "registry.npmjs.org"},
]

DEPENDENCIES = [
    ("PyQt5", "Qt GUI framework", ("PyQt5",)),
    ("Pillow", "image processing", ("PIL",)),
    ("opencv-python", "image preprocessing for local web relay", ("cv2",)),
    ("playwright", "browser automation for web login capture", ("playwright",)),
    ("requests", "HTTP client", ("requests",)),
    ("qrcode", "QR code generation for music login", ("qrcode",)),
    ("mutagen", "local audio metadata parsing", ("mutagen",)),
    ("jieba-fast", "compiled Chinese tokenizer for genie-tts", ("jieba_fast",)),
    ("opencc-python-reimplemented", "Chinese script conversion for the ONNX text frontend", ("opencc",)),
    ("genie-tts", "bilingual ONNX text frontend", ("spec:genie_tts",)),
    ("numpy", "numerical runtime for ONNX voice synthesis", ("numpy",)),
    ("onnx", "ONNX model loader for voice synthesis", ("onnx",)),
    ("onnxruntime", "lightweight ONNX voice inference runtime", ("onnxruntime",)),
    ("rarfile", "safe multi-volume RAR parser", ("rarfile",)),
    ("soundfile", "ONNX voice audio writer", ("soundfile",)),
    ("soxr", "ONNX voice audio resampler", ("soxr",)),
    ("pycaw", "Windows audio meter", ("pycaw",)),
    ("comtypes", "COM bindings for pycaw", ("comtypes",)),
    ("pywin32", "Windows COM bridge (win32com/pythoncom)", ("pythoncom", "win32com")),
    ("sounddevice", "microphone capture for speech-to-text", ("sounddevice",)),
    ("webrtcvad-wheels", "lightweight speech endpoint detection", ("webrtcvad",)),
    ("vosk", "offline speech-to-text engine", ("vosk",)),
]

TOTAL_STEPS = 10
VOSK_MODEL_MARKERS = ("am", "conf", "graph", "ivector")
VOSK_MODELS_DIR = PROJECT_ROOT / "resc" / "models"
VOSK_MODEL_SPECS = (
    {
        "name": "vosk-model-small-cn-0.22",
        "label": "Chinese",
        "resource_name": "vosk-model-small-cn-0.22.zip",
    },
    {
        "name": "vosk-model-small-en-us-0.15",
        "label": "English",
        "resource_name": "vosk-model-small-en-us-0.15.zip",
    },
)
SEANIMA_TARGET_DIR = PROJECT_ROOT / "resc" / "GIF" / "SEanima"
SEANIMA_RESOURCE_NAME = "SEanima.zip"
SEANIMA_ARCHIVE = PROJECT_ROOT / "resc" / "GIF" / SEANIMA_RESOURCE_NAME
JIEBA_FAST_PACKAGE = "jieba-fast"
JIEBA_FAST_WHEEL_NAME = "jieba_fast-0.53-cp311-cp311-win_amd64.whl"
JIEBA_FAST_WHEEL_SHA256 = "a5d9cf41d6817963a73f672a429dbfe5b03a4ff327cedf490d5f2b21be8c00d0"
BINARY_ONLY_PACKAGES = frozenset({"opencc-python-reimplemented"})
DSH_RUNTIME_INSTALL_TIMEOUT = 30 * 60
PIP_INSTALL_TIMEOUT = 10 * 60
PIP_NETWORK_TIMEOUT = 30
PIP_NETWORK_RETRIES = 2
GET_PIP_URLS = (
    "https://mirrors.aliyun.com/pypi/get-pip.py",
    "https://bootstrap.pypa.io/get-pip.py",
)
GET_PIP_DOWNLOAD_TIMEOUT = 30
PACKAGE_REQUIREMENTS = {
    "opencc-python-reimplemented": "opencc-python-reimplemented>=0.1.7,<1",
}
