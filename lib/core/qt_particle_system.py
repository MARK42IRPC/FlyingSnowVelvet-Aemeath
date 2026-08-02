"""Compatibility imports for the Qt particle overlay backend."""

from lib.core.qt_bridge.particle_system import (
    ParticleOverlay,
    _ParticleSpatialIndex,
    _merged_tile_rects,
    _particle_bounds,
    _prepare_particles_for_inplace_update,
    _region_for_tiles,
    _snapshot_particles_for_update,
    _tile_keys_for_bounds,
    _tile_keys_for_region,
    _update_particles_batch,
)

__all__ = [
    "ParticleOverlay",
    "_ParticleSpatialIndex",
    "_merged_tile_rects",
    "_particle_bounds",
    "_prepare_particles_for_inplace_update",
    "_region_for_tiles",
    "_snapshot_particles_for_update",
    "_tile_keys_for_bounds",
    "_tile_keys_for_region",
    "_update_particles_batch",
]
