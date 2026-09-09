from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

from experiments.clip_trellis_pilot.prepare_toys4k_subset import (
    extract_selected,
    select_rows,
)
from experiments.clip_trellis_pilot.score_clip import summarize


def _row(category: str, index: int, payload: bytes = b"x") -> dict[str, str]:
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "file_identifier": f"{category}/{category}_{index:03d}/{category}_{index:03d}.blend",
        "aesthetic_score": "5.0",
        "captions": "[]",
    }


def test_toys_subset_is_deterministic_and_category_diverse() -> None:
    rows = [
        _row(category, index)
        for category in ("chair", "mug", "car", "boat", "robot", "lamp")
        for index in range(3)
    ]

    first = select_rows(rows, count=4, seed=123)
    second = select_rows(list(reversed(rows)), count=4, seed=123)

    assert first == second
    assert len({row["file_identifier"].split("/", 1)[0] for row in first}) == 4


def test_extract_selected_only_writes_requested_members(tmp_path: Path) -> None:
    wanted_payload = b"wanted"
    ignored_payload = b"ignored"
    wanted = _row("chair", 1, wanted_payload)
    ignored = _row("mug", 2, ignored_payload)
    archive = tmp_path / "toys.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(
            f"toys4k_blend_files/{wanted['file_identifier']}",
            wanted_payload,
        )
        handle.writestr(
            f"toys4k_blend_files/{ignored['file_identifier']}",
            ignored_payload,
        )

    records = extract_selected(archive, tmp_path / "subset", [wanted])

    assert records[0]["status"] == "extracted"
    assert Path(records[0]["local_path"]).read_bytes() == wanted_payload
    assert not (
        tmp_path / "subset" / "assets" / ignored["file_identifier"]
    ).exists()


def test_summary_uses_failure_zero_and_two_point_margin() -> None:
    rows = [
        {"case_id": "T01", "track": "text", "method": "adsl", "mean_clip": 30.0},
        {"case_id": "T01", "track": "text", "method": "trellis", "mean_clip": 29.0},
        {"case_id": "T02", "track": "text", "method": "adsl", "mean_clip": 0.0},
        {"case_id": "T02", "track": "text", "method": "trellis", "mean_clip": 0.0},
    ]

    result = summarize(rows, margin=2.0)

    assert result[0]["adsl_mean_failure_zero"] == 15.0
    assert result[0]["trellis_mean_failure_zero"] == 14.5
    assert result[0]["mean_delta_adsl_minus_trellis"] == 0.5
    assert result[0]["rough_effect"] == "close_within_margin"
