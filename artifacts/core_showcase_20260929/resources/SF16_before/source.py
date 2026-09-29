from adsl.core import *


MATTE_BLACK = (0.018, 0.020, 0.024)


class SidePanel(Asset):
    def __init__(self, name, center):
        super().__init__(label=name)
        self.panel = self.attach_part(
            "panel_body",
            Cube(
                (40.0, 450.0, 1800.0),
                center=center,
                color=MATTE_BLACK,
            ),
        )


class Shelf(Asset):
    def __init__(self, name):
        super().__init__(label=name)
        self.shelf = self.attach_part(
            "shelf_body",
            Cube(
                (1045.0, 410.0, 36.0),
                center=(0.0, 0.0, 0.0),
                color=MATTE_BLACK,
            ),
        )


class SlantedBackPanel(Asset):
    def __init__(self):
        super().__init__(label="slanted_back_panel")
        panel = Cube(
            (1045.0, 24.0, 260.0),
            center=(0.0, 0.0, 0.0),
            color=MATTE_BLACK,
        )
        panel = rotate_shape(panel, axis="+x", angle=-7.0, center=(0.0, 0.0, 0.0))
        self.panel = self.attach_part("panel_body", panel)


# Root geometry is authored directly in assembly coordinates.  Every other body
# uses its own simple local frame and is positioned solely by assembly.connect.
left_side_panel = SidePanel(
    "left_side_panel",
    center=(-542.5, 0.0, 900.0),
)
top_shelf = Shelf("top_shelf")
right_side_panel = SidePanel(
    "right_side_panel",
    center=(0.0, 0.0, 0.0),
)
middle_shelf = Shelf("middle_shelf")
lower_shelf = Shelf("lower_shelf")
slanted_back_panel = SlantedBackPanel()


assembly = FixedAssembly(root_id="left_side_panel", mm_per_unit=1.0)
assembly.add_part(
    "left_side_panel",
    left_side_panel,
    components=("left_side_panel",),
)
assembly.add_part(
    "top_shelf",
    top_shelf,
    components=("top_shelf",),
)
assembly.add_part(
    "right_side_panel",
    right_side_panel,
    components=("right_side_panel",),
)
assembly.add_part(
    "middle_shelf",
    middle_shelf,
    components=("middle_shelf",),
)
assembly.add_part(
    "lower_shelf",
    lower_shelf,
    components=("lower_shelf",),
)
assembly.add_part(
    "slanted_back_panel",
    slanted_back_panel,
    components=("slanted_back_panel",),
)


# One concealed connector specification is shared by all shelf/side interfaces.
# Width lies along Y, thickness along Z, and insertion follows each frame's +Z.
shelf_side_joint = TabSlot(
    width_mm=120.0,
    thickness_mm=18.0,
    insertion_mm=20.0,
    slot_depth_mm=20.0,
    fit_offset_mm=0.2,
    root_overlap_mm=8.0,
    opening_extension_mm=0.5,
    lead_in_mm=2.0,
)

back_panel_joint = TabSlot(
    width_mm=100.0,
    thickness_mm=12.0,
    insertion_mm=20.0,
    slot_depth_mm=20.0,
    fit_offset_mm=0.2,
    root_overlap_mm=8.0,
    opening_extension_mm=0.5,
    lead_in_mm=2.0,
)


# Top shelf slides leftward into the inner face of the rooted left side panel.
assembly.connect(
    "connect_top_shelf_to_left_side",
    tab_part="top_shelf",
    slot_part="left_side_panel",
    tab_frame=InterfaceFrame(
        origin=(-522.5, 0.0, 0.0),
        x_axis=(0.0, 1.0, 0.0),
        insert_axis=(-1.0, 0.0, 0.0),
    ),
    slot_frame=InterfaceFrame(
        origin=(-522.5, 0.0, 1650.0),
        x_axis=(0.0, 1.0, 0.0),
        insert_axis=(-1.0, 0.0, 0.0),
    ),
    parameters=shelf_side_joint,
    parameter_name="shelf_side_joint",
    tab_port="left_end_tab",
    slot_port="upper_shelf_slot",
)


