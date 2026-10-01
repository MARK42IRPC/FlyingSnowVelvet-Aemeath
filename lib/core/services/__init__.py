"""Backend-neutral product services shared by every rendering backend.

Design rules for this package:

* It is part of ``lib/core`` and must stay importable without PyQt.
* It must not import ``lib.script``; the dependency direction runs
  ``lib/script`` -> ``lib/core``, never the other way round.
* It owns resolution of product *numbers*, not product *pixels*. Colour,
  layout and typography belong to ``lib/core/render/visuals``.
"""
