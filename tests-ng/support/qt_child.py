"""Child interpreter that runs exactly one tests-ng Qt class.

The parent (``scripts/run_tests.py``) invokes this with ``-k module.ClassName``.
Doing the load in a fresh interpreter is the whole point: Qt keeps animation
drivers, timers and native window state in process globals, so one leaked
window in class A cannot be observed while class B runs.
"""

from __future__ import annotations

import argparse
import os
import unittest


def _configure_headless_qt() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import PyQt5.QtCore
    except ImportError:
        return
    qt_root = os.path.dirname(PyQt5.QtCore.__file__)
    os.environ.setdefault(
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        os.path.join(qt_root, "Qt5", "plugins", "platforms"),
    )
    os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(qt_root, "Qt5", "plugins"))


def _load_class(name: str) -> unittest.TestSuite:
    module_name, class_name = name.rsplit(".", 1)
    module = __import__(module_name, fromlist=[class_name])
    return unittest.defaultTestLoader.loadTestsFromTestCase(
        getattr(module, class_name)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-k", "--class", dest="class_name", required=True)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _configure_headless_qt()
    result = unittest.TextTestRunner(
        verbosity=2 if args.verbose else 1
    ).run(_load_class(args.class_name))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
