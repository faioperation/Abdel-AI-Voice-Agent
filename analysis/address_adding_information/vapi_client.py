import asyncio
import requests as _requests
import httpx
import logging
from .config import VAPI_API_KEY, VAPI_BASE
from .file_utils import get_mime_type
from app import http_client

logger = logging.getLogger(__name__)

def vapi_headers():
    return {"Authorization": f"Bearer {VAPI_API_KEY}", "Content-Type": "application/json"}

async def upload_file_to_vapi(content: bytes, filename: str) -> str:
    """
    Upload a file to Vapi using requests in a thread pool with retry logic.
    """
    mime_type = get_mime_type(filename)

    def _sync_upload():
        files = {"file": (filename, content, mime_type)}
        # Try up to 3 times
        max_retries = 3
        last_err = None
        
        for attempt in range(max_retries):
            try:
                response = _requests.post(
                    f"{VAPI_BASE}/file",
                    headers={"Authorization": f"Bearer {VAPI_API_KEY}"},
                    files=files,
                    timeout=60
                )
                return response.status_code, response.text
            except Exception as e:
                last_err = e
                logger.warning(f"Upload attempt {attempt+1} failed: {e}")
                if attempt < max_retries - 1:
                    import time
                    time.sleep(1) # Wait a bit before retry
                continue
        raise last_err or Exception("Upload failed after multiple attempts")

    status_code, text = await asyncio.to_thread(_sync_upload)
    if status_code not in (200, 201):
        raise Exception(f"Upload failed ({status_code}): {text}")
    import json as _json
    return _json.loads(text)["id"]


async def delete_file_from_vapi(file_id: str):
    """Delete a file from Vapi storage."""
    try:
#        async with httpx.AsyncClient(timeout=10) as client:
        client = http_client.get_vapi_client()
        response = await client.delete(
            f"{VAPI_BASE}/file/{file_id}",
            headers={"Authorization": f"Bearer {VAPI_API_KEY}"}
        )
        if response.status_code not in (200, 204):
            logger.warning(f"File delete from Vapi returned {response.status_code}: {response.text}")
    except Exception as e:
        logger.warning(f"Error deleting file {file_id} from Vapi: {e}")

async def create_query_tool(file_ids: list, tool_name: str = "knowledge-search") -> str:
    payload = {
        "type": "query",
        "function": {"name": tool_name},
        "knowledgeBases": [{
            "provider": "google",
            "name": "pizzeria-kb",
            "description": "Restaurant menu, pricing, offers and Pizzeria Network information",
            "fileIds": file_ids
        }]
    }
#    async with httpx.AsyncClient(timeout=20) as client:
    client = http_client.get_vapi_client()
    response = await client.post(f"{VAPI_BASE}/tool", json=payload, headers=vapi_headers())
    if response.status_code not in (200, 201):
        raise Exception(f"Tool creation failed: {response.text}")
    return response.json()["id"]

async def attach_tool_to_assistant(assistant_id: str, tool_id: str, current_model: dict):
    if "toolIds" not in current_model or current_model["toolIds"] is None:
        current_model["toolIds"] = []
    existing_tool_ids = current_model["toolIds"]
    if tool_id not in existing_tool_ids:
        existing_tool_ids.append(tool_id)
    patch_payload = {
        "model": {
            "provider": current_model.get("provider", "openai"),
            "model": current_model.get("model", "gpt-4o"),
            "messages": current_model.get("messages", []),
            "toolIds": existing_tool_ids,
            "temperature": current_model.get("temperature", 0.3)
        }
    }
    
    # Preserve url and headers if they were intentionally set by the user
    if "url" in current_model:
        patch_payload["model"]["url"] = current_model["url"]
    if "headers" in current_model:
        patch_payload["model"]["headers"] = current_model["headers"]
#    async with httpx.AsyncClient(timeout=20) as client:
    client = http_client.get_vapi_client()
    response = await client.patch(
        f"{VAPI_BASE}/assistant/{assistant_id}",
        json=patch_payload,
        headers=vapi_headers()
    )
    if response.status_code not in (200, 201):
        raise Exception(f"Attach failed: {response.text}")

