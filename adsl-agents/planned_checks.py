"""Opt-in plan validation. No model-generated commands or checker framework."""
from typing import Literal
import json
from agents import AgentOutputSchema
from agents.exceptions import ModelBehaviorError
from pydantic import BaseModel, ConfigDict, Field

from .feedback_schema import stable_hash
from .models import CheckerSpec

TOOLS = ("topology", "standing", "overhang", "fea")

FEA_SCREENING_ASSUMPTIONS = {
    "purpose": "User-authorized standardized screening under existing profiles, not certification of prompt-described real material.",
    "material": "Use the supplied isotropic_PLA_screening_proxy unchanged, including for wood/metal/upholstery prompts.",
    "selection": "Choose the supplied category profile by functional use and corresponding load-bearing regions. Chairs: seat/back; tables: tabletop; shelves: shelf/tier. Lamp/speaker profiles apply only if their configured loads meaningfully match the described use.",
    "support": "Existing solver fixes all three translational DOFs on global minimum-Z nodes, selected with z <= zmin + max(height * 1e-6, 1e-9); requires at least 3 support nodes.",
    "support_source": "experiments/load_bearing_structural_performance/analyze.py::solve_level and write_deck",
    "execution_validation": "Actual load-region matching and valid support selection must be checked at execution; topology must satisfy FEA prerequisites. A planned selection is not proof these checks passed.",
    "needs_spec": "Use when no existing category/load/support assumption fits, the required functional region is absent/ambiguous, or the fixed support idealization is inapplicable. Do not invent replacement numbers or reject solely because prompt material differs from PLA.",
    "immutable": "Keep supplied material, force values/directions, solver support rule, scale and thresholds. FEA use scale is separate from overhang print scale.",
}

PLANNER_INSTRUCTIONS = (
    'Plan checks only; never edit. Return exactly one entry for each of topology, standing, overhang, fea. '
    'Use only supplied profile IDs; do not invent commands, loads, materials or thresholds. '
    'FIELD CONTRACT: selected=true requires a supplied applicable profile_id and skip_reason=null. '
    'selected=false requires profile_id=null and skip_reason="NEEDS_SPEC" or "NOT_APPLICABLE". '
    'A profile you considered but rejected may be mentioned in reason, NEVER retained in profile_id. '
    'Valid omitted FEA example: {"name":"fea","selected":false,"profile_id":null,'
    '"reason":"Support conditions need specification","skip_reason":"NEEDS_SPEC"}. '
    'User-required tools are listed in required and cannot silently be omitted. '
    'If required topology cannot be represented by supplied profiles, explain the unresolved '
    'assumption; do not invent a continuity contract or misrepresent applicability to pass validation. '
    'one_piece means connectivity of the final assembled solids, not necessarily monolithic manufacture. '
    'Standing applies to independent floor/table use. FEA is the user-authorized standardized '
    'screening described in fea_screening_assumptions, not real-material certification. '
    'Select an existing applicable profile by functional semantics. Wood/metal/upholstery versus '
    'the supplied PLA proxy is NOT itself grounds for NEEDS_SPEC. The fixed minimum-Z support '
    'rule is implemented by the solver even when absent from the profile JSON; do not claim '
    'support is missing solely because that JSON has no support field. Missing or inapplicable '
    'functional load regions/support assumptions still require NEEDS_SPEC; never invent values. '
    'Printing overhang uses a separate fixed printing scale. Explain applicability against prompt. '
    'For a correction, resolve ALL validation_errors together; retain valid decisions and do not '
    'change a skipped tool to selected merely to fix a field-format error.'
)


class PlanValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__('; '.join(errors))


class PlannedTool(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal["topology", "standing", "overhang", "fea"]
    selected: bool = Field(description="True only when an applicable supplied profile is selected.")
    profile_id: str | None = Field(description="If selected=true: supplied applicable profile ID. If selected=false: null, even when a profile was considered and rejected.")
    reason: str = Field(min_length=1, max_length=1000)
    skip_reason: Literal["NOT_APPLICABLE", "NEEDS_SPEC"] | None = Field(default=None,
        description="If selected=true: null. If selected=false: NEEDS_SPEC for missing conditions, otherwise NOT_APPLICABLE.")


class ToolPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_use: str = Field(min_length=1, max_length=1000)
    tools: list[PlannedTool]
    unresolved_assumptions: list[str] = Field(default_factory=list)


class ToolPlanOutputSchema(AgentOutputSchema):
    """Keep the requested strict schema; isolate received field errors per tool."""
    def __init__(self):
        super().__init__(ToolPlan)

    def validate_json(self, json_str: str):
        try:
            payload = json.loads(json_str)
        except (ValueError, TypeError) as exc:
            raise ModelBehaviorError("Planner response is not valid JSON; no tool decisions recoverable") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get('tools'), list):
            raise ModelBehaviorError("Planner response has no identifiable tools list")
        return payload


