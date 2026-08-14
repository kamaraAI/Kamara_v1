from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.supabase_client import get_supabase_admin

from .plans import PLAN_CATALOG, normalize_feature_name, normalize_plan_name

logger = logging.getLogger("KamaraLogger")


@dataclass(frozen=True)
class SubscriptionSummary:
    user_id: str
    plan_tier: str
    subscription_status: str
    trial_ends_at: str | None
    usage: dict[str, int | None]
    limits: dict[str, Any]


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _format_datetime(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    return parsed.isoformat() if parsed else None


def _safe_execute(query):
    try:
        return query.execute()
    except Exception as exc:
        raise exc


def get_subscription_row(user_id: str, supabase=None) -> dict[str, Any]:
    supabase = supabase or get_supabase_admin()
    try:
        response = _safe_execute(
            supabase.table("subscriptions")
            .select("id, user_id, plan_id, status, trial_started_at, trial_ends_at, current_period_start, current_period_end, created_at")
            .eq("user_id", user_id)
            .maybe_single()
        )
        return getattr(response, "data", None) or {}
    except Exception as exc:
        logger.warning("Subscription lookup failed for %s: %s", user_id, exc)
        return {}


def get_plan_name_from_row(subscription_row: dict[str, Any], supabase=None) -> str:
    plan_id = subscription_row.get("plan_id")
    if not plan_id:
        return "starter"

    supabase = supabase or get_supabase_admin()
    try:
        response = _safe_execute(
            supabase.table("plans").select("name").eq("id", plan_id).maybe_single()
        )
        plan_row = getattr(response, "data", None) or {}
        return normalize_plan_name(plan_row.get("name"))
    except Exception as exc:
        logger.warning("Plan lookup failed for plan_id=%s: %s", plan_id, exc)
        return "starter"


def get_usage_count(
    user_id: str,
    usage_key: str,
    *,
    period_start: datetime | None = None,
    supabase=None,
) -> int:
    return 0


def record_usage_event(
    user_id: str,
    usage_key: str,
    *,
    quantity: int = 1,
    supabase=None,
) -> None:
    return None


def expire_due_trials(*, supabase=None, now: datetime | None = None) -> int:
    supabase = supabase or get_supabase_admin()
    now = now or datetime.now(timezone.utc)
    expired_count = 0

    try:
        response = _safe_execute(
            supabase.table("subscriptions")
            .select("id, user_id, status, trial_ends_at")
            .eq("status", "trial")
        )
        rows = getattr(response, "data", None) or []
        for row in rows:
            trial_ends_at = _parse_datetime(row.get("trial_ends_at"))
            if trial_ends_at is None or trial_ends_at > now:
                continue

            try:
                supabase.table("subscriptions").update(
                    {
                        "status": "expired",
                        "updated_at": now.isoformat(),
                    }
                ).eq("id", row["id"]).execute()
                expired_count += 1
            except Exception as exc:
                logger.warning("Failed to expire trial for subscription %s: %s", row.get("id"), exc)

    except Exception as exc:
        logger.warning("Trial expiry sweep skipped: %s", exc)

    return expired_count


def get_subscription_summary(user_id: str, supabase=None) -> SubscriptionSummary:
    supabase = supabase or get_supabase_admin()
    subscription_row = get_subscription_row(user_id, supabase=supabase)
    plan_tier = get_plan_name_from_row(subscription_row, supabase=supabase)
    plan_config = PLAN_CATALOG.get(plan_tier, PLAN_CATALOG["starter"])
    status_name = str(subscription_row.get("status") or "trial").lower()
    trial_ends_at = _format_datetime(subscription_row.get("trial_ends_at"))
    period_start = _parse_datetime(subscription_row.get("current_period_start") or subscription_row.get("trial_started_at") or subscription_row.get("created_at"))

    usage: dict[str, int | None] = {
        "message_send": get_usage_count(user_id, "message_send", period_start=period_start, supabase=supabase),
        "course_generation": get_usage_count(user_id, "course_generation", period_start=period_start, supabase=supabase),
    }

    return SubscriptionSummary(
        user_id=user_id,
        plan_tier=plan_tier,
        subscription_status=status_name,
        trial_ends_at=trial_ends_at,
        usage=usage,
        limits=plan_config,
    )


def evaluate_feature_access(
    user_id: str,
    feature_name: str,
    *,
    quantity: int = 1,
    size_bytes: int | None = None,
    content_chars: int | None = None,
    has_external_source: bool = False,
    supabase=None,
) -> dict[str, Any]:
    return {
        "allowed": True,
        "error_code": None,
        "reason": None,
        "required_plan": None,
        "feature": normalize_feature_name(feature_name),
        "plan_tier": "open",
        "subscription_status": "open",
        "message": None,
        "used": None,
        "limit": None,
        "remaining": None,
    }


def enforce_feature_access(
    user_id: str,
    feature_name: str,
    *,
    quantity: int = 1,
    size_bytes: int | None = None,
    content_chars: int | None = None,
    has_external_source: bool = False,
    supabase=None,
) -> dict[str, Any]:
    return evaluate_feature_access(
        user_id,
        feature_name,
        quantity=quantity,
        size_bytes=size_bytes,
        content_chars=content_chars,
        has_external_source=has_external_source,
        supabase=supabase,
    )
