from adsl.core import *


BLACK = (0.025, 0.028, 0.03)
DARK_BLACK = (0.015, 0.017, 0.018)
BEIGE = (0.88, 0.80, 0.66)
TRIM_BEIGE = (0.72, 0.62, 0.47)


class CurvedTripodLeg(Asset):
    def __init__(self):
        super().__init__(label="CurvedTripodLeg")
        # A shallow S-like descent from the hub toward a floor pad.
        points = (
            (0.025, 0.0, 0.190),
            (0.085, 0.0, 0.175),
            (0.155, 0.0, 0.125),
            (0.235, 0.0, 0.065),
            (0.320, 0.0, 0.025),
        )
        for index in range(len(points) - 1):
            self.attach_part(
                f"curved_segment_{index + 1}",
                Cylinder(
                    radius=0.011,
                    p0=points[index],
                    p1=points[index + 1],
                    color=BLACK,
                ),
            )


class TripodBase(Asset):
    def __init__(self):
        super().__init__(label="TripodBase")

        self.hub = self.attach_part(
            "tripod_hub",
            Sphere(radius=0.050, center=(0.0, 0.0, 0.180), color=BLACK),
        )
        self.hub_collar = self.attach_part(
            "hub_collar",
            Cylinder(
                radius=0.040,
                height=0.105,
                center=(0.0, 0.0, 0.180),
                axis="z",
                color=DARK_BLACK,
            ),
        )

        legs = Asset("ThreeCurvedLegs")
        for index, angle in enumerate((0.0, 120.0, 240.0), 1):
            leg = rotate_shape(
                CurvedTripodLeg(), axis="+z", angle=angle, center=(0.0, 0.0, 0.0)
            )
            legs.attach_part(f"leg_{index}", leg)
        self.legs = self.attach_part("curved_tripod_legs", legs)

        foot_seed = Cylinder(
            radius=0.034,
            height=0.024,
            center=(0.0, 0.0, 0.012),
            axis="z",
            color=DARK_BLACK,
        )
        self.feet = self.attach_part(
            "feet",
            radial_shapes(
                [foot_seed.copy(), foot_seed.copy(), foot_seed.copy()],
                radius=0.320,
                axis="+z",
                center=(0.0, 0.0, 0.0),
                start_angle=0.0,
                sweep=360.0,
                rotate_with_layout=False,
            ),
        )


class DecorativeTwist(Asset):
    def __init__(self):
        super().__init__(label="DecorativeTwist")

        # Two opposed, tapered helical paths. Both converge onto the central
        # shaft at their ends, keeping the ornament open but structurally joined.
        path_a = (
            (0.0000, 0.0000, 0.7800),
            (0.0125, 0.0217, 0.8133),
            (-0.0200, 0.0346, 0.8467),
            (-0.0450, 0.0000, 0.8800),
            (-0.0225, -0.0390, 0.9133),
            (0.0225, -0.0390, 0.9467),
            (0.0450, 0.0000, 0.9800),
            (0.0225, 0.0390, 1.0133),
            (-0.0225, 0.0390, 1.0467),
            (-0.0450, 0.0000, 1.0800),
            (-0.0200, -0.0346, 1.1133),
            (0.0125, -0.0217, 1.1467),
            (0.0000, 0.0000, 1.1800),
        )
        path_b = tuple((-x, -y, z) for x, y, z in path_a)

        for rod_index, path in enumerate((path_a, path_b), 1):
            rod = Asset(f"HelicalRod{rod_index}")
            for segment_index in range(len(path) - 1):
                rod.attach_part(
                    f"segment_{segment_index + 1}",
                    Cylinder(
                        radius=0.009,
                        p0=path[segment_index],
                        p1=path[segment_index + 1],
                        color=BLACK,
                    ),
                )
            self.attach_part(f"helical_rod_{rod_index}", rod)

        self.lower_collar = self.attach_part(
            "lower_twist_collar",
            Cylinder(
                radius=0.027,
                height=0.030,
                center=(0.0, 0.0, 0.785),
                axis="z",
                color=DARK_BLACK,
            ),
        )
        self.upper_collar = self.attach_part(
            "upper_twist_collar",
            Cylinder(
                radius=0.027,
                height=0.030,
                center=(0.0, 0.0, 1.175),
                axis="z",
                color=DARK_BLACK,
            ),
        )


