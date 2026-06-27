import logging
import json
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
import requests
import httpx
from app.database import get_db, CallRecord, Assistant, Order
from app.auth import get_current_user
from app.config import VAPI_BASE, VAPI_API_KEY
from app.vapi_client import vapi_headers
from app import http_client

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/api/start-call")
async def start_call(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    body = await request.json()
    assistant_id = body.get("assistant_id")
    phone_number = body.get("phone_number")
    if not assistant_id or not phone_number:
        raise HTTPException(400, "Missing fields")
    payload = {"assistantId": assistant_id, "customer": {"number": phone_number}}
#    async with httpx.AsyncClient(timeout=30) as client:
    client = http_client.get_vapi_client()
    resp = await client.post(f"{VAPI_BASE}/call", json=payload, headers=vapi_headers())
    if resp.status_code not in (200, 201):
        raise HTTPException(resp.status_code, resp.text)
    call_data = resp.json()
    call_id = call_data["id"]
    new_call = CallRecord(
        id=call_id,
        assistant_id=assistant_id,
        phone_number=phone_number,
        started_at=datetime.utcnow(),
        status="initiated"
    )
    db.add(new_call)
    db.query(Assistant).filter(Assistant.id == assistant_id).update(
        {Assistant.call_count: Assistant.call_count + 1}
    )
    db.commit()
    return {"success": True, "call_id": call_id}


@router.post("/api/webhook/call")
async def call_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        data = await request.json()
        logger.info(f"[VAPI WEBHOOK] Received event: {json.dumps(data)}")
        
        # Vapi sends call info either at root or inside message
        call_obj = data.get("call") or data.get("message", {}).get("call") or {}
        call_id = call_obj.get("id")
        message = data.get("message", {})
        event = message.get("type") or data.get("type")

        logger.info(f"[VAPI WEBHOOK] CallID: {call_id}, Event: {event}")

        # Pre-warm cache the moment the call goes live
        status = message.get("status") or data.get("status")
        if event == "status-update" and status == "in-progress":
            assistant_obj = message.get("assistant") or data.get("assistant") or {}
            system_msgs = assistant_obj.get("model", {}).get("messages", [])
            system_content = next(
                (m["content"] for m in system_msgs if isinstance(m, dict) and m.get("role") == "system"), None
            )
            if system_content:
                from app.routes.custom_llm import prewarm_openai_cache
                import asyncio
                asyncio.create_task(prewarm_openai_cache(system_content))

        # Handle call-ended event
        if call_id and event == "call-ended":
            call = db.query(CallRecord).filter(CallRecord.id == call_id).first()
            if call:
                call.status = "completed"
                call.duration = call_obj.get("duration", 0)
                call.recording_url = call_obj.get("recordingUrl")
                call.ended_at = datetime.utcnow()
                db.commit()
                logger.info(f"[VAPI WEBHOOK] Updated call record for {call_id}")
        
        # Handle tool-call event
        if event in ["tool-call", "tool-calls"]:
            tool_calls = message.get("toolCalls", [])
            results = []
            for tc in tool_calls:
                func = tc.get("function", {})
                if func.get("name") == "save_order":
                    args = func.get("arguments", {})
                    # Vapi sometimes sends arguments as a JSON string
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    logger.info(f"[VAPI WEBHOOK] Tool Call 'save_order' with args: {args}")
                    
                    # Get phone from call object (real inbound caller ID), fallback to N/A
                    phone = (call_obj.get("customer") or {}).get("number") or \
                            (call_obj.get("customer") or {}).get("phoneNumber") or "N/A"
                    
                    try:
                        total_raw = args.get("total_price", 0)
                        if total_raw is None:
                            total_raw = 0
                        if isinstance(total_raw, str):
                            import re
                            # Replace comma with dot (European format)
                            total_raw = total_raw.replace(',', '.')
                            # Remove all non-numeric characters except dot
                            total_raw = re.sub(r'[^\d.]', '', total_raw)
                            # Handle empty string after cleaning
                            if not total_raw:
                                total_raw = 0
                        parsed_total = round(float(total_raw), 2)  # Preserve decimals e.g. 18.98
                    except (ValueError, TypeError):
                        parsed_total = 0.0
                        
                    # We no longer reject the order if parsed_total <= 0.0
                    # because it causes valid orders to be dropped if the AI fails to calculate the price.
                    if parsed_total <= 0.0:
                        logger.warning(f"[VAPI WEBHOOK] Saving order for {args.get('customer_name')} despite $0 price.")
                        
                    order_type_raw = args.get("order_type")
                    order_type = str(order_type_raw).upper() if order_type_raw else "PICKUP"
                    
                    delivery_address_raw = args.get("delivery_address")
                    delivery_address = str(delivery_address_raw).strip() if delivery_address_raw else ""
                    
                    base_name = args.get("customer_name")
                    base_name = str(base_name) if base_name else "Unknown"
                    
                    if order_type == "DELIVERY" and delivery_address:
                        display_name = f"{base_name} (LEVERING: {delivery_address})"
                    elif order_type == "PICKUP" or not delivery_address:
                        display_name = f"{base_name} (AFHENTNING)"
                    else:
                        display_name = base_name

                    order_items = args.get("order_items") or []
                    if not isinstance(order_items, list):
                        order_items = [order_items]

                    new_order = Order(
                        name=display_name,
                        phone=phone,
                        order=json.dumps(order_items),
                        total=parsed_total,
                        call_id=call_id
                    )
                    db.add(new_order)
                    db.commit()
                    logger.info(f"[VAPI WEBHOOK] Order saved successfully for {new_order.name}")

                    # ── SMS Notification ────────────────────────────
                    try:
                        import asyncio
                        from app.sms import send_order_sms

                        assistant_id_for_sms = call_obj.get("assistantId")
                        forwarding_number = None

                        if assistant_id_for_sms:
                            assistant_rec = db.query(Assistant).filter(
                                Assistant.id == assistant_id_for_sms
                            ).first()
                            if assistant_rec and assistant_rec.forwarding_number:
                                forwarding_number = assistant_rec.forwarding_number

                        if forwarding_number:
                            sms_order_data = {
                                "customer_name": new_order.name,
                                "phone": new_order.phone,
                                "items": args.get("order_items", []),
                                "total": float(new_order.total),
                            }
                            asyncio.create_task(
                                send_order_sms(forwarding_number, sms_order_data)
                            )
                            logger.info(f"[VAPI WEBHOOK] SMS task dispatched to {forwarding_number}")
                        else:
                            logger.info("[VAPI WEBHOOK] No forwarding_number set — SMS skipped")
                    except Exception as sms_err:
                        logger.warning(f"[VAPI WEBHOOK] SMS dispatch error (non-fatal): {sms_err}")
                    # ── End SMS Notification ────────────────────────

                    results.append({
                        "toolCallId": tc.get("id"),
                        "result": "Order captured and saved to database successfully."
                    })
            return {"results": results}

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"[VAPI WEBHOOK] ERROR: {str(e)}", exc_info=True)
        return {"error": str(e)}, 500


