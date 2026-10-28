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
缓冲：LTS1.0.7pre4 起的应用内更新器（`install_resource_bundle`）已经同时读分片与 Deflate，
但更早的客户端只会把分片归档里的占位条目解成空文件。等所有在用客户端都升级到读得懂分片的
版本后，构建时加 `--resource-sharded` 即可让资源包改用同一套 LZMA2 分片布局。

在线版 EXE 不生成这份分片归档，也不绑定版本：它内嵌的只是一个几百字节的 bootstrap marker
（`.fsv-online-resource-required` + `.fsv-install-root`），安装时从两个固定发布槽读取
`updates/latest.json`，再按清单里的 `asset_path`、`size` 与 `sha256` 下载当前资源包。
因此同一份 `dist/FlyingSnowVelvet-Online-Installer.exe` 可跨版本复用；新增版本只需发布新的
`updates/FlyingSnowVelvet-<version>-Resources.zip` 并把 `latest.json` 指向它。

在线版的 `payload_info.h` 把 `FSV_PAYLOAD_ARCHIVE_BYTES`、`FSV_PAYLOAD_FILE_COUNT` 和
`FSV_PAYLOAD_UNCOMPRESSED_BYTES` 全部写成 `0`，`resource_urls.h` 也只含发布槽根地址，
不含版本或文件名。原生安装器用 `FSV_ONLINE_BUILD` 而不是这些常量判断构建模式：在线模式在
进入向导第二步时解析清单，用 `file_count` / `uncompressed_bytes` 做空间预估（旧清单可缺省，
下载后由 `fsv_zip_get_statistics()` 按真实目录复核）；下载完成后再校验长度与 SHA-256。
发布脚本 `scripts/publish_offline_installer.py --resource` 会写入这两个统计字段。

需要单独重建在线 bootstrap 时，不必准备 payload 工作区：

```powershell
python scripts/build_offline_installer.py --online
```

默认输出 `dist/FlyingSnowVelvet-Online-Installer.exe`；带 `--workspace` 时仍会顺带生成
该版本的资源包，供发布脚本使用。离线版继续按版本命名并内置完整归档。

原生安装器执行顺序：

1. 显示默认安装目录；自定义目录调用系统文件夹选择器。非空目录自动创建空的
   `飞行雪绒` 子目录或带序号目录。
2. 显示预计占用空间、文件数和磁盘余量。
3. 工作线程复制并校验内置归档，调用内置 ZIP/Deflate/LZMA2/Zip64 解压器，实时报告当前
   文件、百分比、已解压文件/字节数和 ETA。校验进度达到 100% 后仍会继续进入解压阶段，
   不启动外部自解压程序。
   在线版先下载并 SHA-256 校验 `latest.json` 指定的资源包，再走同一解压器；青色下载条
   从 0 开始，离线版因归档内置而直接显示“已完成”。
4. 在同一卷临时目录完成 marker、Python、启动器、卸载器校验后原子切换目录；失败时
   清理本次临时目录并保留旧安装。
5. 显示“安装完成”，只有用户点击“退出安装并启动飞行雪绒”才启动包内 launcher。

安装器必须能在**不干净的目录**上跑完，这是用户实测报过的 183
（`当文件已存在时，无法创建该文件。`）那一类问题：

- 目标文件用 `CREATE_ALWAYS` 打开，同名残留直接覆盖（旧行为是 `CREATE_NEW`，路径上只要
  有上一次中断留下的文件，整个安装就以 183/80 中断）。CRC 校验仍然拒收写不完整的文件。
- 残留形状冲突（该是目录的地方是文件、该是文件的地方是目录）由
  `remove_conflicting_entry(path, recursive)` 清理。`recursive` 只对「需要把该路径当文件用」
  的 `open_entry_output()` 开放；`ensure_directory()` 只清普通文件，因为分片 worker 是并行
  的，递归删除会拆掉兄弟 worker 刚写好的目录树。
- 建目录整段（读属性 → 清冲突 → `CreateDirectoryW` → 复验）带重试，只重试兄弟 worker
  能造成的竞争错误；这是唯一能让并行解压在不干净目录上稳定收敛的做法。
- 切换目录前，`has_install_marker()` 认定「带 `.fsv-install-root` 但 launcher/Python 缺失」
  的目录是**自己**的半成品安装，允许覆盖修复（否则它会像外来目录一样被
  `ERROR_DIR_NOT_EMPTY` 拒掉，用户再也装不上）；真正无标记的非空外来目录仍然拒绝。
- 开始解压前，`cleanup_orphan_staging_directories()` 清掉目标卷上
  `FSV-<pid>-<tid>-<tick>` 形状、持有者进程已死的暂存目录（每个几百兆），先腾出空间再装。
  只认这个精确形状，`FSV-` 开头的无关目录不动。

离线 payload 的 `app/` 放三个可执行文件：`启动飞行雪绒.exe`、
`卸载飞行雪绒.exe` 与 `FSVUpdater.exe`。安装包不再生成 `启动程序.bat`，也不提供 ASCII 别名：
安装完成、桌面快捷方式和开机启动都直接指向包内启动 exe，避免 cmd.exe 按系统代码页
解析 UTF-8 中文路径，也避免启动时闪现控制台窗口。

