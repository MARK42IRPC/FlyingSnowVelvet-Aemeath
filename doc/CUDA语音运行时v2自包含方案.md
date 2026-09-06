# CUDA 语音运行时 v2 自包含方案

更新时间：2026-09-06

本文是 v2 运行时的当前契约。权威实现为
`lib/core/cuda_runtime_bundle_v2.py`，构建入口为
`scripts/build_cuda_runtime_v2.py`，离线验收入口为
`scripts/verify_cuda_runtime_v2.py`。v2 与现有的 r1 CUDA 包并行存在；没有完成
真实模型回归和发布清单更新前，不替换 r1 的客户端入口。

## 目标和边界

v2 是一个可以直接解压并启动的 Windows x64 运行时包。包内包含完整的 CPython
3.11、ONNX Runtime CUDA Python 表面、CUDA/cuDNN 运行库、Worker 和启动器。运行
时不创建 venv，不调用 pip，不读取系统 Python，不要求安装 CUDA Toolkit 或 cuDNN，
也不从网络自修复。

唯一允许的主机前提是兼容版本的 NVIDIA 显示驱动。驱动负责内核模式 GPU 接口，
不是可随包重新分发的用户态 CUDA 组件。没有 NVIDIA 驱动、驱动版本低于清单门槛或
GPU 不支持所选架构时，运行时应报告不可用并回退到 DirectML/CPU；不得偷偷寻找
系统上的其它 Python、CUDA 或 ONNX Runtime。

| 边界 | v2 行为 |
| --- | --- |
| Python | 包内 `python/python.exe` 与 `python/python311.dll`，ABI 固定 `cp311-win_amd64` |
| ORT | 包内 `onnxruntime-gpu`，Provider 固定 `CUDAExecutionProvider` |
| CUDA/cuDNN | 包内 `runtime/cuda/bin/`，只发布 Manifest 白名单 DLL |
| Worker | 包内 `worker/cuda_worker.py`，stdio 协议 `stdio-v1` |
| 启动 | `worker/launch_cuda_worker.cmd` 或包内 Python 启动器，显式设置隔离环境 |
| 外部依赖 | 只有 NVIDIA 显示驱动；不需要网络、Toolkit、cuDNN、pip 或系统 Python |

## 归档布局

归档顶层只放元数据和 `payload/`。`payload` 是安装后的运行树，不把开发机的
venv 元数据或绝对路径带入包：

```text
manifest.json
SHA256SUMS.txt
payload/
  python/
    python.exe
    python311.dll
    Lib/...
  runtime/
    Lib/site-packages/onnxruntime/...
    Lib/site-packages/onnxruntime/capi/onnxruntime.dll
    Lib/site-packages/onnxruntime/capi/onnxruntime_providers_cuda.dll
    cuda/bin/<CUDA 与 cuDNN 白名单 DLL>
  worker/
    cuda_worker.py
    launch_cuda_worker.cmd
    launch_cuda_worker.py
```

`manifest.json` 的 `files` 数组为每个 payload 文件记录相对路径、角色、字节数和
SHA-256。`integrity` 记录算法、文件数和总字节数；`SHA256SUMS.txt` 必须与数组逐项
相同。路径只允许正斜杠相对路径，禁止 `..`、绝对路径、重复成员和符号链接。

Manifest 的关键字段如下：

```json
{
  "format": "fsv-cuda-runtime",
  "format_version": 2,
  "python": {"abi": "cp311-win_amd64", "executable": "python/python.exe"},
  "onnxruntime": {
    "package": "onnxruntime-gpu",
    "provider": "CUDAExecutionProvider"
  },
  "cuda": {
    "major": 12,
    "cudnn_major": 9,
    "dll_directory": "runtime/cuda/bin",
    "required_dlls": [
      "cublasLt64_12.dll", "cublas64_12.dll", "cufft64_11.dll", "cudart64_12.dll",
      "cudnn_engines_precompiled64_9.dll", "cudnn_adv64_9.dll", "cudnn_ops64_9.dll",
      "cudnn_heuristic64_9.dll", "cudnn_graph64_9.dll",
      "cudnn_engines_runtime_compiled64_9.dll", "cudnn_engines_tensor_ir64_9.dll",
      "cudnn64_9.dll"
    ]
  },
  "worker": {
    "protocol": "stdio-v1",
    "entry": "worker/cuda_worker.py",
    "launcher": "worker/launch_cuda_worker.cmd"
  },
  "external_prerequisites": [{
    "kind": "nvidia_display_driver",
    "minimum_version": "531.61",
    "network_required": false
  }]
}
```

清单中的 `bundle_id` 由排序后的路径、大小和哈希计算；任何 payload 改动都会产
生新的 bundle ID。发布目录旁的 `<archive>.release.json` 再记录归档字节数和归档
SHA-256，客户端只接受预置的完整哈希，不接受仅按文件名判断的包。

## 启动隔离

启动器永远把包内目录放到搜索路径首位：

```text
PYTHONHOME = <bundle>/python
PYTHONPATH = <bundle>/runtime/Lib/site-packages;<bundle>/worker
PATH       = <bundle>/python;<bundle>/python/Scripts;
             <bundle>/runtime/cuda/bin
PYTHONNOUSERSITE = 1
```

