from adsl.core import *
import math


# Frozen manufacturing/request values.  This assembly intentionally has one
# continuous print part, so the fit allowance is retained but no interface uses it.
MM_PER_UNIT = 1.0
FIT_OFFSET_MM = 0.2
FINAL_SIZE_MM = (68.59887936937257, 68.5988891875806, 150.0)
SHADE_RADIUS = FINAL_SIZE_MM[1] / 2.0
RADIAL_SEGMENTS = 128


class CylindricalShade(Asset):
    """Open-ended drum shade with gently rolled upper and lower lips."""

    def __init__(self):
        super().__init__(label="cylindrical_shade")

        # This closed (r,z) ring describes only the shade wall.  Its center is
        # absent, leaving true circular openings at both ends.
        shade_wall_profile = Polygon([
            (33.45, 92.00),
            (33.88, 92.12),
            (34.17, 92.48),
            (SHADE_RADIUS, 93.05),
            (SHADE_RADIUS, 148.95),
            (34.17, 149.52),
            (33.88, 149.88),
            (33.45, 150.00),
            (32.08, 150.00),
            (31.76, 149.78),
            (31.57, 149.30),
            (31.54, 148.78),
            (31.54, 93.22),
            (31.57, 92.70),
            (31.76, 92.22),
            (32.08, 92.00),
        ])
        wall = rotate_extrude(
            shade_wall_profile,
            segments=RADIAL_SEGMENTS,
            color=(0.72, 0.69, 0.62),
        )
        self.wall = self.attach_part("rounded_open_drum", wall)


class NarrowNeckAndCollar(Asset):
    """Turned stem and compact transition collar directly below the shade."""

    def __init__(self):
        super().__init__(label="narrow_neck_and_collar")
        # Keep the revolved profile a small, safely representable distance from
        # the axis.  A positively overlapping centered core closes the turning
        # without the zero-radius triangle fans that collapse in float32 GLB.
        inner_radius = 0.20
        core_radius = 0.32
        exterior_profile = [
            (5.20, 77.5),
            (5.55, 79.0),
            (6.60, 80.4),
            (7.55, 82.0),
            (7.30, 83.3),
            (6.10, 84.5),
            (4.75, 85.6),
            (4.35, 87.2),
            (4.75, 88.6),
            (6.35, 89.4),
            (8.60, 90.2),
            (10.30, 91.2),
            (10.65, 92.0),
            (9.65, 92.8),
            (7.15, 93.45),
            (6.05, 94.2),
            (6.05, 96.4),
        ]
        annular_profile = Polygon(
            [(inner_radius, 77.5)]
            + exterior_profile
            + [(inner_radius, 96.4)]
        )
        annular_turning = rotate_extrude(
            annular_profile,
            segments=RADIAL_SEGMENTS,
            color=(0.82, 0.80, 0.74),
        )
        closure_core = Cylinder(
            core_radius,
            height=96.4 - 77.5,
            center=(0.0, 0.0, (77.5 + 96.4) / 2.0),
            axis="z",
            color=(0.82, 0.80, 0.74),
        )
        turning = boolean_union(annular_turning, closure_core)
        self.turning = self.attach_part("turned_neck_and_upper_collar", turning)


