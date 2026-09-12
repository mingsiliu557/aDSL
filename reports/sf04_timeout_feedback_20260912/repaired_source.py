from adsl.core import *


RED = (0.92, 0.025, 0.035)
RED_DARK = (0.72, 0.012, 0.02)
FRAME = (0.055, 0.065, 0.075)
BLACK = (0.002, 0.002, 0.003)


def rounded_block(label, size, center, color, radius=0.06, *, staged_union=False):
    """A compact rounded rectangular solid assembled from smooth primitives."""
    sx, sy, sz = size
    r = min(radius, sx / 2.0 - 0.001, sy / 2.0 - 0.001, sz / 2.0 - 0.001)
    cx, cy, cz = center
    pieces = [
        Cube((sx - 2*r, sy, sz), center=center, color=color),
        Cube((sx, sy - 2*r, sz), center=center, color=color),
        Cube((sx, sy, sz - 2*r), center=center, color=color),
    ]
    # Rounded longitudinal edges, followed by spherical corner caps.
    for axis, offset in (
        ("x", (0, sy/2-r, sz/2-r)),
        ("x", (0, -sy/2+r, sz/2-r)),
        ("x", (0, sy/2-r, -sz/2+r)),
        ("x", (0, -sy/2+r, -sz/2+r)),
    ):
        p = (cx + offset[0], cy + offset[1], cz + offset[2])
        pieces.append(Cylinder(r, height=sx-2*r, center=p, axis=axis, color=color))
    for axis, offset, length in (
        ("y", (sx/2-r, 0, sz/2-r), sy-2*r),
        ("y", (-sx/2+r, 0, sz/2-r), sy-2*r),
        ("y", (sx/2-r, 0, -sz/2+r), sy-2*r),
        ("y", (-sx/2+r, 0, -sz/2+r), sy-2*r),
        ("z", (sx/2-r, sy/2-r, 0), sz-2*r),
        ("z", (-sx/2+r, sy/2-r, 0), sz-2*r),
        ("z", (sx/2-r, -sy/2+r, 0), sz-2*r),
        ("z", (-sx/2+r, -sy/2+r, 0), sz-2*r),
    ):
        p = (cx + offset[0], cy + offset[1], cz + offset[2])
        pieces.append(Cylinder(r, height=length, center=p, axis=axis, color=color))
    for dx in (-sx/2+r, sx/2-r):
        for dy in (-sy/2+r, sy/2-r):
            for dz in (-sz/2+r, sz/2-r):
                pieces.append(Sphere(r, center=(cx+dx, cy+dy, cz+dz), color=color))
    if staged_union:
        # SF04 seat apron only: identical operands, bounded-tested association.
        result = boolean_union(*pieces[:3])
        result = boolean_union(result, *pieces[3:15])
        result = boolean_union(result, *pieces[15:])
    else:
        result = boolean_union(*pieces)
    result.label = label
    return result


class TaperedLeg(Asset):
    def __init__(self, x, y):
        super().__init__(label="TaperedLeg")
        # Two subtly different diameters create a clearly tapered, sturdy leg.
        upper = Cylinder(0.075, height=0.45, center=(x, y, 0.48), axis="z", color=FRAME)
        lower = Cylinder(0.055, height=0.43, center=(x, y, 0.075), axis="z", color=FRAME)
        self.upper = self.attach_part("upper_support", upper)
        self.lower = self.attach_part("tapered_lower", lower)
        self.foot = self.attach_part(
            "rubber_foot", Cylinder(0.062, height=0.035, center=(x, y, 0.025), axis="z", color=(0.018, 0.02, 0.022))
        )


