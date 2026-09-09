from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from .checkers import load_checker_spec
from .models import CheckerSpec, ObjectRequest, RepairPolicy
from .service import ObjectWorkflow
from .utils.io import read_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adsl-run")
    parser.add_argument("--model-config", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    edit = subparsers.add_parser("edit")
    resume = subparsers.add_parser("resume")
    for command in (create, edit):
        command.add_argument("requirement")
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--task-id", default=None)
        command.add_argument("--image", type=Path, action="append", default=[])
        command.add_argument("--articulation", action="store_true")
        command.add_argument("--max-rounds", type=int, default=2)
        command.add_argument(
            "--checker-config",
            type=Path,
            action="append",
            default=[],
            help="JSON CheckerSpec; repeat to register multiple engineering gates.",
        )
        command.add_argument("--repair-policy-config", type=Path)
    edit.add_argument("--source", type=Path, required=True)
    edit.add_argument("--edit-kind", choices=["continue", "extend", "variant"], default="continue")
    edit.add_argument(
        "--check-first",
        action="store_true",
        help="Execute and check the supplied source before the first agent patch.",
    )
    resume.add_argument("--output", type=Path, required=True)
    resume.add_argument("--task-id", default=None)
    resume.add_argument("--requirement", default=None)
    resume.add_argument("--max-rounds", type=int, default=2)
    resume.add_argument("--checker-config", type=Path, action="append", default=[])
    resume.add_argument("--repair-policy-config", type=Path)
    return parser


def _load_checker_specs(paths: Sequence[Path]) -> tuple[CheckerSpec, ...]:
    specs = tuple(load_checker_spec(path) for path in paths)
    names = [spec.name for spec in specs]
    if len(names) != len(set(names)):
        raise ValueError("checker names must be unique")
    return specs


def _load_repair_policy(path: Path | None, fallback: object = None) -> RepairPolicy:
    if path is not None:
        return RepairPolicy.model_validate(read_json(path.expanduser().resolve()))
    if fallback is not None:
        return RepairPolicy.model_validate(fallback)
    return RepairPolicy()


def _resume_request(args: argparse.Namespace) -> ObjectRequest:
    config_path = args.output.expanduser().resolve() / "runtime_config.json"
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    payload = read_json(config_path).get("request", {})
    checker_specs = (
        _load_checker_specs(args.checker_config)
        if args.checker_config
        else tuple(
            CheckerSpec.model_validate(value)
            for value in payload.get("checker_specs", [])
        )
    )
    return ObjectRequest(
        requirement=args.requirement or str(payload.get("requirement", "")),
        workspace=args.output,
        task_id=args.task_id or str(payload.get("task_id", "")),
        image_paths=tuple(Path(value) for value in payload.get("image_paths", [])),
        articulation=bool(payload.get("articulation", False)),
        max_rounds=args.max_rounds,
        checker_specs=checker_specs,
        check_first=bool(payload.get("check_first", False)),
        repair_policy=_load_repair_policy(
            args.repair_policy_config, payload.get("repair_policy")
        ),
    )


async def _run(args: argparse.Namespace) -> dict[str, object]:
    workflow = ObjectWorkflow(args.model_config)
    if args.command == "resume":
        result = await workflow.resume(_resume_request(args))
        return {
            key: str(value) if isinstance(value, Path) else value
            for key, value in asdict(result).items()
        }
    request = ObjectRequest(
        requirement=args.requirement,
        workspace=args.output,
        task_id=args.task_id or uuid4().hex,
        image_paths=tuple(args.image),
        articulation=args.articulation,
        max_rounds=args.max_rounds,
        checker_specs=_load_checker_specs(args.checker_config),
        check_first=bool(getattr(args, "check_first", False)),
        repair_policy=_load_repair_policy(args.repair_policy_config),
    )
    if args.command == "create":
        result = await workflow.generate(request)
    else:
        result = await workflow.edit(request, source=args.source, edit_kind=args.edit_kind)
    return {key: str(value) if isinstance(value, Path) else value for key, value in asdict(result).items()}


def main_cli(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(json.dumps(asyncio.run(_run(args)), indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
