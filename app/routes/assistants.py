import json
import logging
from datetime import datetime
from fastapi import APIRouter, File, UploadFile, Form, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List, Union
from sqlalchemy.orm import Session
import httpx
import os
from app.database import get_db, Assistant, KnowledgeBase
from app.auth import get_current_user
from app.config import PIZZERIA_SYSTEM_PROMPT, VAPI_BASE, BACKEND_URL, VAPI_SECRET
from app.vapi_client import upload_file_to_vapi, create_query_tool, attach_tool_to_assistant, vapi_headers, delete_file_from_vapi, create_order_tool
from app.file_utils import extract_text_from_bytes

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/api/create-assistant")
async def create_assistant(
    assistant_name: str = Form(...),
    welcome_message: str = Form("Velkommen til FoodVoice punktum A I! Hvad kan jeg hjælpe dig med i dag?"),
    file: Optional[UploadFile] = File(None),

    db: Session = Depends(get_db)
):
    vapi_file_ids = []
    file_names = []
    extracted_texts = []
    if file:
        content = await file.read()
        file_id = await upload_file_to_vapi(content, file.filename)
        vapi_file_ids.append(file_id)
        file_names.append(file.filename)
        text = extract_text_from_bytes(content, file.filename)
        extracted_texts.append(text)

    # Load the base prompt
    prompt_file = "system_prompt.txt"
    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            base_prompt = f.read()
    except Exception as e:
        logger.error(f"Error loading prompt file {prompt_file}: {e}")
        base_prompt = PIZZERIA_SYSTEM_PROMPT # Fallback


    # --- HYBRID KB LOGIC ---
    MAX_INJECTION_LENGTH = 10000
    use_query_tool = False
    
    if extracted_texts:
        combined_menu_text = "\n\n".join(extracted_texts)
        if len(combined_menu_text) <= MAX_INJECTION_LENGTH:
            # Small files: Inject directly into prompt (No RAG needed)
            placeholder = "[The menu data will be extracted from your KB file and placed here. If this section is empty, use 'knowledge-search' for all items.]"
            menu_injection = (
                "# MENUDATA (STRENG KILDE)\n"
                "VIGTIGT: Tilbyd KUN varer og priser fra listen nedenfor. Hvis en kunde spørger om noget, "
                "der IKKE er på listen, skal du høfligt informere om, at det ikke er tilgængeligt. "
                "Gæt ALDRIG eller brug viden udefra.\n\n"
                + combined_menu_text
            )

            if placeholder in base_prompt:
                used_prompt = base_prompt.replace(placeholder, menu_injection)
            else:
                used_prompt = base_prompt + "\n\n" + menu_injection
        else:
            # Large files: Do not inject. Rely solely on query_tool (RAG)
            use_query_tool = True
            used_prompt = base_prompt
    else:
        used_prompt = base_prompt


    transcriber_config = {
        "model": "default",
        "language": "da",
        "provider": "speechmatics",
        "fallbackPlan": {
            "transcribers": [
                {
                    "model": "nova-3-general",
                    "language": "da-DK",
                    "numerals": False,
                    "provider": "deepgram",
                    "confidenceThreshold": 0.4
                }
            ]
        }
    }

    voice_config = {
        "model": "sonic-3.5",
        "voiceId": "a466f9e2-28eb-4bb7-925c-8e8984950700",
        "provider": "cartesia",
        "language": "da"
    }

    model = "gpt-4o"
    llm_provider = "openai"

    if BACKEND_URL:
        clean_backend_url = BACKEND_URL.rstrip('/')
    else:
        clean_backend_url = "https://test6.fireai.agency" # Fallback to known backend

    assistant_payload = {
        "name": assistant_name,
        "transcriber": transcriber_config,
        "model": {
            "provider": "custom-llm",
            "model": model,
            "url": f"{clean_backend_url}/api/chat/completions",
            "messages": [{"role": "system", "content": used_prompt}],
            "temperature": 0.3,
            "headers": {
                "x-vapi-secret": VAPI_SECRET
            }
        },
        "voice": voice_config,
        "recordingEnabled": True,
        "firstMessage": welcome_message,
        "endCallMessage": "Tak for dit opkald, have en god dag!",
        "silenceTimeoutSeconds": 30,
        "maxDurationSeconds": 600,
        "backchannelingEnabled": False,
        "backgroundDenoisingEnabled": True,
        "startSpeakingPlan": {
            "waitSeconds": 0.1,
            "smartEndpointingEnabled": True,
            "smartEndpointingPlan": {
                "provider": "vapi"
            },
            "transcriptionEndpointingPlan": {
                "onNumberSeconds": 0.2,
                "onPunctuationSeconds": 0.1,
                "onNoPunctuationSeconds": 0.3
            }
        },
        "stopSpeakingPlan": {
            "numWords": 0,
            "voiceSeconds": 0.3,
            "backoffSeconds": 0.6
        }
    }



    if BACKEND_URL:
        clean_backend_url = BACKEND_URL.rstrip('/')
        assistant_payload["serverUrl"] = f"{clean_backend_url}/api/webhook/call"
        assistant_payload["server"] = {
            "url": f"{clean_backend_url}/api/webhook/call",
            "timeoutSeconds": 20
        }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{VAPI_BASE}/assistant", json=assistant_payload, headers=vapi_headers())

    if resp.status_code not in (200, 201):
        error_detail = resp.text
        print(f"VAPI ERROR ({resp.status_code}): {error_detail}")
        return {"success": False, "error": f"Vapi Error ({resp.status_code}): {error_detail}"}


    vapi_data = resp.json()
    assistant_id = vapi_data["id"]
    current_model = vapi_data.get("model", {})

    try:
        query_tool_id = None
        if vapi_file_ids and use_query_tool:
            query_tool_id = await create_query_tool(vapi_file_ids)
            await attach_tool_to_assistant(assistant_id, query_tool_id, current_model)

        # Always attach order tool
        order_tool_id = await create_order_tool(language="da")
        await attach_tool_to_assistant(assistant_id, order_tool_id, current_model)
    except Exception as e:
        logger.warning(f"Tool error: {e}")

    new_assistant = Assistant(
        id=assistant_id,
        name=assistant_name,
        model=model,
        voice_id="a466f9e2-28eb-4bb7-925c-8e8984950700",
        system_prompt=used_prompt,
        language="da",
        vapi_data=json.dumps(vapi_data),
        query_tool_id=query_tool_id,
        file_ids=json.dumps(vapi_file_ids)
    )
    db.add(new_assistant)
    for idx, fname in enumerate(file_names):
        kb_entry = KnowledgeBase(
            assistant_id=assistant_id,
            file_name=fname,
            vapi_file_id=vapi_file_ids[idx],
            extracted_text=extracted_texts[idx] if idx < len(extracted_texts) else ""
        )
        db.add(kb_entry)
    db.commit()
    return {"success": True, "assistant_id": assistant_id}

