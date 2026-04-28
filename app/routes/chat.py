import json
import httpx
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session
from app.database import get_db, Assistant, KnowledgeBase, ConversationHistory, Order
from app.auth import get_current_user
from app.config import PIZZERIA_SYSTEM_PROMPT, OPENAI_API_KEY

router = APIRouter()

# ──────────────────────────────────────────────
# Tool schemas available to the chat agent
# ──────────────────────────────────────────────
KNOWLEDGE_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "knowledge-search",
        "description": (
            "Search the pizza menu Knowledge Base to get EXACT prices, item names, "
            "sizes, and availability. ALWAYS call this before quoting any price. "
            "Never guess a price — always search first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The item or question to search for in the menu."
                }
            },
            "required": ["query"]
        }
    }
}

SAVE_ORDER_TOOL = {
    "type": "function",
    "function": {
        "name": "save_order",
        "description": (
            "Save the customer's confirmed pizza order to the database. "
            "ONLY call this AFTER: (1) you have the customer's NAME, "
            "(2) you have looked up ALL prices from the Knowledge Base, "
            "(3) you have read back the full order summary with total, AND "
            "(4) the customer has explicitly said YES / confirmed. "
            "DO NOT call this prematurely."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "The customer's name."
                },
                "order_items": {
                    "type": "array",
                    "description": "List of ordered items.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name":     {"type": "string"},
                            "size":     {"type": "string"},
                            "quantity": {"type": "number"}
                        }
                    }
                },
                "total_price": {
                    "type": "number",
                    "description": "The final total calculated from Knowledge Base prices."
                }
            },
            "required": ["customer_name", "order_items", "total_price"]
        }
    }
}


def _search_kb(query: str, kb_list) -> str:
    """
    Search KB text. If the total KB is small, return the whole thing 
    to prevent the AI from missing context or confusing prices.
    """
    combined = "\n\n".join(
        k.extracted_text for k in kb_list if k.extracted_text
    )
    if not combined:
        return "No menu data found. Please check with staff."

    # If the menu is short (under 8000 chars, easily fits in context), just return it all
    if len(combined) < 8000:
        return combined

    query_terms = [t for t in query.lower().split() if len(t) > 2]
    lines = combined.split("\n")

    hit_indices = []
    for i, line in enumerate(lines):
        line_lower = line.lower()
        if any(term in line_lower for term in query_terms):
            hit_indices.append(i)

    if not hit_indices:
        return combined[:1200]

    context_lines = set()
    for idx in hit_indices:
        for j in range(max(0, idx - 5), min(len(lines), idx + 6)):
            context_lines.add(j)

    result = "\n".join(lines[i] for i in sorted(context_lines))
    return result[:4000]


@router.post("/api/chat-with-agent")
async def chat_with_agent(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user)
):
    try:
        body        = await request.json()
        assistant_id = body.get("assistant_id")
        message      = body.get("message")
        session_id   = body.get("session_id") or f"fallback_{assistant_id}"

        if not assistant_id or not message:
            return {"response": "Error: Missing assistant_id or message."}

        assistant = db.query(Assistant).filter(Assistant.id == assistant_id).first()
        if not assistant:
            return {"response": "Error: Agent not found."}

        # ── Cleanup stale history (> 30 min) ──────────────────────────
        thirty_mins_ago = datetime.utcnow() - timedelta(minutes=30)
        db.query(ConversationHistory).filter(
            ConversationHistory.session_id == session_id,
            ConversationHistory.created_at < thirty_mins_ago
        ).delete()

        # ── Load session history ───────────────────────────────────────
        db_history = db.query(ConversationHistory).filter(
            ConversationHistory.session_id == session_id
        ).order_by(ConversationHistory.created_at.asc()).all()

        # ── Load KB files (for local knowledge-search tool) ───────────
        kb_list = db.query(KnowledgeBase).filter(
            KnowledgeBase.assistant_id == assistant_id
        ).all()

        # System prompt — DO NOT inject KB text here.
        # The AI must use the knowledge-search tool to find prices.
        system_content = assistant.system_prompt or PIZZERIA_SYSTEM_PROMPT
        messages = [{"role": "system", "content": system_content}]

        for h in db_history:
            messages.append({"role": h.role, "content": h.content})
        messages.append({"role": "user", "content": message})

        if not OPENAI_API_KEY:
            return {"response": "Error: OpenAI API Key missing."}

        # ── Tools available to the model ─────────────────────────────
        tools = [SAVE_ORDER_TOOL]
        if kb_list:
            tools.insert(0, KNOWLEDGE_SEARCH_TOOL)

        ai_message = ""
        MAX_TOOL_ROUNDS = 5

        for _ in range(MAX_TOOL_ROUNDS):
            payload = {
                "model": assistant.model or "gpt-4o-mini",
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0.5
            }

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "Content-Type": "application/json"
                    }
                )

            if resp.status_code != 200:
                print(f"[CHAT] OpenAI Error: {resp.text}")
                return {"response": f"OpenAI error: {resp.status_code}"}

            data   = resp.json()
            choice = data["choices"][0]
            ai_msg = choice["message"]
            ai_message = ai_msg.get("content") or ""
            tool_calls = ai_msg.get("tool_calls")

            if not tool_calls:
                # No more tool calls — final answer
                break

            # ── Process tool calls ────────────────────────────────────
            messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls})

            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                except Exception:
                    fn_args = {}

                if fn_name == "knowledge-search":
                    query  = fn_args.get("query", "")
                    result = _search_kb(query, kb_list)
                    print(f"[CHAT] knowledge-search query='{query}' → {result[:100]}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result
                    })

                elif fn_name == "save_order":
                    # Save the confirmed order to DB
                    new_order = Order(
                        name=fn_args.get("customer_name") or "Unknown",
                        phone="Chat Test",
                        order=json.dumps(fn_args.get("order_items") or []),
                        total=round(float(fn_args.get("total_price", 0)), 2),
                        call_id=f"chat_{session_id}"
                    )
                    db.add(new_order)
                    db.commit()
                    print(f"[CHAT] Order saved for '{new_order.name}' total=${new_order.total}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": "Order saved successfully."
                    })
                    # After saving, let model generate the final confirmation message
                else:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": "Unknown tool."
                    })

        # ── Persist interaction ───────────────────────────────────────
        db.add(ConversationHistory(session_id=session_id, role="user",      content=message))
        db.add(ConversationHistory(session_id=session_id, role="assistant", content=ai_message))
        db.commit()

        return {"response": ai_message}

    except Exception as e:
        import traceback
        print(f"[CHAT] CRITICAL ERROR: {e}")
        print(traceback.format_exc())
        return {"response": f"System Error: {str(e)}"}