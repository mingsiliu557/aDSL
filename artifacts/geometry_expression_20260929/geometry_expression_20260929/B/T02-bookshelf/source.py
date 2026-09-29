from adsl.core import *
import math


DEEP_RED = (0.48, 0.025, 0.045)
DARK_FRAME = (0.13, 0.075, 0.055)
FOOT_COLOR = (0.10, 0.055, 0.04)


def curved_shelf_profile(width, back_y, front_side_y, bow_depth, arc_samples=32):
    """Plan outline with a straight rear edge and a parabolic bowed front edge."""
    half_width = width / 2.0
    points = [
        (-half_width, back_y),
        (half_width, back_y),
        (half_width, front_side_y),
    ]
    for index in range(1, arc_samples + 1):
        x = half_width - width * index / arc_samples
        normalized_x = x / half_width
        y = front_side_y - bow_depth * (1.0 - normalized_x * normalized_x)
        points.append((x, y))
    return Polygon(points)


class CurvedShelf(Asset):
    def __init__(self, width, back_y, front_side_y, bow_depth, thickness):
        super().__init__(label="Deep-red curved shelf")
        profile = curved_shelf_profile(
            width=width,
            back_y=back_y,
            front_side_y=front_side_y,
            bow_depth=bow_depth,
            arc_samples=36,
        )
        self.slab = self.attach_part(
            "curved_slab",
            linear_extrude(profile, thickness, color=DEEP_RED),
        )


class Book(Asset):
    def __init__(self, dimensions, color, label="Book"):
        super().__init__(label=label)
        width, depth, height = dimensions
        self.volume = self.attach_part(
            "book_volume",
            Cube((width, depth, height), color=color),
        )
        band_height = min(0.035, height * 0.12)
        band = Cube(
            (width + 0.012, depth + 0.012, band_height),
            color=(0.88, 0.78, 0.56),
        )
        band = align_anchors(
            band,
            self.volume,
            anchor="bottom",
            target_anchor="bottom",
            offset=(0.0, 0.0, height * 0.16),
        )
        self.spine_band = self.attach_part("spine_band", band)


class StorageBox(Asset):
    def __init__(self, dimensions, body_color, lid_color):
        super().__init__(label="Storage box")
        width, depth, height = dimensions
        self.body = self.attach_part(
            "body",
            Cube((width, depth, height), color=body_color),
        )
        lid = Cube(
            (width + 0.055, depth + 0.055, 0.075),
            color=lid_color,
        )
        lid = align_anchors(
            lid,
            self.body,
            anchor="bottom",
            target_anchor="top",
        )
        self.lid = self.attach_part("lid", lid)


class HorizontalBookStack(Asset):
    def __init__(self):
        super().__init__(label="Horizontal stack of books")
        books = [
            Book((1.05, 0.58, 0.085), (0.12, 0.32, 0.58), "Blue horizontal book"),
            Book((0.92, 0.54, 0.095), (0.76, 0.42, 0.09), "Ochre horizontal book"),
            Book((0.98, 0.56, 0.075), (0.24, 0.52, 0.30), "Green horizontal book"),
        ]
        self.books = self.attach_part(
            "stacked_books",
            stack_shapes(books, axis="+z", gap=0.012),
        )


class SideSupport(Asset):
    def __init__(self, side_sign):
        side_name = "left" if side_sign < 0 else "right"
        super().__init__(label=f"{side_name.capitalize()} stepped side support")

        outer_x = side_sign * 2.38
        inner_x = side_sign * 1.78
        foot_height = 0.12
        lower_top = 2.56
        post_width = 0.18
        post_depth = 0.22

        foot = Cube(
            (0.56, 1.98, foot_height),
            center=(outer_x, -0.10, foot_height / 2.0),
            color=FOOT_COLOR,
        )
        self.foot = self.attach_part("floor_foot", foot)

        for position_name, y in (("front", -0.63), ("rear", 0.55)):
            post_height = lower_top - foot_height
            post = Cube(
                (post_width, post_depth, post_height),
                center=(outer_x, y, foot_height + post_height / 2.0),
                color=DARK_FRAME,
            )
            self.attach_part(f"lower_{position_name}_post", post)

            outer_node = Cube(
                (post_width, post_depth, 0.18),
                center=(outer_x, y, 2.47),
            )
            inner_node = Cube(
                (post_width, post_depth, 0.18),
                center=(inner_x, y, 3.15),
            )
            upper_transition = hull(
                outer_node,
                inner_node,
                color=DARK_FRAME,
            )
            self.attach_part(f"upper_{position_name}_inward_support", upper_transition)


