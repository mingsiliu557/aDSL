from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import time
import traceback
from typing import Any
from pydantic import ValidationError

from .checkers import (
    CheckerRun,
    required_checker_failures,
    run_checkers,
)
from .feedback_schema import build_analysis_context, findings_payload, localized_mesh_feedback
from .localization import LocalizationReport, localize_findings
from .models import (
    AnalysisContext,
    CheckerFinding,
    CheckerResult,
    CheckerSpec,
    CodeCriticDecision,
    DebuggerDecision,
    EditKind,
    EditPlan,
    EngineeringCriticDecision,
    PlannedEngineeringCriticDecision,
    ImageCriticDecision,
    ObjectPlan,
    FixedAssemblyPlan,
    FixedAssemblyConfig,
    ObjectRequest,
    ObjectRunResult,
    RepairProposal,
    RepairTarget,
)
from .prompts import object_prompt
from .repair_controller import RepairController
from .overhang_edit import (budget_remaining, reserve_attempt, opportunities, protection_check,
                           original_execution, allowed_edit, compare_measurements,
                           version_record, version_assets, assert_version, file_hash, edit_outcome, review_images,
                           OVERHANG_OPTIMIZATION_INSTRUCTION, attempt_feedback,
                           MODEL_LOCATION_INSTRUCTION, reliable_location, inferred_proposal,
                           inferred_scope_unchanged, PLANNED_LOCATION_INSTRUCTION,
                           experiment_protection)
from .repair_policy import (
    assess_candidate,
    immutable_inputs_match,
    validate_patch_scope,
)
from .source_index import SourceIndex, load_source_index
from .tools import AgentToolContext, PATCH_TOOLS, READ_TOOLS, WRITE_TOOLS
from .tools.context import patch_failure
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


def _load_joint_source_index(path: Path | None, output: Path):
    if path is None:
        return None
    try:
        return load_source_index(path)
    except (ValidationError, FileNotFoundError) as error:
        write_json(output/'source_index_unavailable.json', {
            'status':'UNAVAILABLE', 'path':str(path), 'error_type':type(error).__name__,
            'reason':'Index missing or invalid; source-based inference remains available.',
            'tool_confirmed':False})
        return None


def _actionable_findings(run: CheckerRun) -> list[CheckerFinding]:
    return [finding for finding in run.result.findings
            if (finding.category == "optimization_opportunity" and run.result.status == "PASS")
            or localized_mesh_feedback(run.result, finding) or (run.result.status == "FAIL"
            and finding.category in {"physical_violation", "geometry_failure"}
            and finding.repairability in {"geometry", "design_variable"})]


