# Release Playbook

适用于 `LTS1.0.5pre3` 及后续 `pre` 系列发布。目标是：**版本号一致、文档一致、发布包不夹带运行时垃圾文件**。

## 1. 更新版本号

至少同步以下位置：

- `config/version_info.py`
- `README.md`
- `CHANGELOG.md`
- `AA更新日志.txt`

资料舱脚本和两个打包脚本都会直接读取 `config/version_info.py`，不再单独维护默认版本号。

## 2. 发布前检查

### 文档与门户

```powershell
python scripts/generate_doc_portal.py
```

检查：

- `AA使用必读.html` 能正常生成
- 门户中的版本号与当前版本一致
- 文档卡片、开发贡献、赞助名单都能正确显示

### 静态检查

```powershell
python -m compileall config lib scripts install_deps.py
python scripts/package_release.py --dry-run
python scripts/package_green_release.py --dry-run
```

### 运行冒烟

至少确认一次：

- 桌宠能正常启动和退出
- 控制面板能正常打开与保存
- AI 回复可用
- GSV / STT（若启用）不会阻塞退出
- 音乐搜索与播放不回归

## 3. 生成发布包

普通发布包：

```powershell
python scripts/package_release.py --version LTS1.0.5pre3
```

输出示例：

- `dist/FlyingSnowVelvet-LTS1.0.5pre3.zip`
- `dist/FlyingSnowVelvet-LTS1.0.5pre3-manifest.json`

绿色资源包：

```powershell
python scripts/package_green_release.py --version LTS1.0.5pre3
```

输出示例：

- `dist/FlyingSnowVelvet-LTS1.0.5pre3-green.zip`
- `dist/FlyingSnowVelvet-LTS1.0.5pre3-green-manifest.json`

## 4. 发布包内容要求

必须排除：

- `logs/`
- `resc/user/`
- `dist/`
- `__pycache__/`
- `.git/`
- `.github/`
- 本地临时文件、调试文件、共享待同步缓存

发布前建议再检查一次：

```powershell
git status --short
git status --ignored --short
```

确保没有把运行时状态或本地测试文件混入发布提交。

## 5. Git 标签与 Release

示例：

```powershell
git tag -a LTS1.0.5pre3 -m "LTS 1.0.5 pre3"
git push origin LTS1.0.5pre3
```

Release 建议上传：

- `dist/FlyingSnowVelvet-LTS1.0.5pre3.zip`
- `dist/FlyingSnowVelvet-LTS1.0.5pre3-green.zip`（如需要）
- `AA使用必读.html`

Release Notes 直接整理自 `CHANGELOG.md` 当前版本段落即可。

## 6. 发布后

- 推送分支与标签
- 检查 CI 结果
- 如本次改动影响运行目录、配置迁移或缓存结构，记得在群内或说明文档中补充迁移提示

发版本质上是“把当前可运行状态冻结成一个可复现快照”，不是把开发机的运行痕迹一起打进去。
