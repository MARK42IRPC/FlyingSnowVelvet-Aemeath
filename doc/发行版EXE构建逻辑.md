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
`nltk`、`regex`、`jieba-fast`、`opencc`、`soundfile` 和 `soxr`。构建器按实际发行版
metadata 解析依赖，并剪掉测试、文档、头文件/C++ 源码/链接期文件、缓存和未使用
Qt/Node 子树。纯 Python `jieba`、`jieba_fast` 的关键词抽取与 SWIG 源码、只用 Jython
加载的 `*.p` 概率表、npm 包内的 Yarn 插件、仓库根目录的测试日志与开发者 `py.ini`
同样不进包。Qt 的软件 OpenGL 回退 `opengl32sw.dll` 也不进包：工作台不渲染 OpenGL
（没有 `QOpenGLWidget`、Qt Quick 或 Qt WebEngine，`opengl` 后端未实现），
`tests/test_qt_dependency_boundaries.py` 负责在有人引入 GL 用法时拦住。

基础包明确拒绝 Torch、CUDA、NVIDIA、TensorRT、`onnxruntime-gpu`；DirectML 只在
`runtime/onnx-directml/1.22.0-cp311-win_amd64` 中以独立 overlay 提供。启动器设置
绝对路径并清空 `PYTHONHOME`、`PYTHONPATH`、`NODE_PATH`、外部 Qt/OpenSSL 等环境变量，
因此不会受到用户本机 Python/Node 污染。

`resc/GIF/SEanima/` 必须以文件夹进入 payload，`SEanima.zip` 只可作为构建输入，
不得出现在最终 manifest。Vosk 中英文模型目录同样直接进入 payload。用户设置、密钥、
登录态、日志和缓存保存在 `C:\AemeathDeskPet`，不进入发行包。
仓库根目录的设计预览页（`.localpage/`，旧名 `local_pages/`）只在本机保留：
`.gitignore` 不跟踪它，构建器也不把它复制进 `app/`，旧工作区里的 `app/local_pages/`
会被安装器归档过滤器丢弃。

## 单 EXE 协议

构建器先用 Python 标准库生成 Zip64 归档，再把归档和 64 字节尾记录追加到原生 PE：
`24 字节 magic + 8 字节归档长度 + 32 字节 SHA-256`。更新器和安装器都在解压前流式校验
该尾记录，避免把截断或源码 ZIP 当作程序包。

内置归档仍是结构完整的普通 ZIP：每个 payload 文件都有一条带真实路径和真实大小的条目，
所以应用内更新器照旧遍历并校验全部路径，原生安装器也照旧从目录读出文件数与字节总数。
区别在于条目自身不再携带字节——文件内容按目录顺序拼成 4 路独立的 LZMA2 固态分片，
分片以普通 STORED 条目 `.fsv-shard-NNN.fsvlzma` 存放，`.fsv-shard-index.bin` 逐行记录
每个条目属于哪个分片、在解出流中的偏移与长度。原始 LZMA2 流不自带字典大小，所以
`installer/windows/src/zip_extract.h` 的 `FSV_ZIP_SHARD_DICT_PROPERTY` 必须与
`scripts/build_offline_installer.py` 的 `PAYLOAD_SHARD_DICT_PROPERTY` 一致（64 MiB = `0x1C`）；
打包时会断言，`tests/test_offline_installer_archive.py` 也会核对两处常量。

收益与代价：`payload.zip` 从 353,348,712 字节降到 257,549,096 字节，安装器从
354,045,608 字节降到 258,262,888 字节（−27.1%）。解压仍由建文件与落盘主导（实测
17,124 个文件的纯 `inflate` 只占 4.1 秒），每个分片边解 LZMA2 边写自己的文件，分片之间
并行，所以同一台机器上用同一份原生解压器实测总耗时从 56.1 秒降到 43.7 秒。分片解码器
沿用解压线程池的自适应负载门与最低线程优先级：并发分片数取逻辑核数减 2、上限 4，其他
进程占用超过 65% 时向单分片收敛，因此不会吃满任意一核。写入路径照旧按未压缩大小预分配
文件；解压期的额外内存主要是每个活跃分片的 64 MiB 解码字典。

在线资源包（`FlyingSnowVelvet-<version>-Resources.zip`）默认仍发布 Deflate，作为分片切换的
缓冲：LTS1.0.7pre4 的应用内更新器（`install_resource_bundle`）已经同时读分片与 Deflate，
但更早的客户端只会把分片归档里的占位条目解成空文件。等所有在用客户端都升级到读得懂分片的
版本后，构建时加 `--resource-sharded` 即可让资源包改用同一套 LZMA2 分片布局。

原生安装器执行顺序：

1. 显示默认安装目录；自定义目录调用系统文件夹选择器。非空目录自动创建空的
   `飞行雪绒` 子目录或带序号目录。
2. 显示预计占用空间、文件数和磁盘余量。
3. 工作线程复制并校验内置归档，调用内置 ZIP/Deflate/LZMA2/Zip64 解压器，实时报告当前
   文件、百分比、已解压文件/字节数和 ETA。校验进度达到 100% 后仍会继续进入解压阶段，
   不启动外部自解压程序。
4. 在同一卷临时目录完成 marker、Python、启动器、卸载器校验后原子切换目录；失败时
   清理本次临时目录并保留旧安装。
5. 显示“安装完成”，只有用户点击“退出安装并启动飞行雪绒”才启动包内 launcher。

离线 payload 的 `app/` 只放两个可执行文件：`启动飞行雪绒.exe` 与
`卸载飞行雪绒.exe`。安装包不再生成 `启动程序.bat`，也不提供 ASCII 别名：
安装完成、桌面快捷方式和开机启动都直接指向包内启动 exe，避免 cmd.exe 按系统代码页
解析 UTF-8 中文路径，也避免启动时闪现控制台窗口。

更新器把现有安装目录通过 `--update-target` 传给同一个 EXE；安装成功后写回
`app/resc/user/update_state.json`。卸载器采用与安装器一致的工作台亮色界面（品牌头、
白卡片、鸿蒙内嵌字体），提供“删除语音包”和“删除用户数据”两个默认不勾选的复选框；
确认后由独立临时 helper 删除安装目录，并按选项清理 `C:\AemeathDeskPet` 下的语音包、
推理运行时、记忆/配置/Apikey/日志与桌面办公区。可选清理带前缀护栏，永不删除安装
目录及其祖先；helper 用独立临时副本避免删除自身时锁定。

## 构建与审计

```powershell
python scripts/build_offline_distribution.py --help
python scripts/build_offline_installer.py --help
python -m unittest tests.test_offline_distribution tests.test_offline_installer_archive tests.test_windows_zip_extract tests.test_update_installer -q
```

发布工作流 `.github/workflows/publish-pack.yml` 会在当前仓库 checkout 后创建隔离
构建环境、执行 `npm ci --omit=dev --ignore-scripts`、准备资源、构建并验证 EXE，最终
只上传 versioned installer 和 manifest；旧 ZIP/green 资产会被删除。
