# Windows 原生离线安装器

`src/main.c`、`src/zip_extract.c`、`src/launcher.c` 和 `src/uninstaller.c` 组成
发行版的原生入口。构建脚本 `scripts/build_offline_installer.py` 使用 VS2022
`cl.exe /MT` 编译，不依赖用户机器上的 Python、Node、PowerShell 或 `tar.exe`。

安装器采用 `C stub + appended payload`：PE 文件末尾附加 ZIP 和固定格式的
`24 字节 magic + 8 字节归档长度 + 32 字节 SHA-256` 尾记录。启动时先流式校验
尾记录和归档，再用内置 Deflate/Zip64 解压器展开到同卷临时目录。

界面采用工作台/公告面板风格的三步亮色向导，顶部显示品牌与步骤导航，中部承载当前操作内容，底部提供操作按钮；
所有产品文字使用资源内嵌的 `resc/FRONTS/HarmonyOS_Sans_SC_Bold.ttf` 鸿蒙字体。

1. 显示默认安装目录；点击“自定义安装目录”调用系统文件夹选择器。目标目录非空
   时自动创建 `飞行雪绒` 或带序号的空子目录，不覆盖用户文件。
2. 显示预计占用空间、归档文件数和目标磁盘可用空间。
3. 工作线程负责校验、解压和目录切换，主线程持续处理 Windows 消息。进度显示当前
   文件、已解压文件/字节数和预计剩余时间（按最近 2 秒完成的文件数取平均速率估算，
   每 2 秒刷新一次，单个大文件不会把估计值带得上下跳）；校验到 100% 后会明确进入内置解压阶段，
   不调用外部子进程。解压本身由 `zip_extract.c` 的线程池完成：工作线程数取逻辑核
   数减 2（上限 4），线程优先级最低，并每 250ms 采样整机与自身的 CPU 占用：其他
   进程已占用 65% 以上时向单线程收敛，空闲时也不超过逻辑核的一半，多个核心分摊
   负载而不会吃满任意一个核心，界面与系统仍留有余量。文件按未压缩大小预分配并
   声明顺序扫描，减少写入争抢。两条进度条与语音包安装页同款：24px 高、1px 描边、3px 圆角、
   条内居中文字，由 `progress_subclass_proc` 自绘。上方青色是资源归档的下载/校验，
   离线包内置资源时直接显示“已完成”；下方粉色是解压与安装进度，显示百分比。

解压必须能在**不干净的目录**上跑完（用户实测报过 `当文件已存在时，无法创建该文件。`，
183/80，卡在解压阶段装不下去）：

- 目标文件用 `CREATE_ALWAYS` 打开，同名残留直接覆盖；写不完整的文件仍由 CRC 校验拒收。
- 残留的形状冲突（该是目录的地方是文件、该是文件的地方是目录）由
  `remove_conflicting_entry()` 清理。它带一个 `recursive` 开关：只有「需要把该路径当文件用」
  的 `open_entry_output()` 才允许整棵删除；`ensure_directory()` 只清普通文件，因为分片 worker
  是并行的，递归删除会拆掉兄弟 worker 刚写好的目录树。
- 建目录整段（读属性 → 清冲突 → `CreateDirectoryW` → 复验）带重试，且只重试兄弟 worker
  能造成的竞争错误；这是让并行解压在不干净目录上稳定收敛的唯一做法。
- 切换目录前，`has_install_marker()` 把「带 `.fsv-install-root` 但 launcher/Python 缺失」的
  目录认定为自己的半成品安装，允许覆盖修复；否则它会像外来目录一样被 `ERROR_DIR_NOT_EMPTY`
  拒掉，用户再也装不上。真正无标记的非空外来目录仍然拒绝。
- 开始解压前，`cleanup_orphan_staging_directories()` 清掉目标卷上
  `FSV-<pid>-<tid>-<tick>` 形状、持有者进程已死的暂存目录（每个几百兆），先腾出空间。
  只认这个精确形状，`FSV-` 开头的无关目录不动。

安装成功后显示“安装完成”，用户点击“退出安装并启动飞行雪绒”才启动
`app\启动飞行雪绒.exe`。启动器设置绝对的包内 Python 3.11、Node 24.13.0 和 Qt
路径，并清理外部 `PYTHONPATH`、`PYTHONHOME`、`NODE_PATH`、Qt/OpenSSL 等覆盖。
`app\` 只包含 `启动飞行雪绒.exe` 与 `卸载飞行雪绒.exe` 两个可执行文件，不再生成
`启动程序.bat` 或 ASCII 别名；快捷方式、开机启动和应用内重启都直接复用启动 exe。

更新器从对应 ONNX 语音包的 Hugging Face / ModelScope 仓库下载外层 ZIP，解包后校验其中唯一的
离线安装器 EXE，并可通过 `--update-target` 预填现有安装目录；成功切换后将状态文件复制到
`app\resc\user\update_state.json`。

`app\卸载飞行雪绒.exe` 与安装器共用同一套工作台亮色令牌、品牌头和鸿蒙内嵌字体，
中部提供两个自绘复选框：“删除语音包”（`C:\AemeathDeskPet\voice`、`models\vosk`
与 `start_gsvmove.bat`）和“删除用户数据”（记忆、用户配置、Apikey、日志与桌面
“飞行雪绒办公区”）；两者默认不勾选，并带有小字说明。复选框使用 `BS_OWNERDRAW`：
原生 `BS_AUTOCHECKBOX` 会在用户点击时把自己的方块和虚线焦点框画到自绘控件上，
勾选状态由对话框自身保存并回应 `BM_SETCHECK`/`BM_GETCHECK`。安装器与卸载器的
按钮、复选框都用青色描边环表示键盘焦点，不再使用 Windows 原生虚线焦点框。
卸载器打开时就会主动关掉飞行雪绒，而不是等用户自己退出：桌宠、语音推理运行时与办公
侧车都跑在安装目录里，占着 `app\runtime` 下的 exe/dll 时删除会失败并留下残渣。命中
规则只看映像路径——进程的 exe 落在安装目录或共享契约目录 `C:\AemeathDeskPet` 里才处理，
系统里别处的 `python.exe` / `node.exe` / `ollama.exe` 一律不碰；先对所有顶层窗口发
`WM_CLOSE`，让桌宠走自己的退出流程并收掉子服务，宽限 2.5 秒后仍在的进程再
`TerminateProcess`，最多重复 4 轮。这一遍在后台线程里跑，不耽误窗口显示；点「卸载飞行
雪绒」后的清理线程在统计文件之前会再收一遍（界面对应显示“正在关闭飞行雪绒及其服务
组件...”，主体文案同步改成“桌宠及其服务组件已在后台关闭”），所以中途又被拉起来的
桌宠同样会先被关掉，删除阶段不会撞文件占用。

清理界面复用安装器的一青一粉进度条：先遍历统计待删除文件（青色条在总数未知时
来回扫动），再执行删除并显示“已删除 N / M 个文件 · X / Y”，条内居中显示百分比或
“已完成”。
确认后由独立临时 helper
（`--cleanup <root> <pid> [--delete-voice-package] [--delete-user-data]`）删除安装
目录和选中的 `C:\AemeathDeskPet` 契约目录，可选清理带安全护栏，永不删除安装目录
及其祖先。

构建与测试：

```powershell
python scripts/build_offline_distribution.py --help
python scripts/build_offline_installer.py --help
python -m unittest tests.test_windows_zip_extract tests.test_update_installer -q
python -m unittest tests.test_publish_offline_installer -q
```
