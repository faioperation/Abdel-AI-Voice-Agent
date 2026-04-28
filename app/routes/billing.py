from fastapi import APIRouter, Depends
from app.auth import get_current_user
from app.config import VAPI_PUBLIC_KEY

router = APIRouter()


@router.get("/api/billing")
async def get_billing_info(user=Depends(get_current_user)):
    """
    Vapi does not expose a public REST API for account balance.
    Balance must be checked from the Vapi dashboard.
    We return a link to the billing page.
    """
    return {
        "balance": None,
        "dashboard_url": "https://dashboard.vapi.ai/billing",
        "message": "Vapi does not expose a balance API endpoint. Please check your balance directly in the Vapi Dashboard."
    }


@router.get("/api/config")
async def get_public_config():
    """Return safe public config for frontend. Publicly accessible."""
    return {
        "vapi_public_key": VAPI_PUBLIC_KEY
    }
