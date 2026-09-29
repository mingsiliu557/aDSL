from adsl.core import *


WOOD = (0.50, 0.20, 0.085)
WOOD_LIGHT = (0.55, 0.23, 0.105)
WOOD_DARK = (0.45, 0.17, 0.065)
WOOD_MID = (0.48, 0.19, 0.078)


class SidePanel(Asset):
    def __init__(self, component_name, x_center, side):
        super().__init__(label=component_name)

        self.panel_core = self.attach_part(
            "panel_core",
            Cube((6.0, 32.0, 200.0), center=(x_center, 0.0, 100.0), color=WOOD),
        )

        # Broad, low-contrast inlaid bands read as continuous vertical grain
        # without changing the panel envelope or resembling perforations.
        outer_x = -49.95 if side == "left" else 49.95
        vertical_grain = Asset("vertical_wood_grain")
        outer_grain_specs = (
            (-11.8, 1.15, WOOD_DARK),
            (-4.1, 0.85, WOOD_LIGHT),
            (4.3, 1.05, WOOD_MID),
            (11.7, 0.90, WOOD_LIGHT),
        )
        for index, (y_pos, width_y, color) in enumerate(outer_grain_specs, 1):
            vertical_grain.attach_part(
                f"outer_grain_{index}",
                Cube(
                    (0.10, width_y, 190.0),
                    center=(outer_x, y_pos, 100.0),
                    color=color,
                ),
            )

        front_y = -15.95
        front_grain_specs = (
            (-1.95, 0.80, WOOD_DARK),
            (-0.65, 0.95, WOOD_LIGHT),
            (0.75, 0.85, WOOD_MID),
            (2.00, 0.75, WOOD_LIGHT),
        )
        for index, (local_x, width_x, color) in enumerate(front_grain_specs, 1):
            vertical_grain.attach_part(
                f"front_edge_grain_{index}",
                Cube(
                    (width_x, 0.10, 190.0),
                    center=(x_center + local_x, front_y, 100.0),
                    color=color,
                ),
            )
        self.vertical_grain = self.attach_part("vertical_grain", vertical_grain)


class HorizontalMember(Asset):
    def __init__(self, component_name, z_center, is_top_cap=False):
        super().__init__(label=component_name)

        self.board_core = self.attach_part(
            "board_core",
            Cube((88.0, 32.0, 6.0), center=(0.0, 0.0, z_center), color=WOOD),
        )

        # Keep every decorative band well inside the x-end connector regions.
        # The shallow overlap exposes color on the face while preserving the
        # exact 88 x 32 x 6 mm board envelope.
        lengthwise_grain = Asset("lengthwise_wood_grain")
        top_grain_specs = (
            (-11.2, 1.15, WOOD_DARK),
            (-4.0, 0.85, WOOD_LIGHT),
            (4.2, 1.05, WOOD_MID),
            (11.1, 0.90, WOOD_LIGHT),
        )
        for index, (y_pos, width_y, color) in enumerate(top_grain_specs, 1):
            lengthwise_grain.attach_part(
                f"top_grain_{index}",
                Cube(
                    (74.0, width_y, 0.10),
                    center=(0.0, y_pos, z_center + 2.95),
                    color=color,
                ),
            )

        front_y = -15.95
        front_grain_specs = (
            (-1.65, 0.75, WOOD_DARK),
            (0.0, 0.95, WOOD_LIGHT),
            (1.65, 0.75, WOOD_MID),
        )
        for index, (z_offset, height_z, color) in enumerate(front_grain_specs, 1):
            lengthwise_grain.attach_part(
                f"front_edge_grain_{index}",
                Cube(
                    (74.0, 0.10, height_z),
                    center=(0.0, front_y, z_center + z_offset),
                    color=color,
                ),
            )
        self.lengthwise_grain = self.attach_part("lengthwise_grain", lengthwise_grain)


class BookshelfParts(Asset):
    def __init__(self):
        super().__init__(label="TallFiveShelfBookshelfParts")
        self.left_side_panel = self.attach_part(
            "left_side_panel",
            SidePanel("left_side_panel", -47.0, "left"),
        )
        self.right_side_panel = self.attach_part(
            "right_side_panel",
            SidePanel("right_side_panel", 47.0, "right"),
        )
        self.shelf_1_bottom = self.attach_part(
            "shelf_1_bottom",
            HorizontalMember("shelf_1_bottom", 3.0),
        )
        self.shelf_2 = self.attach_part(
            "shelf_2",
            HorizontalMember("shelf_2", 41.8),
        )
        self.shelf_3 = self.attach_part(
            "shelf_3",
            HorizontalMember("shelf_3", 80.6),
        )
        self.shelf_4 = self.attach_part(
            "shelf_4",
            HorizontalMember("shelf_4", 119.4),
        )
        self.shelf_5 = self.attach_part(
            "shelf_5",
            HorizontalMember("shelf_5", 158.2),
        )
        self.top_cap = self.attach_part(
            "top_cap",
            HorizontalMember("top_cap", 197.0, is_top_cap=True),
        )


parts = BookshelfParts()

