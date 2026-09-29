from adsl.core import *


UPHOLSTERY_GRAY = (0.46, 0.47, 0.49)
WOOD_BROWN = (0.30, 0.16, 0.08)
METAL_PEWTER = (0.42, 0.44, 0.46)
BACKGROUND_BLACK = (0.008, 0.008, 0.010)


def ellipsoid(scale, center, color):
    form = scale_shape(
        Sphere(1.0, color=color),
        scale,
        center=(0.0, 0.0, 0.0),
    )
    return translate_shape(form, center)


class RoundedSeatBase(Asset):
    def __init__(self):
        super().__init__(label="Rounded upholstered seat base")

        width = 1.70
        depth = 1.00
        height = 0.28
        radius = 0.14
        center_z = 0.69

        pieces = [
            Cube(
                (width - 2.0 * radius, depth, height),
                center=(0.0, 0.0, center_z),
                color=UPHOLSTERY_GRAY,
            ),
            Cube(
                (width, depth - 2.0 * radius, height),
                center=(0.0, 0.0, center_z),
                color=UPHOLSTERY_GRAY,
            ),
        ]
        for x in (-width / 2.0 + radius, width / 2.0 - radius):
            for y in (-depth / 2.0 + radius, depth / 2.0 - radius):
                pieces.append(
                    Cylinder(
                        radius,
                        height=height,
                        center=(x, y, center_z),
                        axis="z",
                        color=UPHOLSTERY_GRAY,
                    )
                )

        self.rounded_shell = self.attach_part(
            "rounded_shell",
            boolean_union(*pieces),
        )


class DepressedSeatCushion(Asset):
    def __init__(self):
        super().__init__(label="Ergonomic depressed seat cushion")

        # The center pad sits below the four softly raised perimeter bolsters,
        # producing a shallow, comfortable depression without opening a hole.
        center_pad = ellipsoid(
            (0.53, 0.31, 0.075),
            (0.0, -0.03, 0.905),
            UPHOLSTERY_GRAY,
        )
        left_border = ellipsoid(
            (0.115, 0.37, 0.105),
            (-0.59, -0.03, 0.955),
            UPHOLSTERY_GRAY,
        )
        right_border = ellipsoid(
            (0.115, 0.37, 0.105),
            (0.59, -0.03, 0.955),
            UPHOLSTERY_GRAY,
        )
        front_border = ellipsoid(
            (0.56, 0.105, 0.105),
            (0.0, -0.355, 0.955),
            UPHOLSTERY_GRAY,
        )
        rear_border = ellipsoid(
            (0.55, 0.095, 0.10),
            (0.0, 0.285, 0.95),
            UPHOLSTERY_GRAY,
        )

        self.cushion_form = self.attach_part(
            "cushion_form",
            boolean_union(
                center_pad,
                left_border,
                right_border,
                front_border,
                rear_border,
            ),
        )


class CurvedBackrest(Asset):
    def __init__(self):
        super().__init__(label="Curved upholstered backrest")

        # A broad ellipsoidal center gives a smooth lumbar-to-head curve.
        central_back = ellipsoid(
            (0.72, 0.17, 0.58),
            (0.0, 0.36, 1.43),
            UPHOLSTERY_GRAY,
        )
        lower_bridge = ellipsoid(
            (0.67, 0.16, 0.24),
            (0.0, 0.36, 1.02),
            UPHOLSTERY_GRAY,
        )

        # The side wings sit slightly farther toward the front (-y), making the
        # back visibly wrap around the sitter while remaining symmetric.
        left_wing = ellipsoid(
            (0.18, 0.23, 0.46),
            (-0.66, 0.25, 1.39),
            UPHOLSTERY_GRAY,
        )
        right_wing = ellipsoid(
            (0.18, 0.23, 0.46),
            (0.66, 0.25, 1.39),
            UPHOLSTERY_GRAY,
        )

        self.back_shell = self.attach_part(
            "back_shell",
            boolean_union(central_back, lower_bridge, left_wing, right_wing),
        )


