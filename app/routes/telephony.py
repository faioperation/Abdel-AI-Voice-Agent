import httpx
from fastapi import APIRouter, Depends, HTTPException, Body
from app.auth import get_current_user
from app.config import VAPI_API_KEY, VAPI_BASE
from app import http_client

router = APIRouter()


def _vapi_headers():
    return {"Authorization": f"Bearer {VAPI_API_KEY}"}


@router.get("/api/telephony/numbers")
async def get_numbers(user=Depends(get_current_user)):
    client = http_client.get_vapi_client()
    try:
        res = await client.get("/phone-number", headers=_vapi_headers())
        if res.status_code == 200:
            return res.json()
        raise HTTPException(res.status_code, "Error fetching numbers from Vapi")
    except httpx.ReadTimeout:
        raise HTTPException(504, "Timeout fetching phone numbers from Vapi")


@router.post("/api/telephony/numbers")
async def add_number(data: dict = Body(...), user=Depends(get_current_user)):
    provider = data.get("provider", "twilio")
    number = data.get("number", "").strip()
    assistant_id = data.get("assistantId", "")

    if provider == "vonage":
        clean_data = {
            "provider": "vonage",
            "number": number,
            "vonageApiKey": data.get("vonageApiKey", "").strip(),
            "vonageApiSecret": data.get("vonageApiSecret", "").strip(),
        }
    else:
        clean_data = {
            "provider": "twilio",
            "number": number,
            "twilioAccountSid": data.get("twilioAccountSid", "").strip(),
            "twilioAuthToken": data.get("twilioAuthToken", "").strip(),
        }

    if assistant_id:
        clean_data["assistantId"] = assistant_id

    clean_data = {k: v for k, v in clean_data.items() if v}

    headers = {**_vapi_headers(), "Content-Type": "application/json"}
    client = http_client.get_vapi_client()
    try:
        res = await client.post("/phone-number", headers=headers, json=clean_data)
        if res.status_code in (200, 201):
            return res.json()
        print(f"Vapi Phone Add Error ({provider}): {res.text}")
        raise HTTPException(res.status_code, f"Failed to import {provider} number. Check credentials.")
    except httpx.ReadTimeout:
        raise HTTPException(504, f"Timeout importing {provider} number from Vapi")


@router.delete("/api/telephony/numbers/{number_id}")
async def delete_number(number_id: str, user=Depends(get_current_user)):
    client = http_client.get_vapi_client()
    try:
        res = await client.delete(f"/phone-number/{number_id}", headers=_vapi_headers())
        if res.status_code in (200, 204):
            return {"success": True}
        raise HTTPException(res.status_code, "Error deleting number from Vapi")
    except httpx.ReadTimeout:
        raise HTTPException(504, "Timeout deleting phone number from Vapi")
