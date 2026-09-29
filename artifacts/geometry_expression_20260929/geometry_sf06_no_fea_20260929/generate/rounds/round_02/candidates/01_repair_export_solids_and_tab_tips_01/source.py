from adsl.core import *
import math


UPHOLSTERY = (0.43, 0.45, 0.47)
UPHOLSTERY_LIGHT = (0.48, 0.50, 0.52)
UPHOLSTERY_DARK = (0.35, 0.37, 0.39)
WOOD = (0.34, 0.17, 0.075)
NAIL = (0.72, 0.74, 0.76)


def ellipsoid(center, radii, color):
    base = Sphere(1.0, center=center, color=color)
    return scale_shape(base, radii, center=center)


def rounded_box(size, center, radius, color):
    sx, sy, sz = size
    cx, cy, cz = center
    radius = min(radius, sx / 2.0, sy / 2.0, sz / 2.0)
    dx = sx / 2.0 - radius
    dy = sy / 2.0 - radius
    dz = sz / 2.0 - radius
    corners = []
    for x_sign in (-1.0, 1.0):
        for y_sign in (-1.0, 1.0):
            for z_sign in (-1.0, 1.0):
                corners.append(
                    Sphere(
                        radius,
                        center=(cx + x_sign * dx, cy + y_sign * dy, cz + z_sign * dz),
                        color=color,
                    )
                )
    return hull(*corners, color=color)


class SeatShell(Asset):
    def __init__(self):
        super().__init__(label="seat_shell")
        core = rounded_box(
            size=(780.0, 700.0, 170.0),
            center=(0.0, -30.0, 385.0),
            radius=55.0,
            color=UPHOLSTERY,
        )
        # Build this low front roll from strongly overlapping positive-volume
        # solids rather than a single eight-corner hull.  The former hull could
        # leave zero-area triangles at coincident sampled vertices.  This union
        # keeps the same exact AABB: x +/-375, y -400..-280, z 315..460.
        front_roll_center = Cube(
            (646.0, 120.0, 145.0),
            center=(0.0, -340.0, 387.5),
            color=UPHOLSTERY,
        )
        front_roll_cross = Cube(
            (750.0, 20.0, 145.0),
            center=(0.0, -340.0, 387.5),
            color=UPHOLSTERY,
        )
        front_roll_corners = [
            Cylinder(
                52.0,
                height=145.0,
                center=(x, y, 387.5),
                axis="z",
                color=UPHOLSTERY,
            )
            for x in (-323.0, 323.0)
            for y in (-348.0, -332.0)
        ]
        front_roll = boolean_union(
            front_roll_center,
            front_roll_cross,
            *front_roll_corners,
        )
        left_transition = ellipsoid((-350.0, 95.0, 430.0), (70.0, 245.0, 90.0), UPHOLSTERY)
        right_transition = ellipsoid((350.0, 95.0, 430.0), (70.0, 245.0, 90.0), UPHOLSTERY)
        self.body = self.attach_part(
            "rounded_structural_shell",
            boolean_union(core, front_roll, left_transition, right_transition),
        )


class SeatCushion(Asset):
    def __init__(self):
        super().__init__(label="seat_cushion")
        cushion_blank = rounded_box(
            size=(670.0, 550.0, 135.0),
            center=(0.0, -45.0, 502.5),
            radius=62.0,
            color=UPHOLSTERY_LIGHT,
        )
        depression = ellipsoid(
            (0.0, -5.0, 604.0),
            (255.0, 195.0, 78.0),
            UPHOLSTERY_LIGHT,
        )
        self.cushion = self.attach_part(
            "depressed_cushion",
            boolean_difference(cushion_blank, depression),
        )