class CurvedArmrest(Asset):
    def __init__(self, side):
        super().__init__(label="Curved upholstered armrest")
        x = 0.73 if side == "right" else -0.73

        # The arm slopes gently downward toward the open front of the chair.
        arm_rail = Cylinder(
            0.13,
            p0=(x, -0.24, 1.14),
            p1=(x, 0.30, 1.26),
            color=UPHOLSTERY_GRAY,
        )
        front_cap = Sphere(
            0.13,
            center=(x, -0.24, 1.14),
            color=UPHOLSTERY_GRAY,
        )
        rear_cap = Sphere(
            0.13,
            center=(x, 0.30, 1.26),
            color=UPHOLSTERY_GRAY,
        )
        side_support = ellipsoid(
            (0.15, 0.31, 0.29),
            (x, 0.04, 1.00),
            UPHOLSTERY_GRAY,
        )

        self.arm_form = self.attach_part(
            "arm_form",
            boolean_union(arm_rail, front_cap, rear_cap, side_support),
        )


class SquareWoodenLeg(Asset):
    def __init__(self, x, y):
        super().__init__(label="Straight square wooden leg")
        self.leg = self.attach_part(
            "leg",
            Cube(
                (0.14, 0.14, 0.55),
                center=(x, y, 0.275),
                color=WOOD_BROWN,
            ),
        )


class NailheadTrim(Asset):
    def __init__(self):
        super().__init__(label="Front nailhead trim")
        stud_count = 15
        spacing = 0.082
        first_x = -spacing * (stud_count - 1) / 2.0
        studs = [
            Sphere(
                0.027,
                center=(first_x, -0.502, 0.635),
                color=METAL_PEWTER,
            )
            for _ in range(stud_count)
        ]
        self.studs = self.attach_part(
            "studs",
            distribute_along_axis(studs, axis="+x", spacing=spacing),
        )


class PresentationBackdrop(Asset):
    def __init__(self):
        super().__init__(label="Plain black presentation backdrop")
        self.ground = self.attach_part(
            "ground",
            Cube(
                (4.0, 4.0, 0.04),
                center=(0.0, 0.0, -0.02),
                color=BACKGROUND_BLACK,
            ),
        )
        self.back_wall = self.attach_part(
            "back_wall",
            Cube(
                (4.0, 0.04, 3.0),
                center=(0.0, 1.48, 1.50),
                color=BACKGROUND_BLACK,
            ),
        )


class ModernRoundedArmchair(Asset):
    def __init__(self):
        super().__init__(label="Modern rounded upholstered armchair")

        self.seat_base = self.attach_part("seat_base", RoundedSeatBase())
        self.seat_cushion = self.attach_part(
            "seat_cushion",
            DepressedSeatCushion(),
        )
        self.backrest = self.attach_part("backrest", CurvedBackrest())
        self.left_armrest = self.attach_part(
            "left_armrest",
            CurvedArmrest("left"),
        )
        self.right_armrest = self.attach_part(
            "right_armrest",
            CurvedArmrest("right"),
        )

        self.front_left_leg = self.attach_part(
            "front_left_leg",
            SquareWoodenLeg(-0.69, -0.36),
        )
        self.front_right_leg = self.attach_part(
            "front_right_leg",
            SquareWoodenLeg(0.69, -0.36),
        )
        self.rear_left_leg = self.attach_part(
            "rear_left_leg",
            SquareWoodenLeg(-0.69, 0.36),
        )
        self.rear_right_leg = self.attach_part(
            "rear_right_leg",
            SquareWoodenLeg(0.69, 0.36),
        )
        self.front_nailhead_trim = self.attach_part(
            "front_nailhead_trim",
            NailheadTrim(),
        )


class ArmchairPresentation(Asset):
    def __init__(self):
        super().__init__(label="Armchair on black background")
        self.background = self.attach_part("background", PresentationBackdrop())
        self.chair = self.attach_part("chair", ModernRoundedArmchair())


scene = ArmchairPresentation()