def _checker_evidence(
    runs: list[CheckerRun], *, workspace: Path, finding_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Critic: all statuses and unresolved findings. Coder: proposal IDs only."""
    findings = {}
    summaries = []
    for run in runs:
        selected = [
            (index, finding) for index, finding in enumerate(run.result.findings)
            if (finding.category == "optimization_opportunity" or localized_mesh_feedback(run.result, finding) or (run.result.status == "FAIL"
            and finding.category in {"physical_violation", "geometry_failure", "missing_semantics"}))
            and (finding_ids is None or finding.finding_id in finding_ids)
        ]
        if finding_ids is not None and not selected:
            continue
        result_ref = (run.output_dir / "result.json").resolve().relative_to(workspace.resolve()).as_posix()
        summaries.append({
            "checker": run.spec.name, "required": run.spec.required,
            "status": run.result.status,
            **({"summary": run.result.summary.split("\n", 1)[0][:240]} if finding_ids is None else {}),
            "finding_ids": [f.finding_id for _, f in selected],
            "result_ref": result_ref,
        })
        if run.result.status in {"ERROR", "INDETERMINATE"}:
            violation = next(iter(run.result.violations), {})
            summaries[-1].update({
                "stage": str(violation.get("stage", "evaluation"))[:80],
                "code": str(violation.get("code", run.result.status))[:100],
                "geometry_repair_allowed": bool(_actionable_findings(run)),
            })
        for index, finding in selected:
            row = finding.model_dump(exclude={"domain"}, exclude_none=True)
            if finding.metric is None:
                # Preserve scalar legacy measurements, not entire nested reports.
                row["key_values"] = {
                    key: value for key, value in finding.domain.items()
                    if isinstance(value, (int, float, bool))
                    or isinstance(value, str) and len(value) <= 200
                }
            row["result_ref"] = result_ref
            row["result_pointer"] = f"/findings/{index}"
            findings[finding.finding_id] = row
    return {
        "checker_summary": summaries,
        "typed_findings": list(findings.values()),
        "evidence_access": "Use read_file only when inline evidence is insufficient. Select a specific JSON field with json_pointer (e.g. result_pointer + '/metric' or '/domain'), or a bounded text page with offset/max_chars. Follow next_offset only for relevant evidence, not to load every full report.",
    }


def _engineering_feedback(
    runs: list[CheckerRun], *, context: AnalysisContext,
    localization: LocalizationReport | None, history: list[dict[str, object]],
    round_root: Path, workspace: Path,
) -> dict[str, Any]:
    """Add critic context without duplicating detailed checker reports."""
    def relative(path: Path) -> str:
        return path.resolve().relative_to(workspace.resolve()).as_posix()

    evidence = _checker_evidence(runs, workspace=workspace)
    unresolved_ids = {row["finding_id"] for row in evidence["typed_findings"]}
    return {
        **evidence,
        "required_checker_failures": [r.spec.name for r in runs if r.spec.required and r.result.status == "FAIL"],
        "unverified_checks": [r.spec.name for r in runs if r.result.status in {"ERROR", "INDETERMINATE"}],
        "unavailable_feedback_policy": "FEA mesh failures are unverified structural performance, not a strength failure; geometric cause is undetermined. Only mesh findings with geometry_repair_allowed=true and reliable source localization may motivate a local edit within the remaining budget. Other unavailable checks are analysis-only. Do not default to deleting decoration or filling gaps. Never delete mesh elements or change physical assumptions, tolerances, thresholds, checker/mesher/solver settings to pass.",
        "analysis_context": context.model_dump(exclude={"checker_contexts": {"__all__": {"details"}}}),
        "analysis_context_ref": relative(round_root / "analysis_context.json"),
        "localization": [
            row.model_dump(exclude={"candidates"}, exclude_none=True)
            for row in (localization.findings if localization is not None else [])
            if row.finding_id in unresolved_ids
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
            instructions=object_prompt("coder", articulation=request.articulation, fixed_assembly=bool(request.fixed_assembly)),
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
                        **({'fixed_assembly':request.fixed_assembly} if request.fixed_assembly else {}),
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
        if request.fixed_assembly:
            raise ValueError('fixed assembly v1 supports create/resume; arbitrary existing-asset edit is not supported')
        workspace = self._prepare_workspace(request.workspace)
        self._persist_user_input(workspace, request)
        source_path = workspace / "source.py"
        source_input = Path(source).expanduser().resolve()
        if not source_input.is_file():
            raise FileNotFoundError(source_input)
        shutil.copy2(source_input, source_path)
        if request.overhang_experiment:
            shutil.copy2(source_input, workspace / "original_source.py")
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
        if not request.check_first and not request.overhang_experiment:
            reserve_attempt(workspace, request.overhang_experiment, "initial_patch")
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
        ) and self._published_files_exist(workspace) and not request.overhang_experiment and not request.fixed_assembly:
            return self._load_completed_result(workspace, manifest)

        runtime = self._runtime(request, workspace, mode=mode, resume=True)
        runtime.usage.update_manifest(status="running", resumed=True)
        if plan_path.is_file():
            plan_type = (FixedAssemblyPlan if request.fixed_assembly else ObjectPlan) if mode == "generate" else EditPlan
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
        if stage == "planned" and not request.overhang_experiment:
            if mode == "generate":
                if not source_path.read_text(encoding="utf-8").strip():
                    context = AgentToolContext(workspace=workspace, source_path=source_path)
                    coder = runtime.agent(
                        name="object-coder",
                        instructions=object_prompt("coder", articulation=request.articulation, fixed_assembly=bool(request.fixed_assembly)),
                        tools=WRITE_TOOLS,
                    )
                    await runtime.run(
                        agent=coder,
                        input=user_input(
                            json.dumps({
                                "requirement": request.requirement,
                                "articulation_required": request.articulation,
                                "plan": plan.model_dump(),
                                **({'fixed_assembly':request.fixed_assembly} if request.fixed_assembly else {}),
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
                "export_urdf": not bool(request.fixed_assembly),
                "timeout": executor_timeout,
                **({'assembly_geometry_timeout':120, 'physical_checkers':'disabled'} if request.fixed_assembly else {}),
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
            instructions=object_prompt("planner", articulation=request.articulation, fixed_assembly=bool(request.fixed_assembly)),
            output_type=FixedAssemblyPlan if request.fixed_assembly else ObjectPlan,
        )
        result = await runtime.run(
            agent=planner,
            input=user_input(json.dumps({'requirement':request.requirement, 'fixed_assembly':request.fixed_assembly})
                             if request.fixed_assembly else request.requirement, request.image_paths),
            role="planner",
            stage="plan",
        )
        plan = self._typed_output(result.final_output, FixedAssemblyPlan if request.fixed_assembly else ObjectPlan)
        if request.fixed_assembly and (plan.mm_per_unit != request.fixed_assembly['mm_per_unit']
                                       or plan.final_size_mm != request.fixed_assembly['final_size_mm']):
            raise ValueError('assembly plan changed frozen scale or final dimensions')
        return plan

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
        if request.fixed_assembly:
            from .fixed_assembly import iterate_fixed_assembly
            return await iterate_fixed_assembly(self, runtime=runtime, request=request, workspace=workspace,
                source_path=source_path, plan=plan)
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
            instructions=object_prompt("engineering_critic", articulation=request.articulation) +
                ("\n" + (PLANNED_LOCATION_INSTRUCTION if request.overhang_experiment.get('mode') == 'planned_checks'
                          else MODEL_LOCATION_INSTRUCTION) if request.overhang_experiment.get("arm") == "feedback" else ""),
            tools=READ_TOOLS,
            output_type=(PlannedEngineeringCriticDecision if request.overhang_experiment.get('mode') == 'planned_checks'
                         else EngineeringCriticDecision),
            strict_json_schema=False,
        )
        if request.overhang_experiment:
            return await self._iterate_overhang(
                runtime=runtime, request=request, workspace=workspace, source_path=source_path,
                mode=mode, plan=plan, repairer=repairer, image_critic=image_critic,
                code_critic=code_critic, engineering_critic=engineering_critic)
        state = resume_state or {}
        experiment = request.overhang_experiment
        optimize = experiment.get("arm") == "feedback"
        retained_execution = None
        retained_runs: list[CheckerRun] = []
        retained_source = workspace / "retained_source.py"
        retained_path = workspace / "retained_experiment.json"
        if experiment and "original_urdf" in experiment:
            retained_execution = original_execution(experiment)
            if retained_path.is_file():
                saved = read_json(retained_path)
                ex = saved["execution"]
                retained_execution = ExecutionResult(
                    Path(ex["output_root"]), Path(ex["glb_path"]), Path(ex["urdf_path"]) if ex["urdf_path"] else None,
                    tuple(Path(p) for p in ex["render_paths"]), "", "",
                    Path(ex["source_index_path"]) if ex.get("source_index_path") else None)
                from .models import CheckerResult, CheckerSpec
                retained_runs = [CheckerRun(CheckerSpec.model_validate(r["spec"]), CheckerResult.model_validate(r["result"]),
                                           Path(r["output_dir"]), tuple(r["command"])) for r in saved["runs"]]
            elif (workspace / "original_source.py").is_file():
                shutil.copy2(workspace / "original_source.py", retained_source)
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
        appearance_approved: bool | None = None
        checker_runs: list[CheckerRun] = []
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
                if experiment:
                    failures.append({"round": round_number, "stage": "execution_infrastructure", "error": str(exc)[:240]})
                    shutil.copy2(retained_source, source_path)
                    final = (round_number, retained_execution, False, "experiment_execution_unavailable_best_retained")
                    checker_runs = retained_runs
                    break
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
                if experiment and (not budget_remaining(workspace, experiment) or round_number == end_round):
                    shutil.copy2(retained_source, source_path)
                    final = (round_number, retained_execution, False, "candidate_execution_failed_best_retained")
                    checker_runs = retained_runs
                    break
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

            # Opt-in prompt experiment initializes its frozen checker plan from
            # this run's first executable model, before any appearance approval.
            initialize = getattr(self, '_initialize_generated_checks', None)
            if mode == 'generate' and not experiment and initialize is not None:
                return await initialize(runtime=runtime, request=request, workspace=workspace,
                    source_path=source_path, execution=execution, round_number=round_number, plan=plan)

            # Preserve the historical last-round fallback only when no engineering
            # gates are configured. Mandatory checkers always inspect the final round.
            if experiment and not allowed_edit(workspace / "original_source.py", source_path, experiment):
                shutil.copy2(workspace / "original_source.py", source_path)
                final = (round_number, original_execution(experiment), False, "manual_source_scope_violation_original_retained")
                checker_runs = []
                break
            if not request.checker_specs and not experiment and round_number == end_round:
                final = (
                    round_number,
                    execution,
                    False,
                    "round_limit_after_execution",
                )
                break

            image_decision = await self._review_generation_image(
                runtime=runtime, request=request, plan=plan, execution=execution,
                round_number=round_number, max_rounds=end_round, round_root=round_root,
                image_critic=image_critic, image_history=image_history,
                code_critic_corrections=image_critic_corrections)

            checker_runs = run_checkers(
                request.checker_specs,
                execution=execution,
                source_path=source_path,
                round_root=round_root,
            )
            if optimize:
                checker_runs = [CheckerRun(run.spec, run.result.model_copy(update={
                    "findings": opportunities(run.result)}), run.output_dir, run.command)
                    for run in checker_runs]
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
            checker_errors = [run for run in required_checker_failures(checker_runs)
                              if run.result.status in {"ERROR", "INDETERMINATE"}]
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

            # Keep unavailable results (including MESH_INVALID) in history;
            # independent actionable failures may still motivate repairs.
            mandatory_failures = [
                run
                for run in required_checker_failures(checker_runs)
                if _actionable_findings(run)
            ]
            code_decision: CodeCriticDecision | None = None
            appearance_approved = image_decision.approved
            if not image_decision.approved:
                code_decision = await self._review_generation_code(
                    runtime=runtime, request=request, plan=plan, execution=execution,
                    workspace=workspace, source_path=source_path, round_number=round_number,
                    max_rounds=end_round, round_root=round_root, code_critic=code_critic,
                    image_decision=image_decision, code_history=code_history)
                image_critic_corrections = code_decision.image_critic_corrections
                appearance_approved = code_decision.approved

            if not request.checker_specs:
                if experiment:
                    protection = protection_check(Path(experiment["original_urdf"]), execution.urdf_path, experiment)
                    write_json(round_root / "protection.json", protection)
                    if protection["status"] != "PASS":
                        shutil.copy2(workspace / "original_source.py", source_path)
                        final = (round_number, original_execution(experiment), False, "protection_not_confirmed_original_retained")
                        break
                if appearance_approved:
                    reason = (
                        "image_critic_approved"
                        if image_decision.approved
                        else "code_critic_approved"
                    )
                    final = (round_number, execution, True, reason)
                    break
                if experiment and not budget_remaining(workspace, experiment):
                    shutil.copy2(workspace / "original_source.py", source_path)
                    final = (round_number, original_execution(experiment), False, "edit_budget_exhausted_original_retained")
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
            optimization_pending = bool(optimize and appearance_approved and
                                        any(_actionable_findings(r) for r in checker_runs) and
                                        budget_remaining(workspace, experiment))
            if mandatory_failures or optimization_pending:
                if source_index is not None:
                    budget = RepairController(
                        workspace=workspace, round_root=round_root,
                        baseline_source=source_path, source_index=source_index,
                        findings=[f for r in checker_runs for f in _actionable_findings(r)],
                        checker_specs_sha256=analysis_context.checker_specs_sha256,
                        policy=request.repair_policy,
                    ).budget_error()
                    if budget:
                        final = (round_number, execution, False, "no_executable_repair_or_budget_exhausted")
                        break
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
                                    "Optional overhang optimization, NOT a physical failure. First preserve key shape, function and appearance; then reduce total geometric overhang area. Do not scale the object, delete required parts, change printing settings or geometry conditions. Prefer noncritical undersides/transitions/connections. Return no proposals and explain when no reasonable local edit exists. "
                                    if optimize else
                                    "Propose bounded source candidates supported by the "
                                    "typed findings and localization. Do not alter checker "
                                    "assumptions or claim a repair passes before regression."
                                ),
                                "protection_checklist": experiment.get("protection") if experiment else None,
                                "remaining_edit_candidates": budget_remaining(workspace, experiment) if experiment else None,
                                "overhang_measurements": ([{k: r.result.metrics.get(k) for k in (
                                    "overhang_area_mm2", "nominal_contact_area_mm2", "support_required")}
                                    for r in checker_runs] if optimize else None),
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
                    has_required_failures=bool(mandatory_failures),
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

            if appearance_approved and not mandatory_failures and not optimization_pending:
                final = (
                    round_number,
                    execution,
                    not bool(required_checker_failures(checker_runs)),
                    (
                        "appearance_and_required_checkers_approved"
                        if not required_checker_failures(checker_runs)
                        else "appearance_approved_no_actionable_checker_feedback"
                    ),
                )
                if optimize:
                    final = (round_number, execution, bool(appearance_approved),
                             "overhang_measurement_complete_no_pending_edit" if all(r.result.status == "PASS" for r in checker_runs)
                             else "overhang_measurement_unavailable_no_edit")
                break

            if (mandatory_failures or optimization_pending) and engineering_decision is not None:
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
                    checker_runs = accepted_runs
                    if experiment:
                        retained_execution, retained_runs = candidate_outcome.execution, accepted_runs
                        shutil.copy2(source_path, retained_source)
                        from dataclasses import asdict
                        write_json(retained_path, {"execution": asdict(retained_execution), "runs": [
                            {"spec": r.spec.model_dump(), "result": r.result.model_dump(),
                             "output_dir": str(r.output_dir), "command": r.command} for r in retained_runs]})
                    accepted_failures = required_checker_failures(accepted_runs)
                    if candidate_outcome.appearance_approved and not accepted_failures and not optimize:
                        final = (
                            round_number,
                            candidate_outcome.execution,
                            True,
                            "accepted_candidate_passed_all_required_gates",
                        )
                        break
                    execution = candidate_outcome.execution
                    mandatory_failures = [run for run in accepted_failures if _actionable_findings(run)]
                    appearance_approved = candidate_outcome.appearance_approved
                    if appearance_approved and not mandatory_failures and not optimize:
                        final = (round_number, execution, False,
                                 "accepted_working_candidate_checks_unverified")
                        break
                    if round_number < end_round:
                        self._write_checkpoint(
                            workspace, **checkpoint_fields(round_number + 1)
                        )
                        continue
                elif (not any(row.get("candidate") for row in candidate_outcome.attempts)
                      or any(row.get("stage") == "repair_budget" for row in candidate_outcome.attempts)):
                    final = (round_number, execution, False, "no_executable_repair_or_budget_exhausted")
                    break
                elif round_number < end_round:
                    self._write_checkpoint(
                        workspace, **checkpoint_fields(round_number + 1)
                    )
                    continue

                if optimize:
                    final = (round_number, execution, appearance_approved, "overhang_edit_budget_or_round_limit_best_retained")
                    break

            gate_details = {
                "appearance_approved": appearance_approved,
                "required_checker_unavailable": [
                    {
                        "checker": run.spec.name,
                        "summary": run.result.summary,
                    }
                    for run in required_checker_failures(checker_runs)
                    if run.result.status in {"ERROR", "INDETERMINATE"}
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
                final = (round_number, execution, False, "round_budget_exhausted_with_unmet_gates")
                break

            repair_payload: dict[str, object] = {
                "requirement": request.requirement,
                "plan": plan.model_dump(),
                "assignment": (
                    "Apply one minimal patch covering every valid visual change "
                    "and every Engineering Critic change. Preserve unrelated appearance. "
                    "The next round will re-run the original checkers."
                ),
            }
            if experiment and not budget_remaining(workspace, experiment):
                final = (round_number, execution, False, "edit_candidate_budget_exhausted")
                break
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
        if experiment:
            from .models import CheckerResult
            known = {r.spec.name for r in checker_runs}
            for spec in request.checker_specs:
                if spec.name not in known:
                    result = CheckerResult(checker=spec.name, status="INDETERMINATE",
                        summary="Retained asset was not measured in this attempt; no optimization conclusion",
                        violations=[{"code": "NOT_EVALUATED", "stage": "asset_execution"}])
                    directory = workspace / "unverified_checkers" / spec.name
                    write_json(directory / "result.json", result.model_dump())
                    checker_runs.append(CheckerRun(spec, result, directory, ()))
        final_glb, final_urdf, final_renders = self._publish(workspace, execution)
        verification = {
            "required_checkers_passed": not bool(experiment) and bool(request.checker_specs) and not required_checker_failures(checker_runs),
            "overhang_experiment": bool(experiment),
            "checker_statuses": {run.spec.name: run.result.status for run in checker_runs},
            "unverified_checks": [run.spec.name for run in checker_runs
                                  if run.result.status in {"ERROR", "INDETERMINATE"}],
        }
        write_json(workspace / "checker_results.json", {
            **verification,
            "results": [{"required": run.spec.required, "report_path": str(run.output_dir / "result.json"),
                         **run.result.model_dump()} for run in checker_runs],
        })
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
            appearance_approved=(appearance_approved if not any(word in finalization_reason for word in
                                 ("execution_unavailable", "execution_failed", "original_retained", "scope_violation")) else None),
            **verification,
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
            **verification,
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

    async def _iterate_overhang(self, *, runtime, request, workspace, source_path, mode, plan,
                                repairer, image_critic, code_critic, engineering_critic):
        """Opt-in edit routing; execution, critics and acceptance remain shared."""
        options = request.overhang_experiment
        feedback = options.get("arm") == "feedback"
        planned = options.get("mode") == "planned_checks"
        if options.get("arm") not in {"feedback", "control"}:
            raise ValueError("unknown overhang experiment arm")
        if not planned and ((not feedback and request.checker_specs) or any(s.name != "overhang" for s in request.checker_specs)):
            raise ValueError("control has no online checkers; feedback uses overhang only")
        book_path = workspace / "overhang_versions.json"
        if not book_path.exists():
            original_source = workspace / "original_source.py"
            if not original_source.exists():
                shutil.copy2(source_path, original_source)
            original = original_execution(options)
            original_source_asset = original.glb_path.parent / "source.py"
            if original_source_asset.exists() and file_hash(original_source_asset) != file_hash(original_source):
                raise ValueError("initial source does not match original asset source")
            book = {"original": "original", "retained": "original", "candidate": None,
                    "checker_specs": [s.model_dump() for s in request.checker_specs],
                    "unverified_plan_tools": options.get('unverified_plan_tools', []) if planned else [],
                    "attempts": {}, "versions": {"original": version_record("original", original_source, original)}}
            write_json(book_path, book)
        reason = "round_budget_exhausted"
        try:
            book = read_json(book_path)
            # Never repeat a model call whose reservation survived an interruption.
            interrupted = False
            for attempt_id, attempt in book["attempts"].items():
                if attempt["status"] in {"EDITING", "MODEL_STARTED"}:
                    attempt.update(status="INTERRUPTED", accepted=False,
                                   reason="reserved attempt interrupted; not replayed")
                    candidate = workspace / attempt["candidate"]
                    book["versions"].setdefault(attempt_id, version_record(attempt_id, candidate, None))
                    write_json(candidate.parent / "decision.json", attempt)
                    interrupted = True
            write_json(book_path, book)
            if interrupted or book.get("completed"):
                reason = "interrupted_attempt_not_replayed" if interrupted else book["stop_reason"]
            else:
                for round_number in range(int(options.get('initial_round',1)) + len(book["attempts"]), request.max_rounds + 1):
                    book = read_json(book_path)
                    retained = book["versions"][book["retained"]]
                    current_source, execution, runs = version_assets(retained)
                    if not budget_remaining(workspace, options):
                        reason = "edit_budget_exhausted"
                        break
                    ledger = workspace / "edit_attempts.jsonl"
                    if ledger.exists():
                        reservations = [json.loads(line) for line in ledger.read_text().splitlines()]
                        if (request.repair_policy.max_total_candidates is not None and len(reservations) >= request.repair_policy.max_total_candidates) or (reservations and
                                time.time() - reservations[0]["started_at"] >= request.repair_policy.time_budget_seconds):
                            reason = "repair_budget_exhausted"
                            break
                    round_root = workspace / "rounds" / f"round_{round_number:02d}"
                    round_root.mkdir(parents=True, exist_ok=True)
                    # Initial measurements are cached in the version, never rerun for publication.
                    if feedback and not runs:
                        runs = run_checkers(request.checker_specs, execution=execution,
                                            source_path=current_source, round_root=round_root,
                                            **({'require_topology':True} if planned else {}))
                    runs = [CheckerRun(r.spec, r.result.model_copy(update={"findings": opportunities(r.result) if r.spec.name == "overhang" else r.result.findings}),
                                       r.output_dir, r.command) for r in runs] if feedback else []
                    analysis = build_analysis_context(source_path=current_source, geometry_path=execution.glb_path,
                        source_index_path=execution.source_index_path, checker_runs=runs,
                        print_orientation_editable=request.repair_policy.print_orientation_editable)
                    index = (_load_joint_source_index(execution.source_index_path, round_root) if planned
                             else load_source_index(execution.source_index_path) if execution.source_index_path else None)
                    if index and runs:
                        localized, report = localize_findings([f for r in runs for f in r.result.findings], index,
                                                              checker_contexts=analysis.checker_contexts)
                        by_id = {f.finding_id: f for f in localized}
                        runs = [CheckerRun(r.spec, r.result.model_copy(update={"findings": [by_id[f.finding_id]
                                            for f in r.result.findings]}), r.output_dir, r.command) for r in runs]
                        write_json(round_root / "localization.json", report.model_dump())
                    write_json(round_root / "analysis_context.json", analysis.model_dump())
                    for run in runs:
                        write_json(run.output_dir / "result.json", run.result.model_dump())
                    book["versions"][book["retained"]] = version_record(book["retained"], current_source, execution, runs, retained["reviews"])
                    write_json(book_path, book)
                    proposal = RepairProposal(proposal_id=f"local_edit_{round_number}", finding_ids=["local_edit"],
                        hypothesis="Preserve required shape, function and appearance; reduce overhang only where appropriate.",
                        target=RepairTarget(allowed_scopes=options.get("protection", {}).get("allowed_classes", [])),
                        action="reshape")
                    if planned:
                        # Gate repairs are evaluated against the same actual
                        # physical findings sent to Coder, not a placeholder ID.
                        proposal = proposal.model_copy(update={'finding_ids':
                            [f.finding_id for r in runs for f in _actionable_findings(r)] or ['local_edit']})
                    reviews = retained["reviews"]
                    initial = not book["attempts"] and not request.check_first
                    if not initial and reviews.get("appearance_approved") is None:
                        _, reviews = await self._review_candidate_appearance(runtime=runtime, request=request,
                            workspace=workspace, round_number=round_number, proposal_index=0, proposal=proposal,
                            baseline_execution=original_execution(options), candidate_execution=execution,
                            candidate_source=current_source, candidate_root=round_root,
                            image_critic=image_critic, code_critic=code_critic)
                        reviews["protection"] = experiment_protection(Path(options["original_urdf"]), execution.urdf_path,
                            options, exact_check=protection_check)
                    book["versions"][book["retained"]] = version_record(book["retained"], current_source, execution, runs, reviews)
                    write_json(book_path, book)
                    payload = None
                    if initial or not reviews.get("appearance_approved"):
                        origin = "initial_edit" if initial else "gate_patch"
                        payload = {"requirement": request.requirement, "plan": plan.model_dump(),
                                   "assignment": proposal.hypothesis,
                                   "protection_checklist": options.get("protection"),
                                   "image_critic": reviews.get("image_critic"), "code_critic": reviews.get("code_critic"),
                                   "remaining_edit_candidates": budget_remaining(workspace, options)}
                        if feedback:
                            payload["checker_evidence"] = _checker_evidence(runs, workspace=workspace)
                            payload["overhang_measurements"] = [{k: r.result.metrics.get(k) for k in
                                ("overhang_area_mm2", "nominal_contact_area_mm2", "support_required")} for r in runs]
                        decision = EngineeringCriticDecision(approved=False, observations=[], repair_proposals=[proposal])
                    elif not feedback:
                        reason = "control_appearance_review_complete"
                        break
                    elif (not any(_actionable_findings(r) for r in runs) if planned
                          else not runs or any(r.result.status != "PASS" for r in runs)):
                        reason = "overhang_measurement_unavailable"
                        break
                    else:
                        origin = "engineering"
                        context = AgentToolContext(workspace=workspace, source_path=current_source)
                        fallback = index is None or any(not reliable_location(f) for r in runs for f in r.result.findings)
                        result = await runtime.run(agent=engineering_critic, input=user_input(json.dumps({
                            "requirement": request.requirement, "plan": plan.model_dump(),
                            "assigned_source": current_source.relative_to(workspace).as_posix(),
                            "instruction": (("Joint planned checks: preserve appearance and protected geometry first; "
                                "repair actionable hard physical failures before optional overhang reduction. "
                                "Never trade a hard regression for less overhang. Unavailable checks are unverified, "
                                "not physical failures; infrastructure errors are not geometry repair targets. "
                                "You may decline to propose a change. ") if planned else "") + OVERHANG_OPTIMIZATION_INSTRUCTION,
                            "localization_mode": "model_inferred" if fallback else "index_assisted",
                            "localization_instruction": PLANNED_LOCATION_INSTRUCTION if planned else MODEL_LOCATION_INSTRUCTION,
                            **({"current_complete_source": current_source.read_text(),
                                "source_sha256": file_hash(current_source)} if fallback or planned else {}),
                            "checker_evidence": _checker_evidence(runs, workspace=workspace),
                            "overhang_measurements": [{k: r.result.metrics.get(k) for k in
                                ("overhang_area_mm2", "nominal_contact_area_mm2", "support_required")} for r in runs],
                            "protection_checklist": options.get("protection"),
                            "remaining_edit_candidates": budget_remaining(workspace, options),
                            "previous_attempts": attempt_feedback(book["attempts"].values())}, ensure_ascii=False),
                                execution.render_paths if fallback or planned else ()),
                            role=f"engineering-critic:round:{round_number}", stage=f"engineering_critic:{round_number}", context=context)
                        if not planned:
                            self._require_tool_event(context, "read_file", "engineering critic")
                        # Joint mode supplied the complete current source above;
                        # reading it again through a tool is not a correctness gate.
                        decision = self._typed_output(result.final_output, EngineeringCriticDecision)
                        write_json(round_root / "engineering_critique.json", decision.model_dump())
                        if planned and not decision.repair_proposals:
                            category = getattr(decision, 'stop_category', None)
                            book = read_json(book_path)
                            book['stop_category'] = category or 'unspecified'
                            retry = (category in {'insufficient_localization', 'scope_limited'}
                                     and any(_actionable_findings(r) for r in runs)
                                     and not book.get('supplemental_planning_used'))
                            if retry:book['supplemental_planning_used'] = True
                            write_json(book_path, book)
                            if retry:
                                context = AgentToolContext(workspace=workspace, source_path=current_source)
                                result = await runtime.run(agent=engineering_critic, input=user_input(json.dumps({
                                    'requirement':request.requirement, 'instruction':PLANNED_LOCATION_INSTRUCTION,
                                    'assignment':'One supplemental source read and replan only. Related assembly code and helpers are allowed; no class whitelist or bridge_parent prerequisite. May still stop.',
                                    'assigned_source':current_source.relative_to(workspace).as_posix(),
                                    'current_complete_source':current_source.read_text(),
                                    'previous_decision':decision.model_dump(),
                                    'checker_evidence':_checker_evidence(runs,workspace=workspace),
                                    'protection_checklist':options.get('protection'),
                                    'remaining_edit_candidates':budget_remaining(workspace,options)},ensure_ascii=False),execution.render_paths),
                                    role=f'engineering-critic:supplement:{round_number}',stage=f'engineering_supplement:{round_number}',context=context)
                                # Full current source is also supplied inline here.
                                decision=self._typed_output(result.final_output,EngineeringCriticDecision)
                                write_json(round_root/'engineering_supplement.json',decision.model_dump())
                                book=read_json(book_path);book['stop_category']=getattr(decision,'stop_category',None) or 'unspecified'
                                write_json(book_path,book)
                    if not decision.repair_proposals:
                        reason = "no_actionable_proposal"
                        break
                    outcome = await self._attempt_engineering_candidates(runtime=runtime, request=request,
                        workspace=workspace, source_path=current_source, round_root=round_root, round_number=round_number,
                        plan=plan, repairer=repairer, image_critic=image_critic, code_critic=code_critic,
                        baseline_execution=execution, baseline_runs=runs, baseline_analysis_context=analysis,
                        source_index=index, engineering_decision=decision, code_decision=None,
                        edit_payload=payload, edit_origin=origin)
                    if not outcome.execution:
                        reason = str(outcome.attempts[-1].get("status", "candidate_rejected")) if outcome.attempts else "no_candidate"
                        # Rejected edits remain on disk; no improvement need not force another edit.
                        if reason in {"NO_CHANGE", "NO_EFFECT", "NO_PATCH_UNEXPLAINED", "TOOL_ERROR"} or not outcome.attempts:
                            break
        except Exception as error:
            reason = "experiment_error"
            book = read_json(book_path)
            book["error"] = {"stage": "local_edit", "type": type(error).__name__, "reason": str(error)[:300]}
            for attempt_id, attempt in book["attempts"].items():
                if attempt["status"] in {"EDITING", "MODEL_STARTED"}:
                    attempt.update(status="EVALUATION_ERROR", accepted=False, reason=str(error)[:300])
                    candidate = workspace / attempt["candidate"]
                    book["versions"].setdefault(attempt_id, version_record(attempt_id, candidate, None))
                    write_json(candidate.parent / "decision.json", attempt)
            write_json(book_path, book)
        return self._publish_overhang(runtime, workspace, mode, reason, planned_checks=planned)

    def _publish_overhang(self, runtime, workspace, mode, reason, *, planned_checks=False):
        book_path = workspace / "overhang_versions.json"
        book = read_json(book_path)
        retained = book["versions"][book["retained"]]
        source, execution, runs = version_assets(retained)
        for raw in book.get("checker_specs", []):
            spec = CheckerSpec.model_validate(raw)
            if not any(r.spec.name == spec.name for r in runs):
                directory = workspace / "unverified_checkers" / spec.name
                result = CheckerResult(checker=spec.name, status="INDETERMINATE",
                    summary="Retained asset not measured; optimization unverified",
                    violations=[{"code": "NOT_EVALUATED", "stage": "local_edit"}])
                write_json(directory / "result.json", result.model_dump())
                runs.append(CheckerRun(spec, result, directory, ()))
        retained = version_record(book["retained"], source, execution, runs, retained["reviews"])
        book["versions"][book["retained"]] = retained
        shutil.copy2(source, workspace / "source.py")
        glb, urdf, renders = self._publish(workspace, execution)
        pairs = [(source, workspace / "source.py"), (execution.glb_path, glb), *zip(execution.render_paths, renders)]
        if urdf:
            pairs += [(execution.urdf_path, urdf)]
            pairs += [(p, workspace / "meshes" / p.relative_to(execution.urdf_path.parent / "meshes"))
                      for p in (execution.urdf_path.parent / "meshes").rglob("*") if p.is_file()]
        if execution.source_index_path:
            pairs.append((execution.source_index_path, workspace / "source_index.json"))
        for a, b in ((execution.glb_path.parent / "meta.json", workspace / "render/meta.json"),
                     (execution.glb_path.with_name(f"{execution.glb_path.stem}.joint_states.json"),
                      workspace / "scene.joint_states.json")):
            if a.is_file():
                pairs.append((a, b))
        if any(file_hash(a) != file_hash(b) for a, b in pairs):
            raise ValueError("published files do not match retained version")
        verification = {"required_checkers_passed": False, "overhang_experiment": True,
            "checker_statuses": {r.spec.name: r.result.status for r in runs},
            "unverified_checks": [r.spec.name for r in runs if r.result.status in {"ERROR", "INDETERMINATE"}]}
        write_json(workspace / "checker_results.json", {"version_id": book["retained"],
            "record_hash": retained["record_hash"], **verification,
            "results": [{"required": r.spec.required, "report_path": str(r.output_dir / "result.json"),
                         **r.result.model_dump()} for r in runs]})
        write_json(workspace / "appearance_protection.json", {"version_id": book["retained"],
            "record_hash": retained["record_hash"], **retained["reviews"]})
        approved = bool(retained["reviews"].get("appearance_approved") and
                        retained["reviews"].get("protection", {}).get("status") == "PASS")
        if planned_checks:
            approved = approved and all(r.result.status == "PASS" for r in runs if r.spec.required)
            pending = book.get('unverified_plan_tools', [])
            approved = approved and not any(t.get('required') for t in pending)
            verification['unverified_plan_tools'] = pending
            verification['unverified_checks'] = sorted(set(verification['unverified_checks']) | {t['name'] for t in pending})
            verification["required_checkers_passed"] = approved
            verification["planned_checks"] = True
            write_json(workspace / "checker_results.json", {"version_id": book["retained"],
                "record_hash": retained["record_hash"], **verification,
                "results": [{"required": r.spec.required, "report_path": str(r.output_dir / "result.json"),
                             **r.result.model_dump()} for r in runs]})
        book.update(completed=True, stop_reason=reason)
        write_json(book_path, book)
        selected_round = int(book["attempts"].get(book["retained"], {}).get("round", 0))
        fields = dict(status="completed", approved=approved, selected_round=selected_round,
            selected_version=book["retained"], selected_record_hash=retained["record_hash"], final_reason=reason,
            finalization_reason=reason, mode=mode,
            error_type=book.get("error", {}).get("type"), error=book.get("error", {}).get("reason"),
            source_path=str(workspace / "source.py"), glb_path=str(glb), urdf_path=str(urdf) if urdf else None,
            render_paths=[str(p) for p in renders],
            appearance_approved=retained["reviews"].get("appearance_approved"),
            protection=retained["reviews"].get("protection"), **verification,
            checker_results=[r.result.model_dump() for r in runs],
            checker_history=[{"results": [{"required": r.spec.required, **r.result.model_dump()} for r in runs]}],
            edit_attempts=list(book["attempts"].values()))
        runtime.usage.update_manifest(**fields)
        self._write_checkpoint(workspace, stage="completed", **fields)
        return ObjectRunResult(workspace, workspace / "source.py", glb, urdf, tuple(renders),
                               selected_round, approved, runtime.usage.totals())

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
        edit_payload: dict[str, Any] | None = None,
        edit_origin: str = "engineering",
    ) -> _CandidateOutcome:
        attempts: list[dict[str, object]] = []
        experiment = request.overhang_experiment
        if experiment and (edit_payload is not None or experiment.get("arm") == "feedback") and source_index is None:
            source_index = SourceIndex(source_path=str(source_path), source_sha256=file_hash(source_path),
                                       index_sha256="", root_feature_id="manual_scope")
        if source_index is None:
            attempts.append(
                {
                    "accepted": False,
                    "reason": "source index is unavailable; source repair is unresolved",
                }
            )
            return _CandidateOutcome(None, (), False, tuple(attempts))

        baseline_findings = [
            finding for run in baseline_runs for finding in _actionable_findings(run)
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
        def record_attempt(record):
            if experiment and attempt_id is not None:
                record["modification_summary"] = {
                    "proposed_action": str(proposal.action)[:120],
                    "proposed_intent_not_verified_effect": proposal.hypothesis[:500],
                    "actual_changed_symbols": (record.get("scope_validation") or {}).get("changed_symbols", [])[:8],
                    "note": "Changed symbols describe source scope, not proof of geometric improvement."
                }
                if experiment.get("arm") == "feedback":
                    record["localization"] = (proposal.parameter_bounds["_localization"] if infer
                                              else {"method": "index_assisted" if edit_payload is None else "model_inferred",
                                                    "tool_confirmed": False})
                if experiment.get("arm") == "feedback":
                    before_overhang = next((r.result for r in baseline_runs if r.spec.name == "overhang"), None)
                    after_overhang = next((r.result for r in candidate_runs if r.spec.name == "overhang"), None)
                    record["total_area"] = {
                        "before_mm2": before_overhang.metrics.get("overhang_area_mm2") if before_overhang else None,
                        "candidate_mm2": after_overhang.metrics.get("overhang_area_mm2") if after_overhang else None,
                        "comparison_valid": (record.get("overhang_comparison") or {}).get("conclusion")
                                            in {"improved", "unchanged", "worsened"},
                    }
                book = read_json(workspace / "overhang_versions.json")
                info = book["attempts"][attempt_id]
                record.update(attempt_id=attempt_id, origin=info["origin"],
                              parent_version=info["parent_version"])
                record.setdefault("status", "ACCEPTED" if record.get("accepted") else "REJECTED")
                record["version"] = attempt_id
                book["attempts"][attempt_id] = {**info, **record}
                book["versions"][attempt_id] = version_record(
                    attempt_id, candidate_source, candidate_execution, candidate_runs, reviews)
                if record.get("accepted"):
                    assert_version(book["versions"][info["parent_version"]])
                    book["retained"] = attempt_id
                write_json(workspace / "overhang_versions.json", book)
                write_json(candidate_root / "decision.json", record)
            controller.record(record)

        proposals = engineering_decision.repair_proposals[
            : request.repair_policy.max_candidates_per_round
        ]
        rejected_proposals: list[dict[str, object]] = []
        for proposal_index, raw_proposal in enumerate(proposals, 1):
            attempt_id = None
            candidate_execution, candidate_runs, reviews = None, [], {}
            budget_error = ("edit candidate budget exhausted" if experiment and not budget_remaining(workspace, experiment)
                            else None if experiment else controller.budget_error())
            if budget_error:
                attempts.append({"accepted": False, "stage": "repair_budget", "reason": budget_error})
                rejected_proposals.append(
                    {
                        "proposal_id": raw_proposal.proposal_id,
                        "reason": budget_error,
                    }
                )
                break
            targeted = [f for f in baseline_findings if f.finding_id in raw_proposal.finding_ids]
            planned = experiment.get('mode') == 'planned_checks'
            infer = (experiment.get("arm") == "feedback" and edit_payload is None and
                     (planned or not targeted or any(not reliable_location(f) for f in targeted)))
            if infer:
                proposal, proposal_errors = (inferred_proposal(raw_proposal, source_path, experiment, baseline_findings, source_index=source_index)
                                             if planned else inferred_proposal(raw_proposal, source_path, experiment, baseline_findings))
            else:
                proposal, proposal_errors = ((raw_proposal, []) if experiment and edit_payload is not None
                                             else controller.normalize_proposal(raw_proposal))
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
                record_attempt(record)
                continue

            candidate_root, candidate_source, fingerprint = controller.prepare_candidate(
                proposal, proposal_index
            )
            candidate_relative = candidate_source.relative_to(workspace).as_posix()
            if experiment:
                versions = read_json(workspace / "overhang_versions.json")
                parent = versions["retained"]
                assert_version(versions["versions"][parent])
                ledger = workspace / "edit_attempts.jsonl"
                reservations = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
                if (request.repair_policy.max_total_candidates is not None and len(reservations) >= request.repair_policy.max_total_candidates) or (reservations and
                        time.time() - reservations[0]["started_at"] >= request.repair_policy.time_budget_seconds):
                    attempts.append({"stage": "repair_budget", "reason": "repair budget exhausted", "accepted": False})
                    break
                attempt_id = f"attempt_{1 + (len(ledger.read_text().splitlines()) if ledger.exists() else 0):04d}"
                if not reserve_attempt(workspace, experiment, edit_origin, attempt_id=attempt_id, parent=parent):
                    attempts.append({"stage": "repair_budget", "reason": "reservation denied", "accepted": False})
                    break
                versions["candidate"] = attempt_id
                versions["attempts"][attempt_id] = {"status": "EDITING", "origin": edit_origin,
                                                    "parent_version": parent, "candidate": candidate_relative}
                write_json(workspace / "overhang_versions.json", versions)
            try:
                if planned and edit_payload is not None:
                    edit_payload = {**edit_payload, 'assignment':PLANNED_LOCATION_INSTRUCTION + '\n' + str(edit_payload.get('assignment',''))}
                patch_result = await self._repair(
                    runtime=runtime,
                    repairer=repairer,
                    workspace=workspace,
                    source_path=candidate_source,
                    role=f"coder:engineering-candidate:{round_number}:{proposal_index}",
                    stage=(f"{edit_origin}:{round_number}:{proposal_index}" if experiment
                           else f"engineering_candidate_patch:{round_number}:{proposal_index}"),
                    **({"reserved_attempt_id": attempt_id} if experiment else {}),
                    payload=edit_payload if edit_payload is not None else {
                        "requirement": request.requirement,
                        "plan": plan.model_dump(),
                        "assignment": (PLANNED_LOCATION_INSTRUCTION + "\n" if planned else "") + (
                            "Apply only this bounded RepairProposal to the assigned "
                            "candidate source. Preserve everything outside allowed_scopes. "
                            "Do not default to deleting decoration, filling gaps or changing physical conditions. "
                            "Never delete mesh elements or change tolerances, thresholds, "
                            "checker code, mesh generation or solver settings to obtain a pass."
                        ),
                        "repair_proposal": proposal.model_dump(),
                        "protection_checklist": request.overhang_experiment.get("protection"),
                        "checker_evidence": _checker_evidence(
                            baseline_runs, workspace=workspace,
                            finding_ids=set(proposal.finding_ids),
                        ),
                        "analysis_context_ref": (round_root / "analysis_context.json").relative_to(workspace).as_posix(),
                        "code_critic": (
                            code_decision.model_dump()
                            if code_decision is not None and not code_decision.approved
                            else None
                        ),
                    },
                )
                if planned and not isinstance(patch_result, dict):
                    raise TypeError('joint candidate repair must return an edit outcome, not None')
                if experiment and patch_result["status"] != "CHANGED":
                    record = {"round": round_number, "candidate": candidate_relative,
                              "accepted": False, **patch_result}
                    write_json(candidate_root / "decision.json", record)
                    record_attempt(record)
                    attempts.append(record)
                    continue
            except Exception as error:
                record = {
                    "round": round_number,
                    "proposal_id": proposal.proposal_id,
                    "fingerprint": fingerprint,
                    "candidate": candidate_relative,
                    "accepted": False,
                    "reason": f"coder candidate failed: {type(error).__name__}: {error}",
                }
                if planned:
                    report = candidate_root / 'exception.log'
                    report.write_text(traceback.format_exc())
                    record.update(status='FLOW_ERROR', error_type=type(error).__name__,
                                  report_path=str(report))
                write_json(candidate_root / "decision.json", record)
                record_attempt(record)
                attempts.append(record)
                if planned:
                    raise
                continue

            try:
                scope_validation = validate_patch_scope(
                    source_path,
                    candidate_source,
                    proposal=proposal,
                    source_index=source_index,
                    planned_checks=planned,
                )
            except (SyntaxError, ValueError) as error:
                scope_validation = None
                scope_errors = [f"candidate source is invalid: {type(error).__name__}: {error}"]
            else:
                scope_errors = list(scope_validation.violations)
            if request.overhang_experiment and not planned and not allowed_edit(
                    workspace / "original_source.py", candidate_source, request.overhang_experiment):
                scope_errors.append("candidate outside manual experiment source scope")
                scope_validation = None
            location = proposal.parameter_bounds.get("_localization") if infer else None
            if location and not inferred_scope_unchanged(source_path, candidate_source, location):
                scope_errors.append("candidate changed code outside inferred source target")
                scope_validation = None
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
                record_attempt(record)
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
            except AssetInfrastructureError as error:
                if not experiment:
                    raise
                record = {"accepted": False, "candidate": candidate_relative, "status": "EXECUTION_ERROR",
                          "reason": str(error)[:300]}
                write_json(candidate_root / "decision.json", record)
                record_attempt(record)
                attempts.append(record)
                continue
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
                record_attempt(record)
                attempts.append(record)
                continue

            if experiment:
                versions = read_json(workspace / "overhang_versions.json")
                versions["versions"][attempt_id] = version_record(attempt_id, candidate_source, candidate_execution)
                write_json(workspace / "overhang_versions.json", versions)
            candidate_runs = run_checkers(
                request.checker_specs,
                execution=candidate_execution,
                source_path=candidate_source,
                round_root=candidate_root,
                **({'require_topology':True} if planned else {}),
            ) if not experiment or experiment.get("arm") == "feedback" else []
            if experiment:
                versions = read_json(workspace / "overhang_versions.json")
                versions["versions"][attempt_id] = version_record(attempt_id, candidate_source, candidate_execution, candidate_runs)
                write_json(workspace / "overhang_versions.json", versions)
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
                record_attempt(record)
                attempts.append(record)
                continue

            candidate_index = None
            if (
                candidate_execution.source_index_path is not None
                and candidate_execution.source_index_path.is_file()
            ):
                candidate_index = (_load_joint_source_index(candidate_execution.source_index_path,candidate_root)
                                   if planned else load_source_index(candidate_execution.source_index_path))
            if candidate_index is not None:
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

            candidate_appearance_approved, reviews = await self._review_candidate_appearance(
                runtime=runtime, request=request, workspace=workspace, round_number=round_number,
                proposal_index=proposal_index, proposal=proposal, baseline_execution=baseline_execution,
                candidate_execution=candidate_execution, candidate_source=candidate_source,
                candidate_root=candidate_root, image_critic=image_critic, code_critic=code_critic)

            protection = (experiment_protection(Path(request.overhang_experiment["original_urdf"]),
                                           candidate_execution.urdf_path, request.overhang_experiment, exact_check=protection_check)
                          if request.overhang_experiment else None)
            if protection is not None:
                write_json(candidate_root / "protection.json", protection)
                reviews["protection"] = protection
            before_overhang = next((r.result for r in baseline_runs if r.spec.name == "overhang"), None)
            after_overhang = next((r.result for r in candidate_runs if r.spec.name == "overhang"), None)
            comparison = (compare_measurements(before_overhang, after_overhang)
                          if experiment and before_overhang and after_overhang else None)
            from .repair_policy import CandidateDecision
            decision = (CandidateDecision(accepted=bool(candidate_appearance_approved and protection and protection["status"] == "PASS"),
                        reason="control appearance and protection review; no area selection")
                        if experiment.get("arm") == "control" else assess_candidate(
                [run.result for run in baseline_runs],
                [run.result for run in candidate_runs],
                target_finding_ids=proposal.finding_ids,
                appearance_approved=candidate_appearance_approved,
                policy=request.repair_policy,
                overhang_optimization=experiment.get("arm") == "feedback" and experiment.get("mode") != "planned_checks",
                planned_checks=experiment.get("mode") == "planned_checks",
                protection=protection,
            ))
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
                "protection": protection,
                "overhang_comparison": comparison,
                **decision.model_dump(),
            }
            write_json(candidate_root / "decision.json", record)
            record_attempt(record)
            attempts.append(record)
            if decision.accepted:
                if not experiment:
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

    async def _review_generation_image(self, *, runtime, request, plan, execution,
                                       round_number, max_rounds, round_root, image_critic,
                                       image_history, code_critic_corrections, render_issue=None,
                                       assembly_context=None):
        """Ordinary aDSL generation review; no baseline/preservation judgement."""
        renders = execution.render_paths if execution else ()
        if request.fixed_assembly and not renders:
            write_json(round_root/'image_critique.json', {'status':'SKIPPED', 'approved':None,
                'reason':'No current render; source diagnosis remains available'})
            return None
        payload = {
            'requirement':request.requirement, 'planner_checklist':self._critic_checklist(plan),
            'round':round_number, 'max_rounds':max_rounds,
            'reference_image_count':len(request.image_paths), 'render_image_count':len(renders),
            'previous_image_decisions':image_history, 'code_critic_corrections':code_critic_corrections,
        }
        if request.overhang_experiment:
            payload['protection_checklist'] = request.overhang_experiment.get('protection')
        if request.fixed_assembly and assembly_context is not None:
            payload['assembly_context'] = assembly_context
        if render_issue:
            payload['render_issue'] = render_issue  # Negative availability evidence only.
        result = await runtime.run(agent=image_critic,
            input=user_input(json.dumps(payload, ensure_ascii=False), (*request.image_paths, *renders)),
            role=f'image-critic:round:{round_number}', stage=f'image_critic:{round_number}')
        decision = self._typed_output(result.final_output, ImageCriticDecision)
        write_json(round_root/'image_critique.json', decision.model_dump())
        image_history.append(decision.model_dump())
        return decision

    async def _review_generation_code(self, *, runtime, request, plan, execution,
                                      workspace, source_path, round_number, max_rounds,
                                      round_root, code_critic, image_decision, code_history,
                                      render_issue=None, assembly_context=None):
        """Code review of the same generated object and current Image judgement."""
        context = AgentToolContext(workspace=workspace, source_path=source_path)
        payload = {
            'requirement':request.requirement, 'plan':plan.model_dump(),
            'assigned_source':source_path.relative_to(workspace).as_posix(),
            'round':round_number, 'max_rounds':max_rounds,
            'image_critic':image_decision.model_dump() if image_decision else None,
            'previous_code_decisions':code_history,
        }
        if request.fixed_assembly:
            payload['fixed_assembly'] = request.fixed_assembly
            if assembly_context is not None:
                payload['assembly_context'] = assembly_context
        if render_issue:
            payload['render_issue'] = render_issue
        renders = execution.render_paths if execution else ()
        result = await runtime.run(agent=code_critic,
            input=user_input(json.dumps(payload, ensure_ascii=False), (*request.image_paths, *renders)),
            role=f'code-critic:round:{round_number}', stage=f'code_critic:{round_number}', context=context)
        decision = self._normalize_code_critic_decision(
            self._typed_output(result.final_output, CodeCriticDecision),
            source_grounded=any(event.tool == 'read_file' for event in context.events))
        write_json(round_root/'code_critique.json', decision.model_dump())
        code_history.append(decision.model_dump())
        return decision

    async def _review_candidate_appearance(self, *, runtime, request, workspace, round_number,
                                          proposal_index, proposal, baseline_execution,
                                          candidate_execution, candidate_source, candidate_root,
                                          image_critic, code_critic):
        candidate_relative = candidate_source.relative_to(workspace).as_posix()
        candidate_code_decision = None
        preservation_payload = {
            "protection_checklist": request.overhang_experiment.get("protection"),
            "requirement": request.requirement,
            "round": round_number,
            "proposal": proposal.model_dump(),
            "review_mode": "candidate_preservation",
            "image_order": {
                "reference_count": len(request.image_paths),
                "baseline_count": len(baseline_execution.render_paths) if baseline_execution else 0,
                "candidate_count": len(candidate_execution.render_paths) if candidate_execution else 0,
            },
            "instruction": (
                "Judge whether the candidate preserves the requested appearance and "
                "function. Do not infer checker success from images."
            ),
        }
        baseline_images = baseline_execution.render_paths if baseline_execution else ()
        candidate_images = candidate_execution.render_paths if candidate_execution else ()
        images = (*request.image_paths, *baseline_images, *candidate_images)
        if request.overhang_experiment:
            images, mapping = review_images(request.image_paths, baseline_execution.render_paths,
                                            candidate_execution.render_paths)
            preservation_payload["image_order"] = mapping
            preservation_payload["instruction"] += (
                " Image indices are 1-based positions in the attached unique images, not contiguous groups."
                " Use reference_indices, baseline_indices and candidate_indices to identify every view."
                " A repeated index means identical image bytes serving multiple roles;"
                " it does not prove geometry or protected dimensions are unchanged."
            )
            write_json(candidate_root / "image_input_mapping.json", mapping)
        image_result = await runtime.run(
            agent=image_critic,
            input=user_input(json.dumps(preservation_payload, ensure_ascii=False), images),
            role=f"image-critic:candidate:{round_number}:{proposal_index}",
            stage=f"candidate_image_critic:{round_number}:{proposal_index}",
        )
        candidate_image_decision = self._typed_output(image_result.final_output, ImageCriticDecision)
        image_payload = candidate_image_decision.model_dump()
        write_json(
            candidate_root / "image_critique.json",
            image_payload,
        )
        candidate_appearance_approved = image_payload['approved'] if image_payload else None
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
                            "image_critic": image_payload,
                        },
                        ensure_ascii=False,
                    ),
                    images,
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

        return candidate_appearance_approved, {
            "appearance_approved": candidate_appearance_approved,
            "image_critic": image_payload,
            "code_critic": candidate_code_decision.model_dump() if candidate_code_decision else None,
            **({"image_input_mapping": mapping} if request.overhang_experiment else {}),
        }

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
        reserved_attempt_id: str | None = None,
        allow_no_change: bool = False,
    ) -> dict[str, Any] | None:
        config_path = workspace / "runtime_config.json"
        options = read_json(config_path).get("request", {}).get("overhang_experiment", {}) if config_path.exists() else {}
        if options and not reserved_attempt_id:
            raise ValueError("experiment edits require an isolated reserved candidate")
        if options:
            versions = read_json(workspace / "overhang_versions.json")
            attempt = versions["attempts"].get(reserved_attempt_id)
            if not attempt or attempt["status"] != "EDITING" or (workspace / attempt["candidate"]).resolve() != source_path.resolve():
                raise ValueError("invalid or already executed experiment reservation")
            attempt["status"] = "MODEL_STARTED"
            write_json(workspace / "overhang_versions.json", versions)
        isolated = bool(options) or allow_no_change
        context = AgentToolContext(workspace=workspace, source_path=source_path, record_noop_patch=isolated,
                                   patch_scope_id=reserved_attempt_id or stage)
        assigned_source = source_path.relative_to(workspace).as_posix()
        payload = {"assigned_source": assigned_source, **payload}
        before = file_hash(source_path) if isolated else None
        if isolated:
            payload["no_change_contract"] = 'If no appropriate edit exists, return {"edit_action":"NO_CHANGE","reason":"..."}. Do not apply a dummy patch.'
        try:
            result = await runtime.run(
            agent=repairer,
            input=json.dumps(payload, ensure_ascii=False),
            role=role,
            stage=stage,
            context=context,
            )
        except Exception as error:
            if not isolated:
                raise
            if options.get('mode') == 'planned_checks' and isinstance(error, (TypeError, AttributeError, KeyError, AssertionError)):
                raise
            return edit_outcome(None, context.events, before, file_hash(source_path), error=error)
        if isolated:
            return edit_outcome(result.final_output, context.events, before, file_hash(source_path))
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
        if expected == "apply_patch" and patch_failure(context.events):
            raise RuntimeError(f"{actor}: {patch_failure(context.events)}")
        if not any(event.tool == expected and event.success for event in context.events):
            raise RuntimeError(f"{actor} did not use required tool {expected}")

    @staticmethod
    def _validate_request(request: ObjectRequest) -> None:
        if not request.requirement.strip():
            raise ValueError("requirement must not be empty")
        if not request.task_id.strip():
            raise ValueError("task_id must not be empty")
        if request.max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        if request.fixed_assembly:
            FixedAssemblyConfig.model_validate(request.fixed_assembly)
            if request.articulation or request.overhang_experiment:
                raise ValueError('fixed assembly cannot reuse articulation or overhang/planned-checks modes')
        names = [spec.name for spec in request.checker_specs]
        if len(names) != len(set(names)):
            raise ValueError("checker names must be unique")
        if request.overhang_experiment:
            options = request.overhang_experiment
            if options.get("arm") not in {"control", "feedback"}:
                raise ValueError("overhang experiment arm must be control or feedback")
            if options.get("mode") == "planned_checks":
                if options.get('protection_policy') != 'explicit_task_constraints':
                    raise ValueError('joint mode must explicitly configure task constraints; do not silently remove legacy protection')
                if options["arm"] != "feedback" or not set(names) <= {"topology", "standing", "overhang", "fea"}:
                    raise ValueError("planned checks requires feedback and whitelisted tools")
                # Missing topology blocks FEA execution, not independent checks.
            elif names != (["overhang"] if options["arm"] == "feedback" else []):
                raise ValueError("experiment permits only overhang in feedback arm and no checkers in control arm")
            if not (options.get('mode') == 'planned_checks' and options.get('max_candidates', 2) is None) and int(options.get("max_candidates", 2)) < 1:
                raise ValueError("edit budget must be positive")
            if (not options.get("protection", {}).get("surfaces") and options.get('mode') != 'planned_checks') or not options.get("original_urdf"):
                raise ValueError("experiment requires explicit measurable protection and original URDF")
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
                "overhang_experiment": request.overhang_experiment,
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