assembly = FixedAssembly(root_id="side_left", mm_per_unit=1.0)
assembly.add_part("side_left", parts.left_side_panel, components=("left_side_panel",))
assembly.add_part("side_right", parts.right_side_panel, components=("right_side_panel",))
assembly.add_part("shelf_bottom", parts.shelf_1_bottom, components=("shelf_1_bottom",))
assembly.add_part("shelf_level_2", parts.shelf_2, components=("shelf_2",))
assembly.add_part("shelf_level_3", parts.shelf_3, components=("shelf_3",))
assembly.add_part("shelf_level_4", parts.shelf_4, components=("shelf_4",))
assembly.add_part("shelf_level_5", parts.shelf_5, components=("shelf_5",))
assembly.add_part("cap_top", parts.top_cap, components=("top_cap",))

shelf_side_tabslot = TabSlot(
    width_mm=10.0,
    thickness_mm=3.5,
    insertion_mm=4.0,
    slot_depth_mm=4.5,
    fit_offset_mm=0.2,
    root_overlap_mm=0.75,
    opening_extension_mm=0.5,
    lead_in_mm=0.35,
)


def shelf_frame(x_position, y_position, z_position, direction):
    return InterfaceFrame(
        origin=(x_position, y_position, z_position),
        x_axis=(0.0, 1.0, 0.0),
        insert_axis=direction,
    )


def connect_interface(connection_id, tab_part, slot_part, tab_port, slot_port, frame):
    assembly.connect(
        connection_id,
        tab_part=tab_part,
        slot_part=slot_part,
        tab_frame=frame,
        slot_frame=frame,
        parameters=shelf_side_tabslot,
        parameter_name="shelf_side_tabslot",
        tab_port=tab_port,
        slot_port=slot_port,
    )


# Bottom shelf: first left interface places the shelf; first right interface places the right side.
connect_interface(
    "s1_left_front", "shelf_bottom", "side_left",
    "left_front_tab", "shelf_1_front_slot",
    shelf_frame(-44.0, -10.0, 3.0, (-1.0, 0.0, 0.0)),
)
connect_interface(
    "s1_left_rear", "shelf_bottom", "side_left",
    "left_rear_tab", "shelf_1_rear_slot",
    shelf_frame(-44.0, 10.0, 3.0, (-1.0, 0.0, 0.0)),
)
connect_interface(
    "s1_right_front", "shelf_bottom", "side_right",
    "right_front_tab", "shelf_1_front_slot",
    shelf_frame(44.0, -10.0, 3.0, (1.0, 0.0, 0.0)),
)
connect_interface(
    "s1_right_rear", "shelf_bottom", "side_right",
    "right_rear_tab", "shelf_1_rear_slot",
    shelf_frame(44.0, 10.0, 3.0, (1.0, 0.0, 0.0)),
)

# Remaining shelves and the cap are each placed from their first left-side interface.
for shelf_index, part_id, z_position in (
    (2, "shelf_level_2", 41.8),
    (3, "shelf_level_3", 80.6),
    (4, "shelf_level_4", 119.4),
    (5, "shelf_level_5", 158.2),
):
    connect_interface(
        f"s{shelf_index}_left_front", part_id, "side_left",
        "left_front_tab", f"shelf_{shelf_index}_front_slot",
        shelf_frame(-44.0, -10.0, z_position, (-1.0, 0.0, 0.0)),
    )
    connect_interface(
        f"s{shelf_index}_left_rear", part_id, "side_left",
        "left_rear_tab", f"shelf_{shelf_index}_rear_slot",
        shelf_frame(-44.0, 10.0, z_position, (-1.0, 0.0, 0.0)),
    )
    connect_interface(
        f"s{shelf_index}_right_front", part_id, "side_right",
        "right_front_tab", f"shelf_{shelf_index}_front_slot",
        shelf_frame(44.0, -10.0, z_position, (1.0, 0.0, 0.0)),
    )
    connect_interface(
        f"s{shelf_index}_right_rear", part_id, "side_right",
        "right_rear_tab", f"shelf_{shelf_index}_rear_slot",
        shelf_frame(44.0, 10.0, z_position, (1.0, 0.0, 0.0)),
    )

connect_interface(
    "cap_left_front", "cap_top", "side_left",
    "left_front_tab", "top_cap_front_slot",
    shelf_frame(-44.0, -10.0, 197.0, (-1.0, 0.0, 0.0)),
)
connect_interface(
    "cap_left_rear", "cap_top", "side_left",
    "left_rear_tab", "top_cap_rear_slot",
    shelf_frame(-44.0, 10.0, 197.0, (-1.0, 0.0, 0.0)),
)
connect_interface(
    "cap_right_front", "cap_top", "side_right",
    "right_front_tab", "top_cap_front_slot",
    shelf_frame(44.0, -10.0, 197.0, (1.0, 0.0, 0.0)),
)
connect_interface(
    "cap_right_rear", "cap_top", "side_right",
    "right_rear_tab", "top_cap_rear_slot",
    shelf_frame(44.0, 10.0, 197.0, (1.0, 0.0, 0.0)),
)

scene = assembly.scene()
