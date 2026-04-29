import asyncio
import requests as _requests
import httpx
import logging
from .config import VAPI_API_KEY, VAPI_BASE
from .file_utils import get_mime_type

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
        async with httpx.AsyncClient(timeout=10) as client:
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
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{VAPI_BASE}/tool", json=payload, headers=vapi_headers())
    if response.status_code not in (200, 201):
        raise Exception(f"Tool creation failed: {response.text}")
    return response.json()["id"]

async def attach_tool_to_assistant(assistant_id: str, tool_id: str, current_model: dict):
    existing_tool_ids = current_model.get("toolIds", [])
    if tool_id not in existing_tool_ids:
        existing_tool_ids.append(tool_id)
    patch_payload = {
        "model": {
            "provider": current_model.get("provider", "openai"),
            "model": current_model.get("model", "gpt-4o-mini"),
            "messages": current_model.get("messages", []),
            "toolIds": existing_tool_ids
        }
    }
    async with httpx.AsyncClient(timeout=20) as client:
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
    
    # 1. Check if tool already exists
    try:
        async with httpx.AsyncClient(timeout=20) as client:
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

                        # If we have a BACKEND_URL or need language update, patch the tool
                        if BACKEND_URL or should_update:
                            server_url = f"{BACKEND_URL}/api/webhook/call"
                            existing_server = t.get("server", {}) or {}
                            
                            patch_payload = {}
                            if existing_server.get("url") != server_url:
                                patch_payload["server"] = {"url": server_url}
                            
                            if should_update:
                                if language == "da":
                                    patch_payload["messages"] = [
                                        {"type": "request-start", "content": "Gemmer dine ordreoplysninger..."},
                                        {"type": "request-complete", "content": "Ordre gemt med succes."},
                                        {"type": "request-failed", "content": "Beklager, jeg kunne ikke gemme ordren. Prøv venligst igen."}
                                    ]
                                else:
                                    patch_payload["messages"] = [
                                        {"type": "request-start", "content": "Saving your order details..."},
                                        {"type": "request-complete", "content": "Order details saved successfully."},
                                        {"type": "request-failed", "content": "Sorry, I couldn't save the order details. Please try again."}
                                    ]
                            
                            if patch_payload:
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
            {"type": "request-start", "content": "Gemmer dine ordreoplysninger..."},
            {"type": "request-complete", "content": "Ordre gemt med succes."},
            {"type": "request-failed", "content": "Beklager, jeg kunne ikke gemme ordren. Prøv venligst igen."}
        ]

    payload = {
        "type": "function",
        "messages": messages,
        "function": {
            "name": tool_name,
            "description": "Saves the confirmed pizza order. Use ONLY after customer confirms final price. Read prices from # MENU DATA.",

            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string", "description": "The customer's name."},
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
                    "total_price": {"type": "number", "description": "The final total (sum of all items). MUST NOT BE 0. Read prices from prompt!"}
                },
                "required": ["customer_name", "order_items", "total_price"]
            }
        }
    }

    # Add server URL if providing reliability for voice calls
    if BACKEND_URL:
        payload["server"] = {"url": f"{BACKEND_URL}/api/webhook/call"}

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{VAPI_BASE}/tool", json=payload, headers=vapi_headers())
    if response.status_code not in (200, 201):
        raise Exception(f"Order tool creation failed: {response.text}")
    return response.json()["id"]