class ErgonomicBackrest(Asset):
    def __init__(self):
        super().__init__(label="backrest")
        lower = ellipsoid((0.0, 268.0, 535.0), (350.0, 92.0, 125.0), UPHOLSTERY)
        lumbar = ellipsoid((0.0, 250.0, 635.0), (345.0, 82.0, 150.0), UPHOLSTERY)
        shoulder = ellipsoid((0.0, 282.0, 735.0), (325.0, 105.0, 155.0), UPHOLSTERY)
        crown = ellipsoid((0.0, 300.0, 800.0), (285.0, 100.0, 100.0), UPHOLSTERY)
        lower_blend = hull(lower, lumbar, color=UPHOLSTERY)
        upper_blend = hull(lumbar, shoulder, crown, color=UPHOLSTERY)
        self.pad = self.attach_part(
            "curved_support_pad",
            boolean_union(lower_blend, upper_blend),
        )


class CurvedArmrest(Asset):
    def __init__(self, side):
        super().__init__(label="left_armrest" if side < 0 else "right_armrest")
        x_outer = 365.0 * side
        x_inner = 340.0 * side
        main_bolster = ellipsoid((x_outer, 25.0, 555.0), (85.0, 245.0, 92.0), UPHOLSTERY)
        front_drop = ellipsoid((x_outer, -190.0, 500.0), (85.0, 82.0, 92.0), UPHOLSTERY)
        rear_blend = ellipsoid((x_inner, 230.0, 555.0), (95.0, 105.0, 115.0), UPHOLSTERY)
        arm_form = hull(main_bolster, front_drop, rear_blend, color=UPHOLSTERY)
        self.bolster = self.attach_part("curved_elbow_bolster", arm_form)


class FrontApron(Asset):
    def __init__(self):
        super().__init__(label="front_apron")
        apron = rounded_box(
            size=(760.0, 95.0, 150.0),
            center=(0.0, -342.5, 365.0),
            radius=42.0,
            color=UPHOLSTERY_DARK,
        )
        self.band = self.attach_part("rounded_front_band", apron)


class MountingDeck(Asset):
    def __init__(self):
        super().__init__(label="underseat_mounting_deck")
        deck = rounded_box(
            size=(735.0, 590.0, 64.0),
            center=(0.0, 0.0, 332.0),
            radius=24.0,
            color=UPHOLSTERY_DARK,
        )
        blocks = [deck]
        for x in (-330.0, 330.0):
            for y in (-255.0, 255.0):
                blocks.append(
                    rounded_box(
                        size=(104.0, 104.0, 82.0),
                        center=(x, y, 341.0),
                        radius=16.0,
                        color=UPHOLSTERY_DARK,
                    )
                )
        self.deck = self.attach_part("reinforced_receiver_deck", boolean_union(*blocks))


class NailheadTrim(Asset):
    def __init__(self):
        super().__init__(label="nailhead_trim")
        nailheads = Asset("centered_nailhead_row")
        count = 13
        x_start = -285.0
        pitch = 570.0 / (count - 1)
        for index in range(count):
            x = x_start + index * pitch
            cap = Sphere(9.0, center=(x, -391.0, 338.0), color=NAIL)
            nailheads.attach_part(f"nailhead_{index + 1:02d}", cap)
        self.row = self.attach_part("front_row", nailheads)


class ChairBody(Asset):
    def __init__(self):
        super().__init__(label="chair_body")
        self.seat_shell = self.attach_part("seat_shell", SeatShell())
        self.underseat_mounting_deck = self.attach_part("underseat_mounting_deck", MountingDeck())
        self.front_apron = self.attach_part("front_apron", FrontApron())
        self.seat_cushion = self.attach_part("seat_cushion", SeatCushion())
        self.backrest = self.attach_part("backrest", ErgonomicBackrest())
        self.left_armrest = self.attach_part("left_armrest", CurvedArmrest(-1))
        self.right_armrest = self.attach_part("right_armrest", CurvedArmrest(1))
        self.nailhead_trim = self.attach_part("nailhead_trim", NailheadTrim())


