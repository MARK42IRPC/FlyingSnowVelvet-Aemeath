# 飞行雪绒发行流程

正式发行物是一个包裹 Windows 离线安装器 EXE 的 ZIP。源码仓库、桌宠开发入口和发行版
构建入口保持在同一个 Git 工作区；不会从桌面上的其它发行版目录复制文件。

## 版本与发布槽

版本唯一来源是 `config/version_info.py` 的 `APP_VERSION`。推送 `PACK` 或 `v*`
标签后，`.github/workflows/publish-pack.yml` 在 Windows runner 中执行完整构建，
并将以下文件上传到 ONNX 语音包的两个模型仓库：

- `updates/FlyingSnowVelvet-<version>-Offline-Installer.zip`
- `updates/FlyingSnowVelvet-<version>-manifest.json`
- `updates/latest.json`

发布目标固定为 Hugging Face `Mark42IRP/Aemeath_onnx_GSV_model` 和 ModelScope
`Mark42IRPC/GSV_onnx_Aemeath_Pack`。两个仓库的 `updates/latest.json` 必须指向同一版本、
同一 revision 和同一 ZIP SHA-256；更新器只接受清单指定的单文件安装器 ZIP，不接受模型仓库的
源码归档或其它 ZIP。

## 本地构建

构建机需要 Windows、Python 3.11 64 位、Visual Studio 2022 C 工具链，以及网络
访问 PyPI、Node 下载源和 RESC 资源 release。所有构建输入都在临时目录中准备，
不修改本机 Python site-packages：

```powershell
$root = (Resolve-Path .).Path
$build = Join-Path $root 'build/offline-release'
$venv = Join-Path $build 'venv'
python -m venv $venv
$python = Join-Path $venv 'Scripts/python.exe'
& $python -m pip install --upgrade pip
```

在隔离 site-packages 中安装 `requirements.txt` 的 CPU 依赖；`genie-tts==2.0.2`
必须用 `--no-deps` 安装，并显式准备 `onnx`、CPU `onnxruntime`、`tokenizers`、
`pypinyin`、`g2pM`、`nltk`、`regex`、`jieba` 和 `jieba-fast`。不要安装 Torch、
CUDA、NVIDIA、TensorRT 或 `onnxruntime-gpu`。

准备好以下构建输入后执行：

```powershell
& $python scripts/build_offline_distribution.py `
  --source-root $root `
  --workspace (Join-Path $build 'workspace') `
  --python-home (python -c 'import sys; print(sys.prefix)') `
  --site-packages (Join-Path $build 'cpu-site-packages') `
  --dsh-node-runtime (Join-Path $build 'node-v24.13.0-win-x64') `
  --dsh-node-modules (Join-Path $root 'services/dsh-office-runtime/node_modules') `
  --directml-wheel (Join-Path $build 'directml/onnxruntime_directml-1.22.0-cp311-cp311-win_amd64.whl')

& $python scripts/build_offline_installer.py `
  --workspace (Join-Path $build 'workspace') `
  --version (Get-Content config/version_info.py | Select-String 'APP_VERSION' | ForEach-Object { $_.Line.Split('"')[1] })
```

`build_offline_distribution.py` 会把当前仓库源码、Vosk 中英文模型、
`resc/GIF/SEanima/` 文件夹、固定 Node 和 DSH production `node_modules` 收集到
payload；`build_offline_installer.py` 再编译原生安装器并追加 ZIP 与 SHA-256 尾记录。
最终 payload 不包含 `SEanima.zip`、构建脚本、测试目录、用户状态或开发缓存。

发布工作流需要配置仓库 secrets `HF_TOKEN` 与 `MODELSCOPE_TOKEN`，仅用于模型仓库上传；
本地可使用 `scripts/publish_offline_installer.py --token-file <令牌文件>`，令牌不会写入发布清单。

## 依赖边界

- 基础包：CPU `onnx`/`onnxruntime`、`genie-tts` 双语 ONNX 前端、`jieba`/
  `jieba-fast`、Vosk、PyQt5、音频和桌面桥接依赖。
- 可选 overlay：固定版本 `onnxruntime-directml`，位于
  `runtime/onnx-directml/1.22.0-cp311-win_amd64`，不与 CPU site-packages 混合。
- 不打包：Torch、CUDA DLL、NVIDIA、TensorRT、`onnxruntime-gpu` 及其依赖链。
- DSH：Node 24.13.0 与 `npm ci --omit=dev --ignore-scripts` 生成的 production
  `node_modules`，由原生启动器设置绝对路径；不会回退系统 Node。

## 安装器验收

在断网和带污染环境变量的临时目录中验收：

1. 第一步显示默认目录；选择非空目录时自动创建 `飞行雪绒` 空子目录。
2. 第二步显示目标空间、归档文件数和可用空间。
3. 第三步实时显示当前文件、百分比、已解压文件/字节数和 ETA；校验达到 100% 后
   必须继续进入原生解压阶段，不得卡在“正在启动自解压”。
4. 完成后显示“安装完成”，只有用户点击“退出安装并启动飞行雪绒”才启动程序。
5. 启动器只使用 payload 内 Python/Node，并清理 `PYTHONPATH`、`PYTHONHOME`、
   `NODE_PATH`、外部 Qt/OpenSSL 等环境覆盖。
6. `app/卸载飞行雪绒.exe` 删除安装目录及 `C:\AemeathDeskPet` 契约目录。

更新流程下载同一个离线安装器 EXE，校验尾记录后传入现有安装目录；桌宠退出后由
安装器完成目录切换并写回 `app/resc/user/update_state.json`。更新器不执行 ZIP 覆盖
安装 helper。

## 发布前命令

```powershell
& 'C:\Users\白草净华\AppData\Local\Programs\Python\Python311\python.exe' -m compileall -q config lib scripts install_deps.py install_deps
& 'C:\Users\白草净华\AppData\Local\Programs\Python\Python311\python.exe' -m unittest discover -s tests -p 'test_*.py' -q
git diff --check
```

发布前确认 `manifest.json` 中存在 `SEanima/`、Vosk、CPU ONNX、`genie_tts`、
`jieba` 和 DirectML overlay，并确认不存在 `SEanima.zip`、Torch、NVIDIA、CUDA
DLL、历史 green 资产或 ZIP 发布资产。
