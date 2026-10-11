from adsl.core import *
import math

# Faceted low-poly profiles are deliberately kept coarse to preserve the angular
# planes of the reference animal.
def ellipse_profile(a, b, count=8, offset_u=0.0, offset_v=0.0):
    return Polygon([
        (offset_u + a * math.cos(2.0 * math.pi * i / count),
         offset_v + b * math.sin(2.0 * math.pi * i / count))
        for i in range(count)
    ])


def lofted_x(sections, positions, count=8, color=(0.42, 0.48, 0.43)):
    # The elk faces toward -x, so authored sections are often listed from
    # shoulder toward muzzle.  loft requires increasing axis coordinates;
    # reorder the paired sections without changing their correspondence.
    ordered = sorted(zip(positions, sections), key=lambda item: item[0])
    ordered_positions = [item[0] for item in ordered]
    ordered_sections = [item[1] for item in ordered]
    profiles = [ellipse_profile(w, h, count, 0.0, cz)
                for w, h, cz in ordered_sections]
    return loft(profiles, ordered_positions, axis="x", interpolation="linear",
                samples_per_span=2, color=color)


def lofted_z(sections, positions, count=6, color=(0.34, 0.40, 0.35)):
    profiles = [ellipse_profile(a, b, count, cx, cy)
                for a, b, cx, cy in sections]
    return loft(profiles, positions, axis="z", interpolation="linear",
                samples_per_span=1, color=color)


def beam(p0, p1, radius, color=(0.28, 0.33, 0.29)):
    # Cylinders retain a crisp faceted silhouette while allowing the open spaces
    # between antler tines to remain completely visible.
    return Cylinder(radius, p0=p0, p1=p1, color=color)


class Torso(Asset):
    def __init__(self):
        super().__init__("torso")
        body = lofted_x(
            [(20.0, 22.0, 56.0), (28.0, 29.0, 57.0),
             (30.0, 28.0, 56.0), (22.0, 23.0, 57.0)],
            [-35.0, -18.0, 20.0, 48.0], count=8,
            color=(0.40, 0.46, 0.41),
        )
        self.attach_part("body_mass", body)


class Neck(Asset):
    def __init__(self):
        super().__init__("neck")
        neck = lofted_x(
            [(19.0, 25.0, 70.0), (15.0, 25.0, 79.0),
             (12.0, 20.0, 91.0), (11.0, 16.0, 101.0)],
            [-36.0, -42.0, -45.0, -47.0], count=7,
            color=(0.38, 0.44, 0.39),
        )
        self.attach_part("neck_mass", neck)


class Head(Asset):
    def __init__(self):
        super().__init__("head")
        skull = lofted_x(
            [(13.0, 14.0, 103.0), (12.0, 13.0, 104.0),
             (9.0, 10.0, 103.0)],
            [-48.0, -57.0, -64.0], count=7,
            color=(0.39, 0.45, 0.40),
        )
        self.attach_part("skull", skull)


class Muzzle(Asset):
    def __init__(self):
        super().__init__("muzzle")
        muzzle = lofted_x(
            [(8.0, 7.0, 101.0), (8.5, 6.0, 99.5),
             (6.5, 5.0, 98.5)],
            [-61.0, -67.0, -70.959227], count=6,
            color=(0.30, 0.35, 0.31),
        )
        self.attach_part("nose", muzzle)


class Ears(Asset):
    def __init__(self):
        super().__init__("ears")
        for side, name in ((-1, "ear_left"), (1, "ear_right")):
            # A thin triangular prism, splayed away from the antler bases.
            y0 = side * 8.0
            ear_profile = Polygon([
                (-51.0, y0), (-43.0, y0 + side * 2.0),
                (-47.0, y0 + side * 11.0), (-55.0, y0 + side * 5.0)
            ])
            ear = linear_extrude(ear_profile, 7.0, scale_top=(0.42, 0.42),
                                 color=(0.34, 0.40, 0.35))
            ear = translate_shape(ear, (0.0, 0.0, 105.0))
            self.attach_part(name, ear)


class Legs(Asset):
    def __init__(self):
        super().__init__("legs")
        # Front legs are nearly straight and slender.
        for side, y in ((-1, -14.0), (1, 14.0)):
            front = lofted_z(
                [(5.0, 5.5, -26.0, y), (4.2, 4.8, -25.0, y),
                 (3.3, 3.7, -24.0, y)],
                [5.0, 34.0, 55.0], count=6)
            self.attach_part("front_leg_" + ("left" if side < 0 else "right"), front)
        # Rear legs are made from two angled, overlapping faceted members to
        # produce the characteristic hock rather than a straight column.
        for side, y in ((-1, -14.0), (1, 14.0)):
            upper0 = (29.0, y, 54.0)
            upper1 = (34.0, y, 25.0)
            lower1 = (27.0, y, 5.0)
            rear = hull(
                beam(upper0, upper1, 4.8),
                beam(upper1, lower1, 3.5),
                color=(0.34, 0.40, 0.35),
            )
            self.attach_part("rear_leg_" + ("left" if side < 0 else "right"), rear)


