from adsl.core import *
import math


ELEPHANT_GRAY = (0.48, 0.50, 0.50)
EYE_DARK = (0.055, 0.06, 0.06)


def ellipsoid(center, radii, color=ELEPHANT_GRAY):
    """Create a softly rounded ellipsoid from a public sphere primitive."""
    form = Sphere(1.0, center=center, color=color)
    return scale_shape(form, radii, center=center)


def cubic_bezier(p0, p1, p2, p3, samples):
    points = []
    for index in range(samples):
        t = index / float(samples)
        u = 1.0 - t
        points.append(
            (
                u * u * u * p0[0]
                + 3.0 * u * u * t * p1[0]
                + 3.0 * u * t * t * p2[0]
                + t * t * t * p3[0],
                u * u * u * p0[1]
                + 3.0 * u * u * t * p1[1]
                + 3.0 * u * t * t * p2[1]
                + t * t * t * p3[1],
            )
        )
    return points


class FanEar(Asset):
    def __init__(self, side_y, label):
        super().__init__(label=label)

        # Smooth asymmetric fan outline in the world x-z side plane.  The rear
        # (+x) half is broad while the lower-front edge folds gently inward.
        outline = []
        outline += cubic_bezier(
            (-0.82, 3.18), (-0.62, 3.48), (-0.12, 3.52), (0.24, 3.34), 8
        )
        outline += cubic_bezier(
            (0.24, 3.34), (0.62, 3.15), (0.72, 2.76), (0.55, 2.43), 8
        )
        outline += cubic_bezier(
            (0.55, 2.43), (0.34, 2.12), (-0.06, 2.10), (-0.34, 2.32), 8
        )
        outline += cubic_bezier(
            (-0.34, 2.32), (-0.50, 2.47), (-0.39, 2.66), (-0.62, 2.78), 6
        )
        outline += cubic_bezier(
            (-0.62, 2.78), (-0.78, 2.89), (-0.88, 3.02), (-0.82, 3.18), 6
        )

        ear_profile = Polygon(outline)
        ear = linear_extrude(
            ear_profile,
            0.10,
            center=True,
            color=ELEPHANT_GRAY,
        )
        # Profile y becomes world z; centered extrusion thickness becomes y.
        ear = rotate_shape(ear, axis="+x", angle=90.0, center=(0.0, 0.0, 0.0))
        ear = translate_shape(ear, (0.0, side_y, 0.0))
        self.panel = self.attach_part("panel", ear)


class OrganicLeg(Asset):
    def __init__(self, center_x, center_y, label, upper_fullness=1.0):
        super().__init__(label=label)

        # Short discs define a flared foot, narrow ankle and fuller upper leg.
        # Local pairwise hulls retain the ankle indentation that a global hull
        # would erase, while producing continuous gently tapered segments.
        sections = (
            (0.06, 0.255, 0.225),
            (0.20, 0.270, 0.235),
            (0.43, 0.175, 0.165),
            (0.88, 0.190, 0.175),
            (1.25, 0.215 * upper_fullness, 0.195 * upper_fullness),
            (1.50, 0.245 * upper_fullness, 0.215 * upper_fullness),
        )

        discs = []
        for z, radius_x, radius_y in sections:
            disc = Cylinder(
                1.0,
                height=0.12,
                center=(center_x, center_y, z),
                axis="z",
                color=ELEPHANT_GRAY,
            )
            disc = scale_shape(
                disc,
                (radius_x, radius_y, 1.0),
                center=(center_x, center_y, z),
            )
            discs.append(disc)

        segments = []
        for index in range(len(discs) - 1):
            segments.append(
                hull(discs[index], discs[index + 1], color=ELEPHANT_GRAY)
            )
        leg = boolean_union(*segments)
        self.form = self.attach_part("form", leg)


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


class StylizedElephant(Asset):
    def __init__(self):
        super().__init__(label="StylizedUpwardTrunkElephant")

        # Barrel torso: broad shoulders, gently arched back, rounded rump.
        shoulder = ellipsoid((-0.05, 0.0, 2.13), (0.76, 0.58, 0.76))
        middle = ellipsoid((0.65, 0.0, 2.10), (0.92, 0.60, 0.73))
        rump = ellipsoid((1.36, 0.0, 2.05), (0.72, 0.57, 0.68))
        lower_fill = ellipsoid((0.66, 0.0, 1.88), (0.86, 0.55, 0.47))
        body = hull(shoulder, middle, rump, lower_fill, color=ELEPHANT_GRAY)
        self.body = self.attach_part("body", body)

        # Tall head with a convex crown and a narrower cheek, overlapping the
        # shoulder deeply enough to avoid a separate neck.
        crown = ellipsoid((-0.79, 0.0, 2.91), (0.59, 0.51, 0.69))
        forehead = ellipsoid((-0.93, 0.0, 2.62), (0.53, 0.49, 0.67))
        cheek = ellipsoid((-0.91, 0.0, 2.30), (0.45, 0.45, 0.54))
        neck_blend = ellipsoid((-0.43, 0.0, 2.48), (0.58, 0.50, 0.62))
        head = hull(crown, forehead, cheek, neck_blend, color=ELEPHANT_GRAY)
        self.head = self.attach_part("head", head)

        # Far-side parts are attached first in the hierarchy; both ears remain
        # thin side panels rather than circular discs.
        self.far_ear = self.attach_part("far_ear", FanEar(0.505, "FarEar"))
        self.near_ear = self.attach_part("near_ear", FanEar(-0.555, "NearEar"))

        self.trunk = self.attach_part("trunk", CurvedTrunk())

        self.front_far_leg = self.attach_part(
            "front_far_leg", OrganicLeg(-0.16, 0.355, "FrontFarLeg")
        )
        self.rear_far_leg = self.attach_part(
            "rear_far_leg", OrganicLeg(1.28, 0.355, "RearFarLeg", upper_fullness=1.07)
        )
        self.front_near_leg = self.attach_part(
            "front_near_leg", OrganicLeg(-0.16, -0.355, "FrontNearLeg")
        )
        self.rear_near_leg = self.attach_part(
            "rear_near_leg", OrganicLeg(1.28, -0.355, "RearNearLeg", upper_fullness=1.07)
        )

        # Small lateral eyes, intentionally subtle and only slightly proud of
        # the head surface.
        near_eye = Sphere(0.043, center=(-1.18, -0.475, 2.88), color=EYE_DARK)
        far_eye = Sphere(0.043, center=(-1.18, 0.475, 2.88), color=EYE_DARK)
        self.near_eye = self.attach_part("near_eye", near_eye)
        self.far_eye = self.attach_part("far_eye", far_eye)


scene = StylizedElephant()
