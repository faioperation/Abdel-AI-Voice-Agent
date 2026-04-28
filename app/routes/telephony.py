import httpx
from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import get_current_user
from app.config import VAPI_API_KEY, VAPI_BASE

router = APIRouter()

@router.get("/api/telephony/numbers")
async def get_numbers(user=Depends(get_current_user)):
    url = f"{VAPI_BASE}/phone-number"
    headers = {"Authorization": f"Bearer {VAPI_API_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(url, headers=headers)
        if res.status_code == 200:
            return res.json()
        raise HTTPException(res.status_code, "Error fetching numbers from Vapi")

@router.post("/api/telephony/numbers")
async def add_number(data: dict = Body(...), user=Depends(get_current_user)):
    """
    Import a phone number. Supports Twilio and Vonage providers.
    For Twilio: provider, number, twilioAccountSid, twilioAuthToken, assistantId (optional)
    For Vonage: provider, number, vonageApiKey, vonageApiSecret, assistantId (optional)
    """
    url = f"{VAPI_BASE}/phone-number"
    headers = {
        "Authorization": f"Bearer {VAPI_API_KEY}",
        "Content-Type": "application/json"
    }

    provider = data.get("provider", "twilio")
    number = data.get("number", "").strip()
    assistant_id = data.get("assistantId", "")

    # Build provider-specific payload
    if provider == "vonage":
        clean_data = {
            "provider": "vonage",
            "number": number,
            "vonageApiKey": data.get("vonageApiKey", "").strip(),
            "vonageApiSecret": data.get("vonageApiSecret", "").strip(),
        }
    else:
        # Default: Twilio
        clean_data = {
            "provider": "twilio",
            "number": number,
            "twilioAccountSid": data.get("twilioAccountSid", "").strip(),
            "twilioAuthToken": data.get("twilioAuthToken", "").strip(),
        }

    # Only add assistantId if non-empty
    if assistant_id:
        clean_data["assistantId"] = assistant_id

    # Remove any empty string values
    clean_data = {k: v for k, v in clean_data.items() if v}

    async with httpx.AsyncClient() as client:
        res = await client.post(url, headers=headers, json=clean_data)
        if res.status_code in (200, 201):
            return res.json()

        print(f"Vapi Phone Add Error ({provider}): {res.text}")
        raise HTTPException(res.status_code, f"Failed to import {provider} number. Check credentials.")

@router.delete("/api/telephony/numbers/{number_id}")
async def delete_number(number_id: str, user=Depends(get_current_user)):
    url = f"{VAPI_BASE}/phone-number/{number_id}"
    headers = {"Authorization": f"Bearer {VAPI_API_KEY}"}
    async with httpx.AsyncClient() as client:
        res = await client.delete(url, headers=headers)
        if res.status_code in (200, 204):
            return {"success": True}
        raise HTTPException(res.status_code, "Error deleting number from Vapi")