随后使用 `-I` 启动 `<bundle>/worker/launch_cuda_worker.py`，由它加载
`cuda_worker.py`。启动器不使用 `python`、`py`、`where` 或虚拟环境激活脚本，因此
系统 PATH 中即使没有 Python 也不会改变行为。`worker_launch_command()` 可供应用
编排器取得同一命令和环境，避免 UI 或业务模块重新拼接路径。

Python 启动器会在设置包内路径前再次清除继承的 `PYTHON*`、`PYENV*`、`CONDA*`、
`VIRTUAL_ENV*`、`CUDA_PATH*` 和动态库搜索变量；`PATH` 只保留包内 Python、Scripts
和 CUDA bin。这样即使调用方来自 Conda 或 venv，Worker 的解释器、模块和用户态
CUDA DLL 仍由同一个已校验的 payload 提供。

## 构建

构建机先准备一个已经通过真实 CUDA 模型回归的目录，目录只需要有上述
`python/`、`runtime/`、`worker/` 三棵树。构建器只读取这些文件，不联网、不运行
包管理器、不编译：

```powershell
py -3 scripts/build_cuda_runtime_v2.py `
  --source-root C:\build\fsv-cuda-v2-payload `
  --output dist\aemeath-cuda-v2-r2-cp311-win_amd64.zip
```

如果 Worker 启动器已由发布流水线生成，可加 `--no-generate-launchers`；否则构建器
会写入固定模板。`--list-only` 只计算并打印文件清单，适合在不生成归档时审查包
边界。构建完成后必须同时保存 ZIP 和 `.release.json`，两者都进入发布审核。

## 离线验收

验收脚本只读本地 ZIP，使用标准库完成归档哈希、路径检查、解压、Manifest 校验、
逐文件哈希和启动命令检查，不发起网络请求：

```powershell
py -3 scripts/verify_cuda_runtime_v2.py `
  --archive dist\aemeath-cuda-v2-r2-cp311-win_amd64.zip `
  --expected-sha256 <release SHA-256> `
  --driver-version 551.23 `
  --report dist\aemeath-cuda-v2-r2.verify.json
```

`--driver-version` 是现场已读取的驱动版本；省略时仍会验证“唯一外部前提”的清单
结构，但报告中驱动状态为未知。应用实际启动前应使用本机驱动探测结果调用
`verify_driver_requirement()`；版本不足必须阻止激活。驱动探测不得下载驱动或调用
包管理器。

验收失败的 ZIP 不得进入激活目录。测试中对归档成员增加 `../`、重复文件、符号
链接、未列出的文件和单字节篡改都必须得到失败结果。

## 安装与激活边界

安装器把 ZIP 解压到同盘的隐藏 staging 目录，然后按以下顺序操作：

1. 校验发布 SHA-256、归档路径和大小。
2. 安全解压并执行 `validate_payload_tree()`，确认 Manifest、清单和每个文件一致。
3. 检查本机 NVIDIA 驱动门槛；不满足时删除 staging 并保留旧运行时。
4. 在 staging 中执行 Worker/ORT 的最小 CUDA 探测和语音模型回归。
5. 关闭旧 Worker 后，将已验证 staging 目录原子改名为版本目录。
6. 最后写入 `runtime.json`，其中包含 `bundle_id`、归档 SHA-256、ABI、Provider 和
   `source: "bundled-v2"`。标记文件写入失败时不报告成功。

启动阶段只读取带有匹配 Manifest 和完整标记的版本目录；不扫描用户其它目录，
不把系统 pip 安装的 `onnxruntime-gpu` 当作 v2。更新失败不会破坏当前有效版本，
旧版本的清理延后到下一次启动并再次通过就绪检查之后。

## 与 r1 的关系

r1 仍是当前已有的精简 CUDA 包，使用目标机 Python venv。v2 改变了运行时边界，
因此必须使用新的格式版本、归档名、bundle ID 和客户端就绪标记，不能复用 r1 的
SHA-256 或把 r1 ZIP 当作 v2。迁移门槛包括：

- 三档语音模型的中文、英文和混合文本真实 CUDA 推理；
- 在没有 Python、CUDA Toolkit、cuDNN 和 pip 的干净 Windows 主机上启动 Worker；
- 最低驱动及至少一个较新的驱动分支各完成一次回归；
- 两个发布镜像下载副本的归档哈希、大小和离线验收报告完全一致；
- 许可证和 NVIDIA/ORT 第三方声明随包保留。

在这些证据齐全前，客户端可以继续选择 r1 或 DirectML/CPU；不得在检测不到 v2
时联网安装一个未清单化的外部 CUDA 环境。

## 当前实现和测试

`lib/core/cuda_runtime_bundle_v2.py` 提供 `build_bundle()`、
`validate_manifest()`、`validate_payload_tree()`、`verify_archive_offline()` 和
`worker_launch_command()`。对应测试位于
`tests/test_cuda_runtime_bundle_v2.py`，覆盖确定性归档、驱动门槛、启动环境隔离、
篡改检测和 ZIP 路径安全。完整发布前仍需按维护手册执行全仓测试，并在真实 NVIDIA
硬件上完成上面的模型回归；这些硬件验证不由标准库离线验收替代。
