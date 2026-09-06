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
   文件、百分比、已解压文件/字节数和预计剩余时间；校验到 100% 后会明确进入内置
   解压阶段，不调用外部子进程。

安装成功后显示“安装完成”，用户点击“退出安装并启动飞行雪绒”才启动
`app\启动飞行雪绒.exe`。启动器设置绝对的包内 Python 3.11、Node 24.13.0 和 Qt
路径，并清理外部 `PYTHONPATH`、`PYTHONHOME`、`NODE_PATH`、Qt/OpenSSL 等覆盖。
同时提供 `app\FlyingSnowVelvetLauncher.exe` ASCII 别名；生成的
`启动程序.bat` 只调用该别名并使用 ASCII 编码，避免 cmd.exe 在非 UTF-8 代码页下误读
UTF-8 BOM 或中文文件名。

更新器从对应 ONNX 语音包的 Hugging Face / ModelScope 仓库下载外层 ZIP，解包后校验其中唯一的
离线安装器 EXE，并可通过 `--update-target` 预填现有安装目录；成功切换后将状态文件复制到
`app\resc\user\update_state.json`。`app\卸载飞行雪绒.exe`
使用独立清理 helper 删除安装目录和 `C:\AemeathDeskPet` 契约目录。

构建与测试：

```powershell
python scripts/build_offline_distribution.py --help
python scripts/build_offline_installer.py --help
python -m unittest tests.test_windows_zip_extract tests.test_update_installer -q
python -m unittest tests.test_publish_offline_installer -q
```
