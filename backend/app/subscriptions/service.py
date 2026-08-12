from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status

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
    supabase = supabase or get_supabase_admin()

    try:
        query = supabase.table("subscription_usage_events").select("id", count="exact").eq("user_id", user_id).eq("feature_key", usage_key)
        if period_start is not None:
            query = query.gte("created_at", period_start.isoformat())

        response = _safe_execute(query)
        count = getattr(response, "count", None)
        if isinstance(count, int):
            return count
        return len(getattr(response, "data", None) or [])
    except Exception as exc:
        logger.warning("Usage lookup failed for %s/%s: %s", user_id, usage_key, exc)
        return 0


def record_usage_event(
    user_id: str,
    usage_key: str,
    *,
    quantity: int = 1,
    supabase=None,
) -> None:
    supabase = supabase or get_supabase_admin()
    try:
        supabase.table("subscription_usage_events").insert(
            {
                "user_id": user_id,
                "feature_key": usage_key,
                "quantity": quantity,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
    except Exception as exc:
        logger.warning("Usage event insert skipped for %s/%s: %s", user_id, usage_key, exc)


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
    supabase = supabase or get_supabase_admin()
    summary = get_subscription_summary(user_id, supabase=supabase)
    feature_key = normalize_feature_name(feature_name)
    plan_config = PLAN_CATALOG.get(summary.plan_tier, PLAN_CATALOG["starter"])
    status_name = summary.subscription_status
    now = datetime.now(timezone.utc)

    trial_ends_at = _parse_datetime(summary.trial_ends_at)
    if status_name == "trial" and trial_ends_at is not None and trial_ends_at <= now:
        status_name = "expired"

    if status_name == "expired" and summary.plan_tier != "pro":
        return {
            "allowed": False,
            "error_code": "subscription_required",
            "reason": "trial_expired",
            "required_plan": "pro",
            "feature": feature_key,
            "plan_tier": summary.plan_tier,
            "subscription_status": status_name,
            "message": "Your trial has ended. Upgrade to Pro to continue.",
            "remaining": 0,
            "limit": plan_config.get("message_limit"),
        }

    if feature_key == "message_send":
        limit = plan_config.get("message_limit")
        used = int(summary.usage.get("message_send") or 0)
        next_total = used + quantity
        if isinstance(limit, int) and next_total > limit:
            return {
                "allowed": False,
                "error_code": "subscription_required",
                "reason": "message_limit_reached",
                "required_plan": "pro",
                "feature": feature_key,
                "plan_tier": summary.plan_tier,
                "subscription_status": status_name,
                "message": f"Starter plan includes only {limit} messages. Upgrade to Pro to keep sending messages.",
                "used": used,
                "limit": limit,
                "remaining": max(limit - used, 0),
            }

    if feature_key == "external_source" and not bool(plan_config.get("allow_external_sources")):
        return {
            "allowed": False,
            "error_code": "subscription_required",
            "reason": "external_sources_blocked",
            "required_plan": "pro",
            "feature": feature_key,
            "plan_tier": summary.plan_tier,
            "subscription_status": status_name,
            "message": "External sources are available on Pro only.",
        }

    if feature_key == "pdf_upload":
        max_pdf_mb = plan_config.get("max_pdf_mb")
        if isinstance(size_bytes, int) and isinstance(max_pdf_mb, int) and size_bytes > max_pdf_mb * 1024 * 1024:
            return {
                "allowed": False,
                "error_code": "subscription_required",
                "reason": "pdf_size_limit",
                "required_plan": "pro",
                "feature": feature_key,
                "plan_tier": summary.plan_tier,
                "subscription_status": status_name,
                "message": f"PDF uploads above {max_pdf_mb} MB require Pro.",
                "limit_mb": max_pdf_mb,
            }

    if feature_key == "note_size":
        max_note_chars = plan_config.get("max_note_chars")
        if isinstance(content_chars, int) and isinstance(max_note_chars, int) and content_chars > max_note_chars:
            return {
                "allowed": False,
                "error_code": "subscription_required",
                "reason": "note_size_limit",
                "required_plan": "pro",
                "feature": feature_key,
                "plan_tier": summary.plan_tier,
                "subscription_status": status_name,
                "message": f"That note is too large for the Starter plan. Upgrade to Pro to publish larger notes.",
                "limit_chars": max_note_chars,
            }

    return {
        "allowed": True,
        "error_code": None,
        "reason": None,
        "required_plan": None,
        "feature": feature_key,
        "plan_tier": summary.plan_tier,
        "subscription_status": status_name,
        "message": None,
        "used": summary.usage.get(feature_key) if feature_key in summary.usage else None,
        "limit": plan_config.get("message_limit") if feature_key == "message_send" else None,
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
    decision = evaluate_feature_access(
        user_id,
        feature_name,
        quantity=quantity,
        size_bytes=size_bytes,
        content_chars=content_chars,
        has_external_source=has_external_source,
        supabase=supabase,
    )

    if not decision["allowed"]:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=decision)

    return decision
