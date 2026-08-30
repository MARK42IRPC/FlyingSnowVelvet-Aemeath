"""Dependency installer implementation exposed for tests and maintenance tools."""

import sys

from . import installer as _installer

# Preserve the historical ``import install_deps`` surface while the root
# install_deps.py remains the executable entry point.
sys.modules[__name__] = _installer
