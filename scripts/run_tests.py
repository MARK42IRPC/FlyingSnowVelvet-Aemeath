"""Run the ``tests-ng`` suite in one fixed, isolated order.

The layers are split by what a test needs to trust:

* ``pure``: imports no Qt and never opens a window; runs in this process.
* ``static``: audits source trees and boundaries; runs in this process.
* ``qt``: every test class runs in its own child interpreter.  A crash in the
  Qt animation driver fails one class with an exit code instead of killing the
  whole run, and no window can outlive the class that opened it.

Usage:

    py -3 scripts/run_tests.py                    # all three layers
    py -3 scripts/run_tests.py --layer qt -v      # one layer, per class
    py -3 -m unittest tests_ng.test_pure_contracts
"""

from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT_DIR / "tests-ng"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

LAYERS = ("pure", "static", "qt")


class _PlaceholderTest(unittest.TestCase):
    """Result entry for a class that could not report its own failure."""

    def __init__(self, test_id: str) -> None:
        super().__init__()
        self._test_id = test_id

    def id(self) -> str:
        return self._test_id

    def __str__(self) -> str:
        return self._test_id


def _discover(layer: str) -> unittest.TestSuite:
    """Load ``tests_ng.test_<layer>_*`` modules directly.

    ``discover(top_level_dir=ROOT_DIR)`` would turn the dash in ``tests-ng``
    into an unimportable package name for child processes, so the parent
    imports the loader package (``tests_ng``) and walks its modules by name.
    """
    tests_package = importlib.import_module("tests_ng")
    suite = unittest.TestSuite()
    for path in sorted(Path(tests_package.__path__[0]).glob(f"test_{layer}_*.py")):
        module = importlib.import_module(f"tests_ng.{path.stem}")
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    return suite


def _flatten(suite) -> list[unittest.TestCase]:
    tests: list[unittest.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            tests.extend(_flatten(item))
        else:
            tests.append(item)
    return tests


def _run_suite(suite, verbosity: int, failfast: bool):
    runner = unittest.TextTestRunner(
        stream=sys.stderr, verbosity=verbosity, failfast=failfast
    )
    return runner.run(suite)


def run_in_process(layer: str, verbosity: int, failfast: bool):
    return _run_suite(_discover(layer), verbosity, failfast)


def _child_env() -> dict[str, str]:
    """Give a Qt child the same isolation the app uses on headless machines."""
    import PyQt5.QtCore

    qt_root = os.path.dirname(PyQt5.QtCore.__file__)
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault(
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        os.path.join(qt_root, "Qt5", "plugins", "platforms"),
    )
    env.setdefault("QT_PLUGIN_PATH", os.path.join(qt_root, "Qt5", "plugins"))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONFAULTHANDLER"] = "1"
    return env


def run_qt_class(cls: str) -> tuple[int, float, str]:
    """Run one test class in a fresh interpreter and return its exit code."""
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "tests_ng.support.qt_child", "-k", cls],
        cwd=str(ROOT_DIR),
        env=_child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.perf_counter() - started
    output = "\n".join(
        line
        for line in (completed.stdout + completed.stderr).splitlines()
        if line.strip()
    )
    return completed.returncode, elapsed, output


def run_qt_layer(verbosity: int, failfast: bool) -> unittest.TestResult:
    tests = _flatten(_discover("qt"))
    classes: list[str] = []
    for test in tests:
        cls = type(test).__qualname__
        module = type(test).__module__
        name = f"{module}.{cls}"
        if name not in classes:
            classes.append(name)

    result = unittest.TestResult()
    result.testsRun = len(tests)
    started = time.perf_counter()
    for cls in classes:
        if verbosity >= 2:
            print(f"  [qt] {cls} ... ", end="", flush=True)
        code, elapsed, output = run_qt_class(cls)
        if code == 0:
            if verbosity >= 2:
                print(f"ok ({elapsed:.2f}s)")
            continue
        detail = output or f"child interpreter exited with {code}"
        result.errors.append(
            (_PlaceholderTest(cls), (RuntimeError, RuntimeError(detail), None))
        )
        print(f"  [qt] {cls} FAILED (exit {code}, {elapsed:.2f}s)", file=sys.stderr)
        print(detail, file=sys.stderr)
        if failfast:
            break
    print(
        f"  qt: {len(classes)} classes, {result.testsRun} tests in "
        f"{time.perf_counter() - started:.1f}s",
        file=sys.stderr,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run tests-ng layer by layer.")
    parser.add_argument(
        "--layer",
        choices=LAYERS,
        action="append",
        help="run only this layer (repeatable)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "-x", "--failfast", action="store_true", help="stop after the first failure"
    )
    args = parser.parse_args(argv)
    layers = tuple(args.layer) if args.layer else LAYERS
    verbosity = 2 if args.verbose else 1
    failed = False
    for layer in layers:
        print(f"== {layer} ==", file=sys.stderr)
        if layer == "qt":
            result = run_qt_layer(verbosity, args.failfast)
        else:
            result = run_in_process(layer, verbosity, args.failfast)
        print(
            f"  {layer}: ran {result.testsRun}, "
            f"{len(result.failures)} failed, {len(result.errors)} errors",
            file=sys.stderr,
        )
        failed = failed or not result.wasSuccessful()
        if failed and args.failfast:
            break
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
