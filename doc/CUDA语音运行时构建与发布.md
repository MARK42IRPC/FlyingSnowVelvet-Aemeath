# CUDA 语音运行时构建与发布

更新时间：2026-09-01

本文定义现有 **r1** 运行时的裁剪、验证和发布流程。r1 面向 Windows 64 位
Python 3.11 venv，运行时只服务于当前独立 ONNX 语音包，不作为通用 CUDA 或
ONNX Runtime 发行物。新的包内 CPython/Worker 方案属于独立的 **v2** 契约，见
[CUDA 语音运行时 v2 自包含方案](CUDA语音运行时v2自包含方案.md)；在 v2 完成真实模型回归和发布审核前，不能把 r1 包当作 v2，也不能复用 r1 的哈希或激活标记。

## 1. 推进计划

| 阶段 | 范围 | 完成标准 | 当前状态 |
| --- | --- | --- | --- |
| 第一阶段 | 保留官方 ORT 1.22 二进制，按真实加载结果裁掉未使用 CUDA 包、开发文件和 Python 工具 | 可复现构建；ZIP 双重校验；全新 venv 中英文及混合文本真实推理通过；客户端按需下载并可回退 DirectML/CPU | 客户端集成、RTX 3050 验证、双仓上传与公开对象哈希验收均完成 |
| 第二阶段 | 根据三档语音包的 ONNX 算子并集编译 reduced-operator ORT CUDA | 覆盖 contrib 算子、控制流、Sequence、STFT/DFT 和 I/O Binding；三档模型全量回归 | 未开始；先评估相对第一阶段的净节省 |
| 第三阶段 | 评估 TensorRT RTX 或其它非 ORT 后端 | 明确图改写、插件、缓存和 GPU 架构兼容成本 | 暂不推进；不属于即插即用替换 |

第一阶段不删除官方 ORT 内部算子，也不修改模型图。它只裁剪 Python 包表面、CUDA 动态库和安装环境，因此风险明显低于算子级自编译。

## 2. 第一阶段产物

固定产物：

```text
aemeath-onnx-cuda-r1-ort1.22-cu12-cp311-win_amd64.zip
```

当前已验证构建：

| 项目 | 值 |
| --- | --- |
| Bundle ID | `r1-1a5fe403cf843d3f` |
| ZIP 大小 | `1,700,089,579` 字节，约 1.58 GiB |
| 解压 payload | `2,532,836,762` 字节，约 2.36 GiB |
| payload 文件数 | 26 |
| ZIP SHA-256 | `643225a1b6544315b6b3d0c41cc5ed65be15c5b1ea7fb33ee3295bc3d5d348b1` |
| 已验证模型 | 节约包 `format_version=2`、`runtime_revision=8` |
| 已验证 GPU | NVIDIA GeForce RTX 3050 |

现有完整 CUDA venv 约 3.62 GiB；精简安装约 2.36 GiB，解压体积减少约 1.26 GiB。ZIP 使用 Deflate 压缩原生 DLL，远端下载量约 1.58 GiB。

## 3. 文件边界

Bundle 保留：

- 官方 `onnxruntime-gpu==1.22.0` 的 Python Session/I/O Binding 入口、核心 DLL、CUDA Provider DLL和许可证。
- CUDA Runtime：`cudart64_12.dll`。
- cuBLAS：`cublas64_12.dll`、`cublasLt64_12.dll`。
- cuFFT：`cufft64_11.dll`。
- 当前真实语音推理加载的 cuDNN 9 基础、图、算子、启发式和三个 engine DLL。

Bundle 不包含：

- Python 解释器、pip、setuptools 和 wheel；**这是 r1 的边界**，目标机器使用选定的 64 位 Python 3.11 创建 venv。v2 将 CPython 3.11、启动器和 Worker 放在包内，不能沿用此条目。
- CUDA 头文件、静态库和 import library。
- cuRAND、cuSPARSE、cuSOLVER、NVRTC、nvJitLink 和 NVTX。
- ORT TensorRT Provider、量化工具、transformer 工具和训练接口。

白名单唯一事实源是 `lib/core/voice_runtime_contract.py`。构建器、安装器和 Worker 禁止各自维护不同的 DLL 列表。

## 4. 构建与验证

从已经完成真实 CUDA 推理的官方 pip 构建环境生成。该构建环境不得放在客户端受管的 `voice/runtimes/onnx-cuda/` 下，因为启动清理器会删除非当前 Bundle：

```powershell
py -3 scripts/build_cuda_voice_runtime.py `
  --source-root <官方完整 CUDA 构建环境> `
  --voice-package C:\AemeathDeskPet\voice\ONNX_aimisiV2 `
  --output dist\aemeath-onnx-cuda-r1-ort1.22-cu12-cp311-win_amd64.zip
