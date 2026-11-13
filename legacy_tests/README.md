# 归档测试库（legacy）

这里的 238 个模块是 `tests/` 之前的全量单元测试，2026-11-13 整体停用并原样归档；
目录名从 `tests` 改为 `legacy_tests`，文件内容没有改动。

停用原因：整套 `unittest discover` 在同一进程里顺序创建大量 Qt 窗口，一次原生
`FAST_FAIL_FATAL_APP_EXIT`（`TypeError: () missing 1 required positional argument: 's'`
来自动画驱动里的信号反弹）就会让整个进程无摘要退出；把顺序、日志或 Python 包装改动一点，
崩溃点就跟着移动，说明它是跨模块的进程级状态问题而不是某个用例的断言问题。逐个修补代价
远高于重做一小套可隔离的测试。

## 这里的内容

- 所有模块保持原样，连同 `dx/`、`native/`（不再被自动发现）。
- 不进入 CI，不参与 `py -3 scripts/run_tests.py`。
- 只作为“某个行为以前怎么测的”参考，或按文件显式运行：

```powershell
py -3 -m unittest legacy_tests.test_release_common
```

（`legacy_tests/__init__.py` 把包路径指向本目录；文件内部的模块级 `import tests.xxx`
不会自动生效，需要时请复制用例到 `tests-ng/` 再改成新套件的导入路径。）

## 新套件

见 `tests-ng/` 与 `scripts/run_tests.py`。规则只有三条：

1. 不导入 Qt 的逻辑放 `test_pure_*`，只读源码的审计放 `test_static_*`。
2. 任何 `QApplication`/`QWidget` 都放 `test_qt_*`，并且每个测试类自己收拾窗口。
3. 不要写 `taskkill`、`tasklist` 或对整机 Python 进程做操作；需要进程断言时用
   `tests_ng.support.process`。
