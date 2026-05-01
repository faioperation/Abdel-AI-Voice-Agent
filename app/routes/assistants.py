import json
import logging
from datetime import datetime
from fastapi import APIRouter, File, UploadFile, Form, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
import httpx
import os
from app.database import get_db, Assistant, KnowledgeBase
from app.auth import get_current_user
from app.config import PIZZERIA_SYSTEM_PROMPT, VAPI_BASE, BACKEND_URL
from app.vapi_client import upload_file_to_vapi, create_query_tool, attach_tool_to_assistant, vapi_headers, delete_file_from_vapi, create_order_tool
from app.file_utils import extract_text_from_bytes

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/api/create-assistant")
async def create_assistant(
    assistant_name: str = Form(...),
    model: str = Form("gemini-2.0-flash"),
    voice_id: str = Form("Hp07ONf6C5qlCKOeB4oo"),
    system_prompt: str = Form(None),
    language: str = Form("da"),
    files: list[UploadFile] = File(None),

    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    vapi_file_ids = []
    file_names = []
    extracted_texts = []
    if files:
        for file in files:
            content = await file.read()
            file_id = await upload_file_to_vapi(content, file.filename)
            vapi_file_ids.append(file_id)
            file_names.append(file.filename)
            text = extract_text_from_bytes(content, file.filename)
            extracted_texts.append(text)

    # Load the correct base prompt based on language
    prompt_file = "system_prompt_da.txt" if language == "da" else "system_prompt_en.txt"
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
            placeholder = "[The menu data will be extracted from your KB file and placed here.]"
            if language == "da":
                menu_injection = (
                    "# MENUDATA (STRENG KILDE)\n"
                    "VIGTIGT: Tilbyd KUN varer og priser fra listen nedenfor. Hvis en kunde spørger om noget, "
                    "der IKKE er på listen, skal du høfligt informere om, at det ikke er tilgængeligt. "
                    "Gæt ALDRIG eller brug viden udefra.\n\n"
                    + combined_menu_text
                )
            else:
                menu_injection = (
                    "# MENU DATA (STRICT SOURCE)\n"
                    "CRITICAL: ONLY offer items and prices listed below. If a customer asks for something NOT in this list, "
                    "politely inform them that it is not available. DO NOT guess or use outside knowledge.\n\n"
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


    # Determine TTS provider — Flash v2.5 for 11labs (fastest), native for Vapi voices
    vapi_voices = ["Elliot", "Savannah", "Rohan", "Emma", "Clara", "Nico", "Kai", "Sagar"]
    provider = "vapi" if voice_id in vapi_voices else "11labs"

    # --- KEYWORD BOOSTING FOR ACCURACY ---
    keywords = []
    if language == "da":
        keywords.extend(["skinke", "løg", "ananas", "champignon", "hvidløg", "dressing", "sodavand", "levering", "afhentning", "størrelse", "pizza", "pepperoni", "margherita", "oksekød", "kylling", "bacon"])


    
    if extracted_texts:
        import re
        for text in extracted_texts:
            # Include Danish characters æ, ø, å
            found = re.findall(r'[a-zA-ZæøåÆØÅ]+', text)
            keywords.extend([k.lower() for k in found if len(k) > 3])
    
    # Final cleanup: lowercase, alpha only, unique, limit to 50
    unique_keywords = sorted(list(set(keywords)))
    unique_keywords = [k for k in unique_keywords if k.isalpha()][:50]
    print(f"CLEANED KEYWORDS: {unique_keywords}")




    # Build voice config — Multilingual v2 for Native Danish, Flash v2.5 for English speed
    voice_config = {"provider": provider, "voiceId": voice_id, "speed": 1.1, "stability": 0.5, "similarityBoost": 0.8}
    if provider == "11labs":
        voice_config["model"] = "eleven_multilingual_v2" if language == "da" else "eleven_flash_v2_5"


    # Determine LLM provider
    groq_models = ["llama-3.3-70b-versatile", "llama-3.1-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]
    
    if any(model.startswith("gemini-") for model in [model]):
        llm_provider = "google"
    elif model in groq_models:
        llm_provider = "groq"
    else:
        llm_provider = "openai"



    assistant_payload = {
        "name": assistant_name,
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-2",
            "language": language,
            "keywords": unique_keywords,
            "smartFormat": True
        },
        "model": {
            "provider": llm_provider,
            "model": model,
            "messages": [{"role": "system", "content": used_prompt}],
            "temperature": 0.4 
        },
        "voice": voice_config,
        "startSpeakingPlan": {
            "waitSeconds": 0.4, 
            "smartEndpointingEnabled": True
        }, 
        
        "silenceTimeoutSeconds": 30,

        "firstMessage": "Velkommen til Pizzeria Network! Hvad kan jeg hjælpe dig med i dag?" if language == "da" else "Welcome to Pizzeria Network! How can I help you today?",
        "endCallMessage": "Tak for dit opkald, have en god dag!" if language == "da" else "Thank you for calling, have a great day!",
        "recordingEnabled": True,
        "maxDurationSeconds": 600,
    }



    if BACKEND_URL:
        assistant_payload["serverUrl"] = f"{BACKEND_URL}/api/webhook/call"

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
        order_tool_id = await create_order_tool(language=language)
        await attach_tool_to_assistant(assistant_id, order_tool_id, current_model)
    except Exception as e:
        logger.warning(f"Tool error: {e}")

    new_assistant = Assistant(
        id=assistant_id,
        name=assistant_name,
        model=model,
        voice_id=voice_id,
        system_prompt=used_prompt,
        language=language,
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
    # We always want Constantin Birkedal to be available as he is the requested default
    constantin = {"id": "Hp07ONf6C5qlCKOeB4oo", "name": "Constantin Birkedal", "provider": "11labs"}
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{VAPI_BASE}/voice", headers=vapi_headers())
        voices = []
        if resp.status_code == 200:
            voices = resp.json()
        
        # If the response is empty or failed, use a curated fallback with native options
        if not voices:
            voices = [
                {"id": "jsCqWAovK2LkecY7zXl4", "name": "Freja (Native Danish)", "provider": "11labs"},
                {"id": "CJVigY5qzO86Hvf0ASMj", "name": "Erik (Native Danish)", "provider": "11labs"},
                {"id": "IKne3meq5aSn9XLyUdCD", "name": "Charlie (English)", "provider": "11labs"},
                {"id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel (English)", "provider": "11labs"},
                {"id": "Elliot", "name": "Elliot (Vapi)", "provider": "vapi"}
            ]
            
        # Ensure Constantin Birkedal is present and at the top
        # Check if he's already in the list (by ID)
        if not any(v.get('id') == constantin['id'] or v.get('voiceId') == constantin['id'] for v in voices):
            voices.insert(0, constantin)
            
        return voices
    except Exception as e:
        logger.error(f"Error fetching Vapi voices: {e}")
        return [constantin]

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
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    assistant = db.query(Assistant).filter(Assistant.id == assistant_id).first()
    if not assistant:
        raise HTTPException(404)

    new_file_ids = []
    new_file_names = []
    new_extracted = []
    for file in files:
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
            p_file = "system_prompt_da.txt" if assistant.language == "da" else "system_prompt_en.txt"
            try:
                with open(p_file, "r", encoding="utf-8") as f:
                    new_base = f.read()
            except:
                new_base = PIZZERIA_SYSTEM_PROMPT

            placeholder = "[The menu data will be extracted from your KB file and placed here.]"
            if assistant.language == "da":
                menu_injection = "# MENUDATA (STRENG KILDE)\n" + combined_text
            else:
                menu_injection = "# MENU DATA (STRICT SOURCE)\n" + combined_text
                
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
            
            patch_payload = {
                "model": {
                    "provider": current_model.get("provider", "google"),
                    "model": current_model.get("model", "gemini-2.0-flash"),
                    "messages": updated_messages,
                    "toolIds": toolIds
                }
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
        patch_payload["model"] = {
            "provider": current_model.get("provider", "openai"),
            "model": current_model.get("model", "gpt-4o-mini"),
            "messages": updated_messages,
            "toolIds": current_model.get("toolIds", [])
        }
        assistant.system_prompt = data.system_prompt

    if data.model is not None:
        if "model" not in patch_payload:
            patch_payload["model"] = {
                "provider": current_model.get("provider", "openai"),
                "model": data.model,
                "messages": current_model.get("messages", []),
                "toolIds": current_model.get("toolIds", [])
            }
        else:
            patch_payload["model"]["model"] = data.model
        assistant.model = data.model

    if data.voice_id is not None:
        vapi_voices = ["Elliot", "Savannah", "Rohan", "Emma", "Clara", "Nico", "Kai", "Sagar"]
        provider = "vapi" if data.voice_id in vapi_voices else "11labs"
        voice_patch = {"provider": provider, "voiceId": data.voice_id, "speed": 1.1, "stability": 0.5, "similarityBoost": 0.8}
        if provider == "11labs":
            target_lang = data.language if data.language is not None else assistant.language
            voice_patch["model"] = "eleven_multilingual_v2" if target_lang == "da" else "eleven_flash_v2_5"
        patch_payload["voice"] = voice_patch
        assistant.voice_id = data.voice_id

    if data.language is not None:
        assistant.language = data.language
        # Load the new correct prompt for this language
        new_prompt_file = "system_prompt_da.txt" if data.language == "da" else "system_prompt_en.txt"
        try:
            with open(new_prompt_file, "r", encoding="utf-8") as f:
                new_base_prompt = f.read()
            # If there's menu data in DB, re-inject it
            kb_list = db.query(KnowledgeBase).filter(KnowledgeBase.assistant_id == assistant_id).all()
            if kb_list:
                combined_menu = "\n\n".join([k.extracted_text for k in kb_list if k.extracted_text])
                placeholder = "[The menu data will be extracted from your KB file and placed here.]"
                if placeholder in new_base_prompt:
                    new_base_prompt = new_base_prompt.replace(placeholder, combined_menu)
                else:
                    new_base_prompt = new_base_prompt + "\n\n# MENU DATA\n" + combined_menu
            
            assistant.system_prompt = new_base_prompt
            # Update patch payload
            if "model" not in patch_payload:
                patch_payload["model"] = current_model.copy()
            
            messages = patch_payload["model"].get("messages", [])
            updated_messages = [m for m in messages if m.get("role") != "system"]
            updated_messages.insert(0, {"role": "system", "content": new_base_prompt})
            patch_payload["model"]["messages"] = updated_messages
            
            # Update transcriber language
            patch_payload["transcriber"] = vapi_assistant.get("transcriber", {})
            patch_payload["transcriber"]["language"] = data.language
            
            # Update first message
            patch_payload["firstMessage"] = "Velkommen til Pizzeria Network! Hvad kan jeg hjælpe dig med?" if data.language == "da" else "Welcome to Pizzeria Network! How can I help you?"
            
        except Exception as e:
            logger.error(f"Error switching prompt during update: {e}")


    if patch_payload:
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


            for va in vapi_assistants:
                assistant_id  = va.get("id")
                current_model = va.get("model", {})
                
                # Determine language from DB or default to 'en'
                db_a = db.query(Assistant).filter(Assistant.id == assistant_id).first()
                va_lang = db_a.language if db_a else "en"
                extracted_texts = kb_map.get(assistant_id, [])

                # Ensure order tool is fresh and correct for this language
                order_tool_id = await create_order_tool(language=va_lang)

                # Load correct prompt
                p_file = "system_prompt_da.txt" if va_lang == "da" else "system_prompt_en.txt"
                try:
                    with open(p_file, "r", encoding="utf-8") as f:
                        final_prompt = f.read()
                except:
                    final_prompt = PIZZERIA_SYSTEM_PROMPT

                if extracted_texts:
                    combined_menu_text = "\n\n".join(extracted_texts)
                    placeholder = "[The menu data will be extracted from your KB file and placed here.]"
                    if va_lang == "da":
                        menu_header = "# MENUDATA (STRENG KILDE)\n"
                    else:
                        menu_header = "# MENU DATA (STRICT SOURCE)\n"
                    
                    menu_injection = menu_header + combined_menu_text
                    
                    if placeholder in final_prompt:
                        final_prompt = final_prompt.replace(placeholder, menu_injection)
                    else:
                        final_prompt = final_prompt + "\n\n" + menu_injection

                # Rebuild messages
                messages = [m for m in current_model.get("messages", []) if m.get("role") != "system"]
                messages.insert(0, {"role": "system", "content": final_prompt})

                # Generate keywords for this assistant
                current_keywords = ["pizza", "pepperoni", "margherita", "oksekød", "kylling", "bacon"]
                if va_lang == "da":
                    current_keywords.extend(["skinke", "løg", "ananas", "champignon", "hvidløg", "dressing", "sodavand", "levering", "afhentning", "størrelse"])
                
                if extracted_texts:
                    import re
                    for text in extracted_texts:
                        found = re.findall(r'[a-zA-ZæøåÆØÅ]+', text)
                        current_keywords.extend([k.lower() for k in found if len(k) > 3])
                
                unique_kw = sorted(list(set(current_keywords)))
                unique_kw = [k for k in unique_kw if k.isalpha()][:50]

                patch_payload = {
                    "model": {
                        "provider": current_model.get("provider", "google"),
                        "model": current_model.get("model", "gemini-2.0-flash"),
                        "messages": [{"role": "system", "content": final_prompt}],
                        "toolIds": list(set(current_model.get("toolIds", []) + [order_tool_id]))
                    },
                    "transcriber": {
                        "provider": "deepgram",
                        "model":    "nova-2",
                        "language": va_lang,
                        "keywords": unique_kw,
                        "smartFormat": True
                    },
                    "firstMessage": "Velkommen til Pizzeria Network! Hvad kan jeg hjælpe dig med?" if va_lang == "da" else "Welcome to Pizzeria Network! How can I help you?",
                    "voice": {
                        "speed": 1.1,
                        "stability": 0.5,
                        "similarityBoost": 0.8,
                        "model": "eleven_multilingual_v2" if va_lang == "da" else "eleven_flash_v2_5"
                    }
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