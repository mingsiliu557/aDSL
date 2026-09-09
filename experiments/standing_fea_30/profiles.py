from __future__ import annotations

from dataclasses import dataclass
from typing import Final


SELECTION_SEED: Final = "20260909"
CAP3D_REVISION: Final = "0f05a726ff7eb3db93444a4efc65f0552bce84a4"
MARVEL_REVISION: Final = "b44089646984abc6976baefdd5289b2198c46e2c"
SOURCE_FILES: Final = {
    f"cap3d/{CAP3D_REVISION}/Cap3D_automated_ShapeNet.csv":
        "d1e881d7c22d3313ab989d1fdf5e43559526fe4a9e1d7e7b1c2d48ba702a9e54",
    f"cap3d/{CAP3D_REVISION}/Cap3D_automated_ABO.csv":
        "442d23e1dc39b52e002c9fbc556a48ffdf1dfd255552982778dde1cdc497cbaf",
    f"marvel_40m/{MARVEL_REVISION}/annotation/shapenet.csv":
        "af1faec8ad4601a1c7d424a6cf26edce4a6acec8e2e41676ab109837d2290c85",
    f"marvel_40m/{MARVEL_REVISION}/annotation/abo.csv":
        "00776554b146d6e78d4c51d955c66ae4db152cbc41c0ae7e7bae19c4ea60da3d",
}


@dataclass(frozen=True)
class CategoryDefinition:
    name: str
    shapenet_names: tuple[str, ...]
    shapenet_synsets: tuple[str, ...]
    category_terms: tuple[str, ...]
    semantic_terms: tuple[str, ...]
    risk_terms: tuple[str, ...]
    slots: tuple[tuple[str, str], ...]
    fea_config: str
    scale_measure: str
    scale_target_m: float


FOUR_TWO_SLOTS = (
    ("shapenet", "cap3d"),
    ("shapenet", "marvel"),
    ("shapenet", "cap3d"),
    ("shapenet", "marvel"),
    ("abo", "cap3d"),
    ("abo", "marvel"),
)
FIVE_ONE_SLOTS = (
    ("shapenet", "cap3d"),
    ("shapenet", "marvel"),
    ("shapenet", "cap3d"),
    ("shapenet", "marvel"),
    ("shapenet", "cap3d"),
    ("abo", "marvel"),
)


CATEGORIES: Final = (
    CategoryDefinition(
        name="chair_stool",
        shapenet_names=("chair",),
        shapenet_synsets=("03001627",),
        category_terms=("chair", "stool"),
        semantic_terms=("seat", "back", "backrest"),
        risk_terms=(
            "tall", "high-back", "high back", "bar stool", "swivel",
            "slanted", "angled", "three-legged", "three leg", "legs",
        ),
        slots=FOUR_TWO_SLOTS,
        fea_config="fea_chair_stool.json",
        scale_measure="height",
        scale_target_m=0.9,
    ),
    CategoryDefinition(
        name="table_desk",
        shapenet_names=("table",),
        shapenet_synsets=("04379243",),
        category_terms=("table", "desk"),
        semantic_terms=("tabletop", "table top", "desktop", "top"),
        risk_terms=(
            "pedestal", "console", "narrow", "slanted", "irregular",
            "drawer", "leg", "base",
        ),
        slots=FOUR_TWO_SLOTS,
        fea_config="fea_table_desk.json",
        scale_measure="height",
        scale_target_m=0.75,
    ),
    CategoryDefinition(
        name="bookshelf",
        shapenet_names=("bookshelf",),
        shapenet_synsets=("02871439",),
        category_terms=("bookshelf", "bookcase", "shelving", "shelf"),
        semantic_terms=("shelf", "shelves", "tier"),
        risk_terms=("tall", "narrow", "vertical", "pillar", "slanted", "tier"),
        slots=FIVE_ONE_SLOTS,
        fea_config="fea_bookshelf.json",
        scale_measure="height",
        scale_target_m=1.8,
    ),
    CategoryDefinition(
        name="floor_lamp",
        shapenet_names=("lamp",),
        shapenet_synsets=("03636649",),
        category_terms=("floor lamp", "street lamp", "standing lamp"),
        semantic_terms=("shade", "lamp head", "light head", "lantern"),
        risk_terms=(
            "slender", "curved", "arc", "tripod", "spherical base",
            "small base", "tall", "pole", "stem", "base",
        ),
        slots=FOUR_TWO_SLOTS,
        fea_config="fea_floor_lamp.json",
        scale_measure="height",
        scale_target_m=1.6,
    ),
    CategoryDefinition(
        name="tower_speaker",
        shapenet_names=("loudspeaker",),
        shapenet_synsets=("03691459",),
        category_terms=("speaker", "loudspeaker"),
        semantic_terms=("speaker", "driver", "woofer", "enclosure", "cabinet"),
        risk_terms=("tower", "standing", "tripod", "tall", "vertical", "slender", "pedestal"),
        slots=FIVE_ONE_SLOTS,
        fea_config="fea_tower_speaker.json",
        scale_measure="height",
        scale_target_m=1.2,
    ),
)


def definition(name: str) -> CategoryDefinition:
    return next(value for value in CATEGORIES if value.name == name)


def prompt_is_eligible(
    category: CategoryDefinition,
    *,
    dataset: str,
    filtered_name: str,
    synset_id: str,
    prompt: str,
) -> bool:
    text = " ".join(prompt.lower().split())
    if dataset == "shapenet":
        recognized = (
            filtered_name.lower().strip() in category.shapenet_names
            or synset_id in category.shapenet_synsets
        )
    else:
        recognized = any(term in text for term in category.category_terms)
    # Caption sources occasionally contradict the coarse dataset label.  Keep
    # the experimental object category semantic, because the FEA load profile
    # is category-specific.
    if category.name == "table_desk":
        recognized = recognized and any(term in text for term in ("table", "desk"))
        recognized = recognized and "lamp" not in text
    elif category.name == "bookshelf":
        recognized = recognized and any(
            term in text for term in ("bookshelf", "bookcase", "shelving unit")
        )
        recognized = recognized and not any(term in text for term in ("desk", "table"))
    elif category.name == "floor_lamp":
        recognized = recognized and any(
            term in text for term in ("floor lamp", "street lamp", "standing lamp")
        )
        recognized = recognized and not any(
            term in text for term in ("desk lamp", "table lamp", "pendant")
        )
    elif category.name == "tower_speaker":
        recognized = recognized and "speaker stand" not in text
    if category.name == "chair_stool":
        # The chair FEA profile applies independent loads to the seat and back.
        # Requiring both names here prevents a known load-region miss from being
        # introduced by prompt sampling rather than by generation.
        semantic_match = "seat" in text and any(
            term in text for term in ("back", "backrest")
        )
    else:
        semantic_match = any(term in text for term in category.semantic_terms)
    return (
        recognized
        and semantic_match
        and any(term in text for term in category.risk_terms)
    )


__all__ = [
    "CAP3D_REVISION",
    "CATEGORIES",
    "CategoryDefinition",
    "MARVEL_REVISION",
    "SELECTION_SEED",
    "SOURCE_FILES",
    "definition",
    "prompt_is_eligible",
]
