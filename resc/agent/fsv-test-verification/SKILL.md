---
name: fsv-test-verification
description: Use when selecting tests, verifying a Flying Snow Velvet change, diagnosing a regression, or preparing an implementation handoff.
---

# Test And Verification

验证范围跟随改动风险和维护手册，不用“测试通过”替代实际命令和结果。

- 先运行目标模块的最小测试、静态引用检查或语法检查，再扩大到接口相关测试。
- 通用验收命令为：
  - `py -3 -m compileall -q config lib scripts install_deps.py`
  - `py -3 -m unittest discover -s tests -p "test_*.py" -q`
  - `py -3 -m unittest tests.test_offline_distribution tests.test_update_installer`
- 修改离线安装器、资源或发行清单时追加 `py -3 -m unittest tests.test_offline_distribution tests.test_windows_zip_extract tests.test_update_installer`。
- 修改办公侧车时优先运行 `tests.test_dsh_runtime_contract`、`tests.test_dsh_office_sidecar`、`tests.test_office_*` 和相关工作台/模式测试。
- 修改视觉描述或后端时遵守维护手册的 presenter、Qt 基准、DX 和 DPI 验证矩阵。
- 测试失败时记录第一个真实失败、根因、已尝试的修复和未执行项；不要把环境缺失、授权等待或无匹配搜索误报为代码失败。
- 交接要列出实际命令、通过/失败结果、工作树状态、接口变化、剩余风险和未验证的外部条件。

不要为了让测试通过而删除断言、放宽契约或跳过与改动直接相关的测试。
