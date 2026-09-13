from __future__ import annotations

import pytest

from adsl.core import RoundedPad


def _children(pad: RoundedPad):
    return list(pad.rounded_form._children.values())


def _sphere_centers(pad: RoundedPad):
    return [
        tuple(child._primitives[0]["params"]["center"])
        for child in _children(pad)
        if child.label == "Sphere"
    ]


def test_rounded_pad_radius_below_boundary_uses_four_unique_caps():
    pad = RoundedPad(0.88, 0.72, 0.23, radius=0.26)

    centers = _sphere_centers(pad)
    assert len(centers) == 4
    assert len(set(centers)) == 4


def test_rounded_pad_radius_near_boundary_uses_capsule_caps():
    width, depth = 0.88, 0.72
    limit = min(width, depth) * 0.5
    scale = max(width, depth, limit)
    radius = limit - 0.25e-6 * scale

    pad = RoundedPad(width, depth, 0.23, radius=radius)

    centers = _sphere_centers(pad)
    assert len(centers) == 2
    assert len(set(centers)) == 2


def test_rounded_pad_radius_above_boundary_raises():
    with pytest.raises(ValueError, match="exceeds boundary"):
        RoundedPad(0.88, 0.72, 0.23, radius=0.37)
