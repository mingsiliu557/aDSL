# Extracted without geometry changes from elephant_000 B/source.py.
# Source SHA256: 8214009f19a423f017ff4eb716e0ede9338862d0550c7f47b710170b1dd78f9f
from adsl.core import *
import math
ELEPHANT_GRAY = (.5, .5, .5)

def ellipsoid(center, radii, color=ELEPHANT_GRAY):
    """Create a softly rounded ellipsoid from a public sphere primitive."""
    form = Sphere(1.0, center=center, color=color)
    return scale_shape(form, radii, center=center)

class CurvedTrunk(Asset):
    def __init__(self):
        super().__init__(label="CurvedTrunk")

        # The sampled centerline descends from the face, rounds through a broad
        # forward U, and finishes in an unmistakably upward-directed tip.
        stations = (
            ((-1.13, -0.01, 2.49), 0.305),
            ((-1.23, -0.01, 2.24), 0.285),
            ((-1.27, -0.01, 1.92), 0.255),
            ((-1.29, -0.01, 1.58), 0.225),
            ((-1.37, -0.01, 1.26), 0.195),
            ((-1.53, -0.01, 1.00), 0.170),
            ((-1.75, -0.01, 0.84), 0.145),
            ((-1.98, -0.01, 0.85), 0.125),
            ((-2.16, -0.01, 1.01), 0.108),
            ((-2.22, -0.01, 1.24), 0.100),
        )

        nodes = [Sphere(radius, center=point, color=ELEPHANT_GRAY) for point, radius in stations]

        # Use explicit tapered frustums between the rounded stations.  This
        # preserves the smooth taper while avoiding numerically degenerate
        # convex-hull facets on the nearly vertical first trunk span.
        segments = []
        radial_samples = 24
        for index in range(len(stations) - 1):
            p0, radius0 = stations[index]
            p1, radius1 = stations[index + 1]
            dx, dy, dz = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            circle = Polygon(
                [
                    (
                        radius0 * math.cos(2.0 * math.pi * sample / radial_samples),
                        radius0 * math.sin(2.0 * math.pi * sample / radial_samples),
                    )
                    for sample in range(radial_samples)
                ]
            )
            segment = linear_extrude(
                circle,
                length,
                scale_top=(radius1 / radius0, radius1 / radius0),
                color=ELEPHANT_GRAY,
            )
            angle = math.degrees(math.acos(max(-1.0, min(1.0, dz / length))))
            if angle > 1.0e-9:
                segment = rotate_shape(segment, axis=(-dy, dx, 0.0), angle=angle, center=(0.0, 0.0, 0.0))
            segment = translate_shape(segment, p0)
            segments.append(segment)
        trunk = boolean_union(*(segments + nodes))

        # A subtly enlarged rounded terminal gives the small raised-tip flare.
        tip = ellipsoid((-2.22, -0.01, 1.285), (0.115, 0.112, 0.145))
        trunk = boolean_union(trunk, tip)
        self.form = self.attach_part("form", trunk)

scene = CurvedTrunk()