async def create_order_tool(tool_name: str = "save_order", language: str = "en") -> str:
    """
    Ensures a single 'save_order' tool exists in the Vapi account.
    If it exists, reuses it. Includes server URL for better reliability.
    """
    from .config import BACKEND_URL
    
    function_payload = {
        "name": tool_name,
        "description": "Saves the confirmed pizza order. Use ONLY after customer confirms final price. Read prices from # MENU DATA.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "The customer's name."},
                "order_type": {"type": "string", "enum": ["pickup", "delivery"], "description": "Whether the order is for pickup or delivery."},
                "delivery_address": {"type": "string", "description": "The delivery address including zip code if it's a delivery. Leave this empty if pickup."},
                "order_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "size": {"type": "string"},
                            "quantity": {"type": "number"}
                        }
                    }
                },
                "total_price": {"type": "number", "description": "The final total price of the order. You MUST calculate this accurately by adding the exact price of each item from the MENUDATA. NEVER set this to 0."}
            },
            "required": ["customer_name", "order_type", "order_items", "total_price"]
        }
    }

    # 1. Check if tool already exists
    try:
#        async with httpx.AsyncClient(timeout=20) as client:
        client = http_client.get_vapi_client()
        resp = await client.get(f"{VAPI_BASE}/tool", headers=vapi_headers())
        if resp.status_code == 200:
            tools = resp.json()
            for t in tools:
                if t.get("function", {}).get("name") == tool_name:
                    tool_id = t["id"]
                    
                    # Determine if we need to update messages for language
                    current_messages = t.get("messages", [])
                    msg_type = "request-start"
                    is_danish = False
                    for m in current_messages:
                        if m.get("type") == msg_type and "Gemmer" in m.get("content", ""):
                            is_danish = True
                    
                    should_update = False
                    if language == "da" and not is_danish: should_update = True
                    if language == "en" and is_danish: should_update = True

                    server_url = f"{BACKEND_URL}/api/webhook/call" if BACKEND_URL else None
                    existing_server = t.get("server", {}) or {}
                    
                    patch_payload = {
                        "function": function_payload
                    }
                    
                    if server_url and existing_server.get("url") != server_url:
                        patch_payload["server"] = {"url": server_url}
                    
                    if should_update:
                        if language == "da":
                            patch_payload["messages"] = [
                                {"type": "request-start", "content": "Et øjeblik, jeg sender lige din ordre afsted..."},
                                {"type": "request-complete", "content": "Sådan! Din bestilling er nu modtaget og vi går i gang."},
                                {"type": "request-failed", "content": "Beklager, der skete en lille fejl med at gemme ordren. Skal vi prøve igen?"}
                            ]
                        else:
                            patch_payload["messages"] = [
                                {"type": "request-start", "content": "Saving your order details..."},
                                {"type": "request-complete", "content": "Order details saved successfully."},
                                {"type": "request-failed", "content": "Sorry, I couldn't save the order details. Please try again."}
                            ]
                    
                    logger.info(f"Updating tool {tool_name} with: {patch_payload}")
                    await client.patch(f"{VAPI_BASE}/tool/{tool_id}", json=patch_payload, headers=vapi_headers())
                    
                    logger.info(f"Reusing tool: {tool_name} ({tool_id})")
                    return tool_id
    except Exception as e:
        logger.warning(f"Error checking/updating existing tools: {e}")

    # 2. Create new tool if not found
    messages = [
        {"type": "request-start", "content": "Saving your order details..."},
        {"type": "request-complete", "content": "Order details saved successfully."},
        {"type": "request-failed", "content": "Sorry, I couldn't save the order details. Please try again."}
    ]
    if language == "da":
        messages = [
            {"type": "request-start", "content": "Et øjeblik, jeg sender lige din ordre afsted..."},
            {"type": "request-complete", "content": "Sådan! Din bestilling er nu modtaget og vi går i gang."},
            {"type": "request-failed", "content": "Beklager, der skete en lille fejl med at gemme ordren. Skal vi prøve igen?"}
        ]

    payload = {
        "type": "function",
        "messages": messages,
        "function": function_payload
    }

    if BACKEND_URL:
        payload["server"] = {"url": f"{BACKEND_URL}/api/webhook/call"}

