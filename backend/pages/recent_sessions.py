from typing import Any

from fastapi import Depends, HTTPException

from app.auth import verify_student_token
from app.routes import logger, router
from app.supabase_client import get_supabase_admin
from app.subscriptions.service import get_plan_name_from_row, get_subscription_row


def _normalize_plan_tier(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return "starter"


def _derive_session_status(row: dict[str, Any]) -> str:
    raw_status = row.get("status")
    if isinstance(raw_status, str) and raw_status.strip():
        return raw_status.strip().lower()

    if row.get("generated_notes"):
        return "active"

    return "saved"


@router.get("/dashboard/sessions")
async def get_user_historical_sessions(user=Depends(verify_student_token)):
    """
    Secure endpoint: lists previous learning workspaces belonging only to this student.
    """
    student_uuid = user.id
    supabase_admin = get_supabase_admin()
    logger.info("Session log history requested for user: %s", student_uuid)

    current_plan = "starter"
    subscription_status = "trial"

    try:
        try:
            subscription_row = get_subscription_row(str(student_uuid), supabase=supabase_admin)
            current_plan = _normalize_plan_tier(get_plan_name_from_row(subscription_row, supabase=supabase_admin))
            subscription_status = _normalize_plan_tier(subscription_row.get("status"))
        except Exception as subscription_error:
            logger.warning(
                "Subscription lookup skipped for recent sessions: %s",
                str(subscription_error),
            )

        db_query = (
            supabase_admin.table("sessions")
            .select("*")
            .eq("student_id", student_uuid)
            .order("created_at", desc=True)
            .execute()
        )

        sessions = []
        for row in db_query.data or []:
            session_status = _derive_session_status(row)
            sessions.append(
                {
                    **row,
                    "subject": row.get("course"),
                    "topic": row.get("user_prompt"),
                    "course": row.get("course"),
                    "user_prompt": row.get("user_prompt"),
                    "status": session_status,
                    "is_active": session_status == "active",
                    "current_plan": current_plan,
                    "plan_tier": current_plan,
                    "subscription_status": subscription_status,
                }
            )

        return {
            "status": "success",
            "total_sessions": len(sessions),
            "current_plan": current_plan,
            "plan_tier": current_plan,
            "subscription_status": subscription_status,
            "sessions": sessions,
        }
    except Exception as e:
        logger.error("Failed to load historic sessions: %s", str(e))
        raise HTTPException(status_code=500, detail="Failed to fetch workspace history.")