class ShadeSupportRing(Asset):
    def __init__(self, radius, z, segment_length):
        super().__init__(label="ShadeSupportRing")
        tangent_segment = Cylinder(
            radius=0.004,
            height=segment_length,
            center=(0.0, 0.0, z),
            axis="y",
            color=DARK_BLACK,
        )
        ring = radial_shapes(
            [tangent_segment.copy() for _ in range(24)],
            radius=radius,
            axis="+z",
            center=(0.0, 0.0, z),
            start_angle=0.0,
            sweep=360.0,
            rotate_with_layout=True,
        )
        self.segments = self.attach_part("ring_segments", ring)


class FabricLampshade(Asset):
    def __init__(self):
        super().__init__(label="FabricLampshade")
        bottom_z = 1.400
        slice_height = 0.0285
        slice_count = 12
        bottom_radius = 0.310
        top_radius = 0.180

        fabric = Asset("TaperedFabricShell")
        for index in range(slice_count):
            fraction = index / (slice_count - 1)
            radius = bottom_radius + (top_radius - bottom_radius) * fraction
            center_z = bottom_z + slice_height * (index + 0.5)
            outer = Cylinder(
                radius=radius,
                height=slice_height + 0.001,
                center=(0.0, 0.0, center_z),
                axis="z",
                color=BEIGE,
                alpha=0.90,
            )
            inner = Cylinder(
                radius=radius - 0.012,
                height=slice_height + 0.008,
                center=(0.0, 0.0, center_z),
                axis="z",
            )
            fabric.attach_part(
                f"fabric_slice_{index + 1}", boolean_difference(outer, inner)
            )
        self.fabric = self.attach_part("fabric", fabric)

        self.bottom_trim = self.attach_part(
            "bottom_trim",
            Cylinder(
                radius=0.312,
                height=0.014,
                center=(0.0, 0.0, bottom_z + 0.007),
                axis="z",
                color=TRIM_BEIGE,
            ),
        )
        self.top_trim = self.attach_part(
            "top_trim",
            Cylinder(
                radius=0.182,
                height=0.014,
                center=(0.0, 0.0, bottom_z + slice_height * slice_count - 0.007),
                axis="z",
                color=TRIM_BEIGE,
            ),
        )

        frame = Asset("LampshadeFrame")
        frame.attach_part(
            "lower_support_ring", ShadeSupportRing(radius=0.298, z=1.414, segment_length=0.079)
        )
        frame.attach_part(
            "upper_support_ring", ShadeSupportRing(radius=0.174, z=1.728, segment_length=0.047)
        )
        self.frame = self.attach_part("lampshade_frame", frame)


class ClassicTripodFloorLamp(Asset):
    def __init__(self):
        super().__init__(label="ClassicTripodFloorLamp")

        self.tripod_base = self.attach_part("tripod_base", TripodBase())

        self.central_lower_stem = self.attach_part(
            "central_lower_stem",
            Cylinder(
                radius=0.0175,
                p0=(0.0, 0.0, 0.120),
                p1=(0.0, 0.0, 0.790),
                color=BLACK,
            ),
        )
        self.decorative_twist = self.attach_part(
            "decorative_twist", DecorativeTwist()
        )
        self.central_upper_stem = self.attach_part(
            "central_upper_stem",
            Cylinder(
                radius=0.0175,
                p0=(0.0, 0.0, 1.170),
                p1=(0.0, 0.0, 1.475),
                color=BLACK,
            ),
        )
        self.lamp_socket = self.attach_part(
            "lamp_socket",
            Cylinder(
                radius=0.036,
                height=0.120,
                center=(0.0, 0.0, 1.510),
                axis="z",
                color=DARK_BLACK,
            ),
        )
        self.socket_cap = self.attach_part(
            "socket_cap",
            Cylinder(
                radius=0.046,
                height=0.018,
                center=(0.0, 0.0, 1.455),
                axis="z",
                color=BLACK,
            ),
        )
        self.lampshade = self.attach_part("lampshade", FabricLampshade())


scene = ClassicTripodFloorLamp()
