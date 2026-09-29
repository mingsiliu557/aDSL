from adsl.core import *


WOOD = (0.50, 0.24, 0.08)
DARK_METAL = (0.075, 0.085, 0.095)


class TaperedTabletop(Asset):
    """Straight-sided slab, 1500 mm wide at the front and 1420 mm at the back."""

    def __init__(self):
        super().__init__(label="TaperedTabletop")

        slab = Cube(
            (1500.0, 750.0, 55.0),
            center=(0.0, 0.0, 722.5),
            color=WOOD,
        )

        # Intersect the rectangular slab with two very large, rotated boxes.
        # Their inner vertical faces are the exact straight taper lines:
        # right: x + (40/750)y = 730, left: -x + (40/750)y = 730.
        taper_ratio = 40.0 / 750.0
        theta = 3.0535900500422257  # degrees: atan(taper_ratio)
        norm = (1.0 + taper_ratio * taper_ratio) ** 0.5
        halfspace_size = 4000.0
        local_u_center = 730.0 / norm - halfspace_size / 2.0

        right_center = (
            local_u_center / norm,
            local_u_center * taper_ratio / norm,
            722.5,
        )
        right_halfspace = Cube(
            (halfspace_size, halfspace_size, 120.0),
            center=right_center,
            color=WOOD,
        )
        right_halfspace = rotate_shape(
            right_halfspace,
            axis="+z",
            angle=theta,
            center=right_center,
        )

        left_center = (
            -local_u_center / norm,
            local_u_center * taper_ratio / norm,
            722.5,
        )
        left_halfspace = Cube(
            (halfspace_size, halfspace_size, 120.0),
            center=left_center,
            color=WOOD,
        )
        left_halfspace = rotate_shape(
            left_halfspace,
            axis="+z",
            angle=180.0 - theta,
            center=left_center,
        )

        self.body = self.attach_part(
            "tabletop",
            boolean_intersection(slab, right_halfspace, left_halfspace),
        )


class Pedestal(Asset):
    """One continuous dark pedestal print part: broad foot plus upright slab."""

    def __init__(self, side: str):
        super().__init__(label=f"{side.title()}Pedestal")
        x_center = -430.0 if side == "left" else 430.0

        self.upright = self.attach_part(
            f"{side}_leg_upright",
            Cube(
                (90.0, 500.0, 650.0),
                center=(x_center, 0.0, 370.0),
                color=DARK_METAL,
            ),
        )
        self.foot = self.attach_part(
            f"{side}_leg_foot",
            Cube(
                (320.0, 600.0, 45.0),
                center=(x_center, 0.0, 22.5),
                color=DARK_METAL,
            ),
        )


class HorizontalBrace(Asset):
    """A separate rectangular cross-brace whose ends meet the pedestal faces."""

    def __init__(self, name: str, y_depth: float, z_height: float, z_center: float):
        super().__init__(label=name)
        self.body = self.attach_part(
            name,
            Cube(
                (770.0, y_depth, z_height),
                center=(0.0, 0.0, z_center),
                color=DARK_METAL,
            ),
        )


# Build the five planned pre-connector print-piece bodies in assembled coordinates.
tabletop_asset = TaperedTabletop()
left_pedestal_asset = Pedestal("left")
right_pedestal_asset = Pedestal("right")
upper_brace_asset = HorizontalBrace("upper_brace", 70.0, 70.0, 595.0)
lower_brace_asset = HorizontalBrace("lower_brace", 90.0, 80.0, 250.0)


# Shared connector specifications. The fit allowance is the frozen single-sided 0.2 mm.
# Square tab tips avoid optional lead-in facets while preserving every mating dimension.
tabletop_leg_joint = TabSlot(
    width_mm=36.0,
    thickness_mm=50.0,
    insertion_mm=20.0,
    slot_depth_mm=20.0,
    fit_offset_mm=0.2,
    root_overlap_mm=5.0,
    opening_extension_mm=0.5,
    lead_in_mm=0.0,
)
brace_leg_joint = TabSlot(
    width_mm=50.0,
    thickness_mm=55.0,
    insertion_mm=25.0,
    slot_depth_mm=25.0,
    fit_offset_mm=0.2,
    root_overlap_mm=5.0,
    opening_extension_mm=0.5,
    lead_in_mm=0.0,
)


assembly = FixedAssembly(root_id="tabletop", mm_per_unit=1.0)
assembly.add_part("tabletop", tabletop_asset, components=("tabletop",))
assembly.add_part(
    "left_pedestal",
    left_pedestal_asset,
    components=("left_leg_upright", "left_leg_foot"),
)
assembly.add_part(
    "right_pedestal",
    right_pedestal_asset,
    components=("right_leg_upright", "right_leg_foot"),
)
assembly.add_part("upper_brace", upper_brace_asset, components=("upper_brace",))
assembly.add_part("lower_brace", lower_brace_asset, components=("lower_brace",))