#    async with httpx.AsyncClient(timeout=20) as client:
    client = http_client.get_vapi_client()
    response = await client.post(f"{VAPI_BASE}/tool", json=payload, headers=vapi_headers())
    if response.status_code not in (200, 201):
        raise Exception(f"Order tool creation failed: {response.text}")
    return response.json()["id"]

async def create_address_verification_tool(tool_name: str = "verify_delivery_address") -> str:
    """
    Ensures a single 'verify_delivery_address' tool exists in the Vapi account.
    If it exists, updates it. If not, creates it.

    The tool now accepts shop config (lat, lng, radius, postal codes) as arguments
    so the AI reads them from each assistant's MENUDATA and passes them here —
    keeping this endpoint fully stateless and multi-tenant.
    """
    from .config import AI_BACKEND_URL

    function_payload = {
        "name": tool_name,
        "description": (
            "Validates the customer's delivery address using Google Maps and checks "
            "whether it falls within the shop's delivery zone. Call this tool ONLY for "
            "delivery orders, after collecting both the street address and 4-digit postal code. "
            "Read shop_lat, shop_lng, delivery_radius_km, and allowed_postal_codes from the "
            "MENUDATA in your system prompt and pass them as arguments every time you call this tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": (
                        "The customer's street name and house number ONLY "
                        "(e.g. 'Søborg Hovedgade 12'). Do NOT include the postal code or city name."
                    )
                },
                "postal_code": {
                    "type": "string",
                    "description": (
                        "The 4-digit postal code as digits only (e.g. '2860'). "
                        "Convert spoken Danish numbers like 'otteogtyve tres' to '2860' before calling. "
                        "Never pass words or the city name."
                    )
                },
                "shop_lat": {
                    "type": "number",
                    "description": (
                        "The shop's latitude. Read this from the MENUDATA field 'shop_lat' "
                        "in your system prompt (e.g. 55.7295). Never send 0."
                    )
                },
                "shop_lng": {
                    "type": "number",
                    "description": (
                        "The shop's longitude. Read this from the MENUDATA field 'shop_lng' "
                        "in your system prompt (e.g. 12.3755). Never send 0."
                    )
                },
                "delivery_radius_km": {
                    "type": "number",
                    "description": (
                        "The delivery radius in kilometres. Read this from the MENUDATA field "
                        "'delivery_radius_km' in your system prompt (e.g. 7). Never send 0."
                    )
                },
                "allowed_postal_codes": {
                    "type": "string",
                    "description": (
                        "Comma-separated list of postal codes the shop delivers to, "
                        "from the MENUDATA field 'allowed_postal_codes' (e.g. '2860,2800,2830'). "
                        "Send an empty string if not specified in MENUDATA."
                    )
                }
            },
            "required": ["address", "postal_code", "shop_lat", "shop_lng", "delivery_radius_km"]
        }
    }

    clean_backend_url = AI_BACKEND_URL.rstrip('/') if AI_BACKEND_URL else "https://25.fireai.agency"
    server_url = f"{clean_backend_url}/api/verify-address"

    # 1. Check if tool already exists — update it if so
    try:
        client = http_client.get_vapi_client()
        resp = await client.get(f"{VAPI_BASE}/tool", headers=vapi_headers())
        if resp.status_code == 200:
            tools = resp.json()
            for t in tools:
                if t.get("function", {}).get("name") == tool_name:
                    tool_id = t["id"]
                    patch_payload = {
                        "function": function_payload,
                        "server": {"url": server_url}
                    }
                    await client.patch(f"{VAPI_BASE}/tool/{tool_id}", json=patch_payload, headers=vapi_headers())
                    logger.info(f"Updated address verification tool schema: {tool_id}")
                    return tool_id
    except Exception as e:
        logger.warning(f"Error checking/updating address verification tool: {e}")

    # 2. Create new tool if not found
    payload = {
        "type": "function",
        "function": function_payload,
        "server": {"url": server_url}
    }

    client = http_client.get_vapi_client()
    response = await client.post(f"{VAPI_BASE}/tool", json=payload, headers=vapi_headers())
    if response.status_code not in (200, 201):
        raise Exception(f"Address verification tool creation failed: {response.text}")
    logger.info(f"Created new address verification tool")
    return response.json()["id"]