class FreestandingBookshelf(Asset):
    def __init__(self):
        super().__init__(label="Freestanding four-shelf bookshelf")

        shelf_thickness = 0.16
        shelf_top_levels = (0.45, 1.45, 2.45, 3.35)

        self.left_side_support = self.attach_part(
            "left_side_support",
            SideSupport(-1),
        )
        self.right_side_support = self.attach_part(
            "right_side_support",
            SideSupport(1),
        )

        lower_shelves = []
        for index, top_z in enumerate(shelf_top_levels[:3], 1):
            shelf = CurvedShelf(
                width=5.0,
                back_y=0.75,
                front_side_y=-0.85,
                bow_depth=0.40,
                thickness=shelf_thickness,
            )
            shelf = translate_shape(shelf, (0.0, 0.0, top_z - shelf_thickness))
            lower_shelves.append(shelf)
            self.attach_part(f"lower_curved_shelf_{index}", shelf)

        top_shelf = CurvedShelf(
            width=3.80,
            back_y=0.75,
            front_side_y=-0.35,
            bow_depth=0.30,
            thickness=shelf_thickness,
        )
        top_shelf = translate_shape(
            top_shelf,
            (0.0, 0.0, shelf_top_levels[3] - shelf_thickness),
        )
        self.top_shelf = self.attach_part("smaller_top_curved_shelf", top_shelf)

        self.contents = self.attach_part("books_and_storage", Asset("Shelf contents"))

        def place_content(name, item, shelf, x, y):
            placed = align_anchors(
                item,
                shelf,
                anchor="bottom",
                target_anchor="top",
            )
            placed = offset_from(placed, shelf, (x, y, None))
            self.contents.attach_part(name, placed)
            return placed

        # Lowest shelf: three varied upright books and a broad storage box.
        place_content(
            "lowest_blue_book",
            Book((0.18, 0.52, 0.82), (0.10, 0.30, 0.62), "Tall blue book"),
            lower_shelves[0],
            -1.48,
            0.18,
        )
        place_content(
            "lowest_gold_book",
            Book((0.22, 0.48, 0.68), (0.82, 0.48, 0.10), "Gold book"),
            lower_shelves[0],
            -1.24,
            0.20,
        )
        place_content(
            "lowest_green_book",
            Book((0.16, 0.55, 0.91), (0.18, 0.48, 0.26), "Tall green book"),
            lower_shelves[0],
            -1.02,
            0.16,
        )
        place_content(
            "lowest_large_storage_box",
            StorageBox((1.05, 0.68, 0.50), (0.70, 0.56, 0.35), (0.28, 0.18, 0.12)),
            lower_shelves[0],
            0.88,
            0.18,
        )

        # Middle shelf: a true horizontal stack plus two upright books.
        place_content(
            "middle_horizontal_book_stack",
            HorizontalBookStack(),
            lower_shelves[1],
            -0.92,
            0.15,
        )
        place_content(
            "middle_red_book",
            Book((0.20, 0.50, 0.76), (0.66, 0.10, 0.12), "Red upright book"),
            lower_shelves[1],
            0.42,
            0.20,
        )
        place_content(
            "middle_violet_book",
            Book((0.26, 0.56, 0.88), (0.42, 0.20, 0.56), "Violet upright book"),
            lower_shelves[1],
            0.68,
            0.17,
        )

        # Third shelf: a differently proportioned box and two more books.
        place_content(
            "third_compact_storage_box",
            StorageBox((0.78, 0.72, 0.62), (0.22, 0.44, 0.50), (0.78, 0.70, 0.48)),
            lower_shelves[2],
            -1.18,
            0.16,
        )
        place_content(
            "third_teal_book",
            Book((0.19, 0.46, 0.70), (0.08, 0.48, 0.50), "Teal upright book"),
            lower_shelves[2],
            0.25,
            0.22,
        )
        place_content(
            "third_orange_book",
            Book((0.24, 0.54, 0.80), (0.88, 0.31, 0.08), "Orange upright book"),
            lower_shelves[2],
            0.50,
            0.18,
        )


scene = FreestandingBookshelf()