class TurnedPedestalBase(Asset):
    """Single ordered turning with a flat foot, beads, coves, and narrow stem."""

    def __init__(self):
        super().__init__(label="turned_pedestal_base")

        # Exterior points progress monotonically upward.  Closing down the axis
        # makes the turning solid while preserving a truly flat z=0 bed face.
        exterior = [
            (14.2, 0.0),
            (16.1, 0.5),
            (17.4, 1.7),
            (18.0, 3.2),
            (17.8, 4.8),
            (16.8, 6.4),
            (15.2, 8.0),
            (13.3, 9.3),
            (12.2, 10.7),
            (12.0, 12.2),
            (12.6, 13.4),
            (14.0, 14.7),
            (15.0, 16.2),
            (15.2, 18.0),
            (14.5, 19.6),
            (12.7, 21.0),
            (10.2, 22.0),
            (8.6, 23.2),
            (8.25, 24.7),
            (9.0, 26.0),
            (10.9, 27.0),
            (12.5, 28.3),
            (13.1, 29.8),
            (12.6, 31.3),
            (10.8, 32.7),
            (8.5, 33.8),
            (7.1, 35.1),
            (6.8, 36.8),
            (7.4, 38.2),
            (8.8, 39.6),
            (9.7, 41.1),
            (9.5, 42.8),
            (8.4, 44.1),
            (6.8, 45.3),
            (5.9, 47.0),
            (5.8, 49.0),
            (6.15, 51.0),
            (6.9, 52.7),
            (7.75, 54.2),
            (8.05, 55.8),
            (7.6, 57.3),
            (6.5, 58.7),
            (5.65, 60.2),
            (5.35, 62.0),
            (5.55, 64.0),
            (6.25, 66.0),
            (6.75, 67.8),
            (6.45, 69.5),
            (5.75, 71.0),
            (5.15, 72.8),
            (5.0, 74.8),
            (5.2, 77.5),
            (0.0, 77.5),
        ]
        # Keep the revolve itself off the axis, then close its concealed bore
        # with a centered core that overlaps radially by 0.12 mm.  This retains
        # the original exterior and z extents while avoiding degenerate axis
        # fans after float32 display conversion.
        inner_radius = 0.20
        core_radius = 0.32
        annular_profile = Polygon(
            [(inner_radius, 0.0)]
            + exterior[:-1]
            + [(inner_radius, 77.5)]
        )
        annular_turning = rotate_extrude(
            annular_profile,
            segments=RADIAL_SEGMENTS,
            color=(0.82, 0.80, 0.74),
        )
        closure_core = Cylinder(
            core_radius,
            height=77.5,
            center=(0.0, 0.0, 77.5 / 2.0),
            axis="z",
            color=(0.82, 0.80, 0.74),
        )
        turning = boolean_union(annular_turning, closure_core)
        self.turning = self.attach_part("multi_bead_pedestal_turning", turning)


class CentralMountingCore(Asset):
    """Concealed hub and open spoke spider tying the neck to the shade wall."""

    def __init__(self):
        super().__init__(label="central_mounting_core")

        hub = Cylinder(
            6.15,
            height=4.8,
            center=(0.0, 0.0, 94.0),
            axis="z",
            color=(0.78, 0.76, 0.70),
        )
        self.hub = self.attach_part("concealed_center_hub", hub)

        # Six narrow arms retain broad open sectors around the neck rather than
        # closing the lower mouth with a full disk.  They sit just inside the
        # lower lip and overlap both the hub and inner shade wall.
        spoke_length = 27.85
        spoke_center_radius = (4.35 + 32.20) / 2.0
        spoke_blanks = [
            Cube(
                (spoke_length, 1.15, 1.25),
                color=(0.70, 0.68, 0.63),
            )
            for _ in range(6)
        ]
        spider = radial_shapes(
            spoke_blanks,
            radius=spoke_center_radius,
            axis="+z",
            center=(0.0, 0.0, 95.15),
            start_angle=0.0,
            sweep=360.0,
            rotate_with_layout=True,
        )
        self.spider = self.attach_part("open_six_arm_shade_spider", spider)


class LampBody(Asset):
    """Complete fixed lamp, organized by the requested semantic components."""

    def __init__(self):
        super().__init__(label="lamp_body")
        self.cylindrical_shade = self.attach_part(
            "cylindrical_shade", CylindricalShade()
        )
        self.narrow_neck_and_collar = self.attach_part(
            "narrow_neck_and_collar", NarrowNeckAndCollar()
        )
        self.turned_pedestal_base = self.attach_part(
            "turned_pedestal_base", TurnedPedestalBase()
        )
        self.central_mounting_core = self.attach_part(
            "central_mounting_core", CentralMountingCore()
        )


lamp_body = LampBody()
assembly = FixedAssembly(root_id="lamp_body", mm_per_unit=MM_PER_UNIT)
assembly.add_part(
    "lamp_body",
    lamp_body,
    components=(
        "cylindrical_shade",
        "narrow_neck_and_collar",
        "turned_pedestal_base",
        "central_mounting_core",
    ),
)
scene = assembly.scene()