# Vertical mounting frames use the same +z insertion direction on tabs and slots.
def vertical_mount_frame(x_position: float, y_position: float) -> InterfaceFrame:
    return InterfaceFrame(
        origin=(x_position, y_position, 695.0),
        x_axis=(1.0, 0.0, 0.0),
        insert_axis=(0.0, 0.0, 1.0),
    )


# Connection order starts at the tabletop root. The first mount locates each pedestal;
# its separated second mount checks the established pose and adds the second interface.
assembly.connect(
    "left_leg_front_mount",
    tab_part="left_pedestal",
    slot_part="tabletop",
    tab_frame=vertical_mount_frame(-430.0, -150.0),
    slot_frame=vertical_mount_frame(-430.0, -150.0),
    parameters=tabletop_leg_joint,
    parameter_name="tabletop_leg_joint",
    tab_port="top_front_tab",
    slot_port="left_front_leg_slot",
)
assembly.connect(
    "left_leg_rear_mount",
    tab_part="left_pedestal",
    slot_part="tabletop",
    tab_frame=vertical_mount_frame(-430.0, 150.0),
    slot_frame=vertical_mount_frame(-430.0, 150.0),
    parameters=tabletop_leg_joint,
    parameter_name="tabletop_leg_joint",
    tab_port="top_rear_tab",
    slot_port="left_rear_leg_slot",
)
assembly.connect(
    "right_leg_front_mount",
    tab_part="right_pedestal",
    slot_part="tabletop",
    tab_frame=vertical_mount_frame(430.0, -150.0),
    slot_frame=vertical_mount_frame(430.0, -150.0),
    parameters=tabletop_leg_joint,
    parameter_name="tabletop_leg_joint",
    tab_port="top_front_tab",
    slot_port="right_front_leg_slot",
)
assembly.connect(
    "right_leg_rear_mount",
    tab_part="right_pedestal",
    slot_part="tabletop",
    tab_frame=vertical_mount_frame(430.0, 150.0),
    slot_frame=vertical_mount_frame(430.0, 150.0),
    parameters=tabletop_leg_joint,
    parameter_name="tabletop_leg_joint",
    tab_port="top_rear_tab",
    slot_port="right_rear_leg_slot",
)


# Horizontal brace frames. Frame X is along table depth, leaving frame Y vertical;
# frame Z is the specified insertion direction into each pedestal.
def left_brace_frame(z_position: float) -> InterfaceFrame:
    return InterfaceFrame(
        origin=(-385.0, 0.0, z_position),
        x_axis=(0.0, 1.0, 0.0),
        insert_axis=(-1.0, 0.0, 0.0),
    )


def right_brace_frame(z_position: float) -> InterfaceFrame:
    return InterfaceFrame(
        origin=(385.0, 0.0, z_position),
        x_axis=(0.0, 1.0, 0.0),
        insert_axis=(1.0, 0.0, 0.0),
    )


# Each left-end connection locates its brace. Each right-end connection then checks
# the already established world frame and generates the closing paired interface.
assembly.connect(
    "upper_brace_left_mount",
    tab_part="upper_brace",
    slot_part="left_pedestal",
    tab_frame=left_brace_frame(595.0),
    slot_frame=left_brace_frame(595.0),
    parameters=brace_leg_joint,
    parameter_name="brace_leg_joint",
    tab_port="left_end_tab",
    slot_port="upper_brace_inner_slot",
)
assembly.connect(
    "upper_brace_right_mount",
    tab_part="upper_brace",
    slot_part="right_pedestal",
    tab_frame=right_brace_frame(595.0),
    slot_frame=right_brace_frame(595.0),
    parameters=brace_leg_joint,
    parameter_name="brace_leg_joint",
    tab_port="right_end_tab",
    slot_port="upper_brace_inner_slot",
)
assembly.connect(
    "lower_brace_left_mount",
    tab_part="lower_brace",
    slot_part="left_pedestal",
    tab_frame=left_brace_frame(250.0),
    slot_frame=left_brace_frame(250.0),
    parameters=brace_leg_joint,
    parameter_name="brace_leg_joint",
    tab_port="left_end_tab",
    slot_port="lower_brace_inner_slot",
)
assembly.connect(
    "lower_brace_right_mount",
    tab_part="lower_brace",
    slot_part="right_pedestal",
    tab_frame=right_brace_frame(250.0),
    slot_frame=right_brace_frame(250.0),
    parameters=brace_leg_joint,
    parameter_name="brace_leg_joint",
    tab_port="right_end_tab",
    slot_port="lower_brace_inner_slot",
)


scene = assembly.scene()
