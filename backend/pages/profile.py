


import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from app.supabase_client import get_supabase_admin
from   app.auth  import verify_student_token # Your repaired token middleware
from app.subscriptions.service import get_plan_name_from_row, get_subscription_row

logger = logging.getLogger("KamaraLogger")
profile_router = APIRouter(prefix="/api/v1/pages", tags=["Profile Page"])

# 1. Define the clean data structure we will send back to React
class ProfilePageResponse(BaseModel):
    id: str
    email: str
    full_name: str
    plan_tier: str
    subscription_status: str
    avatar_url: Optional[str] = None

@profile_router.get("/dashboard/profile", response_model=ProfilePageResponse)
async def get_user_profile_page(current_user: dict = Depends(verify_student_token)):
    """
    Page-driven endpoint that gathers identity and account tiers 
    for the logged-in student securely using their session token.
    """
    student_id = current_user.id
    logger.info("Fetching profile page details for student UUID: %s", student_id)
    
    supabase = get_supabase_admin()
    
    try:
        # Step A: Query the user's personal details from the profiles table
        profile_query = supabase.table("profiles")\
            .select("email, first_name, last_name, avatar_url")\
            .eq("id", student_id)\
            .maybe_single()\
            .execute()
            
        if not profile_query or not getattr(profile_query, 'data', None):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Student profile metadata records not found."
            )

        profile_data = profile_query.data
        full_name = " ".join(
            part for part in [profile_data.get("first_name"), profile_data.get("last_name")] if part
        ).strip() or profile_data.get("email", "Student")

        # Fallback values if the user hasn't initialized a subscription tracking row yet
        plan_tier = "starter"
        sub_status = "trial"

        try:
            subscription_row = get_subscription_row(str(student_id), supabase=supabase)
            if subscription_row:
                plan_tier = get_plan_name_from_row(subscription_row, supabase=supabase)
                sub_status = str(subscription_row.get("status") or "trial").lower()
        except Exception as subscription_error:
            logger.warning("Subscription lookup skipped for profile: %s", str(subscription_error))

        # Step C: Merge both profiles together into our unified response block
        return {
            "id": student_id,
            "email": profile_data.get("email"),
            "full_name": full_name,
            "plan_tier": plan_tier,
            "subscription_status": sub_status,
            "avatar_url": profile_data.get("avatar_url")
        }

    except HTTPException as http_err:
        raise http_err
    except Exception as e:
        logger.error("❌ PROFILE PAGE ASSEMBLY FAILURE: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load your profile details. Please try again later."
        )