# The right side panel is introduced through the top shelf, completing the frame.
assembly.connect(
    "connect_right_side_to_top_shelf",
    tab_part="right_side_panel",
    slot_part="top_shelf",
    tab_frame=InterfaceFrame(
        origin=(-20.0, 0.0, 750.0),
        x_axis=(0.0, 1.0, 0.0),
        insert_axis=(-1.0, 0.0, 0.0),
    ),
    slot_frame=InterfaceFrame(
        origin=(522.5, 0.0, 0.0),
        x_axis=(0.0, 1.0, 0.0),
        insert_axis=(-1.0, 0.0, 0.0),
    ),
    parameters=shelf_side_joint,
    parameter_name="shelf_side_joint",
    tab_port="upper_inner_tab",
    slot_port="right_end_slot",
)


# The remaining shelves slide rightward into concealed slots in the right panel.
assembly.connect(
    "connect_middle_shelf_to_right_side",
    tab_part="middle_shelf",
    slot_part="right_side_panel",
    tab_frame=InterfaceFrame(
        origin=(522.5, 0.0, 0.0),
        x_axis=(0.0, 1.0, 0.0),
        insert_axis=(1.0, 0.0, 0.0),
    ),
    slot_frame=InterfaceFrame(
        origin=(-20.0, 0.0, 150.0),
        x_axis=(0.0, 1.0, 0.0),
        insert_axis=(1.0, 0.0, 0.0),
    ),
    parameters=shelf_side_joint,
    parameter_name="shelf_side_joint",
    tab_port="right_end_tab",
    slot_port="middle_shelf_slot",
)

assembly.connect(
    "connect_lower_shelf_to_right_side",
    tab_part="lower_shelf",
    slot_part="right_side_panel",
    tab_frame=InterfaceFrame(
        origin=(522.5, 0.0, 0.0),
        x_axis=(0.0, 1.0, 0.0),
        insert_axis=(1.0, 0.0, 0.0),
    ),
    slot_frame=InterfaceFrame(
        origin=(-20.0, 0.0, -450.0),
        x_axis=(0.0, 1.0, 0.0),
        insert_axis=(1.0, 0.0, 0.0),
    ),
    parameters=shelf_side_joint,
    parameter_name="shelf_side_joint",
    tab_port="right_end_tab",
    slot_port="lower_shelf_slot",
)


# The upper rear panel leans seven degrees toward +Y as it rises.  Its connector
# frame follows the panel's tilted vertical direction while insertion remains +X.
tilted_vertical = (0.0, 0.1218693434, 0.9925461516)
assembly.connect(
    "connect_back_panel_to_right_side",
    tab_part="slanted_back_panel",
    slot_part="right_side_panel",
    tab_frame=InterfaceFrame(
        origin=(522.5, 0.0, 0.0),
        x_axis=tilted_vertical,
        insert_axis=(1.0, 0.0, 0.0),
    ),
    slot_frame=InterfaceFrame(
        origin=(-20.0, 197.0, 1669.0 - 900.0),
        x_axis=tilted_vertical,
        insert_axis=(1.0, 0.0, 0.0),
    ),
    parameters=back_panel_joint,
    parameter_name="back_panel_joint",
    tab_port="right_edge_tab",
    slot_port="upper_back_slot",
)


# Print orientations affect only exported individual pieces, never assembly pose.
assembly.set_print_orientation("left_side_panel", rotation_deg=(0.0, 90.0, 0.0))
assembly.set_print_orientation("right_side_panel", rotation_deg=(0.0, 90.0, 0.0))
assembly.set_print_orientation("slanted_back_panel", rotation_deg=(7.0, 0.0, 0.0))

scene = assembly.scene()
