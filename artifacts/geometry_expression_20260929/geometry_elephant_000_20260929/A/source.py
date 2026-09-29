from adsl.core import *


ELEPHANT_GRAY = (0.56, 0.57, 0.56)
ELEPHANT_DARK = (0.075, 0.07, 0.065)


def ellipsoid(size, center=(0.0, 0.0, 0.0), color=ELEPHANT_GRAY):
    form = Sphere(0.5, color=color)
    form = scale_shape(form, size, center=(0.0, 0.0, 0.0))
    return translate_shape(form, center)


def rounded_segment(p0, p1, radius, color=ELEPHANT_GRAY):
    segment = Asset("rounded_segment")
    segment.attach_part("shaft", Cylinder(radius, p0=p0, p1=p1, color=color))
    segment.attach_part("start_round", Sphere(radius, center=p0, color=color))
    segment.attach_part("end_round", Sphere(radius, center=p1, color=color))
    return segment


class ElephantTorso(Asset):
    def __init__(self):
        super().__init__(label="rounded_torso")
        main_mass = ellipsoid((2.20, 1.04, 1.34), center=(0.22, 0.0, 1.72))
        back_crown = ellipsoid((1.72, 0.96, 0.72), center=(0.18, 0.0, 2.04))
        rear_round = ellipsoid((0.72, 0.94, 1.12), center=(1.02, 0.0, 1.68))
        belly_softener = ellipsoid((1.72, 0.88, 0.48), center=(0.20, 0.0, 1.31))
        self.body_mass = self.attach_part(
            "body_mass",
            boolean_union(main_mass, back_crown, rear_round, belly_softener),
        )


class ElephantHead(Asset):
    def __init__(self):
        super().__init__(label="bulbous_head")
        cranium = ellipsoid((1.02, 0.90, 1.18), center=(-0.91, -0.005, 2.08))
        forehead = ellipsoid((0.70, 0.84, 0.86), center=(-1.18, -0.005, 2.20))
        cheek = ellipsoid((0.72, 0.82, 0.78), center=(-1.04, -0.005, 1.84))
        trunk_bridge = ellipsoid((0.52, 0.66, 0.66), center=(-1.25, -0.005, 1.70))
        self.head_mass = self.attach_part(
            "head_mass",
            boolean_union(cranium, forehead, cheek, trunk_bridge),
        )

        self.near_eye = self.attach_part(
            "near_eye",
            Sphere(0.037, center=(-1.305, -0.414, 2.245), color=ELEPHANT_DARK),
        )
        self.far_eye = self.attach_part(
            "far_eye",
            Sphere(0.032, center=(-1.305, 0.414, 2.245), color=ELEPHANT_DARK),
        )


class ElephantEar(Asset):
    def __init__(self, side):
        super().__init__(label="near_ear" if side < 0 else "far_ear")
        y = -0.475 if side < 0 else 0.445
        rear_shift = 0.0 if side < 0 else 0.06
        thickness = 0.135 if side < 0 else 0.115

        upper = ellipsoid((0.82, thickness, 0.72), center=(-0.58 + rear_shift, y, 2.31))
        broad = ellipsoid((0.74, thickness * 1.02, 0.76), center=(-0.48 + rear_shift, y, 2.13))
        lower = ellipsoid((0.48, thickness * 0.92, 0.62), center=(-0.39 + rear_shift, y, 1.86))
        ear_form = boolean_union(upper, broad, lower)
        ear_form = rotate_shape(ear_form, axis="+y", angle=-13.0, center=(-0.49 + rear_shift, y, 2.13))
        self.pinna = self.attach_part("pinna", ear_form)


class ElephantTrunk(Asset):
    def __init__(self):
        super().__init__(label="upward_curving_trunk")
        points = (
            (-1.255, -0.005, 1.82),
            (-1.38, -0.005, 1.55),
            (-1.43, -0.005, 1.25),
            (-1.48, -0.005, 0.96),
            (-1.61, -0.005, 0.76),
            (-1.82, -0.005, 0.66),
            (-2.04, -0.005, 0.69),
            (-2.22, -0.005, 0.84),
            (-2.27, -0.005, 1.02),
            (-2.23, -0.005, 1.14),
        )
        radii = (0.245, 0.225, 0.202, 0.174, 0.151, 0.130, 0.111, 0.094, 0.079)

        for index, radius in enumerate(radii):
            self.attach_part(
                f"trunk_section_{index + 1}",
                rounded_segment(points[index], points[index + 1], radius),
            )

        self.tip = self.attach_part(
            "raised_rounded_tip",
            ellipsoid((0.17, 0.17, 0.20), center=points[-1]),
        )


class ElephantLeg(Asset):
    def __init__(self, name, x, y, far_side=False):
        super().__init__(label=name)
        gray = (0.53, 0.54, 0.53) if far_side else ELEPHANT_GRAY
        upper_radius = 0.245 if not far_side else 0.225
        lower_radius = 0.185 if not far_side else 0.170

        upper = ellipsoid(
            (upper_radius * 2.0, upper_radius * 1.86, 0.78),
            center=(x, y, 1.12),
            color=gray,
        )
        shaft = Cylinder(
            lower_radius,
            p0=(x, y, 0.22),
            p1=(x, y, 1.12),
            color=gray,
        )
        ankle = ellipsoid(
            (lower_radius * 2.05, lower_radius * 1.94, 0.42),
            center=(x, y, 0.34),
            color=gray,
        )
        foot = ellipsoid(
            (0.47 if not far_side else 0.43, 0.42 if not far_side else 0.39, 0.30),
            center=(x - 0.025, y - (0.018 if y < 0 else -0.018), 0.15),
            color=gray,
        )
        self.leg_mass = self.attach_part(
            "integrated_leg_and_foot",
            boolean_union(upper, shaft, ankle, foot),
        )


class ElephantTail(Asset):
    def __init__(self):
        super().__init__(label="small_tail")
        points = (
            (1.28, 0.12, 1.98),
            (1.48, 0.13, 1.84),
            (1.57, 0.13, 1.65),
        )
        self.attach_part("tail_root", rounded_segment(points[0], points[1], 0.036))
        self.attach_part("tail_end", rounded_segment(points[1], points[2], 0.029))
        self.attach_part(
            "tail_tuft",
            ellipsoid((0.13, 0.10, 0.18), center=(1.58, 0.13, 1.59), color=(0.43, 0.44, 0.43)),
        )


class StylizedElephant(Asset):
    def __init__(self):
        super().__init__(label="stylized_upward_trunk_elephant")

        self.far_ear = self.attach_part("far_ear", ElephantEar(side=1))
        self.tail = self.attach_part("tail", ElephantTail())

        self.front_far_leg = self.attach_part(
            "front_far_leg",
            ElephantLeg("front_far_leg", x=-0.46, y=0.29, far_side=True),
        )
        self.rear_far_leg = self.attach_part(
            "rear_far_leg",
            ElephantLeg("rear_far_leg", x=0.84, y=0.29, far_side=True),
        )
        self.body = self.attach_part("body", ElephantTorso())
        self.head = self.attach_part("head", ElephantHead())
        self.near_ear = self.attach_part("near_ear", ElephantEar(side=-1))
        self.trunk = self.attach_part("trunk", ElephantTrunk())
        self.front_near_leg = self.attach_part(
            "front_near_leg",
            ElephantLeg("front_near_leg", x=-0.46, y=-0.31, far_side=False),
        )
        self.rear_near_leg = self.attach_part(
            "rear_near_leg",
            ElephantLeg("rear_near_leg", x=0.84, y=-0.31, far_side=False),
        )


scene = StylizedElephant()
