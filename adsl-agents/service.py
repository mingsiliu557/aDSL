from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any

from .checkers import (
    CheckerRun,
    required_checker_errors,
    required_checker_failures,
    run_checkers,
)
from .feedback_schema import build_analysis_context, findings_payload
from .localization import LocalizationReport, localize_findings
from .models import (
    AnalysisContext,
    CodeCriticDecision,
    DebuggerDecision,
    EditKind,
    EditPlan,
    EngineeringCriticDecision,
    ImageCriticDecision,
    ObjectPlan,
    ObjectRequest,
    ObjectRunResult,
)
from .prompts import object_prompt
from .repair_controller import RepairController
from .repair_policy import (
    assess_candidate,
    immutable_inputs_match,
    validate_patch_scope,
)
from .source_index import SourceIndex, load_source_index
from .tools import AgentToolContext, PATCH_TOOLS, READ_TOOLS, WRITE_TOOLS
from .utils.config import packaged_profile
from .utils.execution import (
    AssetExecutionError,
    AssetInfrastructureError,
    ExecutionResult,
    execute_asset_source,
)
from .utils.inputs import user_input
from .utils.io import read_json, write_json
from .utils.runner import AgentRuntime
from .utils.usage import UsageRecorder


_CONTEXT_POLICY = {
    "planner": "isolated requirement and references",
    "coder_initial": "isolated requirement, plan, and references; source.py is written by tool",
    "coder_repair": "isolated per repair with plan and current feedback; source.py is read and patched by tool",
    "debugger": "isolated per execution failure with current error; source.py is read by tool",
    "image_critic": "isolated per round with current renders, references, and explicit text-only prior judgements",
    "code_critic": "isolated per round with current renders and explicit critic history; source.py is read by tool",
    "engineering_critic": "isolated per round with current renders, structured checker evidence, and source.py read by tool",
}


class WorkflowGateError(RuntimeError):
    pass


def _engineering_feedback(
    runs: list[CheckerRun], *, context: AnalysisContext,
    localization: LocalizationReport | None, history: list[dict[str, object]],
    round_root: Path, workspace: Path,
) -> dict[str, Any]:
    """Send evidence once; preserve full on-disk reports for read_file."""
    def relative(path: Path) -> str:
        return path.resolve().relative_to(workspace.resolve()).as_posix()

    findings = {}
    summaries = []
    for run in runs:
        result_ref = relative(run.output_dir / "result.json")
        summaries.append({
            "checker": run.spec.name, "required": run.spec.required,
            "status": run.result.status, "summary": run.result.summary,
            "finding_ids": [f.finding_id for f in run.result.findings],
            "result_ref": result_ref,
        })
        for finding in run.result.findings:
            # Typed metrics, regions, relations and source candidates remain inline.
            # Legacy-only evidence must not disappear when no typed equivalent exists.
            typed = finding.metric is not None or finding.region is not None or bool(finding.relations)
            row = finding.model_dump(exclude={"domain"} if typed else set(), exclude_none=True)
            row["result_ref"] = result_ref
            findings[finding.finding_id] = row
    return {
        "checker_summary": summaries,
        "required_checker_failures": [r.spec.name for r in required_checker_failures(runs)],
        "typed_findings": list(findings.values()),
        "analysis_context": context.model_dump(exclude={"checker_contexts": {"__all__": {"details"}}}),
        "analysis_context_ref": relative(round_root / "analysis_context.json"),
        "localization": [
            row.model_dump(exclude={"candidates"}, exclude_none=True)
            for row in (localization.findings if localization is not None else [])
        ],
        "localization_ref": relative(round_root / "localization.json") if localization is not None else None,
        "repair_history": [
            {key: row[key] for key in (
                "round", "proposal_id", "candidate", "accepted", "reason",
                "target_improvements", "regressions", "unavailable_checks", "errors",
            ) if key in row}
            for row in history
        ],
        "repair_history_ref": "repair_history.jsonl" if history else None,
        "evidence_access": "Use read_file on result_ref and report refs for full metrics, assumptions and raw evidence before proposing a repair when the inline evidence is insufficient.",
    }


@dataclass(frozen=True)
class _CandidateOutcome:
    execution: ExecutionResult | None
    checker_runs: tuple[CheckerRun, ...]
    appearance_approved: bool
    attempts: tuple[dict[str, object], ...]


def _asset_executor_timeout_seconds() -> float:
    raw = os.environ.get("ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS")
    if raw is None:
        queue_enabled = bool(os.environ.get("ADSL_GPU_RENDER_QUEUE", "").strip())
        return 3660.0 if queue_enabled else 300.0
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ValueError(
            "ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS must be a positive number"
        ) from exc
    if timeout <= 0:
        raise ValueError(
            "ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS must be a positive number"
        )
    return timeout


def _positive_render_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _render_execution_config() -> dict[str, object]:
    engine = os.environ.get("ADSL_RENDER_ENGINE", "BLENDER_EEVEE").strip().upper()
    if engine not in {"CYCLES", "BLENDER_EEVEE"}:
        raise ValueError("ADSL_RENDER_ENGINE must be CYCLES or BLENDER_EEVEE")
    queue_value = os.environ.get("ADSL_GPU_RENDER_QUEUE", "").strip()
    if queue_value and engine != "BLENDER_EEVEE":
        raise ValueError("ADSL_GPU_RENDER_QUEUE supports only BLENDER_EEVEE")
    return {
        "render_backend": "gpu_queue" if queue_value else "local",
        "gpu_render_queue": (
            str(Path(queue_value).expanduser().resolve()) if queue_value else None
        ),
        "render_engine": engine,
        "render_width": _positive_render_env_int("ADSL_RENDER_WIDTH", 1024),
        "render_height": _positive_render_env_int("ADSL_RENDER_HEIGHT", 1024),
        "render_samples": _positive_render_env_int("ADSL_RENDER_SAMPLES", 256),
    }


