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
   文件、已解压文件/字节数和预计剩余时间；校验到 100% 后会明确进入内置解压阶段，
   不调用外部子进程。解压本身由 `zip_extract.c` 的线程池完成：工作线程数取逻辑核
   数减 2（上限 8），线程优先级低于普通，多个核心分摊负载而不会吃满任意一个核心，
   界面与系统仍留有余量。两条进度条与语音包安装页同款：24px 高、1px 描边、3px 圆角、
   条内居中文字，由 `progress_subclass_proc` 自绘。上方青色是资源归档的下载/校验，
   离线包内置资源时直接显示“已完成”；下方粉色是解压与安装进度，显示百分比。

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
