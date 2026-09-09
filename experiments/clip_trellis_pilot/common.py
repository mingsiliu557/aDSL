from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


DATA_MOUNT = Path("/jiigan-hp")
DATA_FSTYPE = "hpvs_fs"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def assert_data_mount(path: str | Path, *, require_write: bool = True) -> Path:
    resolved = Path(path).expanduser().resolve()
    target = resolved if resolved.exists() else resolved.parent
    result = subprocess.run(
        ["findmnt", "-T", str(target), "-n", "-o", "TARGET,FSTYPE"],
        text=True,
        capture_output=True,
        check=True,
    )
    fields = result.stdout.strip().split()
    if fields != [str(DATA_MOUNT), DATA_FSTYPE]:
        raise RuntimeError(
            f"Refusing non-data-disk path {resolved}: findmnt={result.stdout.strip()!r}"
        )
    if require_write and not target.exists():
        raise FileNotFoundError(target)
    return resolved


def completed_render(render_dir: str | Path, expected_views: int = 8) -> bool:
    root = Path(render_dir)
    renders = sorted(root.glob("render_*.png"))
    return (
        len(renders) == expected_views
        and all(path.stat().st_size > 0 for path in renders)
        and (root / "meta.json").is_file()
        and (root / "meta.json").stat().st_size > 0
    )
