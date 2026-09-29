from adsl.core import *


DEEP_RED = (0.48, 0.025, 0.045)
DARK_WOOD = (0.105, 0.075, 0.065)
CHARCOAL = (0.055, 0.060, 0.065)


class CurvedShelf(Asset):
    """A D-shaped shelf with a straight rear edge and a bowed front toward -y."""

    def __init__(self, width, depth, thickness, center_z, label):
        super().__init__(label=label)

        rear_half = Cube(
            (width, depth / 2.0, thickness),
            center=(0.0, depth / 4.0, center_z),
            color=DEEP_RED,
        )
        bowed_disc = Cylinder(
            radius=0.5,
            height=thickness,
            center=(0.0, 0.0, center_z),
            axis="z",
            color=DEEP_RED,
        )
        bowed_disc = scale_shape(
            bowed_disc,
            (width, depth, 1.0),
            center=(0.0, 0.0, center_z),
        )
        self.slab = self.attach_part(
            "curved_slab",
            boolean_union(rear_half, bowed_disc),
        )


class UprightBook(Asset):
    def __init__(self, width, depth, height, color, name="UprightBook"):
        super().__init__(label=name)
        self.cover = self.attach_part(
            "book_body",
            Cube((width, depth, height), color=color),
        )
        # A narrow light spine band helps individual volumes remain readable.
        self.spine_band = self.attach_part(
            "spine_band",
            Cube(
                (width * 0.72, 0.012, height * 0.075),
                center=(0.0, -depth / 2.0 - 0.006, height * 0.20),
                color=(0.90, 0.82, 0.62),
            ),
        )


class StorageBox(Asset):
    def __init__(self, width, depth, height, body_color, lid_color, label="StorageBox"):
        super().__init__(label=label)
        body = Cube(
            (width, depth, height),
            center=(0.0, 0.0, height / 2.0),
            color=body_color,
        )
        self.body = self.attach_part("body", body)
        lid = Cube(
            (width + 0.035, depth + 0.035, 0.035),
            color=lid_color,
        )
        lid = align_anchors(lid, body, anchor="bottom", target_anchor="top")
        self.lid = self.attach_part("lid", lid)
        self.label_panel = self.attach_part(
            "label_panel",
            Cube(
                (width * 0.42, 0.014, height * 0.24),
                center=(0.0, -depth / 2.0 - 0.008, height * 0.54),
                color=(0.90, 0.86, 0.72),
            ),
        )


class SideFrame(Asset):
    def __init__(self, side):
        super().__init__(label="LeftSideFrame" if side < 0 else "RightSideFrame")
        x = side * 1.12

        self.rear_upright = self.attach_part(
            "rear_upright",
            Cube(
                (0.10, 0.10, 2.20),
                center=(x, 0.285, 1.10),
                color=DARK_WOOD,
            ),
        )
        self.front_leg = self.attach_part(
            "front_leg",
            Cube(
                (0.10, 0.10, 2.08),
                center=(x, -0.285, 1.04),
                color=DARK_WOOD,
            ),
        )

        # Narrow front-to-back bearers visibly support each of the three broad shelves.
        shelf_centers = (0.40, 0.92, 1.44)
        for index, shelf_z in enumerate(shelf_centers, 1):
            bearer = Cube(
                (0.16, 0.62, 0.055),
                center=(side * 1.04, 0.0, shelf_z - 0.0775),
                color=CHARCOAL,
            )
            self.attach_part(f"lower_shelf_bearer_{index}", bearer)

        # The top brackets reach inward beneath the reduced-width top shelf.
        top_bracket = Cube(
            (0.38, 0.18, 0.06),
            center=(side * 0.995, 0.055, 1.90),
            color=CHARCOAL,
        )
        self.top_support_bracket = self.attach_part("top_support_bracket", top_bracket)

        # Small triangular-looking knee support assembled from a rotated narrow bar.
        knee = Cube((0.035, 0.22, 0.035), color=CHARCOAL)
        knee = rotate_shape(knee, axis="+x", angle=38.0)
        knee = translate_shape(knee, (x, 0.18, 1.83))
        self.top_knee_brace = self.attach_part("top_knee_brace", knee)


