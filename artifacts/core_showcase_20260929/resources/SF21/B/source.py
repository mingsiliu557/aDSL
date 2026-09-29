from adsl.core import *
import math


BLACK_METAL = (0.025, 0.028, 0.032)
BEIGE_FABRIC = (0.86, 0.77, 0.61)
BEIGE_TRIM = (0.76, 0.67, 0.52)


def tube_along_points(points, radius, color, joint_spheres=True, label="curved_tube"):
    """Build a continuous-looking round tube from sampled centerline points."""
    assembly = Asset(label)
    for index, (p0, p1) in enumerate(zip(points[:-1], points[1:]), 1):
        assembly.attach_part(
            f"segment_{index}",
            Cylinder(radius, p0=p0, p1=p1, color=color),
        )
    if joint_spheres:
        for index, point in enumerate(points[1:-1], 1):
            assembly.attach_part(
                f"joint_{index}",
                Sphere(radius, center=point, color=color),
            )
    return assembly


class BaseHub(Asset):
    def __init__(self):
        super().__init__(label="central_base_hub")
        profile = Polygon([
            (0.0, 0.010),
            (0.030, 0.010),
            (0.042, 0.025),
            (0.043, 0.085),
            (0.034, 0.125),
            (0.014, 0.135),
            (0.0, 0.135),
        ])
        self.body = self.attach_part(
            "rounded_hub_body",
            rotate_extrude(profile, segments=64, color=BLACK_METAL),
        )
        self.collar = self.attach_part(
            "upper_collar",
            Cylinder(0.020, height=0.026, center=(0.0, 0.0, 0.137), color=BLACK_METAL),
        )


class CurvedTripodLegs(Asset):
    def __init__(self):
        super().__init__(label="three_curved_legs")
        # One leg is authored toward +x, then copied into exact 120-degree slots.
        # The final center is one tube radius above z=0, so every foot touches the floor.
        points = [
            (0.034, 0.0, 0.105),
            (0.048, 0.0, 0.076),
            (0.082, 0.0, 0.047),
            (0.132, 0.0, 0.026),
            (0.195, 0.0, 0.014),
            (0.258, 0.0, 0.011),
            (0.300, 0.0, 0.011),
        ]
        reference_leg = tube_along_points(
            points,
            radius=0.011,
            color=BLACK_METAL,
            joint_spheres=True,
            label="curved_leg",
        )
        # Rounded terminal forms a discreet foot whose bottom is exactly z=0.
        reference_leg.attach_part(
            "rounded_foot",
            Sphere(0.011, center=points[-1], color=BLACK_METAL),
        )
        self.tripod = self.attach_part(
            "radial_tripod",
            radial_shapes(
                [reference_leg.copy(), reference_leg.copy(), reference_leg.copy()],
                radius=0.0,
                axis="+z",
                center=(0.0, 0.0, 0.0),
                start_angle=0.0,
                sweep=360.0,
                rotate_with_layout=True,
            ),
        )


class DecorativeTwist(Asset):
    def __init__(self, z0=0.68, z1=1.00, turns=1.25, samples=34):
        super().__init__(label="decorative_twist")
        strand_radius = 0.0062
        max_center_radius = 0.032

        for strand_index, phase in enumerate((0.0, math.pi), 1):
            points = []
            for index in range(samples + 1):
                t = index / samples
                z = z0 + (z1 - z0) * t
                # Sinusoidal envelope brings both strands cleanly into the central shaft.
                radial = max_center_radius * math.sin(math.pi * t)
                angle = phase + 2.0 * math.pi * turns * t
                points.append((radial * math.cos(angle), radial * math.sin(angle), z))
            strand = tube_along_points(
                points,
                radius=strand_radius,
                color=BLACK_METAL,
                joint_spheres=True,
                label=f"twisted_strand_{strand_index}",
            )
            self.attach_part(f"strand_{strand_index}", strand)

        self.lower_transition = self.attach_part(
            "lower_transition_collar",
            Cylinder(0.013, height=0.022, center=(0.0, 0.0, z0), color=BLACK_METAL),
        )
        self.upper_transition = self.attach_part(
            "upper_transition_collar",
            Cylinder(0.013, height=0.022, center=(0.0, 0.0, z1), color=BLACK_METAL),
        )


