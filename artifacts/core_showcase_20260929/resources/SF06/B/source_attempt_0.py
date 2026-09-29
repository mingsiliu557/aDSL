from adsl.core import *
import math


UPHOLSTERY = (0.43, 0.45, 0.47)
UPHOLSTERY_DARK = (0.38, 0.40, 0.42)
WOOD = (0.27, 0.12, 0.055)
METAL = (0.72, 0.74, 0.76)
BLACK = (0.004, 0.004, 0.005)


def ellipsoid(radii, center, color):
    form = Sphere(1.0, color=color)
    form = scale_shape(form, radii, center=(0.0, 0.0, 0.0))
    return translate_shape(form, center)


class RoundedSeatBase(Asset):
    def __init__(self):
        super().__init__(label="RoundedSeatBase")

        # The rear pair is slightly narrower, while the forward centers and
        # broad corner radii create a gently bowed, upholstered front fascia.
        front_left = ellipsoid((0.27, 0.27, 0.155), (-0.745, -0.575, 0.775), UPHOLSTERY_DARK)
        front_right = ellipsoid((0.27, 0.27, 0.155), (0.745, -0.575, 0.775), UPHOLSTERY_DARK)
        rear_left = ellipsoid((0.25, 0.25, 0.155), (-0.705, 0.455, 0.775), UPHOLSTERY_DARK)
        rear_right = ellipsoid((0.25, 0.25, 0.155), (0.705, 0.455, 0.775), UPHOLSTERY_DARK)
        body = hull(
            front_left,
            front_right,
            rear_left,
            rear_right,
            color=UPHOLSTERY_DARK,
        )
        self.body = self.attach_part("upholstered_body", body)


class DepressedSeatCushion(Asset):
    def __init__(self):
        super().__init__(label="DepressedSeatCushion")

        # Four soft lobes establish a rounded trapezoid that is wider at the
        # front and narrower toward the chair back.
        front_left = ellipsoid((0.205, 0.205, 0.135), (-0.525, -0.515, 1.055), UPHOLSTERY)
        front_right = ellipsoid((0.205, 0.205, 0.135), (0.525, -0.515, 1.055), UPHOLSTERY)
        rear_left = ellipsoid((0.195, 0.195, 0.13), (-0.475, 0.245, 1.045), UPHOLSTERY)
        rear_right = ellipsoid((0.195, 0.195, 0.13), (0.475, 0.245, 1.045), UPHOLSTERY)
        cushion_blank = hull(
            front_left,
            front_right,
            rear_left,
            rear_right,
            color=UPHOLSTERY,
        )

        # Only the underside of this broad ellipsoid intersects the cushion,
        # creating a shallow, smooth sat-in depression rather than a deep bowl.
        depression = ellipsoid((0.54, 0.43, 0.15), (0.0, -0.08, 1.245), UPHOLSTERY)
        cushion = boolean_difference(cushion_blank, depression)
        self.cushion = self.attach_part("shallow_depressed_cushion", cushion)


class CurvedBackrest(Asset):
    def __init__(self):
        super().__init__(label="CurvedBackrest")

        # Section centers describe a subtle ergonomic S-curve in side view:
        # lumbar support comes forward before the upper back leans rearward.
        lower = ellipsoid((0.82, 0.24, 0.29), (0.0, 0.47, 1.285), UPHOLSTERY)
        lumbar = ellipsoid((0.90, 0.21, 0.31), (0.0, 0.43, 1.50), UPHOLSTERY)
        upper = ellipsoid((0.84, 0.19, 0.32), (0.0, 0.545, 1.76), UPHOLSTERY)
        crown = ellipsoid((0.69, 0.17, 0.245), (0.0, 0.635, 2.005), UPHOLSTERY)

        lower_transition = hull(lower, lumbar, color=UPHOLSTERY)
        middle_transition = hull(lumbar, upper, color=UPHOLSTERY)
        upper_transition = hull(upper, crown, color=UPHOLSTERY)
        back = boolean_union(lower_transition, middle_transition, upper_transition)
        self.back = self.attach_part("ergonomic_back", back)


class CurvedArmrest(Asset):
    def __init__(self, side):
        label = "LeftArmrest" if side < 0 else "RightArmrest"
        super().__init__(label=label)
        x = 0.875 * side

        # The rail starts behind the cushion front and rises rearward into the
        # lower backrest. Matching centers and radii preserve exact symmetry.
        front = ellipsoid((0.215, 0.255, 0.17), (x, -0.245, 1.185), UPHOLSTERY)
        elbow = ellipsoid((0.225, 0.285, 0.18), (x, 0.005, 1.325), UPHOLSTERY)
        rear = ellipsoid((0.24, 0.285, 0.21), (x, 0.315, 1.43), UPHOLSTERY)
        front_sweep = hull(front, elbow, color=UPHOLSTERY)
        rear_sweep = hull(elbow, rear, color=UPHOLSTERY)
        arm = boolean_union(front_sweep, rear_sweep)
        self.rail = self.attach_part("rounded_rail", arm)


class SquareWoodenLeg(Asset):
    def __init__(self, x, y):
        super().__init__(label="SquareWoodenLeg")
        leg = Cube((0.145, 0.145, 0.625), center=(x, y, 0.3125), color=WOOD)
        self.post = self.attach_part("square_post", leg)


class NailheadTrim(Asset):
    def __init__(self):
        super().__init__(label="NailheadTrim")
        count = 15
        span = 1.50
        for index in range(count):
            x = -span / 2.0 + span * index / (count - 1)
            # A slight forward bow follows the convex front fascia.
            normalized = x / (span / 2.0)
            y = -0.828 - 0.026 * (1.0 - normalized * normalized)
            stud = Sphere(0.035, center=(x, y, 0.735), color=METAL)
            self.attach_part(f"stud_{index + 1:02d}", stud)


class PresentationBackground(Asset):
    def __init__(self):
        super().__init__(label="PresentationBackground")
        floor = Cube((5.0, 4.0, 0.03), center=(0.0, 0.0, -0.015), color=BLACK)
        rear = Cube((5.0, 0.03, 3.2), center=(0.0, 1.62, 1.60), color=BLACK)
        self.floor = self.attach_part("matte_black_floor", floor)
        self.rear = self.attach_part("matte_black_backdrop", rear)


class ModernRoundedArmchair(Asset):
    def __init__(self):
        super().__init__(label="ModernRoundedArmchair")

        self.background = self.attach_part("presentation_background", PresentationBackground())

        self.front_left_leg = self.attach_part(
            "front_left_leg", SquareWoodenLeg(-0.73, -0.515)
        )
        self.front_right_leg = self.attach_part(
            "front_right_leg", SquareWoodenLeg(0.73, -0.515)
        )
        self.rear_left_leg = self.attach_part(
            "rear_left_leg", SquareWoodenLeg(-0.69, 0.37)
        )
        self.rear_right_leg = self.attach_part(
            "rear_right_leg", SquareWoodenLeg(0.69, 0.37)
        )

        self.seat_base = self.attach_part("seat_base", RoundedSeatBase())
        self.seat_cushion = self.attach_part("seat_cushion", DepressedSeatCushion())
        self.backrest = self.attach_part("backrest", CurvedBackrest())
        self.left_armrest = self.attach_part("left_armrest", CurvedArmrest(-1))
        self.right_armrest = self.attach_part("right_armrest", CurvedArmrest(1))
        self.nailhead_trim = self.attach_part("nailhead_trim", NailheadTrim())


scene = ModernRoundedArmchair()