class FreestandingBookshelf(Asset):
    def __init__(self):
        super().__init__(label="FreestandingFourShelfBookshelf")

        # Exactly four deep-red shelf slabs.
        shelf_1 = CurvedShelf(2.10, 0.68, 0.10, 0.40, "BottomCurvedShelf")
        shelf_2 = CurvedShelf(2.10, 0.68, 0.10, 0.92, "LowerMiddleCurvedShelf")
        shelf_3 = CurvedShelf(2.10, 0.68, 0.10, 1.44, "UpperMiddleCurvedShelf")
        shelf_4 = CurvedShelf(1.65, 0.52, 0.10, 1.98, "TopCurvedShelf")
        self.shelf_1_bottom = self.attach_part("shelf_1_bottom", shelf_1)
        self.shelf_2_lower_middle = self.attach_part("shelf_2_lower_middle", shelf_2)
        self.shelf_3_upper_middle = self.attach_part("shelf_3_upper_middle", shelf_3)
        self.shelf_4_top = self.attach_part("shelf_4_top", shelf_4)

        self.left_side_structure = self.attach_part(
            "left_side_structure", SideFrame(-1)
        )
        self.right_side_structure = self.attach_part(
            "right_side_structure", SideFrame(1)
        )

        # Thin rear rails stabilize the frame without reading as extra shelves.
        rear_bracing = Asset("RearCrossBracing")
        for index, z in enumerate((0.66, 1.18, 1.70), 1):
            rear_bracing.attach_part(
                f"rear_rail_{index}",
                Cube(
                    (2.18, 0.055, 0.060),
                    center=(0.0, 0.30, z),
                    color=CHARCOAL,
                ),
            )
        diagonal = Cube((0.045, 0.04, 1.06), color=CHARCOAL)
        diagonal = rotate_shape(diagonal, axis="+y", angle=-61.0)
        diagonal = translate_shape(diagonal, (0.0, 0.315, 1.18))
        rear_bracing.attach_part("diagonal_stabilizer", diagonal)
        self.rear_cross_bracing = self.attach_part("rear_cross_bracing", rear_bracing)

        feet = Asset("FourFloorFeet")
        foot_positions = (
            (-1.12, -0.285),
            (-1.12, 0.285),
            (1.12, -0.285),
            (1.12, 0.285),
        )
        for index, (x, y) in enumerate(foot_positions, 1):
            feet.attach_part(
                f"foot_{index}",
                Cube(
                    (0.18, 0.18, 0.08),
                    center=(x, y, 0.04),
                    color=CHARCOAL,
                ),
            )
        self.feet = self.attach_part("feet", feet)

        self._add_bottom_contents()
        self._add_second_shelf_contents()
        self._add_third_shelf_contents()
        self._add_top_contents()

    def _place_upright(self, book, x, y, shelf_top, angle=0.0):
        if angle != 0.0:
            book = rotate_shape(book, axis="+z", angle=angle)
        book = align_anchors(
            book,
            (x, y, shelf_top),
            anchor="bottom",
        )
        return book

    def _add_bottom_contents(self):
        contents = Asset("BottomShelfBooksAndBoxes")
        shelf_top = 0.45
        specs = (
            (-0.78, 0.11, 0.13, 0.22, 0.33, (0.15, 0.35, 0.62), 0.0),
            (-0.61, 0.10, 0.16, 0.22, 0.29, (0.90, 0.55, 0.12), -5.0),
            (-0.43, 0.10, 0.11, 0.21, 0.36, (0.18, 0.58, 0.38), 0.0),
            (-0.28, 0.10, 0.14, 0.23, 0.31, (0.62, 0.18, 0.30), 0.0),
        )
        for index, (x, y, w, d, h, color, angle) in enumerate(specs, 1):
            book = UprightBook(w, d, h, color, f"BottomBook{index}")
            contents.attach_part(
                f"book_{index}",
                self._place_upright(book, x, y, shelf_top, angle),
            )

        box_a = StorageBox(0.38, 0.30, 0.20, (0.73, 0.63, 0.38), (0.25, 0.42, 0.48))
        box_a = align_anchors(box_a, (0.30, 0.04, shelf_top), anchor="bottom")
        contents.attach_part("storage_box_1", box_a)

        box_b = StorageBox(0.30, 0.27, 0.25, (0.30, 0.42, 0.62), (0.83, 0.70, 0.30))
        box_b = align_anchors(box_b, (0.76, 0.05, shelf_top), anchor="bottom")
        contents.attach_part("storage_box_2", box_b)
        self.book_group_bottom = self.attach_part("bottom_shelf_contents", contents)

    def _add_second_shelf_contents(self):
        contents = Asset("SecondShelfAssortment")
        shelf_top = 0.97
        specs = (
            (-0.73, 0.08, 0.10, 0.22, 0.30, (0.55, 0.24, 0.67), 0.0),
            (-0.59, 0.08, 0.13, 0.20, 0.36, (0.18, 0.50, 0.64), 0.0),
            (-0.42, 0.08, 0.12, 0.23, 0.27, (0.83, 0.31, 0.20), 4.0),
            (-0.25, 0.08, 0.15, 0.21, 0.33, (0.40, 0.62, 0.23), 0.0),
            (-0.07, 0.08, 0.11, 0.22, 0.29, (0.86, 0.66, 0.16), 0.0),
        )
        for index, (x, y, w, d, h, color, angle) in enumerate(specs, 1):
            book = UprightBook(w, d, h, color, f"SecondShelfBook{index}")
            contents.attach_part(
                f"upright_book_{index}",
                self._place_upright(book, x, y, shelf_top, angle),
            )

        horizontal_books = []
        for width, depth, thick, color in (
            (0.38, 0.24, 0.055, (0.20, 0.36, 0.58)),
            (0.34, 0.22, 0.050, (0.70, 0.22, 0.18)),
            (0.30, 0.20, 0.045, (0.32, 0.56, 0.30)),
        ):
            horizontal_books.append(Cube((width, depth, thick), color=color))
        stack = stack_shapes(horizontal_books, axis="+z", gap=0.008)
        stack = align_anchors(stack, (0.62, 0.06, shelf_top), anchor="bottom")
        contents.attach_part("horizontal_book_stack", stack)
        self.book_group_shelf_2 = self.attach_part("second_shelf_contents", contents)

    def _add_third_shelf_contents(self):
        contents = Asset("ThirdShelfBoxAndBooks")
        shelf_top = 1.49

        box = StorageBox(0.46, 0.31, 0.27, (0.44, 0.30, 0.20), (0.74, 0.57, 0.30))
        box = align_anchors(box, (-0.54, 0.045, shelf_top), anchor="bottom")
        contents.attach_part("medium_storage_box", box)

        specs = (
            (0.03, 0.08, 0.11, 0.21, 0.31, (0.18, 0.46, 0.55), 0.0),
            (0.18, 0.08, 0.14, 0.22, 0.37, (0.70, 0.20, 0.24), 0.0),
            (0.36, 0.08, 0.12, 0.20, 0.29, (0.80, 0.53, 0.15), -4.0),
            (0.53, 0.08, 0.15, 0.23, 0.34, (0.28, 0.58, 0.34), 0.0),
            (0.71, 0.08, 0.10, 0.20, 0.26, (0.38, 0.31, 0.66), 0.0),
        )
        for index, (x, y, w, d, h, color, angle) in enumerate(specs, 1):
            book = UprightBook(w, d, h, color, f"ThirdShelfBook{index}")
            contents.attach_part(
                f"book_{index}",
                self._place_upright(book, x, y, shelf_top, angle),
            )
        self.storage_box_shelf_3 = self.attach_part("third_shelf_contents", contents)

    def _add_top_contents(self):
        contents = Asset("SparseTopShelfObjects")
        shelf_top = 2.03

        book_1 = UprightBook(0.13, 0.18, 0.27, (0.22, 0.48, 0.67), "TopBookOne")
        book_1 = self._place_upright(book_1, -0.18, 0.04, shelf_top, 0.0)
        contents.attach_part("small_book_1", book_1)

        book_2 = UprightBook(0.11, 0.17, 0.23, (0.76, 0.34, 0.18), "TopBookTwo")
        book_2 = self._place_upright(book_2, -0.03, 0.04, shelf_top, 3.0)
        contents.attach_part("small_book_2", book_2)

        compact_box = StorageBox(0.30, 0.22, 0.17, (0.33, 0.50, 0.46), (0.82, 0.70, 0.42))
        compact_box = align_anchors(compact_box, (0.40, 0.04, shelf_top), anchor="bottom")
        contents.attach_part("compact_storage_box", compact_box)
        self.top_shelf_objects = self.attach_part("top_shelf_objects", contents)


scene = FreestandingBookshelf()