@router.get("/api/calls")
async def get_calls(assistant_id: str = None, user=Depends(get_current_user)):
    """
    Fetch calls directly from Vapi API so ALL calls (inbound + outbound) are visible.
    """
    headers = {"Authorization": f"Bearer {VAPI_API_KEY}"}
    params = {"limit": 100}
    if assistant_id:
        params["assistantId"] = assistant_id

#    async with httpx.AsyncClient(timeout=20) as client:
    client = http_client.get_vapi_client()
    res = await client.get(f"{VAPI_BASE}/call", headers=headers, params=params)

    if res.status_code != 200:
        raise HTTPException(res.status_code, f"Error fetching calls from Vapi: {res.text}")

    raw = res.json()
    # Vapi returns a list or {results: [...]}
    if isinstance(raw, list):
        calls_raw = raw
    else:
        calls_raw = raw.get("results", raw.get("calls", []))

    calls = []
    for c in calls_raw:
        # Skip calls with 0 duration (failed/unknown)
        # ── Timestamps & duration ───────────────────────────────────
        artifact = c.get("artifact") or {}
        duration = artifact.get("recordingDuration") or c.get("duration", 0)
        
        started = c.get("startedAt") or c.get("createdAt") or ""
        ended = c.get("endedAt") or ""

        if not duration and started and ended:
            try:
                s = datetime.fromisoformat(started.replace("Z", "+00:00"))
                e = datetime.fromisoformat(ended.replace("Z", "+00:00"))
                duration = int((e - s).total_seconds())
            except Exception:
                duration = 0
        
        # Still 0 duration? Skip it as per user request
        if duration:
            try:
                duration = int(round(float(duration)))
            except:
                duration = 0
                
        if not duration or duration <= 0:
            continue

        customer = c.get("customer") or {}
        caller_number = customer.get("number", "")
        if not caller_number:
            var_customer = (c.get("variableValues") or {}).get("customer") or {}
            caller_number = var_customer.get("number", "")
        if not caller_number:
            pn = c.get("phoneNumber") or {}
            if isinstance(pn, dict):
                caller_number = pn.get("number", "Unknown")
            else:
                caller_number = "Unknown"

        # ── Recording & Transcript ──────────────────────────────────
        artifact = c.get("artifact") or {}
        recording_url = artifact.get("recordingUrl") or c.get("recordingUrl") or None
        transcript = artifact.get("transcript") or ""

        call_type = c.get("type", "")
        type_label = "📞 Inbound" if "inbound" in call_type.lower() else "📤 Outbound"

        calls.append({
            "id": c.get("id", ""),
            "assistant_id": c.get("assistantId", ""),
            "phone_number": caller_number,
            "started_at": started,
            "ended_at": ended,
            "status": c.get("status", "unknown"),
            "duration": duration,
            "recording_url": recording_url,
            "transcript": transcript,
            "type": type_label,
            "cost": c.get("cost"),
        })

    return {"calls": calls, "total": len(calls)}