class StructuralFrame(Asset):
    def __init__(self):
        super().__init__(label="StructuralFrame")
        self.apron = self.attach_part(
            "seat_apron", rounded_block("MatteSeatFrame", (1.62, 0.88, 0.22), (0, -0.02, 0.70), FRAME, 0.055, staged_union=True)
        )
        self.front_rail = self.attach_part(
            "front_rail", rounded_block("FrontRail", (1.48, 0.13, 0.24), (0, -0.47, 0.75), FRAME, 0.04)
        )
        self.back_rail = self.attach_part(
            "back_rail", rounded_block("BackRail", (1.48, 0.13, 0.24), (0, 0.39, 0.75), FRAME, 0.04)
        )
        for i, (x, y) in enumerate(((-0.62, -0.30), (0.62, -0.30), (-0.62, 0.27), (0.62, 0.27)), 1):
            self.attach_part("leg_%d" % i, TaperedLeg(x, y))


class UpholsteredSeat(Asset):
    def __init__(self):
        super().__init__(label="UpholsteredSeat")
        self.cushion = self.attach_part(
            "smooth_red_cushion",
            rounded_block("SeatCushion", (1.36, 0.78, 0.30), (0, -0.07, 0.94), RED, 0.085),
        )


class AsymmetricalBackrest(Asset):
    def __init__(self):
        super().__init__(label="AsymmetricalBackrest")
        # Separate upholstered halves make the intentional unequal recline legible.
        left = rounded_block("LeftBackPanel", (0.70, 0.20, 1.18), (-0.355, 0.34, 1.58), RED, 0.075)
        right = rounded_block("RightBackPanel", (0.70, 0.20, 1.18), (0.355, 0.34, 1.58), RED, 0.075)
        self.left_panel = self.attach_part("left_less_reclined", rotate_shape(left, axis="+x", angle=-8, center=(-0.355, 0.34, 1.58)))
        self.right_panel = self.attach_part("right_more_reclined", rotate_shape(right, axis="+x", angle=-19, center=(0.355, 0.34, 1.58)))
        self.top_bridge = self.attach_part(
            "soft_top_bridge", rounded_block("BackTop", (1.22, 0.18, 0.12), (0, 0.30, 2.17), RED, 0.045)
        )


class Armrest(Asset):
    def __init__(self, side, x):
        super().__init__(label=side.capitalize() + "Armrest")
        self.pad = self.attach_part(
            "upholstered_pad",
            rounded_block(side.capitalize()+"ArmPad", (0.24, 0.88, 0.66), (x, -0.03, 1.25), RED, 0.075),
        )
        self.support = self.attach_part(
            "matte_side_support",
            rounded_block(side.capitalize()+"SideFrame", (0.27, 0.80, 0.54), (x, -0.03, 0.88), FRAME, 0.045),
        )


class ModernAsymmetricalArmchair(Asset):
    def __init__(self):
        super().__init__(label="Modern Asymmetrical Red Armchair")
        self.frame = self.attach_part("structural_frame", StructuralFrame())
        self.seat = self.attach_part("upholstered_seat", UpholsteredSeat())
        self.backrest = self.attach_part("asymmetrical_backrest", AsymmetricalBackrest())
        self.left_armrest = self.attach_part("left_armrest", Armrest("left", -0.81))
        self.right_armrest = self.attach_part("right_armrest", Armrest("right", 0.81))
        # A discreet dark plinth reinforces stable ground contact without distracting from upholstery.
        self.plinth = self.attach_part("low_base", rounded_block("LowBase", (1.42, 0.72, 0.10), (0, -0.01, 0.23), FRAME, 0.035))


class BlackDisplay(Asset):
    def __init__(self):
        super().__init__(label="BlackDisplayBackground")
        self.backdrop = self.attach_part("uniform_backdrop", Cube((6.0, 0.08, 4.5), center=(0, 1.35, 1.65), color=BLACK))
        self.floor = self.attach_part("black_floor", Cube((6.0, 4.0, 0.06), center=(0, 0.25, -0.035), color=BLACK))


scene = Asset("IsolatedArmchairDisplay")
scene.attach_part("display_background", BlackDisplay())
scene.attach_part("chair_body", ModernAsymmetricalArmchair())
