import json
import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.address_validation import process_delivery_address

router = APIRouter(prefix="/api/verify-address", tags=["Address Verification"])
logger = logging.getLogger(__name__)


# ── Vapi envelope parsing ─────────────────────────────────────────────────────

def _extract_tool_call(body: dict):
    """
    Extract (toolCallId, arguments_dict) from Vapi's tool-call envelope.
    Handles both string and dict arguments.
    Falls back to flat body for manual curl testing.
    """
    msg = body.get("message", {}) if isinstance(body, dict) else {}
    raw = msg.get("toolCalls") or msg.get("toolCallList") or []
    for call in raw:
        cid = call.get("id") or call.get("toolCallId")
        fn = call.get("function", call)
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        return (cid, args or {})
    # Fallback: flat body for manual/curl testing
    if isinstance(body, dict) and ("postal_code" in body or "address" in body):
        return (body.get("toolCallId"), body)
    return (None, {})


def _make_vapi_response(call_id: Optional[str], deliverable: bool, suggestion: str):
    """Build the Vapi-compliant results envelope. Always returns HTTP 200."""
    result_obj = {"deliverable": deliverable, "suggestion": suggestion}
    return JSONResponse(status_code=200, content={
        "results": [{
            "toolCallId": call_id or "",
            "result": json.dumps(result_obj, ensure_ascii=False),
        }]
    })


# ── Route handlers (registered on both "" and "/" to avoid 307 redirect) ──────

async def _handle_verify(request: Request):
    """
    Shared handler for address verification — speaks Vapi's tool-call protocol.

    Expected tool-call arguments from Vapi (set in each assistant's tool definition):
        address              (str)  — full address spoken by the customer
        postal_code          (str)  — postal code spoken by the customer
        shop_lat             (float)— shop latitude  (injected from system prompt)
        shop_lng             (float)— shop longitude (injected from system prompt)
        delivery_radius_km   (float)— delivery radius in km (from system prompt)
        allowed_postal_codes (str, optional) — comma-separated list e.g. "2860,2800"

    The shop config (lat/lng/radius/postal codes) is NOT stored server-side —
    it travels with each assistant via its system prompt, making this endpoint
    fully multi-tenant and stateless.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    call_id, args = _extract_tool_call(body)

    # ── Extract address fields ────────────────────────────────────────────────
    raw_address  = str(args.get("address", "")).strip()
    postal_code  = str(args.get("postal_code", "")).strip()

    # ── Extract per-assistant shop config (passed by the AI from system prompt) ─
    try:
        shop_lat = float(args.get("shop_lat", 0))
    except (TypeError, ValueError):
        shop_lat = 0.0

    try:
        shop_lng = float(args.get("shop_lng", 0))
    except (TypeError, ValueError):
        shop_lng = 0.0

    try:
        delivery_radius_km = float(args.get("delivery_radius_km", 7))
    except (TypeError, ValueError):
        delivery_radius_km = 7.0

    # allowed_postal_codes arrives as a comma-separated string or a JSON list
    raw_codes = args.get("allowed_postal_codes", "")
    if isinstance(raw_codes, list):
        allowed_postal_codes = [str(c).strip() for c in raw_codes if str(c).strip()]
    elif isinstance(raw_codes, str) and raw_codes.strip():
        allowed_postal_codes = [c.strip() for c in raw_codes.split(",") if c.strip()]
    else:
        allowed_postal_codes = []

    logger.info(
        "[ADDRESS] Received — toolCallId=%s, address=%r, postal=%r, shop=(%.4f,%.4f), radius=%.1f km",
        call_id, raw_address, postal_code, shop_lat, shop_lng, delivery_radius_km,
    )

    # ── Guard: shop coordinates must be provided ──────────────────────────────
    if shop_lat == 0.0 or shop_lng == 0.0:
        logger.warning("[ADDRESS] shop_lat/shop_lng not provided in tool call — cannot do zone check.")
        return _make_vapi_response(
            call_id, False,
            "I'm unable to check the delivery zone right now. Please ask a team member to confirm."
        )

    # ── Guard: address must be provided ──────────────────────────────────────
    if not raw_address or not postal_code:
        logger.warning("[ADDRESS] Missing address or postal_code in args: %r", args)
        return _make_vapi_response(
            call_id, False,
            "Missing address or postal code — please ask the customer to provide both."
        )

    # ── Run the full pipeline ─────────────────────────────────────────────────
    try:
        pipeline_result = await process_delivery_address(
            raw_address=raw_address,
            postal_code=postal_code,
            shop_lat=shop_lat,
            shop_lng=shop_lng,
            delivery_radius_km=delivery_radius_km,
            allowed_postal_codes=allowed_postal_codes or None,
        )
        deliverable = pipeline_result["deliverable"]
        suggestion  = pipeline_result["suggestion"]
        logger.info("[ADDRESS] Pipeline result — deliverable=%s, suggestion=%r", deliverable, suggestion)
        return _make_vapi_response(call_id, deliverable, suggestion)

    except Exception as exc:
        logger.error("[ADDRESS] Unexpected error in pipeline: %s", exc, exc_info=True)
        return _make_vapi_response(
            call_id, False,
            "I had trouble verifying that address. Let me get someone to help confirm the delivery details."
        )


# Register on BOTH "" and "/" to avoid FastAPI 307 redirect (saves ~100-300ms)
@router.post("")
async def verify_address_no_slash(request: Request):
    return await _handle_verify(request)

@router.post("/")
async def verify_address_with_slash(request: Request):
    return await _handle_verify(request)