def _resolve_tool(tool: PlannedTool, registry: dict, required) -> dict:
    errors = []
    if tool.name in required and not tool.selected:
        errors.append(f"{tool.name}.selected: user-required tool omitted; select an applicable supplied profile or report the unresolved requirement, never invent applicability")
    if tool.selected:
        if tool.skip_reason is not None:
            errors.append(f"{tool.name}.skip_reason: set null when selected=true")
        if tool.profile_id not in registry:
            errors.append(f"{tool.name}.profile_id: selected=true requires an applicable supplied profile ID")
        else:
            profile = registry[tool.profile_id]
            if profile["tool"] != tool.name or not profile["applicable"]:
                errors.append(f"{tool.name}.profile_id: profile does not fit use/specification")
    else:
        if tool.profile_id is not None:
            errors.append(f"{tool.name}.profile_id: selected=false requires null; keep selected=false and explain the rejected profile in reason")
        if tool.skip_reason is None:
            errors.append(f"{tool.name}.skip_reason: selected=false requires NEEDS_SPEC or NOT_APPLICABLE")
    if errors:
        raise PlanValidationError(errors)
    if not tool.selected:
        return {**tool.model_dump(), "status": tool.skip_reason, "spec": None}
    profile = registry[tool.profile_id]
    spec = CheckerSpec.model_validate(profile["spec"])
    if spec.name != tool.name:
        raise PlanValidationError([f"{tool.name}: profile/checker name mismatch"])
    return {**tool.model_dump(), "status": "SELECTED", "spec": spec.model_dump(),
            "configuration": profile["configuration"]}


def resolve_plan(plan: ToolPlan, registry: dict, *, required=("topology", "overhang")) -> dict:
    if len(plan.tools) != 4 or {t.name for t in plan.tools} != set(TOOLS):
        raise PlanValidationError(["tools: exactly one entry for each of topology, standing, overhang, fea is required"])
    rows, errors = [], []
    for tool in plan.tools:
        try:
            rows.append(_resolve_tool(tool, registry, required))
        except PlanValidationError as exc:
            errors.extend(exc.errors)
    if any(t.name == "fea" and t.selected for t in plan.tools) and not any(
            t.name == "topology" and t.selected for t in plan.tools):
        errors.append("fea: FEA requires a selected topology precheck")
    if errors:
        raise PlanValidationError(errors)
    result = {"object_use": plan.object_use, "tools": rows,
              "unresolved_assumptions": plan.unresolved_assumptions}
    return {**result, "sha256": stable_hash(result)}


def resolve_partial_plan(plan: ToolPlan | dict, registry: dict, *, required=("topology", "overhang")) -> dict:
    """Invalid/missing/duplicate entries disable only their named tool, never siblings."""
    payload = plan.model_dump() if isinstance(plan, ToolPlan) else plan
    if not isinstance(payload, dict) or not isinstance(payload.get("tools"), list):
        raise PlanValidationError(["whole plan is not an identifiable JSON tool list"])
    rows, warnings = [], []
    for entry in payload["tools"]:
        if not isinstance(entry, dict) or entry.get("name") not in TOOLS:
            warnings.append("unknown/unidentifiable tool entry ignored; no command executed")
    for name in TOOLS:
        entries = [x for x in payload["tools"] if isinstance(x, dict) and x.get("name") == name]
        try:
            if len(entries) != 1:
                raise PlanValidationError([f"{name}: expected one entry, got {len(entries)}"])
            row = _resolve_tool(PlannedTool.model_validate(entries[0]), registry, required)
        except (ValueError, KeyError, TypeError) as exc:
            errors = exc.errors if isinstance(exc, PlanValidationError) else [f"{name}: invalid tool schema/profile ({type(exc).__name__})"]
            row = {"name": name, "selected": False, "profile_id": None,
                   "status": "PLAN_INVALID", "spec": None, "errors": errors,
                   "reason": "; ".join(errors), "required": name in required,
                   "skip_reason": None}
        rows.append(row)
    invalid = [r for r in rows if r["status"] == "PLAN_INVALID"]
    status = "PLAN_PARTIAL" if invalid or warnings else "PLAN_VALID"
    if all(r["status"] == "PLAN_INVALID" for r in rows): status = "PLAN_INVALID"
    result = {"object_use": payload.get("object_use", ""), "tools": rows,
              "unresolved_assumptions": payload.get("unresolved_assumptions", []),
              "status": status, "warnings": warnings,
              "errors": [e for r in invalid for e in r["errors"]]}
    # FEA selection is retained; execution, not plan normalization, records dependency block.
    return {**result, "sha256": stable_hash(result)}


def verify_plan(resolved: dict) -> None:
    if stable_hash({k: v for k, v in resolved.items() if k != "sha256"}) != resolved["sha256"]:
        raise ValueError("frozen plan changed")


def keep_valid_plan_entries(previous: dict, draft: dict) -> dict:
    """One correction may fix invalid entries, not erase previously valid decisions."""
    fixed = {r['name']: {k:v for k,v in r.items() if k in PlannedTool.model_fields}
             for r in previous['tools'] if r['status'] != 'PLAN_INVALID'}
    tools = [x for x in draft.get('tools',[]) if not isinstance(x,dict) or x.get('name') not in fixed]
    return {**draft, 'tools': tools + list(fixed.values())}