@router.get("/api/assistants")
def get_assistants(db: Session = Depends(get_db), user=Depends(get_current_user)):
    assistants = db.query(Assistant).order_by(Assistant.created_at.desc()).all()
    res = []
    for a in assistants:
        res.append({
            "id": a.id,
            "name": a.name,
            "model": a.model,
            "voice_id": a.voice_id,
            "language": a.language,
            "system_prompt": a.system_prompt,
            "created_at": str(a.created_at)
        })

    return {"assistants": res, "total": len(assistants)}


@router.get("/api/vapi-voices")
async def get_vapi_voices(user=Depends(get_current_user)):
    """Fetch voices from Vapi API (configured voices in the user's account)."""
    """Return the hardcoded Cartesia voice to ensure only the specified voice can be selected."""
    cartesia_voice = {
        "id": "a466f9e2-28eb-4bb7-925c-8e8984950700",
        "name": "Cartesia Sonic 3.5 (Danish)",
        "provider": "cartesia"
    }
    return [cartesia_voice]

@router.get("/api/assistant/{assistant_id}")
async def get_assistant_detail(assistant_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    assistant = db.query(Assistant).filter(Assistant.id == assistant_id).first()
    if not assistant:
        raise HTTPException(404, "Assistant not found")
    kb_list = db.query(KnowledgeBase).filter(KnowledgeBase.assistant_id == assistant_id).all()

    async with httpx.AsyncClient(timeout=10) as client:
        get_resp = await client.get(f"{VAPI_BASE}/assistant/{assistant_id}", headers=vapi_headers())
    
    if get_resp.status_code != 200:
        # Fallback if Vapi lookup fails
        pass
        
    return {
        "id": assistant.id,
        "name": assistant.name,
        "model": assistant.model,
        "voice_id": assistant.voice_id,
        "language": assistant.language,
        "system_prompt": assistant.system_prompt or PIZZERIA_SYSTEM_PROMPT,
        "created_at": str(assistant.created_at),
        "files": [{"name": k.file_name, "vapi_file_id": k.vapi_file_id} for k in kb_list]
    }


@router.get("/api/assistant/{assistant_id}/knowledge")
def get_knowledge(assistant_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    assistant = db.query(Assistant).filter(Assistant.id == assistant_id).first()
    if not assistant:
        raise HTTPException(404)
    kb_list = db.query(KnowledgeBase).filter(KnowledgeBase.assistant_id == assistant_id).all()
    return {
        "assistant_name": assistant.name,
        "files": [{"name": k.file_name, "vapi_file_id": k.vapi_file_id} for k in kb_list],
        "system_prompt": assistant.system_prompt or PIZZERIA_SYSTEM_PROMPT,
        "knowledge_text": "\n\n".join([k.extracted_text for k in kb_list if k.extracted_text])[:4000]
    }

@router.post("/api/assistant/{assistant_id}/add-files")
async def add_files_to_assistant(
    assistant_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    assistant = db.query(Assistant).filter(Assistant.id == assistant_id).first()
    if not assistant:
        raise HTTPException(404)

    new_file_ids = []
    new_file_names = []
    new_extracted = []
    
    content = await file.read()
    file_id = await upload_file_to_vapi(content, file.filename)
    new_file_ids.append(file_id)
    new_file_names.append(file.filename)
    new_extracted.append(extract_text_from_bytes(content, file.filename))

    existing_ids = json.loads(assistant.file_ids) if assistant.file_ids else []
    updated_ids = existing_ids + new_file_ids

    async with httpx.AsyncClient(timeout=30) as client:
        get_resp = await client.get(f"{VAPI_BASE}/assistant/{assistant_id}", headers=vapi_headers())
        current_model = get_resp.json().get("model", {}) if get_resp.status_code == 200 else {}

        MAX_INJECTION_LENGTH = 10000
        
        # We need to re-evaluate size
        all_texts = []
        # Get existing kb texts from db
        existing_kbs = db.query(KnowledgeBase).filter(KnowledgeBase.assistant_id == assistant_id).all()
        for k in existing_kbs:
            if k.extracted_text: all_texts.append(k.extracted_text)
        all_texts.extend(new_extracted)
        
        combined_text = "\n\n".join(all_texts)
        
        if len(combined_text) <= MAX_INJECTION_LENGTH:
            # Small files -> Inject into prompt, remove query_tool if exists
            p_file = "system_prompt.txt"
            try:
                with open(p_file, "r", encoding="utf-8") as f:
                    new_base = f.read()
            except:
                new_base = PIZZERIA_SYSTEM_PROMPT

            placeholder = "[The menu data will be extracted from your KB file and placed here. If this section is empty, use 'knowledge-search' for all items.]"
            menu_injection = "# MENUDATA (STRENG KILDE)\n" + combined_text
                
            if placeholder in new_base:
                new_prompt = new_base.replace(placeholder, menu_injection)
            else:
                new_prompt = new_base + "\n\n" + menu_injection
                
            # Update Prompt
            messages = current_model.get("messages", [])
            updated_messages = [m for m in messages if m.get("role") != "system"]
            updated_messages.insert(0, {"role": "system", "content": new_prompt})
            
            # Remove query tool if it exists
            toolIds = current_model.get("toolIds", [])
            if assistant.query_tool_id and assistant.query_tool_id in toolIds:
                toolIds.remove(assistant.query_tool_id)
                # also delete from vapi
                try: await client.delete(f"{VAPI_BASE}/tool/{assistant.query_tool_id}", headers=vapi_headers())
                except: pass
                assistant.query_tool_id = None
            
            clean_backend_url = BACKEND_URL.rstrip('/') if BACKEND_URL else "https://test6.fireai.agency"
            patch_payload = {
                "model": {
                    "provider": "custom-llm",
                    "model": "gpt-4o",
                    "url": f"{clean_backend_url}/api/chat/completions",
                    "messages": updated_messages,
                    "toolIds": toolIds,
                    "temperature": 0.3,
                    "headers": {
                        "x-vapi-secret": VAPI_SECRET
                    }
                }
            }
            if BACKEND_URL:
                patch_payload["serverUrl"] = f"{clean_backend_url}/api/webhook/call"
                patch_payload["server"] = {
                    "url": f"{clean_backend_url}/api/webhook/call",
                    "timeoutSeconds": 20
                }
            assistant.system_prompt = new_prompt
            await client.patch(f"{VAPI_BASE}/assistant/{assistant_id}", json=patch_payload, headers=vapi_headers())

        else:
            # Large files -> Use Query Tool, keep prompt clean
            if assistant.query_tool_id:
                patch_payload = {
                    "knowledgeBases": [{
                        "provider": "google",
                        "name": "pizzeria-kb",
                        "description": "Restaurant menu, pricing, offers and Pizzeria Network information",
                        "fileIds": updated_ids
                    }]
                }
                patch_resp = await client.patch(
                    f"{VAPI_BASE}/tool/{assistant.query_tool_id}",
                    json=patch_payload,
                    headers=vapi_headers()
                )
                if patch_resp.status_code not in (200, 201):
                    raise HTTPException(400, f"Tool update failed: {patch_resp.text}")
            else:
                new_tool_id = await create_query_tool(updated_ids)
                await attach_tool_to_assistant(assistant_id, new_tool_id, current_model)
                assistant.query_tool_id = new_tool_id

    assistant.file_ids = json.dumps(updated_ids)
    for idx, fname in enumerate(new_file_names):
        kb_entry = KnowledgeBase(
            assistant_id=assistant_id,
            file_name=fname,
            vapi_file_id=new_file_ids[idx],
            extracted_text=new_extracted[idx]
        )
        db.add(kb_entry)
    db.commit()
    return {"success": True, "added_files": new_file_names}

@router.get("/api/assistant/{assistant_id}/kb-files")
def get_kb_files(assistant_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    files = db.query(KnowledgeBase).filter(KnowledgeBase.assistant_id == assistant_id).all()
    return {"files": [{"name": f.file_name, "vapi_file_id": f.vapi_file_id} for f in files]}

class UpdateAssistant(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    voice_id: Optional[str] = None
    language: Optional[str] = None


@router.patch("/api/assistant/{assistant_id}")
async def update_assistant(assistant_id: str, data: UpdateAssistant, db: Session = Depends(get_db), user=Depends(get_current_user)):
    assistant = db.query(Assistant).filter(Assistant.id == assistant_id).first()
    if not assistant:
        raise HTTPException(404, "Assistant not found")

    # Fetch current Vapi state
    async with httpx.AsyncClient(timeout=20) as client:
        get_resp = await client.get(f"{VAPI_BASE}/assistant/{assistant_id}", headers=vapi_headers())
        if get_resp.status_code != 200:
            raise HTTPException(500, "Failed to fetch assistant from Vapi")
        vapi_assistant = get_resp.json()
        current_model = vapi_assistant.get("model", {})

    patch_payload = {}

    if data.name is not None:
        patch_payload["name"] = f"{data.name}"
        assistant.name = data.name

    if data.system_prompt is not None:
        messages = current_model.get("messages", [])
        updated_messages = [m for m in messages if m.get("role") != "system"]
        updated_messages.insert(0, {"role": "system", "content": data.system_prompt})
        clean_backend_url = BACKEND_URL.rstrip('/') if BACKEND_URL else "https://test6.fireai.agency"
        patch_payload["model"] = {
            "provider": "custom-llm",
            "model": "gpt-4o",
            "url": f"{clean_backend_url}/api/chat/completions",
            "messages": updated_messages,
            "toolIds": current_model.get("toolIds", []),
            "temperature": 0.3,
            "headers": {
                "x-vapi-secret": VAPI_SECRET
            }
        }
        assistant.system_prompt = data.system_prompt

    if data.model is not None:
        assistant.model = "gpt-4o"
        if "model" not in patch_payload:
            clean_backend_url = BACKEND_URL.rstrip('/') if BACKEND_URL else "https://test6.fireai.agency"
            patch_payload["model"] = {
                "provider": "custom-llm",
                "model": "gpt-4o",
                "url": f"{clean_backend_url}/api/chat/completions",
                "messages": current_model.get("messages", []),
                "toolIds": current_model.get("toolIds", []),
                "temperature": 0.3,
                "headers": {
                    "x-vapi-secret": VAPI_SECRET
                }
            }

    if data.voice_id is not None:
        patch_payload["voice"] = {
            "model": "sonic-3.5",
            "voiceId": "a466f9e2-28eb-4bb7-925c-8e8984950700",
            "provider": "cartesia",
            "language": "da"
        }
        assistant.voice_id = "a466f9e2-28eb-4bb7-925c-8e8984950700"

    if data.language is not None:
        assistant.language = "da"


    if patch_payload:
        if BACKEND_URL:
            clean_backend_url = BACKEND_URL.rstrip('/')
            patch_payload["serverUrl"] = f"{clean_backend_url}/api/webhook/call"
            patch_payload["server"] = {
                "url": f"{clean_backend_url}/api/webhook/call",
                "timeoutSeconds": 20
            }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.patch(f"{VAPI_BASE}/assistant/{assistant.id}", json=patch_payload, headers=vapi_headers())
        if resp.status_code not in (200, 201) and resp.status_code != 204:
            raise HTTPException(500, f"Failed updating Vapi: {resp.text}")

    db.commit()
    return {"success": True}

@router.delete("/api/assistant/{assistant_id}/kb-files/{file_id}")
async def delete_kb_file(assistant_id: str, file_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    assistant = db.query(Assistant).filter(Assistant.id == assistant_id).first()
    if not assistant:
        raise HTTPException(404, "Assistant not found")

    kb_file = db.query(KnowledgeBase).filter(KnowledgeBase.assistant_id == assistant_id, KnowledgeBase.vapi_file_id == file_id).first()
    if not kb_file:
        raise HTTPException(404, "KnowledgeBase file not found")

    # Delete file from Vapi storage
    await delete_file_from_vapi(file_id)

    # Update DB file_ids and query tool
    existing_ids = json.loads(assistant.file_ids) if assistant.file_ids else []
    if file_id in existing_ids:
        existing_ids.remove(file_id)
        assistant.file_ids = json.dumps(existing_ids)

        # Update Query Tool to remove file
        if assistant.query_tool_id:
            patch_payload = {
                "knowledgeBases": [{
                    "provider": "google",
                    "name": "pizzeria-kb",
                    "description": "Restaurant menu, pricing, offers and Pizzeria Network information",
                    "fileIds": existing_ids
                }]
            }
            async with httpx.AsyncClient(timeout=20) as client:
                await client.patch(
                    f"{VAPI_BASE}/tool/{assistant.query_tool_id}",
                    json=patch_payload,
                    headers=vapi_headers()
                )

    db.delete(kb_file)
    db.commit()
    return {"success": True}

@router.delete("/api/assistant/{assistant_id}")
async def delete_assistant(assistant_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    try:
        # Delete all KB files from Vapi first
        kb_files = db.query(KnowledgeBase).filter(KnowledgeBase.assistant_id == assistant_id).all()
        for kb_file in kb_files:
            if kb_file.vapi_file_id:
                await delete_file_from_vapi(kb_file.vapi_file_id)

        # Delete the assistant and its query tool from Vapi
        async with httpx.AsyncClient(timeout=20) as client:
            await client.delete(f"{VAPI_BASE}/assistant/{assistant_id}", headers=vapi_headers())
            assistant = db.query(Assistant).filter(Assistant.id == assistant_id).first()
            if assistant and assistant.query_tool_id:
                await client.delete(f"{VAPI_BASE}/tool/{assistant.query_tool_id}", headers=vapi_headers())
    except Exception as e:
        logger.warning(f"Error during Vapi deletion (continuing): {e}")

    db.query(KnowledgeBase).filter(KnowledgeBase.assistant_id == assistant_id).delete()
    db.query(Assistant).filter(Assistant.id == assistant_id).delete()
    db.commit()
    return {"success": True}

@router.get("/api/fix-vapi-tool")
async def fix_vapi_tool(db: Session = Depends(get_db)):
    """
    Force-updates the Vapi 'save_order' tool and attaches it to all assistants.
    """
    from app.database import init_db
    try:
        init_db()
        # 1. Try to delete existing tool
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(f"{VAPI_BASE}/tool", headers=vapi_headers())
                if resp.status_code == 200:
                    for t in resp.json():
                        if t.get("function", {}).get("name") == "save_order":
                            await client.delete(f"{VAPI_BASE}/tool/{t['id']}", headers=vapi_headers())
        except: pass
        
        # 2. Create fresh tool
        tool_id = await create_order_tool()
        
        # 3. Attach new tool to ALL assistants in the Vapi account
        updated_count = 0
        try:
            get_all_resp = await client.get(f"{VAPI_BASE}/assistant", headers=vapi_headers())
            if get_all_resp.status_code == 200:
                for vapi_assistant in get_all_resp.json():
                    assistant_id = vapi_assistant.get("id")
                    current_model = vapi_assistant.get("model", {})
                    try:
                        await attach_tool_to_assistant(assistant_id, tool_id, current_model)
                        updated_count += 1
                    except: pass
        except Exception as api_err:
            logger.warning(f"Failed to fetch all assistants from Vapi: {api_err}")

        return {
            "success": True, 
            "message": f"Tool Recreated and attached to {updated_count} Vapi assistants.", 
            "tool_id": tool_id
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/api/fix-all-assistants")
async def fix_all_assistants_prompt(db: Session = Depends(get_db)):
    """
    Push the latest merged bilingual system prompt to ALL Vapi assistants.
    Also updates the local DB. Call this once after any system_prompt.txt change.
    """
    updated = []
    failed  = []

    try:
        # Fetch all assistants from Vapi
        async with httpx.AsyncClient(timeout=30) as client:
            get_resp = await client.get(f"{VAPI_BASE}/assistant", headers=vapi_headers())
            if get_resp.status_code != 200:
                return {"success": False, "error": f"Failed to list Vapi assistants: {get_resp.text}"}

            vapi_assistants = get_resp.json()
            
            # Build kb_map from DB
            kb_list = db.query(KnowledgeBase).all()
            kb_map = {}
            for k in kb_list:
                if k.assistant_id not in kb_map:
                    kb_map[k.assistant_id] = []
                if k.extracted_text:
                    kb_map[k.assistant_id].append(k.extracted_text)

            # Cache tool creation/update to avoid hitting Vapi rate limits in the loop
            order_tool_cache = {}
            import asyncio

            for va in vapi_assistants:
                assistant_id  = va.get("id")
                current_model = va.get("model", {})
                
                # Determine language from DB or default to 'en'
                db_a = db.query(Assistant).filter(Assistant.id == assistant_id).first()
                va_lang = db_a.language if db_a else "en"
                extracted_texts = kb_map.get(assistant_id, [])

                # Ensure order tool is fresh and correct for this language
                if va_lang not in order_tool_cache:
                    order_tool_cache[va_lang] = await create_order_tool(language=va_lang)
                    # Small delay to respect Vapi's API rate limits
                    await asyncio.sleep(1)
                order_tool_id = order_tool_cache[va_lang]

                # Load correct prompt
                p_file = "system_prompt.txt"
                try:
                    with open(p_file, "r", encoding="utf-8") as f:
                        final_prompt = f.read()
                except:
                    final_prompt = PIZZERIA_SYSTEM_PROMPT

                if extracted_texts:
                    combined_menu_text = "\n\n".join(extracted_texts)
                    placeholder = "[The menu data will be extracted from your KB file and placed here. If this section is empty, use 'knowledge-search' for all items.]"
                    menu_header = "# MENUDATA (STRENG KILDE)\n"
                    
                    menu_injection = menu_header + combined_menu_text
                    
                    if placeholder in final_prompt:
                        final_prompt = final_prompt.replace(placeholder, menu_injection)
                    else:
                        final_prompt = final_prompt + "\n\n" + menu_injection

                # Rebuild messages
                messages = [m for m in current_model.get("messages", []) if m.get("role") != "system"]
                messages.insert(0, {"role": "system", "content": final_prompt})

                transcriber_config = {
                    "model": "default",
                    "language": "da",
                    "provider": "speechmatics",
                    "fallbackPlan": {
                        "transcribers": [
                            {
                                "model": "nova-3-general",
                                "language": "da-DK",
                                "numerals": False,
                                "provider": "deepgram",
                                "confidenceThreshold": 0.4
                            }
                        ]
                    }
                }

                current_voice = {
                    "model": "sonic-3.5",
                    "voiceId": "a466f9e2-28eb-4bb7-925c-8e8984950700",
                    "provider": "cartesia",
                    "language": "da"
                }

                clean_backend_url = BACKEND_URL.rstrip('/') if BACKEND_URL else "https://test6.fireai.agency"
                patch_payload = {
                    "model": {
                        "provider": "custom-llm",
                        "model": "gpt-4o",
                        "url": f"{clean_backend_url}/api/chat/completions",
                        "messages": [{"role": "system", "content": final_prompt}],
                        "toolIds": list(set(current_model.get("toolIds", []) + [order_tool_id])),
                        "temperature": 0.3,
                        "headers": {
                            "x-vapi-secret": VAPI_SECRET
                        }
                    },
                    "transcriber": transcriber_config,
                    "recordingEnabled": True,
                    "firstMessage": "Velkommen til FoodVoice punktum A I! Hvad kan jeg hjælpe dig med i dag?",
                    "endCallMessage": "Tak for dit opkald, have en god dag!",
                    "silenceTimeoutSeconds": 30,
                    "maxDurationSeconds": 600,
                    "backchannelingEnabled": False,
                    "backgroundDenoisingEnabled": True,
                    "startSpeakingPlan": {
                        "waitSeconds": 0.1,
                        "smartEndpointingEnabled": True,
                        "smartEndpointingPlan": {
                            "provider": "vapi"
                        },
                        "transcriptionEndpointingPlan": {
                            "onNumberSeconds": 0.2,
                            "onPunctuationSeconds": 0.1,
                            "onNoPunctuationSeconds": 0.3
                        }
                    },
                    "stopSpeakingPlan": {
                        "numWords": 0,
                        "voiceSeconds": 0.3,
                        "backoffSeconds": 0.6
                    },
                    "voice": current_voice
                }

                if BACKEND_URL:
                    patch_payload["serverUrl"] = f"{clean_backend_url}/api/webhook/call"
                    patch_payload["server"] = {
                        "url": f"{clean_backend_url}/api/webhook/call",
                        "timeoutSeconds": 20
                    }

                resp = await client.patch(
                    f"{VAPI_BASE}/assistant/{assistant_id}",
                    json=patch_payload,
                    headers=vapi_headers()
                )

                if resp.status_code in (200, 201):
                    updated.append(assistant_id)
                    # Also sync to local DB
                    db_a = db.query(Assistant).filter(Assistant.id == assistant_id).first()
                    if db_a:
                        db_a.system_prompt = final_prompt
                        db_a.language      = va_lang

                else:
                    failed.append({"id": assistant_id, "error": resp.text})
                    logger.warning(f"Failed to update assistant {assistant_id}: {resp.text}")

                # Introduce a small sleep to avoid hitting Vapi's API rate limits for assistant patch requests
                await asyncio.sleep(1)

        db.commit()
        return {
            "success":  True,
            "updated":  len(updated),
            "failed":   len(failed),
            "details":  {"updated_ids": updated, "failures": failed}
        }

    except Exception as e:
        logger.error(f"fix-all-assistants error: {e}")
        return {"success": False, "error": str(e)}