class SquareWoodenLeg(Asset):
    def __init__(self, label):
        super().__init__(label=label)
        shaft = Cube((70.0, 70.0, 300.0), center=(0.0, 0.0, 150.0), color=WOOD)
        shoulder = Cube((78.0, 78.0, 24.0), center=(0.0, 0.0, 288.0), color=WOOD)
        self.shaft = self.attach_part("square_shaft", boolean_union(shaft, shoulder))


chair_body = ChairBody()
front_left_leg = SquareWoodenLeg("front_left_leg")
front_right_leg = SquareWoodenLeg("front_right_leg")
rear_left_leg = SquareWoodenLeg("rear_left_leg")
rear_right_leg = SquareWoodenLeg("rear_right_leg")

assembly = FixedAssembly(root_id="chair_body", mm_per_unit=1.0)
assembly.add_part(
    "chair_body",
    chair_body,
    components=(
        "seat_shell",
        "seat_cushion",
        "backrest",
        "left_armrest",
        "right_armrest",
        "front_apron",
        "underseat_mounting_deck",
        "nailhead_trim",
    ),
)
assembly.add_part("leg_front_left", front_left_leg, components=("front_left_leg",))
assembly.add_part("leg_front_right", front_right_leg, components=("front_right_leg",))
assembly.add_part("leg_rear_left", rear_left_leg, components=("rear_left_leg",))
assembly.add_part("leg_rear_right", rear_right_leg, components=("rear_right_leg",))

shared_leg_mount = TabSlot(
    width_mm=38.0,
    thickness_mm=38.0,
    insertion_mm=30.0,
    slot_depth_mm=34.0,
    fit_offset_mm=0.2,
    root_overlap_mm=7.0,
    opening_extension_mm=1.0,
    lead_in_mm=0.0,
)

leg_tab_frame = InterfaceFrame(
    origin=(0.0, 0.0, 300.0),
    x_axis=(1.0, 0.0, 0.0),
    insert_axis=(0.0, 0.0, 1.0),
)

assembly.connect(
    "mount_front_left_leg",
    tab_part="leg_front_left",
    slot_part="chair_body",
    tab_frame=leg_tab_frame,
    slot_frame=InterfaceFrame(origin=(-330.0, -255.0, 300.0), x_axis=(1.0, 0.0, 0.0), insert_axis=(0.0, 0.0, 1.0)),
    parameters=shared_leg_mount,
    parameter_name="shared_leg_mount",
    tab_port="top_mount_tab",
    slot_port="underside_front_left_slot",
)
assembly.connect(
    "mount_front_right_leg",
    tab_part="leg_front_right",
    slot_part="chair_body",
    tab_frame=leg_tab_frame,
    slot_frame=InterfaceFrame(origin=(330.0, -255.0, 300.0), x_axis=(1.0, 0.0, 0.0), insert_axis=(0.0, 0.0, 1.0)),
    parameters=shared_leg_mount,
    parameter_name="shared_leg_mount",
    tab_port="top_mount_tab",
    slot_port="underside_front_right_slot",
)
assembly.connect(
    "mount_rear_left_leg",
    tab_part="leg_rear_left",
    slot_part="chair_body",
    tab_frame=leg_tab_frame,
    slot_frame=InterfaceFrame(origin=(-330.0, 255.0, 300.0), x_axis=(1.0, 0.0, 0.0), insert_axis=(0.0, 0.0, 1.0)),
    parameters=shared_leg_mount,
    parameter_name="shared_leg_mount",
    tab_port="top_mount_tab",
    slot_port="underside_rear_left_slot",
)
assembly.connect(
    "mount_rear_right_leg",
    tab_part="leg_rear_right",
    slot_part="chair_body",
    tab_frame=leg_tab_frame,
    slot_frame=InterfaceFrame(origin=(330.0, 255.0, 300.0), x_axis=(1.0, 0.0, 0.0), insert_axis=(0.0, 0.0, 1.0)),
    parameters=shared_leg_mount,
    parameter_name="shared_leg_mount",
    tab_port="top_mount_tab",
    slot_port="underside_rear_right_slot",
)

scene = assembly.scene()
