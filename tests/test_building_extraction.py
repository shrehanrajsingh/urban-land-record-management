"""Tests for building and wall extraction from imagery."""

from __future__ import annotations

import numpy as np

from ml.building.extract import (
    _extract_building_mask,
    _extract_wall_edges,
    _mask_to_polygons,
    _pixel_to_world,
    _world_to_pixel,
)


def test_pixel_world_roundtrip():
    meta = {"origin_x": 500000.0, "origin_y": 3000200.0, "gsd": 0.25}
    x, y = _pixel_to_world(100, 200, meta)
    r, c = _world_to_pixel(x, y, meta)
    assert r == 100
    assert c == 200


def test_building_mask_detects_nongreen():
    """A bright red rectangle on a green background should be detected."""
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    # Green background
    img[:, :] = [30, 120, 30]
    # Red building in center
    img[30:60, 40:70] = [180, 60, 50]

    mask = _extract_building_mask(img)
    # The building region should have some detections
    building_pixels = mask[30:60, 40:70].sum()
    assert building_pixels > 50, f"Expected building detection, got {building_pixels} pixels"


def test_wall_edges_detect_dark_lines():
    """Dark thin lines should be detected as wall edges."""
    img = np.ones((100, 100, 3), dtype=np.uint8) * 150
    # Dark horizontal line
    img[50, 10:90] = [20, 20, 20]
    img[51, 10:90] = [20, 20, 20]

    mask = _extract_wall_edges(img)
    wall_pixels = mask[48:53, 10:90].sum()
    assert wall_pixels > 0, "Expected wall edge detection"


def test_mask_to_polygons_produces_valid_geometries():
    meta = {"origin_x": 0, "origin_y": 100, "gsd": 1.0}
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[20:40, 30:60] = 1
    polygons = _mask_to_polygons(mask, meta)
    assert len(polygons) >= 1
    for p in polygons:
        assert p.is_valid
        assert p.area > 0