在线资源更新由独立 `FSVUpdater.exe` 完成：桌宠先把它和资源 ZIP 复制到
`%TEMP%\FlyingSnowVelvet\updates\<uuid>`，用 UAC `runas` 启动后退出。更新器等待父进程
退出，接管安装根内所有残留进程，校验 ZIP 的 SHA-256，解压到同卷暂存目录并校验安装
标记，再以 `target -> backup`、`staging -> target` 的两次重命名原子切换。旧
`app/resc/user` 先移入暂存目录，失败时先回滚它再删除暂存；成功后写回
`app/resc/user/update_state.json` 并自动启动新 `app\启动飞行雪绒.exe`。更新器清单要求
管理员权限，自身从临时目录运行并切换 CWD，避免锁住安装根。卸载器采用与安装器一致的工作台亮色界面（品牌头、
白卡片、鸿蒙内嵌字体），提供“删除语音包”和“删除用户数据”两个默认不勾选的复选框；
确认后由独立临时 helper 删除安装目录，并按选项清理 `C:\AemeathDeskPet` 下的语音包、
推理运行时、记忆/配置/Apikey/日志与桌面办公区。可选清理带前缀护栏，永不删除安装
目录及其祖先；helper 用独立临时副本避免删除自身时锁定。

## 构建状态指纹

工作区里的 `.fsv-distribution-state.json` 记录本次 payload 用到的输入指纹，供 `--resume`
判断能不能复用已有 payload。指纹对每个文件记录相对路径、大小与 mtime，内容只按预算抽样：
每个文件最多读 `FINGERPRINT_SAMPLE_CHUNK_BYTES`（64 KiB）、每棵输入树最多读
`FINGERPRINT_SAMPLE_BUDGET_BYTES`（50 MiB），候选按「大文件优先、同名按路径」排序，所以
DLL、模型与 wheel 一定进指纹，而十几 GB 的 site-packages / node_modules 不会被整个读一遍。
解释器自带的 `Lib/site-packages` 不进解释器指纹：`copy_python_runtime` 从不复制它，依赖覆盖层
已作为独立输入指纹。因此 `--resume` 的语义是「输入没有明显变化」（增删改文件必然改变指纹，
保持大小与 mtime 的内容改动只对采样到的文件可靠），不是「逐字节相同」。

## 打包期校验

发行包的校验不再逐文件算 SHA-256：`manifest.json` 与安装器内置归档的目录都只记路径与真实
大小，打包期改用两道检查回答「这份包能不能用」：

1. **打包前跑一次真实的桌宠启动与功能测试**（`scripts/verify_payload_runtime.py`）。它用
   payload 自带的 `runtime/python311/python.exe`、在原生 launcher 等价的环境里起两个进程：
   `--mode startup` 走完整的启动路径（补齐上次的待替换文件、预载可选推理运行时、挑桌面后端、
   构造 `ApplicationState`，等到 `APP_INIT_READY` 再有序退出），`--mode features` 在离屏 Qt 里
   构造工作台的全部页面、独立办公窗口、论坛窗口与星空粒子，并逐个探测服务、字体/序列帧/Vosk/
   Node 资源与用户存储可写。任何一项失败都会中止打包（`--skip-launch-check` 只留给调试）。
   它验证的是发行包本身：包内解释器、包内 wheel、包内 DLL 与资源树。
2. **连打两次包并比对哈希**。两次产物必须逐字节相同，一致才保留第一份，不一致直接报错
   （`--skip-reproducible-check` 只打一次）。为此打包自己写进 payload 的文件
   （`.fsv-install-root`、`启动飞行雪绒.exe`、`卸载飞行雪绒.exe`）按 `PACKAGED_FILE_MTIME`
   固定 mtime，C 编译带 `/Brepro`，归档条目与 PE 时间戳都不再跟随构建时刻。`/Brepro` 把
   输入内容哈希写进目标文件，所以它证明的是「同一份源连打两次字节相同」：改一行注释也会让
   发行物哈希变化（目标文件里那处内容哈希变了），换源重打不是同一份产物。

`--resume` 的完整性检查同样只做结构检查（marker 在、清单里的路径都能对上文件与其记录的大小），
不再逐文件重算内容哈希。

## 构建与审计

```powershell
python scripts/build_offline_distribution.py --help
python scripts/build_offline_installer.py --help
python scripts/verify_payload_runtime.py --workspace build/offline-release/workspace
python -m unittest tests.test_offline_distribution tests.test_offline_installer_archive tests.test_windows_zip_extract tests.test_update_installer -q
```

发布工作流 `.github/workflows/publish-pack.yml` 会在当前仓库 checkout 后创建隔离
构建环境、执行 `npm ci --omit=dev --ignore-scripts`、准备资源、构建并验证 EXE，最终
只上传 versioned installer 和 manifest；旧 ZIP/green 资产会被删除。
