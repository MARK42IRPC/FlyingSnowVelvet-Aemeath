"""The archived suite that used to live in ``tests/``.

Files are byte-for-byte the old ``tests/`` modules; only the package name
changed, so deliberate runs look like
``python -m unittest legacy_tests.test_release_common``.  This package is not
discovered by ``scripts/run_tests.py`` and is not part of CI.
"""
