"""Pure request-error classification, also loaded by the independent harness.

No retries, SDK imports, session changes or ledger mutations belong here.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

_CONTEXT_CODES = {'context_too_large', 'context_length_exceeded', 'prompt_too_large',
                  'max_prompt_chars_exceeded', 'input_too_long'}
_SHARED_CODES = {'insufficient_quota', 'quota_exceeded', 'billing_hard_limit_reached',
    'billing_not_active', 'account_deactivated', 'account_disabled', 'invalid_api_key',
    'authentication_error', 'permission_denied'}
_SECRET = re.compile(r'(?i)(bearer\s+|api[_-]?key[\s=:\"\']+|token[\s=:\"\']+)[^\s,\"\'}]+|\bsk-[\w-]+')


def _first(*values):
    return next((v for v in values if v is not None), None)


def safe_request_reason(error):
    return _SECRET.sub('[REDACTED]', str(error))[:300]


def classify_model_request_error(error: Exception) -> dict[str, Any] | None:
    body = getattr(error, 'body', None)
    body = body if isinstance(body, Mapping) else {}
    nested = body.get('error')
    nested = nested if isinstance(nested, Mapping) else {}
    code = _first(getattr(error, 'code', None), nested.get('code'), body.get('code'))
    status = getattr(error, 'status_code', None)
    request_id = _first(getattr(error, 'request_id', None), nested.get('request_id'), body.get('request_id'))
    message = str(_first(nested.get('message'), body.get('message'), str(error))).lower()
    lower_code = code.lower() if isinstance(code, str) else ''
    names = {c.__name__ for c in type(error).__mro__}
    if code in {'AGENT_FEEDBACK_TOO_LARGE', 'AGENT_FEEDBACK_INVALID'}:
        kind = 'input_construction'
    elif lower_code in _CONTEXT_CODES or status == 413 or any(v in message for v in (
            'context_too_large', 'context_length_exceeded', 'maximum context length',
            'context window exceeded', 'input exceeds the context', 'prompt exceeds max_prompt_chars')):
        kind = 'context_limit'
    elif status in {401, 403} or lower_code in _SHARED_CODES or names & {
            'AuthenticationError', 'PermissionDeniedError', 'AssetInfrastructureError'} or any(
                text in message for text in ('insufficient quota', 'exceeded your current quota',
                                             'billing not active', 'shared_environment_unavailable')):
        kind = 'shared_fault'
    elif status in {400, 422}:
        kind = 'request_rejected'
    elif status in {408, 429} or isinstance(status, int) and status >= 500 or lower_code in {
            'request_timeout', 'connection_error', 'timeout'} or names & {
            'TimeoutError', 'ConnectError', 'ConnectTimeout', 'ReadTimeout',
            'APIConnectionError', 'APITimeoutError', 'RateLimitError', 'InternalServerError',
            'PlannerStreamInterrupted'}:
        kind = 'transient'
    else:
        return None
    return {'kind': kind, 'code': code if isinstance(code, str) else None,
            'status_code': status if isinstance(status, int) else None,
            'request_id': request_id if isinstance(request_id, str) else None,
            'reason': safe_request_reason(error),
            'retryable_same_input': None if kind == 'transient' else False}


def terminal_stop_reason(classification):
    if not classification:
        return None
    if classification['kind'] == 'context_limit' or classification['code'] == 'AGENT_FEEDBACK_TOO_LARGE':
        return 'agent_input_too_large'
    if classification['kind'] == 'request_rejected':
        return 'agent_request_rejected'
    if classification['kind'] == 'input_construction':
        return 'agent_feedback_invalid'
    return None


def propagate_request_error(error, classification):
    return (classification is not None and classification['kind'] == 'shared_fault' or
            isinstance(error, (TypeError, AttributeError, KeyError, AssertionError, ImportError)))
