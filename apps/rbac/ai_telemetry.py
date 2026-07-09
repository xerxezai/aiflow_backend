"""
AI Champion — Server-side AI usage telemetry helper
====================================================

Single soft-coded helper that any feature can call to record an `AIUsageLog`
row for the AI Champion leaderboard / cost dashboard.

Usage (any OpenAI / Anthropic / Bedrock call site):

    from apps.rbac.ai_telemetry import record_ai_usage

    started = time.monotonic()
    response = client.chat.completions.create(model='gpt-4o', messages=[...])
    record_ai_usage(
        user=request.user,                # may be None for system tasks
        provider='openai',
        model_name='gpt-4o',
        response=response,                # OpenAI response object — auto-extracts tokens
        application='pid-verification',
        feature='ocr-extract',
        started_at_monotonic=started,
    )

Design contract:
- NEVER raises. Tracking failures are logged and swallowed.
- Pricing comes from the soft-coded `AIPricingConfig` table (Django admin
  editable). Falls back to module-level `DEFAULT_PRICING` if no row found.
- Token extraction is provider-agnostic — works with OpenAI v1 SDK, dict
  responses, and manual override (`tokens_input`/`tokens_output` kwargs).
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SOFT-CODED fallback pricing (USD per 1k tokens) — used only when DB has no
# matching `AIPricingConfig` row. Keep conservative and override via Django
# admin or a data migration. Source: vendor pricing as of 2026-Q1.
# ---------------------------------------------------------------------------
DEFAULT_PRICING = {
    # provider, model_name : (input_per_1k, output_per_1k)
    ('openai',    'gpt-4o'):              (Decimal('0.0025'), Decimal('0.0100')),
    ('openai',    'gpt-4o-mini'):         (Decimal('0.00015'), Decimal('0.00060')),
    ('openai',    'gpt-4-turbo'):         (Decimal('0.0100'), Decimal('0.0300')),
    ('openai',    'gpt-4'):               (Decimal('0.0300'), Decimal('0.0600')),
    ('openai',    'gpt-3.5-turbo'):       (Decimal('0.0005'), Decimal('0.0015')),
    ('anthropic', 'claude-3-5-sonnet'):   (Decimal('0.0030'), Decimal('0.0150')),
    ('anthropic', 'claude-3-opus'):       (Decimal('0.0150'), Decimal('0.0750')),
    ('google',    'gemini-1.5-pro'):      (Decimal('0.0035'), Decimal('0.0105')),
    ('google',    'gemini-1.5-flash'):    (Decimal('0.00035'),Decimal('0.00105')),
}


# ---------------------------------------------------------------------------
def _extract_tokens(response: Any) -> tuple[int, int]:
    """Return (tokens_input, tokens_output) from any common AI response shape."""
    if response is None:
        return 0, 0

    # OpenAI v1 SDK: response.usage.prompt_tokens / completion_tokens
    usage = getattr(response, 'usage', None)
    if usage is not None:
        ti = getattr(usage, 'prompt_tokens', None) or getattr(usage, 'input_tokens', 0)
        to = getattr(usage, 'completion_tokens', None) or getattr(usage, 'output_tokens', 0)
        return int(ti or 0), int(to or 0)

    # Dict-shaped (Bedrock, raw HTTP)
    if isinstance(response, dict):
        usage = response.get('usage') or {}
        ti = usage.get('prompt_tokens') or usage.get('input_tokens') or 0
        to = usage.get('completion_tokens') or usage.get('output_tokens') or 0
        return int(ti), int(to)

    return 0, 0


def _resolve_cost(provider: str, model_name: str,
                  ti: int, to: int) -> Decimal:
    """Look up DB pricing first, then fall back to soft-coded DEFAULT_PRICING."""
    try:
        from .ai_champion_service import resolve_cost_for_request
        cost = resolve_cost_for_request(provider, model_name, ti, to)
        if cost > 0:
            return cost
    except Exception as exc:
        logger.debug("AIPricingConfig lookup failed: %s", exc)

    pricing = DEFAULT_PRICING.get((provider, model_name))
    if not pricing:
        return Decimal('0')
    in_cost, out_cost = pricing
    return ((Decimal(ti) / Decimal('1000')) * in_cost
            + (Decimal(to) / Decimal('1000')) * out_cost)


# ---------------------------------------------------------------------------
def record_ai_usage(
    *,
    user,
    provider: str,
    model_name: str,
    response: Any = None,
    tokens_input: Optional[int] = None,
    tokens_output: Optional[int] = None,
    application: str = '',
    feature: str = '',
    request_id: str = '',
    started_at_monotonic: Optional[float] = None,
    latency_ms: Optional[int] = None,
    success: bool = True,
    error_code: str = '',
    cost_usd: Optional[Decimal] = None,
) -> Optional[Any]:
    """
    Persist a single AIUsageLog. Returns the created instance (or None on failure).
    """
    try:
        from .ai_champion_models import AIUsageLog

        ti = tokens_input if tokens_input is not None else 0
        to = tokens_output if tokens_output is not None else 0
        if (tokens_input is None or tokens_output is None) and response is not None:
            xti, xto = _extract_tokens(response)
            ti = ti or xti
            to = to or xto

        if cost_usd is None:
            cost_usd = _resolve_cost(provider, model_name, ti, to)

        if latency_ms is None:
            if started_at_monotonic is not None:
                latency_ms = int((time.monotonic() - started_at_monotonic) * 1000)
            else:
                latency_ms = 0

        # User can be None for system Celery tasks — skip silently.
        if user is None or not getattr(user, 'is_authenticated', True):
            logger.debug("record_ai_usage skipped: anonymous/system user")
            return None

        return AIUsageLog.objects.create(
            user=user,
            provider=provider[:32],
            model_name=model_name[:128],
            application=(application or '')[:64],
            feature=(feature or '')[:64],
            request_id=(request_id or '')[:64],
            tokens_input=max(0, int(ti)),
            tokens_output=max(0, int(to)),
            cost_usd=cost_usd or Decimal('0'),
            latency_ms=max(0, int(latency_ms or 0)),
            success=bool(success),
            error_code=(error_code or '')[:64],
        )
    except Exception as exc:
        logger.warning("record_ai_usage failed (provider=%s model=%s): %s",
                       provider, model_name, exc)
        return None
