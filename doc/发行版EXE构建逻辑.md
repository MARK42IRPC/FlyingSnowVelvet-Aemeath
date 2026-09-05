# 发行版 EXE 构建逻辑

发行版与桌宠源码使用同一个 Git 工作区。权威入口为：

- `scripts/build_offline_distribution.py`：收集源码、资源、隔离 Python/Node 和依赖；
- `scripts/build_offline_installer.py`：编译原生安装器、启动器、卸载器并生成单 EXE；
- `installer/windows/src/`：原生安装/解压/启动/卸载实现。

构建过程在仓库内的 `build/offline-release` 临时目录执行，不读取或覆盖桌面上其它
发行版工作区。构建机需要 Windows、Python 3.11 64 位、VS2022 C 工具链，以及预先
准备的 CPU site-packages、Node 24.13.0、DSH production `node_modules`、Vosk
模型目录、`resc/GIF/SEanima/` 文件夹和固定 DirectML wheel。

## Payload 布局

```text
payload/
  app/                         源码、资源、DSH profile/bridge、SEanima 文件夹
    services/dsh-office-runtime/node_modules/
    resc/node-24.13.0-win-x64/
  runtime/python311/           Python 3.11 与精简标准库/site-packages
  runtime/onnx-directml/        独立 DirectML overlay
manifest.json
build/payload.zip               仅作为 EXE 内置中间物，不单独发布
dist/FlyingSnowVelvet-<version>-Offline-Installer.exe
```

基础 Python 依赖闭包包括 PyQt5、音频/桌面桥接、Vosk、CPU `onnx`/
`onnxruntime`、`genie-tts==2.0.2` 双语前端、`tokenizers`、`pypinyin`、`g2pM`、
`nltk`、`regex`、`jieba`/`jieba-fast`、`opencc`、`soundfile` 和 `soxr`。构建器按
实际发行版 metadata 解析依赖，并剪掉测试、文档、头文件、缓存和未使用 Qt/Node 子树。

基础包明确拒绝 Torch、CUDA、NVIDIA、TensorRT、`onnxruntime-gpu`；DirectML 只在
`runtime/onnx-directml/1.22.0-cp311-win_amd64` 中以独立 overlay 提供。启动器设置
绝对路径并清空 `PYTHONHOME`、`PYTHONPATH`、`NODE_PATH`、外部 Qt/OpenSSL 等环境变量，
因此不会受到用户本机 Python/Node 污染。

`resc/GIF/SEanima/` 必须以文件夹进入 payload，`SEanima.zip` 只可作为构建输入，
不得出现在最终 manifest。Vosk 中英文模型目录同样直接进入 payload。用户设置、密钥、
登录态、日志和缓存保存在 `C:\AemeathDeskPet`，不进入发行包。

## 单 EXE 协议

构建器先用 Python 标准库生成 Zip64/Deflate 归档，再把归档和 64 字节尾记录追加到
原生 PE：`24 字节 magic + 8 字节归档长度 + 32 字节 SHA-256`。更新器和安装器都在
解压前流式校验该尾记录，避免把截断或源码 ZIP 当作程序包。

原生安装器执行顺序：

1. 显示默认安装目录；自定义目录调用系统文件夹选择器。非空目录自动创建空的
   `飞行雪绒` 子目录或带序号目录。
2. 显示预计占用空间、文件数和磁盘余量。
3. 工作线程复制并校验内置归档，调用内置 ZIP/Deflate/Zip64 解压器，实时报告当前
   文件、百分比、已解压文件/字节数和 ETA。校验进度达到 100% 后仍会继续进入解压阶段，
   不启动外部自解压程序。
4. 在同一卷临时目录完成 marker、Python、启动器、卸载器校验后原子切换目录；失败时
   清理本次临时目录并保留旧安装。
5. 显示“安装完成”，只有用户点击“退出安装并启动飞行雪绒”才启动包内 launcher。

更新器把现有安装目录通过 `--update-target` 传给同一个 EXE；安装成功后写回
`app/resc/user/update_state.json`。卸载器删除安装目录和 `C:\AemeathDeskPet` 契约
目录，使用独立临时 helper 避免删除自身时锁定。

## 构建与审计

```powershell
python scripts/build_offline_distribution.py --help
python scripts/build_offline_installer.py --help
python -m unittest tests.test_offline_distribution tests.test_windows_zip_extract tests.test_update_installer -q
```

发布工作流 `.github/workflows/publish-pack.yml` 会在当前仓库 checkout 后创建隔离
构建环境、执行 `npm ci --omit=dev --ignore-scripts`、准备资源、构建并验证 EXE，最终
只上传 versioned installer 和 manifest；旧 ZIP/green 资产会被删除。
