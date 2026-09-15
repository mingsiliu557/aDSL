"""Per-request opt-in ledger; unresolved requests remain charged at their bound."""
import fcntl
import json
from pathlib import Path
import time
import uuid
import asyncio
from openai import APIError, APIConnectionError
from agents.models.interface import Model

from adsl.agents.utils.io import write_json

LIMIT = 100_000_000


class BudgetModel(Model):
    def __init__(self, model, root: Path, *, request_bound: int, evidence: dict,
                 budget_scope: str = "cliproxy"):
        self.model, self.root, self.bound, self.evidence = model, root, request_bound, evidence
        if budget_scope not in {"stepcode", "cliproxy"}:
            raise ValueError("unknown budget scope")
        self.scope = budget_scope
        self.request_failed = False
        self.stem = "stepcode_usage" if budget_scope == "stepcode" else "cliproxy_token_budget"
        if not 0 < request_bound <= LIMIT or not evidence:
            raise ValueError("verified per-request bound is required")
        root.mkdir(parents=True, exist_ok=True)

    def update(self, callback):
        with (self.root / (self.stem + ".lock")).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.root / (self.stem + ".json")
            state = json.loads(path.read_text()) if path.exists() else {
                "scope": self.scope, "limit": None if self.scope == "stepcode" else LIMIT, "requests": {}}
            if state.get("scope") != self.scope:
                raise ValueError("budget ledger scope mismatch")
            callback(state)
            write_json(path, state)

    async def get_response(self, *args, **kwargs):
        for attempt in range(3 if self.scope == 'stepcode' else 1):
            try:
                return await self._one_response(*args, **kwargs)
            except APIError as error:
                retryable = isinstance(error, APIConnectionError) or getattr(error,'status_code',None) in {429,500,502,503,504}
                if self.scope != 'stepcode' or not retryable or attempt == 2:raise
                # Same agent invocation/candidate, separate request debit; parent
                # case deadline includes backoff. Do not restart the candidate.
                await asyncio.sleep((30,60)[attempt])
                self.request_failed=False

    async def _one_response(self, *args, **kwargs):
        if self.request_failed:
            raise RuntimeError("TOKEN_USAGE_UNRESOLVED: do not replay failed model instance")
        key = uuid.uuid4().hex
        def reserve(state):
            used = sum(r["charged"] for r in state["requests"].values())
            if self.scope == "cliproxy" and any(r["status"] not in {"SETTLED", "BOUNDED_UNKNOWN"} for r in state["requests"].values()):
                raise RuntimeError("TOKEN_USAGE_UNRESOLVED: do not replay unknown requests")
            if self.scope == "cliproxy" and used + self.bound > state["limit"]:
                raise RuntimeError("TOKEN_BUDGET_EXHAUSTED")
            state["requests"][key] = {"status": "RESERVED", "charged": self.bound,
                "started_at": time.time(), "bound_evidence": self.evidence}
        self.update(reserve)
        # StepCode has no shared token cap. Retain unknown usage but allow another
        # case's model instance; the case start marker prevents replay of this case.
        self.request_failed = True
        try:
            result = await self.model.get_response(*args, **kwargs)
        except APIError as error:
            # Keep the full upper-bound debit, not fabricated zero actual usage.
            # A later independent request may use the remaining capped budget.
            def failed(state):
                state['requests'][key].update(status='BOUNDED_UNKNOWN', actual_tokens=None,
                    error_type=type(error).__name__, finished_at=time.time())
            self.update(failed)
            raise
        usage = result.usage
        if usage is None or usage.input_tokens <= 0 or usage.output_tokens < 0:
            raise RuntimeError("TOKEN_USAGE_UNRESOLVED")
        total = usage.input_tokens + usage.output_tokens
        if total > self.bound:
            raise RuntimeError("TOKEN_BOUND_VIOLATED")
        def settle(state):
            state["requests"][key].update(status="SETTLED", charged=total,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                finished_at=time.time())
        self.update(settle)
        self.request_failed = False
        return result

    async def stream_response(self, *args, **kwargs):
        raise RuntimeError("budgeted experiment uses non-streaming responses only")
        yield  # async iterator contract
