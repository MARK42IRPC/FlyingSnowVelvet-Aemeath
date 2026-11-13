"""Loader package for ``tests-ng``.

The implementation lives in ``tests-ng/``; this file only points the package
path there so ``python -m tests_ng...`` and unittest discovery share one name.
"""

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parent.parent / "tests-ng")]