class ObjectWorkflow:
    def __init__(self, model_profile: str | Path | None = None) -> None:
        self.model_profile = Path(model_profile or packaged_profile()).resolve()

    async def generate(self, request: ObjectRequest) -> ObjectRunResult:
        self._validate_request(request)
        workspace = self._prepare_workspace(request.workspace)
        self._persist_user_input(workspace, request)
        source_path = workspace / "source.py"
        source_path.touch(exist_ok=False)
        runtime = self._runtime(request, workspace, mode="generate")
        plan = await self._plan_generation(runtime, request)
        write_json(workspace / "plan.json", plan.model_dump())
        self._write_checkpoint(workspace, mode="generate", stage="planned")

        context = AgentToolContext(workspace=workspace, source_path=source_path)
        coder = runtime.agent(
            name="object-coder",
            instructions=object_prompt("coder", articulation=request.articulation),
            tools=WRITE_TOOLS,
        )
        await runtime.run(
            agent=coder,
            input=user_input(
                json.dumps(
                    {
                        "requirement": request.requirement,
                        "articulation_required": request.articulation,
                        "plan": plan.model_dump(),
                        "assignment": "Write source.py with the complete initial implementation.",
                    },
                    ensure_ascii=False,
                ),
                request.image_paths,
            ),
            role="coder:initial",
            stage="initial_code",
            context=context,
        )
        self._require_tool_event(context, "write_file", "initial coder")
        self._write_checkpoint(workspace, mode="generate", stage="refining", next_round=1)
        return await self._iterate(
            runtime=runtime,
            request=request,
            workspace=workspace,
            source_path=source_path,
            mode="generate",
            plan=plan,
        )

    async def edit(
        self,
        request: ObjectRequest,
        *,
        source: str | Path,
        edit_kind: EditKind = "continue",
    ) -> ObjectRunResult:
        self._validate_request(request)
        workspace = self._prepare_workspace(request.workspace)
        self._persist_user_input(workspace, request)
        source_path = workspace / "source.py"
        source_input = Path(source).expanduser().resolve()
        if not source_input.is_file():
            raise FileNotFoundError(source_input)
        shutil.copy2(source_input, source_path)
        runtime = self._runtime(
            request,
            workspace,
            mode="edit",
            edit_kind=edit_kind,
            source_parent=str(source_input),
            initial_source_sha256=self._source_sha256(source_path),
        )
        plan = await self._plan_edit(runtime, request, source_path, edit_kind)
        write_json(workspace / "plan.json", plan.model_dump())
        self._write_checkpoint(
            workspace,
            mode="edit",
            stage="planned",
            edit_kind=edit_kind,
            source_parent=str(source_input),
        )

        context = AgentToolContext(workspace=workspace, source_path=source_path)
        coder = runtime.agent(
            name="object-coder",
            instructions=object_prompt("coder", articulation=request.articulation),
            tools=PATCH_TOOLS,
        )
        if not request.check_first:
            await runtime.run(
                agent=coder,
                input=user_input(
                    json.dumps(
                        {
                            "edit_kind": edit_kind,
                            "requirement": request.requirement,
                            "plan": plan.model_dump(),
                            "assignment": "Read source.py and apply the requested minimal patch.",
                        },
                        ensure_ascii=False,
                    ),
                    request.image_paths,
                ),
                role="coder:initial",
                stage="initial_patch",
                context=context,
            )
            self._require_tool_event(context, "apply_patch", "initial edit coder")
        self._write_checkpoint(
            workspace,
            mode="edit",
            stage="refining",
            next_round=1,
            edit_kind=edit_kind,
            source_parent=str(source_input),
        )
        return await self._iterate(
            runtime=runtime,
            request=request,
            workspace=workspace,
            source_path=source_path,
            mode="edit",
            plan=plan,
        )

    async def resume(self, request: ObjectRequest) -> ObjectRunResult:
        """Continue an interrupted object run in its existing workspace.

        ``max_rounds`` is the number of additional refinement attempts allowed by
        this resume invocation. Existing plans, source, sessions, usage, and
        completed round artifacts are retained.
        """

        self._validate_request(request)
        workspace = request.workspace.expanduser().resolve()
        if not workspace.is_dir():
            raise FileNotFoundError(workspace)
        source_path = workspace / "source.py"
        plan_path = workspace / "plan.json"
        if not source_path.is_file():
            raise ValueError("object resume requires an existing source.py")

        checkpoint_path = workspace / "checkpoint.json"
        checkpoint = read_json(checkpoint_path) if checkpoint_path.is_file() else {}
        manifest_path = workspace / "run.json"
        manifest = read_json(manifest_path) if manifest_path.is_file() else {}
        mode = str(checkpoint.get("mode") or manifest.get("mode") or "generate")
        if mode not in {"generate", "edit"}:
            raise ValueError(f"unsupported object resume mode: {mode}")
        if (
            checkpoint.get("stage") == "completed"
            or manifest.get("status") == "completed"
        ) and self._published_files_exist(workspace):
            return self._load_completed_result(workspace, manifest)

        runtime = self._runtime(request, workspace, mode=mode, resume=True)
        runtime.usage.update_manifest(status="running", resumed=True)
        if plan_path.is_file():
            plan_type = ObjectPlan if mode == "generate" else EditPlan
            plan = plan_type.model_validate(read_json(plan_path))
        elif mode == "generate":
            plan = await self._plan_generation(runtime, request)
            write_json(plan_path, plan.model_dump())
            checkpoint = {**checkpoint, "mode": mode, "stage": "planned"}
            write_json(checkpoint_path, checkpoint)
        else:
            edit_kind = str(checkpoint.get("edit_kind") or manifest.get("edit_kind") or "continue")
            if edit_kind not in {"continue", "extend", "variant"}:
                raise ValueError(f"unsupported resumed edit kind: {edit_kind}")
            plan = await self._plan_edit(runtime, request, source_path, edit_kind)
            write_json(plan_path, plan.model_dump())
            checkpoint = {
                **checkpoint,
                "mode": mode,
                "stage": "planned",
                "edit_kind": edit_kind,
                "initial_source_sha256": self._source_sha256(source_path),
            }
            write_json(checkpoint_path, checkpoint)

        stage = str(checkpoint.get("stage") or "refining")
        if stage == "planned":
            if mode == "generate":
                if not source_path.read_text(encoding="utf-8").strip():
                    context = AgentToolContext(workspace=workspace, source_path=source_path)
                    coder = runtime.agent(
                        name="object-coder",
                        instructions=object_prompt("coder", articulation=request.articulation),
                        tools=WRITE_TOOLS,
                    )
                    await runtime.run(
                        agent=coder,
                        input=user_input(
                            json.dumps({
                                "requirement": request.requirement,
                                "articulation_required": request.articulation,
                                "plan": plan.model_dump(),
                                "assignment": "Write source.py with the complete initial implementation.",
                            }, ensure_ascii=False),
                            request.image_paths,
                        ),
                        role="coder:initial",
                        stage="initial_code:resume",
                        context=context,
                    )
                    self._require_tool_event(context, "write_file", "initial coder")
            else:
                initial_hash = checkpoint.get("initial_source_sha256")
                if (
                    not request.check_first
                    and (not initial_hash or initial_hash == self._source_sha256(source_path))
                ):
                    context = AgentToolContext(workspace=workspace, source_path=source_path)
                    coder = runtime.agent(
                        name="object-coder",
                        instructions=object_prompt("coder", articulation=request.articulation),
                        tools=PATCH_TOOLS,
                    )
                    await runtime.run(
                        agent=coder,
                        input=user_input(
                            json.dumps({
                                "edit_kind": checkpoint.get("edit_kind", "continue"),
                                "requirement": request.requirement,
                                "plan": plan.model_dump(),
                                "assignment": "Read source.py and apply the requested minimal patch.",
                            }, ensure_ascii=False),
                            request.image_paths,
                        ),
                        role="coder:initial",
                        stage="initial_patch:resume",
                        context=context,
                    )
                    self._require_tool_event(context, "apply_patch", "initial edit coder")
            checkpoint = {**checkpoint, "stage": "refining", "next_round": 1}
            write_json(checkpoint_path, checkpoint)

        return await self._iterate(
            runtime=runtime,
            request=request,
            workspace=workspace,
            source_path=source_path,
            mode=mode,
            plan=plan,
            resume_state=checkpoint,
        )

    def _runtime(
        self,
        request: ObjectRequest,
        workspace: Path,
        *,
        mode: str,
        **fields: object,
    ) -> AgentRuntime:
        executor_timeout = _asset_executor_timeout_seconds()
        runtime = AgentRuntime(
            model_profile=self.model_profile,
            workspace=workspace,
            task_id=request.task_id,
        )
        runtime.write_runtime_config(
            workflow="object_agent",
            request=request,
            execution={
                "render": True,
                "render_view_count": 8,
                "render_elevation": 15.0,
                "export_urdf": True,
                "timeout": executor_timeout,
                **_render_execution_config(),
            },
            context_policy=_CONTEXT_POLICY,
            mode=mode,
            **fields,
        )
        runtime.usage.update_manifest(
            task_id=request.task_id,
            mode=mode,
            requirement=request.requirement,
            context_policy=_CONTEXT_POLICY,
            status="running",
            **fields,
        )
        return runtime

    async def _plan_generation(
        self,
        runtime: AgentRuntime,
        request: ObjectRequest,
    ) -> ObjectPlan:
        planner = runtime.agent(
            name="object-planner",
            instructions=object_prompt("planner", articulation=request.articulation),
            output_type=ObjectPlan,
        )
        result = await runtime.run(
            agent=planner,
            input=user_input(request.requirement, request.image_paths),
            role="planner",
            stage="plan",
        )
        return self._typed_output(result.final_output, ObjectPlan)

    async def _plan_edit(
        self,
        runtime: AgentRuntime,
        request: ObjectRequest,
        source_path: Path,
        edit_kind: EditKind,
    ) -> EditPlan:
        planner = runtime.agent(
            name="object-edit-planner",
            instructions=object_prompt("edit_planner", articulation=request.articulation),
            tools=READ_TOOLS,
            output_type=EditPlan,
        )
        context = AgentToolContext(workspace=runtime.workspace, source_path=source_path)
        result = await runtime.run(
            agent=planner,
            input=user_input(
                json.dumps(
                    {
                        "source_file": "source.py",
                        "edit_kind": edit_kind,
                        "requirement": request.requirement,
                    },
                    ensure_ascii=False,
                ),
                request.image_paths,
            ),
            role="planner",
            stage="edit_plan",
            context=context,
        )
        self._require_tool_event(context, "read_file", "edit planner")
        return self._typed_output(result.final_output, EditPlan)

    async def _iterate(
        self,
        *,
        runtime: AgentRuntime,
        request: ObjectRequest,
        workspace: Path,
        source_path: Path,
        mode: str,
        plan: ObjectPlan | EditPlan,
        resume_state: dict[str, Any] | None = None,
    ) -> ObjectRunResult:
        rounds_root = workspace / "rounds"
        rounds_root.mkdir(exist_ok=resume_state is not None)
        repairer = runtime.agent(
            name="object-coder",
            instructions=object_prompt("coder", articulation=request.articulation),
            tools=PATCH_TOOLS,
        )
        debugger = runtime.agent(
            name="object-debugger",
            instructions=object_prompt("debugger", articulation=request.articulation),
            tools=READ_TOOLS,
            output_type=DebuggerDecision,
        )
        image_critic = runtime.agent(
            name="object-image-critic",
            instructions=object_prompt("image_critic", articulation=request.articulation),
            output_type=ImageCriticDecision,
        )
        code_critic = runtime.agent(
            name="object-code-critic",
            instructions=object_prompt("code_critic", articulation=request.articulation),
            tools=READ_TOOLS,
            output_type=CodeCriticDecision,
        )
        engineering_critic = runtime.agent(
            name="object-engineering-critic",
            instructions=object_prompt("engineering_critic", articulation=request.articulation),
            tools=READ_TOOLS,
            output_type=EngineeringCriticDecision,
            strict_json_schema=False,
        )
        state = resume_state or {}
        image_history: list[dict[str, object]] = list(state.get("image_history", []))
        code_history: list[dict[str, object]] = list(state.get("code_history", []))
        checker_history: list[dict[str, object]] = list(state.get("checker_history", []))
        engineering_history: list[dict[str, object]] = list(
            state.get("engineering_history", [])
        )
        image_critic_corrections: list[str] = list(
            state.get("image_critic_corrections", [])
        )
        failures: list[dict[str, object]] = list(state.get("failures", []))
        final: tuple[int, ExecutionResult, bool, str] | None = None
        start_round = int(state.get("next_round", 1))
        end_round = start_round + request.max_rounds - 1
        executor_timeout = _asset_executor_timeout_seconds()

        def checkpoint_fields(next_round: int) -> dict[str, object]:
            return {
                "mode": mode,
                "stage": "refining",
                "next_round": next_round,
                "image_history": image_history,
                "code_history": code_history,
                "checker_history": checker_history,
                "engineering_history": engineering_history,
                "image_critic_corrections": image_critic_corrections,
                "failures": failures,
            }

        for round_number in range(start_round, end_round + 1):
            round_root = rounds_root / f"round_{round_number:02d}"
            self._preserve_interrupted_round(round_root)
            snapshot = rounds_root / f"round_{round_number:02d}_source.py"
            shutil.copy2(source_path, snapshot)
            try:
                execution = execute_asset_source(
                    source_path,
                    round_root,
                    render=True,
                    export_urdf=True,
                    timeout=executor_timeout,
                )
            except AssetInfrastructureError as exc:
                failure = {
                    "round": round_number,
                    "stage": "execution_infrastructure",
                    "error": str(exc),
                }
                failures.append(failure)
                self._write_checkpoint(
                    workspace, **checkpoint_fields(round_number)
                )
                runtime.usage.update_manifest(
                    status="failed",
                    error_type="AssetInfrastructureError",
                    error="external execution infrastructure failed",
                    failures=failures,
                )
                raise
            except AssetExecutionError as exc:
                failure = {"round": round_number, "stage": "execute", "error": str(exc)}
                failures.append(failure)
                debug_context = AgentToolContext(workspace=workspace, source_path=source_path)
                debug_result = await runtime.run(
                    agent=debugger,
                    input=json.dumps(
                        {
                            "requirement": request.requirement,
                            "plan": plan.model_dump(),
                            "assigned_source": "source.py",
                            "round": round_number,
                            "execution_error": str(exc),
                        },
                        ensure_ascii=False,
                    ),
                    role=f"debugger:round:{round_number}",
                    stage=f"debugger:{round_number}",
                    context=debug_context,
                )
                self._require_tool_event(debug_context, "read_file", "debugger")
                decision = self._typed_output(debug_result.final_output, DebuggerDecision)
                round_root.mkdir(parents=True, exist_ok=True)
                write_json(round_root / "debugger.json", decision.model_dump())
                if round_number == end_round:
                    self._write_checkpoint(
                        workspace, **checkpoint_fields(round_number + 1)
                    )
                    runtime.usage.update_manifest(status="failed", failures=failures)
                    raise AssetExecutionError(
                        f"Final generated source failed execution after "
                        f"{request.max_rounds} attempts: {exc}"
                    ) from exc
                await self._repair(
                    runtime=runtime,
                    repairer=repairer,
                    workspace=workspace,
                    source_path=source_path,
                    role=f"coder:debugger-repair:{round_number}",
                    stage=f"debugger_patch:{round_number}",
                    payload={
                        "requirement": request.requirement,
                        "plan": plan.model_dump(),
                        "assignment": "Patch the exact execution failure diagnosed by the debugger.",
                        "debugger": decision.model_dump(),
                    },
                )
                self._write_checkpoint(
                    workspace, **checkpoint_fields(round_number + 1)
                )
                continue

            # Preserve the historical last-round fallback only when no engineering
            # gates are configured. Mandatory checkers always inspect the final round.
            if not request.checker_specs and round_number == end_round:
                final = (
                    round_number,
                    execution,
                    False,
                    "round_limit_after_execution",
                )
                break

            image_payload = {
                "requirement": request.requirement,
                "planner_checklist": self._critic_checklist(plan),
                "round": round_number,
                "max_rounds": end_round,
                "reference_image_count": len(request.image_paths),
                "render_image_count": len(execution.render_paths),
                "previous_image_decisions": image_history,
                "code_critic_corrections": image_critic_corrections,
            }
            image_result = await runtime.run(
                agent=image_critic,
                input=user_input(
                    json.dumps(image_payload, ensure_ascii=False),
                    (*request.image_paths, *execution.render_paths),
                ),
                role=f"image-critic:round:{round_number}",
                stage=f"image_critic:{round_number}",
            )
            image_decision = self._typed_output(
                image_result.final_output,
                ImageCriticDecision,
            )
            write_json(round_root / "image_critique.json", image_decision.model_dump())
            image_history.append(image_decision.model_dump())

            checker_runs = run_checkers(
                request.checker_specs,
                execution=execution,
                source_path=source_path,
                round_root=round_root,
            )
            analysis_context = build_analysis_context(
                source_path=source_path,
                geometry_path=execution.glb_path,
                source_index_path=execution.source_index_path,
                checker_runs=checker_runs,
                print_orientation_editable=request.repair_policy.print_orientation_editable,
            )
            write_json(round_root / "analysis_context.json", analysis_context.model_dump())
            localization_report: LocalizationReport | None = None
            source_index = None
            if execution.source_index_path is not None and execution.source_index_path.is_file():
                source_index = load_source_index(execution.source_index_path)
                all_findings = [
                    finding
                    for run in checker_runs
                    for finding in run.result.findings
                ]
                localized, localization_report = localize_findings(
                    all_findings,
                    source_index,
                    checker_contexts=analysis_context.checker_contexts,
                )
                localized_by_id = {finding.finding_id: finding for finding in localized}
                checker_runs = [
                    CheckerRun(
                        spec=run.spec,
                        result=run.result.model_copy(
                            update={
                                "findings": [
                                    localized_by_id.get(finding.finding_id, finding)
                                    for finding in run.result.findings
                                ]
                            }
                        ),
                        output_dir=run.output_dir,
                        command=run.command,
                    )
                    for run in checker_runs
                ]
                for run in checker_runs:
                    write_json(run.output_dir / "result.json", run.result.model_dump())
                write_json(
                    round_root / "localization.json",
                    localization_report.model_dump(),
                )
            write_json(
                round_root / "findings.json",
                findings_payload(run.result for run in checker_runs),
            )
            checker_record = {
                "round": round_number,
                "analysis_context": str(round_root / "analysis_context.json"),
                "source_index": (
                    str(execution.source_index_path)
                    if execution.source_index_path is not None
                    else None
                ),
                "localization": (
                    str(round_root / "localization.json")
                    if localization_report is not None
                    else None
                ),
                "results": [
                    {
                        "required": run.spec.required,
                        **run.result.model_dump(),
                        "output_dir": str(run.output_dir),
                    }
                    for run in checker_runs
                ],
            }
            checker_history.append(checker_record)
            checker_errors = required_checker_errors(checker_runs)
            if checker_errors:
                error_details = [
                    {
                        "checker": run.spec.name,
                        "summary": run.result.summary,
                        "output_dir": str(run.output_dir),
                    }
                    for run in checker_errors
                ]
                failure = {
                    "round": round_number,
                    "stage": "checker_feedback_unavailable",
                    "error": "required checker did not produce usable feedback",
                    "details": error_details,
                }
                failures.append(failure)
                runtime.usage.update_manifest(
                    status="running",
                    error_type=None,
                    error=None,
                    failures=failures,
                    checker_history=checker_history,
                )

            # An unavailable checker cannot provide actionable repair guidance. Keep
            # its ERROR result in the evidence/history, but let the visual/code loop
            # continue and never present an infrastructure failure as a repair target.
            mandatory_failures = [
                run
                for run in required_checker_failures(checker_runs)
                if run.result.status != "ERROR"
            ]
            code_decision: CodeCriticDecision | None = None
            appearance_approved = image_decision.approved
            if not image_decision.approved:
                code_context = AgentToolContext(
                    workspace=workspace, source_path=source_path
                )
                code_result = await runtime.run(
                    agent=code_critic,
                    input=user_input(
                        json.dumps(
                            {
                                "requirement": request.requirement,
                                "plan": plan.model_dump(),
                                "assigned_source": "source.py",
                                "round": round_number,
                                "max_rounds": end_round,
                                "image_critic": image_decision.model_dump(),
                                "previous_code_decisions": code_history,
                            },
                            ensure_ascii=False,
                        ),
                        (*request.image_paths, *execution.render_paths),
                    ),
                    role=f"code-critic:round:{round_number}",
                    stage=f"code_critic:{round_number}",
                    context=code_context,
                )
                code_decision = self._normalize_code_critic_decision(
                    self._typed_output(code_result.final_output, CodeCriticDecision),
                    source_grounded=any(
                        event.tool == "read_file" for event in code_context.events
                    ),
                )
                write_json(
                    round_root / "code_critique.json", code_decision.model_dump()
                )
                code_history.append(code_decision.model_dump())
                image_critic_corrections = code_decision.image_critic_corrections
                appearance_approved = code_decision.approved

            if not request.checker_specs:
                if appearance_approved:
                    reason = (
                        "image_critic_approved"
                        if image_decision.approved
                        else "code_critic_approved"
                    )
                    final = (round_number, execution, True, reason)
                    break
                assert code_decision is not None
                await self._repair(
                    runtime=runtime,
                    repairer=repairer,
                    workspace=workspace,
                    source_path=source_path,
                    role=f"coder:critic-repair:{round_number}",
                    stage=f"critic_patch:{round_number}",
                    payload={
                        "requirement": request.requirement,
                        "plan": plan.model_dump(),
                        "assignment": "Patch every valid required change from the Code Critic.",
                        "code_critic": code_decision.model_dump(),
                    },
                )
                self._write_checkpoint(
                    workspace, **checkpoint_fields(round_number + 1)
                )
                continue

            engineering_decision: EngineeringCriticDecision | None = None
            if mandatory_failures:
                engineering_context = AgentToolContext(
                    workspace=workspace, source_path=source_path
                )
                engineering_result = await runtime.run(
                    agent=engineering_critic,
                    input=user_input(
                        json.dumps(
                            {
                                "requirement": request.requirement,
                                "plan": plan.model_dump(),
                                "assigned_source": "source.py",
                                "round": round_number,
                                "max_rounds": end_round,
                                **_engineering_feedback(
                                    checker_runs, context=analysis_context,
                                    localization=localization_report,
                                    history=self._read_repair_history(workspace),
                                    round_root=round_root, workspace=workspace,
                                ),
                                "maximum_repair_proposals": (
                                    request.repair_policy.max_candidates_per_round
                                ),
                                "assignment": (
                                    "Propose bounded source candidates supported by the "
                                    "typed findings and localization. Do not alter checker "
                                    "assumptions or claim a repair passes before regression."
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        (*request.image_paths, *execution.render_paths),
                    ),
                    role=f"engineering-critic:round:{round_number}",
                    stage=f"engineering_critic:{round_number}",
                    context=engineering_context,
                )
                self._require_tool_event(
                    engineering_context, "read_file", "engineering critic"
                )
                engineering_decision = self._normalize_engineering_decision(
                    self._typed_output(
                        engineering_result.final_output,
                        EngineeringCriticDecision,
                    ),
                    has_required_failures=True,
                )
                write_json(
                    round_root / "engineering_critique.json",
                    engineering_decision.model_dump(),
                )
                write_json(
                    round_root / "repair_proposals.json",
                    {
                        "version": 1,
                        "proposals": [
                            proposal.model_dump()
                            for proposal in engineering_decision.repair_proposals
                        ],
                        "unresolved_findings": engineering_decision.unresolved_findings,
                    },
                )
                engineering_history.append(
                    {"round": round_number, **engineering_decision.model_dump()}
                )

            if appearance_approved and not mandatory_failures:
                final = (
                    round_number,
                    execution,
                    not bool(checker_errors),
                    (
                        "appearance_and_required_checkers_approved"
                        if not checker_errors
                        else "appearance_approved_checker_feedback_unavailable"
                    ),
                )
                break

            if mandatory_failures and engineering_decision is not None:
                candidate_outcome = await self._attempt_engineering_candidates(
                    runtime=runtime,
                    request=request,
                    workspace=workspace,
                    source_path=source_path,
                    round_root=round_root,
                    round_number=round_number,
                    plan=plan,
                    repairer=repairer,
                    image_critic=image_critic,
                    code_critic=code_critic,
                    baseline_execution=execution,
                    baseline_runs=checker_runs,
                    baseline_analysis_context=analysis_context,
                    source_index=source_index,
                    engineering_decision=engineering_decision,
                    code_decision=code_decision,
                )
                checker_record["candidate_attempts"] = list(candidate_outcome.attempts)
                if candidate_outcome.execution is not None:
                    accepted_runs = list(candidate_outcome.checker_runs)
                    accepted_record = {
                        "round": round_number,
                        "candidate": True,
                        "results": [
                            {
                                "required": run.spec.required,
                                **run.result.model_dump(),
                                "output_dir": str(run.output_dir),
                            }
                            for run in accepted_runs
                        ],
                    }
                    checker_history.append(accepted_record)
                    accepted_failures = required_checker_failures(accepted_runs)
                    if candidate_outcome.appearance_approved and not accepted_failures:
                        final = (
                            round_number,
                            candidate_outcome.execution,
                            True,
                            "accepted_candidate_passed_all_required_gates",
                        )
                        break
                    execution = candidate_outcome.execution
                    checker_runs = accepted_runs
                    mandatory_failures = accepted_failures
                    appearance_approved = candidate_outcome.appearance_approved
                    if round_number < end_round:
                        self._write_checkpoint(
                            workspace, **checkpoint_fields(round_number + 1)
                        )
                        continue
                elif round_number < end_round:
                    self._write_checkpoint(
                        workspace, **checkpoint_fields(round_number + 1)
                    )
                    continue

            gate_details = {
                "appearance_approved": appearance_approved,
                "required_checker_unavailable": [
                    {
                        "checker": run.spec.name,
                        "summary": run.result.summary,
                    }
                    for run in checker_errors
                ],
                "required_checker_failures": [
                    {
                        "checker": run.spec.name,
                        "status": run.result.status,
                        "summary": run.result.summary,
                        "violations": run.result.violations,
                    }
                    for run in mandatory_failures
                ],
            }
            if round_number == end_round:
                failure = {
                    "round": round_number,
                    "stage": "publication_gate",
                    "error": "mandatory publication gate did not pass",
                    "details": gate_details,
                }
                failures.append(failure)
                self._write_checkpoint(
                    workspace, **checkpoint_fields(round_number + 1)
                )
                runtime.usage.update_manifest(
                    status="failed",
                    error_type="WorkflowGateError",
                    error="mandatory publication gate did not pass",
                    failures=failures,
                    checker_history=checker_history,
                    engineering_critic_history=engineering_history,
                    image_critic_history=image_history,
                    code_critic_history=code_history,
                )
                raise WorkflowGateError(
                    "Final round was not published because appearance and all "
                    "required checker gates did not pass"
                )

            repair_payload: dict[str, object] = {
                "requirement": request.requirement,
                "plan": plan.model_dump(),
                "assignment": (
                    "Apply one minimal patch covering every valid visual change "
                    "and every Engineering Critic change. Preserve unrelated appearance. "
                    "The next round will re-run the original checkers."
                ),
            }
            if code_decision is not None and not code_decision.approved:
                repair_payload["code_critic"] = code_decision.model_dump()
            if engineering_decision is not None:
                # Engineering changes are attempted only in isolated candidates.
                # Reaching this branch means no required checker failure remains.
                raise RuntimeError("unexpected direct engineering repair path")
            await self._repair(
                runtime=runtime,
                repairer=repairer,
                workspace=workspace,
                source_path=source_path,
                role=f"coder:gate-repair:{round_number}",
                stage=f"gate_patch:{round_number}",
                payload=repair_payload,
            )
            self._write_checkpoint(
                workspace, **checkpoint_fields(round_number + 1)
            )

        if final is None:
            raise RuntimeError("Object refinement ended without a publishable execution")
        selected_round, execution, approved, finalization_reason = final
        final_glb, final_urdf, final_renders = self._publish(workspace, execution)
        joint_states_path = workspace / "scene.joint_states.json"
        render_metadata_path = workspace / "render" / "meta.json"
        runtime.usage.update_manifest(
            status="completed",
            error_type=None,
            error=None,
            mode=mode,
            selected_round=selected_round,
            approved=approved,
            critic_skipped=finalization_reason == "round_limit_after_execution",
            finalization_reason=finalization_reason,
            failures=failures,
            image_critic_history=image_history,
            code_critic_history=code_history,
            checker_history=checker_history,
            engineering_critic_history=engineering_history,
            source_path=str(source_path),
            glb_path=str(final_glb),
            urdf_path=None if final_urdf is None else str(final_urdf),
            render_paths=[str(path) for path in final_renders],
            joint_states_path=(
                str(joint_states_path) if joint_states_path.is_file() else None
            ),
            render_metadata_path=(
                str(render_metadata_path) if render_metadata_path.is_file() else None
            ),
            source_index_path=(
                str(workspace / "source_index.json")
                if (workspace / "source_index.json").is_file()
                else None
            ),
        )
        self._write_checkpoint(
            workspace,
            mode=mode,
            stage="completed",
            next_round=selected_round + 1,
            image_history=image_history,
            code_history=code_history,
            checker_history=checker_history,
            engineering_history=engineering_history,
            image_critic_corrections=image_critic_corrections,
            failures=failures,
            approved=approved,
            finalization_reason=finalization_reason,
        )
        return ObjectRunResult(
            workspace=workspace,
            source_path=source_path,
            glb_path=final_glb,
            urdf_path=final_urdf,
            render_paths=tuple(final_renders),
            selected_round=selected_round,
            approved=approved,
            usage=runtime.usage.totals(),
        )

    @staticmethod
    def _write_checkpoint(workspace: Path, **fields: object) -> Path:
        checkpoint_path = workspace / "checkpoint.json"
        payload = read_json(checkpoint_path) if checkpoint_path.is_file() else {
            "version": 1,
            "workflow": "object_agent",
        }
        payload.update(fields)
        return write_json(checkpoint_path, payload)

    @staticmethod
    def _source_sha256(source_path: Path) -> str:
        return hashlib.sha256(source_path.read_bytes()).hexdigest()

    @staticmethod
    def _preserve_interrupted_round(round_root: Path) -> None:
        if not round_root.exists():
            return
        index = 1
        while True:
            preserved = round_root.with_name(f"{round_root.name}_interrupted_{index:02d}")
            if not preserved.exists():
                round_root.rename(preserved)
                return
            index += 1

    @staticmethod
    def _published_files_exist(workspace: Path) -> bool:
        return (workspace / "source.py").is_file() and (workspace / "scene.glb").is_file()

    @staticmethod
    def _load_completed_result(workspace: Path, manifest: dict[str, Any]) -> ObjectRunResult:
        render_root = workspace / "render"
        urdf_path = workspace / "scene.urdf"
        usage = UsageRecorder(workspace).totals()
        return ObjectRunResult(
            workspace=workspace,
            source_path=workspace / "source.py",
            glb_path=workspace / "scene.glb",
            urdf_path=urdf_path if urdf_path.is_file() else None,
            render_paths=tuple(sorted(render_root.glob("*"))) if render_root.is_dir() else (),
            selected_round=int(manifest.get("selected_round", 0)),
            approved=bool(manifest.get("approved", False)),
            usage=usage,
        )

    @staticmethod
    def _read_repair_history(workspace: Path) -> list[dict[str, object]]:
        history_path = workspace / "repair_history.jsonl"
        if not history_path.is_file():
            return []
        rows: list[dict[str, object]] = []
        for line in history_path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows[-20:]

    async def _attempt_engineering_candidates(
        self,
        *,
        runtime: AgentRuntime,
        request: ObjectRequest,
        workspace: Path,
        source_path: Path,
        round_root: Path,
        round_number: int,
        plan: ObjectPlan | EditPlan,
        repairer: Any,
        image_critic: Any,
        code_critic: Any,
        baseline_execution: ExecutionResult,
        baseline_runs: list[CheckerRun],
        baseline_analysis_context: AnalysisContext,
        source_index: SourceIndex | None,
        engineering_decision: EngineeringCriticDecision,
        code_decision: CodeCriticDecision | None,
    ) -> _CandidateOutcome:
        attempts: list[dict[str, object]] = []
        if source_index is None:
            attempts.append(
                {
                    "accepted": False,
                    "reason": "source index is unavailable; source repair is unresolved",
                }
            )
            return _CandidateOutcome(None, (), False, tuple(attempts))

        baseline_findings = [
            finding for run in baseline_runs for finding in run.result.findings
        ]
        controller = RepairController(
            workspace=workspace,
            round_root=round_root,
            baseline_source=source_path,
            source_index=source_index,
            findings=baseline_findings,
            checker_specs_sha256=baseline_analysis_context.checker_specs_sha256,
            policy=request.repair_policy,
        )
        proposals = engineering_decision.repair_proposals[
            : request.repair_policy.max_candidates_per_round
        ]
        rejected_proposals: list[dict[str, object]] = []
        for proposal_index, raw_proposal in enumerate(proposals, 1):
            budget_error = controller.budget_error()
            if budget_error:
                rejected_proposals.append(
                    {
                        "proposal_id": raw_proposal.proposal_id,
                        "reason": budget_error,
                    }
                )
                break
            proposal, proposal_errors = controller.normalize_proposal(raw_proposal)
            if proposal is None:
                record = {
                    "round": round_number,
                    "proposal_id": raw_proposal.proposal_id,
                    "accepted": False,
                    "reason": "proposal rejected before execution",
                    "errors": proposal_errors,
                }
                attempts.append(record)
                rejected_proposals.append(record)
                controller.record(record)
                continue

            candidate_root, candidate_source, fingerprint = controller.prepare_candidate(
                proposal, proposal_index
            )
            candidate_relative = candidate_source.relative_to(workspace).as_posix()
            try:
                await self._repair(
                    runtime=runtime,
                    repairer=repairer,
                    workspace=workspace,
                    source_path=candidate_source,
                    role=f"coder:engineering-candidate:{round_number}:{proposal_index}",
                    stage=f"engineering_candidate_patch:{round_number}:{proposal_index}",
                    payload={
                        "requirement": request.requirement,
                        "plan": plan.model_dump(),
                        "assignment": (
                            "Apply only this bounded RepairProposal to the assigned "
                            "candidate source. Preserve everything outside allowed_scopes."
                        ),
                        "repair_proposal": proposal.model_dump(),
                        "checker_evidence": [
                            run.result.model_dump() for run in baseline_runs
                        ],
                        "code_critic": (
                            code_decision.model_dump()
                            if code_decision is not None and not code_decision.approved
                            else None
                        ),
                    },
                )
            except Exception as error:
                record = {
                    "round": round_number,
                    "proposal_id": proposal.proposal_id,
                    "fingerprint": fingerprint,
                    "candidate": candidate_relative,
                    "accepted": False,
                    "reason": f"coder candidate failed: {type(error).__name__}: {error}",
                }
                write_json(candidate_root / "decision.json", record)
                controller.record(record)
                attempts.append(record)
                continue

            try:
                scope_validation = validate_patch_scope(
                    source_path,
                    candidate_source,
                    proposal=proposal,
                    source_index=source_index,
                )
            except (SyntaxError, ValueError) as error:
                scope_validation = None
                scope_errors = [f"candidate source is invalid: {type(error).__name__}: {error}"]
            else:
                scope_errors = list(scope_validation.violations)
            immutable_ok, immutable_errors = immutable_inputs_match(
                baseline_analysis_context.immutable_inputs
            )
            if scope_validation is None or not scope_validation.valid or not immutable_ok:
                record = {
                    "round": round_number,
                    "proposal_id": proposal.proposal_id,
                    "fingerprint": fingerprint,
                    "candidate": candidate_relative,
                    "accepted": False,
                    "reason": "candidate violated source scope or immutable analysis inputs",
                    "scope_validation": (
                        scope_validation.model_dump() if scope_validation is not None else None
                    ),
                    "errors": [*scope_errors, *immutable_errors],
                }
                write_json(candidate_root / "decision.json", record)
                controller.record(record)
                attempts.append(record)
                continue

            execution_root = candidate_root / "execution"
            try:
                candidate_execution = execute_asset_source(
                    candidate_source,
                    execution_root,
                    render=True,
                    export_urdf=True,
                    timeout=_asset_executor_timeout_seconds(),
                )
            except AssetInfrastructureError:
                raise
            except AssetExecutionError as error:
                record = {
                    "round": round_number,
                    "proposal_id": proposal.proposal_id,
                    "fingerprint": fingerprint,
                    "candidate": candidate_relative,
                    "accepted": False,
                    "reason": f"candidate execution failed: {error}",
                    "scope_validation": scope_validation.model_dump(),
                }
                write_json(candidate_root / "decision.json", record)
                controller.record(record)
                attempts.append(record)
                continue

            candidate_runs = run_checkers(
                request.checker_specs,
                execution=candidate_execution,
                source_path=candidate_source,
                round_root=candidate_root,
            )
            candidate_context = build_analysis_context(
                source_path=candidate_source,
                geometry_path=candidate_execution.glb_path,
                source_index_path=candidate_execution.source_index_path,
                checker_runs=candidate_runs,
                print_orientation_editable=request.repair_policy.print_orientation_editable,
            )
            write_json(
                candidate_root / "analysis_context.json", candidate_context.model_dump()
            )
            if candidate_context.checker_specs_sha256 != baseline_analysis_context.checker_specs_sha256:
                record = {
                    "round": round_number,
                    "proposal_id": proposal.proposal_id,
                    "fingerprint": fingerprint,
                    "candidate": candidate_relative,
                    "accepted": False,
                    "reason": "checker specification hash changed during candidate evaluation",
                }
                write_json(candidate_root / "decision.json", record)
                controller.record(record)
                attempts.append(record)
                continue

            candidate_errors = required_checker_errors(candidate_runs)
            if candidate_errors:
                record = {
                    "round": round_number,
                    "proposal_id": proposal.proposal_id,
                    "fingerprint": fingerprint,
                    "candidate": candidate_relative,
                    "accepted": False,
                    "reason": "required checker infrastructure failed for candidate",
                    "errors": [run.result.summary for run in candidate_errors],
                }
                write_json(candidate_root / "decision.json", record)
                controller.record(record)
                attempts.append(record)
                # Reject this candidate, but continue evaluating later proposals.
                # A checker infrastructure failure is local to the candidate and
                # must not abort the whole repair round.
                continue

            if (
                candidate_execution.source_index_path is not None
                and candidate_execution.source_index_path.is_file()
            ):
                candidate_index = load_source_index(candidate_execution.source_index_path)
                localized, report = localize_findings(
                    [
                        finding
                        for run in candidate_runs
                        for finding in run.result.findings
                    ],
                    candidate_index,
                    checker_contexts=candidate_context.checker_contexts,
                )
                localized_by_id = {finding.finding_id: finding for finding in localized}
                candidate_runs = [
                    CheckerRun(
                        spec=run.spec,
                        result=run.result.model_copy(
                            update={
                                "findings": [
                                    localized_by_id.get(finding.finding_id, finding)
                                    for finding in run.result.findings
                                ]
                            }
                        ),
                        output_dir=run.output_dir,
                        command=run.command,
                    )
                    for run in candidate_runs
                ]
                write_json(candidate_root / "localization.json", report.model_dump())
            write_json(
                candidate_root / "findings.json",
                findings_payload(run.result for run in candidate_runs),
            )

            preservation_payload = {
                "requirement": request.requirement,
                "round": round_number,
                "proposal": proposal.model_dump(),
                "review_mode": "candidate_preservation",
                "image_order": {
                    "reference_count": len(request.image_paths),
                    "baseline_count": len(baseline_execution.render_paths),
                    "candidate_count": len(candidate_execution.render_paths),
                },
                "instruction": (
                    "Judge whether the candidate preserves the requested appearance and "
                    "function. Do not infer checker success from images."
                ),
            }
            image_result = await runtime.run(
                agent=image_critic,
                input=user_input(
                    json.dumps(preservation_payload, ensure_ascii=False),
                    (
                        *request.image_paths,
                        *baseline_execution.render_paths,
                        *candidate_execution.render_paths,
                    ),
                ),
                role=f"image-critic:candidate:{round_number}:{proposal_index}",
                stage=f"candidate_image_critic:{round_number}:{proposal_index}",
            )
            candidate_image_decision = self._typed_output(
                image_result.final_output, ImageCriticDecision
            )
            write_json(
                candidate_root / "image_critique.json",
                candidate_image_decision.model_dump(),
            )
            candidate_appearance_approved = candidate_image_decision.approved
            if not candidate_appearance_approved:
                code_context = AgentToolContext(
                    workspace=workspace, source_path=candidate_source
                )
                candidate_code_result = await runtime.run(
                    agent=code_critic,
                    input=user_input(
                        json.dumps(
                            {
                                **preservation_payload,
                                "assigned_source": candidate_relative,
                                "image_critic": candidate_image_decision.model_dump(),
                            },
                            ensure_ascii=False,
                        ),
                        (
                            *request.image_paths,
                            *baseline_execution.render_paths,
                            *candidate_execution.render_paths,
                        ),
                    ),
                    role=f"code-critic:candidate:{round_number}:{proposal_index}",
                    stage=f"candidate_code_critic:{round_number}:{proposal_index}",
                    context=code_context,
                )
                candidate_code_decision = self._normalize_code_critic_decision(
                    self._typed_output(candidate_code_result.final_output, CodeCriticDecision),
                    source_grounded=any(
                        event.tool == "read_file" for event in code_context.events
                    ),
                )
                write_json(
                    candidate_root / "code_critique.json",
                    candidate_code_decision.model_dump(),
                )
                candidate_appearance_approved = candidate_code_decision.approved

            decision = assess_candidate(
                [run.result for run in baseline_runs],
                [run.result for run in candidate_runs],
                target_finding_ids=proposal.finding_ids,
                appearance_approved=candidate_appearance_approved,
                policy=request.repair_policy,
            )
            record = {
                "round": round_number,
                "proposal_id": proposal.proposal_id,
                "fingerprint": fingerprint,
                "candidate": candidate_relative,
                "baseline_source_sha256": baseline_analysis_context.source_sha256,
                "candidate_source_sha256": candidate_context.source_sha256,
                "baseline_geometry_sha256": baseline_analysis_context.geometry_sha256,
                "candidate_geometry_sha256": candidate_context.geometry_sha256,
                "scope_validation": scope_validation.model_dump(),
                **decision.model_dump(),
            }
            write_json(candidate_root / "decision.json", record)
            controller.record(record)
            attempts.append(record)
            if decision.accepted:
                shutil.copy2(candidate_source, source_path)
                return _CandidateOutcome(
                    candidate_execution,
                    tuple(candidate_runs),
                    candidate_appearance_approved,
                    tuple(attempts),
                )

        write_json(
            round_root / "proposal_rejections.json",
            {"version": 1, "rejections": rejected_proposals},
        )
        return _CandidateOutcome(None, (), False, tuple(attempts))

    async def _repair(
        self,
        *,
        runtime: AgentRuntime,
        repairer: Any,
        workspace: Path,
        source_path: Path,
        role: str,
        stage: str,
        payload: dict[str, object],
    ) -> None:
        context = AgentToolContext(workspace=workspace, source_path=source_path)
        assigned_source = source_path.relative_to(workspace).as_posix()
        payload = {"assigned_source": assigned_source, **payload}
        await runtime.run(
            agent=repairer,
            input=json.dumps(payload, ensure_ascii=False),
            role=role,
            stage=stage,
            context=context,
        )
        self._require_tool_event(context, "apply_patch", stage)

    @staticmethod
    def _publish(
        workspace: Path,
        execution: ExecutionResult,
    ) -> tuple[Path, Path | None, list[Path]]:
        glb_path = workspace / "scene.glb"
        shutil.copy2(execution.glb_path, glb_path)
        urdf_path = None
        workspace_urdf = workspace / "scene.urdf"
        meshes_root = workspace / "meshes"
        if execution.urdf_path is not None:
            urdf_path = workspace_urdf
            shutil.copy2(execution.urdf_path, urdf_path)
            if meshes_root.exists():
                shutil.rmtree(meshes_root)
            shutil.copytree(execution.urdf_path.parent / "meshes", meshes_root)
        else:
            workspace_urdf.unlink(missing_ok=True)
            if meshes_root.exists():
                shutil.rmtree(meshes_root)
        render_root = workspace / "render"
        if render_root.exists():
            shutil.rmtree(render_root)
        render_root.mkdir()
        render_paths: list[Path] = []
        for source_render in execution.render_paths:
            destination = render_root / source_render.name
            shutil.copy2(source_render, destination)
            render_paths.append(destination)
        source_metadata = execution.glb_path.parent / "meta.json"
        if source_metadata.is_file():
            shutil.copy2(source_metadata, render_root / "meta.json")
        source_joint_states = execution.glb_path.with_name(
            f"{execution.glb_path.stem}.joint_states.json"
        )
        workspace_joint_states = workspace / "scene.joint_states.json"
        if source_joint_states.is_file():
            shutil.copy2(source_joint_states, workspace_joint_states)
        else:
            workspace_joint_states.unlink(missing_ok=True)
        workspace_source_index = workspace / "source_index.json"
        if execution.source_index_path is not None and execution.source_index_path.is_file():
            shutil.copy2(execution.source_index_path, workspace_source_index)
        else:
            workspace_source_index.unlink(missing_ok=True)
        return glb_path, urdf_path, render_paths

    @staticmethod
    def _critic_checklist(plan: ObjectPlan | EditPlan) -> list[str]:
        if isinstance(plan, ObjectPlan):
            return plan.critic_checklist
        return [*plan.changes, *[f"preserve: {item}" for item in plan.preserved_features]]

    @staticmethod
    def _require_tool_event(context: AgentToolContext, expected: str, actor: str) -> None:
        if not any(event.tool == expected for event in context.events):
            raise RuntimeError(f"{actor} did not use required tool {expected}")

    @staticmethod
    def _validate_request(request: ObjectRequest) -> None:
        if not request.requirement.strip():
            raise ValueError("requirement must not be empty")
        if not request.task_id.strip():
            raise ValueError("task_id must not be empty")
        if request.max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        names = [spec.name for spec in request.checker_specs]
        if len(names) != len(set(names)):
            raise ValueError("checker names must be unique")
        for image_path in request.image_paths:
            if not Path(image_path).expanduser().resolve().is_file():
                raise FileNotFoundError(image_path)

    @staticmethod
    def _prepare_workspace(value: Path) -> Path:
        workspace = value.expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=False)
        return workspace

    @staticmethod
    def _persist_user_input(workspace: Path, request: ObjectRequest) -> None:
        (workspace / "user_input.txt").write_text(
            request.requirement,
            encoding="utf-8",
        )
        saved_images: list[dict[str, str]] = []
        if request.image_paths:
            image_root = workspace / "user_input_images"
            image_root.mkdir(exist_ok=False)
            for index, image_value in enumerate(request.image_paths, 1):
                source = Path(image_value).expanduser().resolve()
                destination = image_root / f"{index:02d}_{source.name}"
                shutil.copy2(source, destination)
                saved_images.append(
                    {
                        "source_path": str(source),
                        "saved_path": destination.relative_to(workspace).as_posix(),
                    }
                )
        write_json(
            workspace / "user_input.json",
            {
                "requirement": request.requirement,
                "image_paths": saved_images,
                "articulation": request.articulation,
                "max_rounds": request.max_rounds,
                "checker_specs": [spec.model_dump() for spec in request.checker_specs],
                "check_first": request.check_first,
                "repair_policy": request.repair_policy.model_dump(),
            },
        )

    @staticmethod
    def _typed_output(value: Any, expected: type[Any]) -> Any:
        if not isinstance(value, expected):
            raise TypeError(f"Expected {expected.__name__}, got {type(value).__name__}")
        return value

    @staticmethod
    def _normalize_code_critic_decision(
        decision: CodeCriticDecision,
        *,
        source_grounded: bool = True,
    ) -> CodeCriticDecision:
        observations = list(decision.observations)
        approved = decision.approved
        if not source_grounded:
            approved = False
            observations.append(
                "Code Critic did not inspect the assigned source; its decision "
                "cannot approve appearance or function preservation."
            )
        if approved and decision.required_changes:
            approved = False
        if approved != decision.approved or observations != decision.observations:
            return decision.model_copy(
                update={"approved": approved, "observations": observations}
            )
        return decision

    @staticmethod
    def _normalize_engineering_decision(
        decision: EngineeringCriticDecision,
        *,
        has_required_failures: bool,
    ) -> EngineeringCriticDecision:
        if (
            decision.approved
            and (decision.required_changes or decision.repair_proposals)
        ) or has_required_failures:
            return decision.model_copy(update={"approved": False})
        return decision


__all__ = ["ObjectWorkflow", "WorkflowGateError"]