class LampStand(Asset):
    def __init__(self):
        super().__init__(label="slender_black_metal_stand")
        self.lower_shaft = self.attach_part(
            "lower_stand_shaft",
            Cylinder(0.0105, p0=(0.0, 0.0, 0.135), p1=(0.0, 0.0, 0.68), color=BLACK_METAL),
        )
        self.twist = self.attach_part("decorative_twist", DecorativeTwist())
        self.upper_shaft = self.attach_part(
            "upper_stand_shaft",
            Cylinder(0.0105, p0=(0.0, 0.0, 1.00), p1=(0.0, 0.0, 1.48), color=BLACK_METAL),
        )


class ShadeSupport(Asset):
    def __init__(self):
        super().__init__(label="shade_support_and_socket")
        self.socket = self.attach_part(
            "socket",
            Cylinder(0.024, height=0.105, center=(0.0, 0.0, 1.505), color=BLACK_METAL),
        )
        self.neck = self.attach_part(
            "socket_neck",
            Cylinder(0.014, height=0.055, center=(0.0, 0.0, 1.445), color=BLACK_METAL),
        )
        support_ring_profile = Polygon([
            (0.105, 1.355),
            (0.112, 1.355),
            (0.112, 1.361),
            (0.105, 1.361),
        ])
        self.hidden_support_ring = self.attach_part(
            "hidden_support_ring",
            rotate_extrude(support_ring_profile, segments=64, color=BLACK_METAL),
        )
        for index, angle in enumerate((0.0, 120.0, 240.0), 1):
            radians = math.radians(angle)
            endpoint = (0.108 * math.cos(radians), 0.108 * math.sin(radians), 1.358)
            self.attach_part(
                f"support_spoke_{index}",
                Cylinder(0.003, p0=(0.0, 0.0, 1.358), p1=endpoint, color=BLACK_METAL),
            )


class FabricLampshade(Asset):
    def __init__(self):
        super().__init__(label="fabric_lampshade")
        z_bottom = 1.300
        z_top = 1.730
        outer_bottom = 0.240
        outer_top = 0.135
        thickness = 0.009

        # Annular tapered wall: the central apertures at both ends remain open.
        shell_profile = Polygon([
            (outer_bottom, z_bottom),
            (outer_top, z_top),
            (outer_top - thickness, z_top),
            (outer_bottom - thickness, z_bottom),
        ])
        self.fabric_shell = self.attach_part(
            "hollow_tapered_fabric_shell",
            rotate_extrude(
                shell_profile,
                segments=96,
                color=BEIGE_FABRIC,
                alpha=0.84,
            ),
        )

        bottom_rim_profile = Polygon([
            (outer_bottom - 0.004, z_bottom - 0.004),
            (outer_bottom + 0.004, z_bottom - 0.004),
            (outer_bottom + 0.004, z_bottom + 0.004),
            (outer_bottom - 0.004, z_bottom + 0.004),
        ])
        top_rim_profile = Polygon([
            (outer_top - 0.004, z_top - 0.004),
            (outer_top + 0.004, z_top - 0.004),
            (outer_top + 0.004, z_top + 0.004),
            (outer_top - 0.004, z_top + 0.004),
        ])
        self.bottom_rim = self.attach_part(
            "bottom_rim_ring",
            rotate_extrude(bottom_rim_profile, segments=96, color=BEIGE_TRIM),
        )
        self.top_rim = self.attach_part(
            "top_rim_ring",
            rotate_extrude(top_rim_profile, segments=96, color=BEIGE_TRIM),
        )


class ClassicTripodFloorLamp(Asset):
    def __init__(self):
        super().__init__(label="classic_tripod_floor_lamp")
        self.legs = self.attach_part("three_curved_legs", CurvedTripodLegs())
        self.base_hub = self.attach_part("central_base_hub", BaseHub())
        self.stand = self.attach_part("metal_stand", LampStand())
        self.shade_support = self.attach_part("shade_support_and_socket", ShadeSupport())
        self.shade = self.attach_part("fabric_lampshade", FabricLampshade())


scene = ClassicTripodFloorLamp()