```

构建器同时生成 `.zip.release.json`，其中记录 archive 大小、SHA-256、payload 大小、Bundle ID、精确组件版本和逐文件哈希。发布前运行：

```powershell
py -3 scripts/verify_cuda_voice_runtime.py `
  --archive dist\aemeath-onnx-cuda-r1-ort1.22-cu12-cp311-win_amd64.zip `
  --voice-package C:\AemeathDeskPet\voice\ONNX_aimisiV2
```

验证脚本在受管临时目录创建全新 venv，从 ZIP 完成外层哈希、路径、大小和逐文件 SHA-256 校验，然后执行：

1. CUDA `Identity` Session 探测。
2. 当前语音包全部真实模型 Session 创建。
3. 中文真实合成。
4. 英文真实合成。
5. 通过正式 CUDA Worker 入口完成中英混合文本合成。
6. PCM16、单声道、32 kHz、非空且非静音 WAV 校验。

## 5. 下载与激活

安装依赖固定准备 CPU + DirectML，不下载 CUDA。用户进入 AI 设置时才检测 NVIDIA 显卡；存在 NVIDIA 且当前 Bundle 未通过校验时显示“安装N卡推理环境”，用户点击后客户端按 ModelScope、Hugging Face 顺序尝试固定文件：

```text
https://www.modelscope.cn/models/Mark42IRPC/GSV_onnx_Aemeath_Pack/resolve/master/<archive>
https://huggingface.co/Mark42IRP/Aemeath_onnx_GSV_model/resolve/main/<archive>
```

安装窗口展示加速效果、显存影响、下载大小、安装占用、临时空间和两个阶段进度，并提供开始与取消。下载前要求目标磁盘至少有 archive、解压 payload 和 512 MiB 余量，当前约 4.44 GiB；安装器先清理旧版本、旧 pip CUDA 环境与残留包体。逐文件校验完成后，在同盘直接移动 `Lib/site-packages` 到临时 venv，不再复制第二份 2.36 GiB payload。

安装顺序固定为：

```text
限定目录清理旧环境与残留包体
  -> 按需下载
  -> 校验客户端内置 ZIP SHA-256
  -> 安全解压并拒绝绝对路径、..、重复成员和链接
  -> 校验 bundle.json + SHA256SUMS.txt + 每个 payload 文件
  -> 临时 venv 组装并移除 pip/setuptools
  -> CUDA Session 探测
  -> 语音包存在时执行中英文真实推理
  -> 原子激活并写 runtime.json
```

下载、校验、模型加载或推理任一步失败都不得激活新运行时。Bundle 不可用时保持 DirectML/CPU 能力，不得回退全量 PyPI CUDA 环境。只有固定 Bundle 静态检查和实际 CUDA Session 探测都有效时，设置页才显示“N卡加速”开关。

桌宠正式启动时通过后台任务再次执行限定清理，只枚举 `<共享根>/voice/runtimes/onnx-cuda/` 的直接子项，保留当前有效 Bundle，不跟随符号链接或目录联接。

## 6. 上传清单

发布者必须把完全相同的 ZIP 上传到 ModelScope 与 Hugging Face，不能分别重压缩。上传完成后：

1. 分别从两个公开 resolve URL 下载完整文件。
2. 对比内置 SHA-256 与 `.zip.release.json`。
3. 对两个下载副本分别运行真实验证脚本。
4. 通过 `Content-Length` 或 `Range: bytes=0-0` 的 `Content-Range` 确认两个 URL 的大小均为 `1,700,089,579`。
5. 再发布包含对应 URL 和固定哈希的客户端版本。

当前 `r1` 已于 2026-09-01 上传到上述两个语音仓库。两个公开对象均已完整流式读取，字节数和 SHA-256 与本地完成 RTX 3050 中英文及混合推理验证的发布物一致；ModelScope 通过 `Content-Range` 返回总大小，Hugging Face 通过 `Content-Length` 返回总大小。

## 7. 第二阶段门槛

只有算子级自编译能在第一阶段基础上继续获得足够收益时才推进。评估必须覆盖完全包、中等包和节约包全部 ONNX 图，并至少保留：

- `com.microsoft::MatMulNBits` 等 ORT contrib 算子。
- `Loop`、Sequence、动态形状和动态解码状态。
- `STFT`、`DFT` 和 VITS/T2S 使用的声学算子。
- CUDA I/O Binding 和 `CPUExecutionProvider` fallback。

自编译产物使用新的 Bundle Release 和 SHA-256，不能覆盖第一阶段 `r1` 文件。