class Hooves(Asset):
    def __init__(self):
        super().__init__("hooves")
        positions = [(-26.0, -14.0), (-26.0, 14.0),
                     (27.0, -14.0), (27.0, 14.0)]
        for i, (x, y) in enumerate(positions, 1):
            # Broad, forward-pointing wedge: the elk faces -x, so the toe is
            # the negative-x edge.  Keep each hoof centered below its matching
            # shaft; all four extrusions share the same z=0 contact plane.
            hoof_profile = Polygon([
                (x - 6.0, y - 3.5), (x + 4.0, y - 4.0),
                (x + 5.0, y + 3.0), (x - 4.0, y + 4.0)
            ])
            hoof = linear_extrude(hoof_profile, 7.0, scale_top=(0.72, 0.72),
                                  color=(0.20, 0.24, 0.21))
            self.attach_part("hoof_%d" % i, hoof)


class Antlers(Asset):
    def __init__(self):
        super().__init__("antlers")
        dark = (0.25, 0.30, 0.26)
        for side, label in ((-1, "left"), (1, "right")):
            s = float(side)
            pieces = []
            # Main beam rises and sweeps outward in y.
            path = [(-43.0, s * 9.0, 108.0), (-43.0, s * 23.0, 123.0),
                    (-41.0, s * 39.0, 136.0), (-38.0, s * 53.0, 143.0),
                    (-35.0, s * 66.0, 146.0)]
            radii = [4.8, 4.5, 4.0, 3.2]
            for i in range(len(path) - 1):
                pieces.append(beam(path[i], path[i + 1], radii[i], dark))
            # Separated tines, each leaving a deliberate window to the beam.
            tines = [
                ((-43.0, s * 22.0, 123.0), (-48.0, s * 28.0, 143.0)),
                ((-41.0, s * 37.0, 135.0), (-48.0, s * 42.0, 150.0)),
                ((-38.0, s * 51.0, 142.0), (-46.0, s * 55.0, 149.0)),
                ((-35.0, s * 64.0, 146.0), (-39.0, s * 68.0, 150.0)),
            ]
            for p0, p1 in tines:
                pieces.append(beam(p0, p1, 2.7, dark))
            branch_group = Asset(label + "_antler_structure")
            for i, piece in enumerate(pieces):
                branch_group.attach_part("segment_%02d" % i, piece)
            self.attach_part(label, branch_group)


class ElkBody(Asset):
    def __init__(self):
        super().__init__("elk_body")
        self.attach_part("torso", Torso())
        self.attach_part("neck", Neck())
        self.attach_part("head", Head())
        self.attach_part("muzzle", Muzzle())
        self.attach_part("ears", Ears())
        self.attach_part("antlers", Antlers())
        legs = Legs()
        self.attach_part("front_legs", legs.copy())
        # Keep rear and front semantic groups explicit while reusing the same
        # constructed leg geometry is not appropriate; split the child names.
        self.detach_part("front_legs")
        front = Asset("front_legs")
        rear = Asset("rear_legs")
        for name in ("front_leg_left", "front_leg_right"):
            front.attach_part(name, legs.children[name])
        for name in ("rear_leg_left", "rear_leg_right"):
            rear.attach_part(name, legs.children[name])
        self.attach_part("front_legs", front)
        self.attach_part("rear_legs", rear)
        self.attach_part("hooves", Hooves())
        self.attach_part("tail", self._make_tail())

    def _make_tail(self):
        tail = Asset("tail")
        tail.attach_part("tail_beam", beam((46.0, 0.0, 67.0), (61.0, 0.0, 73.0), 4.2,
                                            (0.32, 0.38, 0.33)))
        tail.attach_part("tail_tip", beam((60.0, 0.0, 73.0), (70.959227, 0.0, 78.0), 3.0,
                                           (0.25, 0.30, 0.26)))
        return tail


body = ElkBody()
# Normalize the evaluated assembled-body envelope to the frozen target dimensions.
# The lower AABB corner is the scale center, so the hooves retain z=0 while
# the measured extrema (including cylinder radii) define the final envelope.
target_size = (141.91845400702914, 141.12070655927337, 150.0)
measured_size = shape_size(body)
body_min = shape_min(body)
body = scale_shape(
    body,
    tuple(target_size[i] / measured_size[i] for i in range(3)),
    center=body_min,
)
assembly = FixedAssembly(root_id="elk_body", mm_per_unit=1.0)
assembly.add_part(
    "elk_body",
    body,
    components=("torso", "neck", "head", "muzzle", "ears", "antlers",
                 "front_legs", "rear_legs", "hooves", "tail"),
)
scene = assembly.scene()
