import json
import copy
import logging
import httpx
import re
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from app.config import OPENAI_API_KEY, VAPI_SECRET
from app.phonetics import apply_phonemes
from app import http_client

router = APIRouter()
logger = logging.getLogger(__name__)

_FUZZY_MAP = {
    # Coke Zero variants (must be defined first/longer to match before coke/zero)
    "coke zero": "coca-cola zero",
    "coca cola zero": "coca-cola zero",
    "coca-cola zero": "coca-cola zero",
    "cola zero": "coca-cola zero",

    # General
    "skin": "skinke", "skidt": "skinke",
    "mark rita": "margherita", "margit": "margherita",
    "champagne": "champignon", "sjampanje": "champignon",
    "løb": "løg", "løv": "løg",
    "pepper": "pepperoni", "peppe": "pepperoni", "peperoni": "pepperoni",
    "coke": "coca-cola", "cock": "coca-cola",
    "shawarma": "kebab", "shaw": "kebab", "charma": "kebab", "kabab": "kebab",
    "vend": "vand", "van": "vand", "band": "vand",
    "øksekød": "oksekød", "oksekøb": "oksekød",
    "killing": "kylling",

    # Sauces & Dressings
    "bernaise": "bearnaise", "bearnæse": "bearnaise", "bearnes": "bearnaise",
    "krem fresh": "creme fraiche", "krem fraiche": "creme fraiche", "fraisj": "creme fraiche",
    "ayoli": "aioli", "a oli": "aioli", "ali": "aioli",
    "remolade": "remoulade", "remullade": "remoulade",
    "mayo": "mayonnaise", "majonæse": "mayonnaise",

    # Italian ingredients
    "mozarella": "mozzarella", "mosa rela": "mozzarella", "mozza": "mozzarella",
    "mascarponi": "mascarpone", "masca pone": "mascarpone",
    "gorgon": "gorgonzola", "gorgon zola": "gorgonzola", "gorgotsola": "gorgonzola",
    "ruko la": "rucola", "rugola": "rucola", "rakola": "rucola",
    "bresola": "bresaola", "brisola": "bresaola", "breasola": "bresaola",
    "pastrame": "pastrami", "pastramy": "pastrami",
    "parmesan": "parmesan", "parmasan": "parmesan", "parmanost": "parmesan",
    "paramasanost": "parmesan", "parmasanost": "parmesan",
    "gran baragi": "gran biraghi", "gran biragi": "gran biraghi",
    "pesto": "basilikumspesto",
    "penn": "penne", "pene": "penne", "penner": "penne",
    "spageti": "spaghetti", "spagheti": "spaghetti",
    "prosjutto": "prosciutto", "proshutto": "prosciutto", "proscuitto": "prosciutto",
    "fokatja": "focaccia", "fokacja": "focaccia", "foccacia": "focaccia",
    "kaltzone": "calzone", "kalzone": "calzone",
    "kvatro": "quattro stagioni", "quattro": "quattro stagioni",
    "fungi": "funghi", "funghi": "funghi", "svampe-pizza": "funghi",

    # Spanish / Mexican
    "choriso": "chorizo", "cherizo": "chorizo", "tjoreso": "chorizo",
    "jalapino": "jalapeños", "jala penios": "jalapeños", "halapeno": "jalapeños",
    "avocado": "avokado", "avokato": "avokado", "avocato": "avokado",
    "gwakamole": "guacamole", "guaka": "guacamole", "wakamole": "guacamole",

    # Burger Palace specific
    "delux": "de luxe", "de lux": "de luxe", "de lyks": "de luxe",
    "vegi": "veggie", "veggie": "veggie", "vege": "veggie",
    "bæjkon": "bacon", "beikon": "bacon",
    "sjeddar": "cheddar", "chedar": "cheddar", "cedar": "cheddar",
    "kålslå": "coleslaw", "cole slaw": "coleslaw", "kolslå": "coleslaw",
    "sesar": "caesar", "sisar": "caesar", "keesar": "caesar",
    "krutoner": "croutoner", "croutons": "croutoner",
    "ånjon": "onion rings", "onion": "onion rings", "løgringe": "onion rings",
    "shake": "milkshake", "milksjæjk": "milkshake",
    "siro": "cola zero", "zero": "cola zero", "diet cola": "cola zero",
    "sprit": "sprite", "spræjt": "sprite", "spreit": "sprite",
    "masjrum": "mushroom", "mushroom": "mushroom", "svampe": "mushroom",
    "fokatsjabolle": "focacciabolle", "focacciabolle": "focacciabolle",
    "tjilimajo": "chilimayo", "chili mayo": "chilimayo", "chilisauce": "chilimayo",
    "sændvitsj": "sandwich", "sandvich": "sandwich", "sandvitj": "sandwich",

    # Pops Pizza specific
    "snackbox": "pops snackboks", "snack box": "pops snackboks", "snackbocks": "pops snackboks",
    "hot wings": "hotwings", "hotvings": "hotwings", "hotwing": "hotwings",
    "ice berg": "icebergsalat", "iseberg": "icebergsalat",
    "nugget": "chicken nuggets", "nagets": "chicken nuggets", "naget": "chicken nuggets",
    "fish fillet": "fiskefilet", "fiskefil": "fiskefilet",
    "falafel": "falafel", "falafler": "falafel", "falafl": "falafel",

    # Pommes frites
    "pomfritter": "pommes frites", "pommes": "pommes frites", "fritter": "pommes frites",
    "pomfrit": "pommes frites", "french fries": "pommes frites", "frites": "pommes frites",

    # General non-menu
    "lasagne": "lasagne", "lasagna": "lasagne",
    "dürüm": "dürüm", "kebabmenu": "dürüm",
}

_SORTED_KEYS = sorted(_FUZZY_MAP.keys(), key=len, reverse=True)
_MASTER_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _SORTED_KEYS) + r")\b",
    re.IGNORECASE
)

def preprocess_user_message(text: str) -> str:
    """Cleans common spelling/transcription errors in Danish speech before sending to LLM."""
    if not text:
        return text
    
    def _replacer(match):
        matched_str = match.group(0).lower()
        return _FUZZY_MAP.get(matched_str, match.group(0))
        
    return _MASTER_RE.sub(_replacer, text)

# Characters at which we consider a word complete and safe to flush.
# Flushing only at these boundaries guarantees apply_phonemes always
# receives complete words — compound phoneme entries like "chicken nuggets"
# or "creme fraiche" must never be split mid-phrase.
_BOUNDARY_CHARS = set(" .,?!;\n\r-:")

_MULTI_WORD_FIRST_PARTS = {"pommes"}

async def prewarm_openai_cache(system_prompt_content: str):
    """Fire a minimal background OpenAI request to warm the system prompt cache."""
    try:
        client = http_client.get_openai_client()
        await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "max_tokens": 1,
                "messages": [
                    {"role": "system", "content": system_prompt_content},
                    {"role": "user", "content": "hej"}
                ],
            },
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        )
        logger.info("[CACHE PRE-WARM] Successfully fired OpenAI pre-warming task.")
    except Exception as e:
        logger.warning(f"[CACHE PRE-WARM] Pre-warming failed (best-effort only): {e}")


async def stream_openai_response(payload: dict):
    valid_keys = {
        "messages", "model", "frequency_penalty", "logit_bias", "logprobs",
        "top_logprobs", "max_tokens", "n", "presence_penalty", "response_format",
        "seed", "stop", "stream", "stream_options", "temperature", "top_p",
        "tools", "tool_choice", "parallel_tool_calls", "user"
    }
    openai_payload = {k: v for k, v in payload.items() if k in valid_keys}
    openai_payload["stream"] = True

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    buffer = ""
    client = http_client.get_openai_client()
    first_flush_done = False

    try:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json=openai_payload,
            headers=headers,
            # Override read timeout for streaming — can run for the full call duration
            timeout=httpx.Timeout(connect=10.0, read=620.0, write=10.0, pool=10.0),
        ) as response:

            # ── Non-200 from OpenAI (rate limit, auth error, etc.) ────────────
            if response.status_code != 200:
                body = await response.aread()
                logger.error(
                    "OpenAI returned %s: %s",
                    response.status_code,
                    body.decode(errors="replace")[:500],
                )
                error_payload = {
                    "error": {
                        "message": f"OpenAI error {response.status_code}",
                        "type": "upstream_error",
                        "code": response.status_code,
                    }
                }
                yield f"data: {json.dumps(error_payload)}\n\n"
                yield "data: [DONE]\n\n"
                return

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue

                data_str = line[6:]  # strip "data: "

                if data_str == "[DONE]":
                    # Flush any remaining buffer before closing
                    if buffer:
                        processed = apply_phonemes(buffer)
                        yield f"data: {json.dumps({'choices': [{'delta': {'content': processed}, 'index': 0, 'finish_reason': None}]})}\n\n"
                        buffer = ""
                    yield "data: [DONE]\n\n"
                    break

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = chunk.get("choices", [])
                if not choices:
                    yield f"{line}\n\n"
                    continue

                delta = choices[0].get("delta", {})

                # ── Tool call chunks — flush text buffer first, pass through ──
                if "tool_calls" in delta:
                    if buffer:
                        processed = apply_phonemes(buffer)
                        yield f"data: {json.dumps({'choices': [{'delta': {'content': processed}, 'index': choices[0].get('index', 0), 'finish_reason': None}]})}\n\n"
                        buffer = ""
                    yield f"{line}\n\n"
                    continue

                # ── Text content ──────────────────────────────────────────────
                if "content" in delta and delta["content"] is not None:
                    buffer += delta["content"]

                    last_word = buffer.rstrip().split()[-1].lower() if buffer.rstrip().split() else ""
                    holding_for_compound = last_word in _MULTI_WORD_FIRST_PARTS

                    if not holding_for_compound:
                        # First flush: use FIRST boundary to minimize TTFT
                        # Subsequent flushes: use LAST boundary to ensure full phrases
                        if not first_flush_done:
                            boundary_idx = next(
                                (i for i, c in enumerate(buffer) if c in _BOUNDARY_CHARS), -1
                            )
                        else:
                            boundary_idx = next(
                                (i for i in range(len(buffer) - 1, -1, -1) if buffer[i] in _BOUNDARY_CHARS), -1
                            )

                        if boundary_idx != -1:
                            text_to_flush = buffer[:boundary_idx + 1]
                            buffer = buffer[boundary_idx + 1:]
                            processed = apply_phonemes(text_to_flush)
                            first_flush_done = True
                            new_chunk = copy.deepcopy(chunk)
                            new_chunk["choices"][0]["delta"]["content"] = processed
                            yield f"data: {json.dumps(new_chunk)}\n\n"
                        else:
                            # No boundary yet — emit empty metadata chunk to keep the stream alive
                            empty = copy.deepcopy(chunk)
                            empty["choices"][0]["delta"]["content"] = ""
                            yield f"data: {json.dumps(empty)}\n\n"
                    else:
                        # Holding for compound — emit empty metadata chunk
                        empty = copy.deepcopy(chunk)
                        empty["choices"][0]["delta"]["content"] = ""
                        yield f"data: {json.dumps(empty)}\n\n"

                else:
                    # finish_reason chunk — flush remaining buffer first
                    if buffer and choices[0].get("finish_reason"):
                        processed = apply_phonemes(buffer)
                        flush_chunk = copy.deepcopy(chunk)
                        flush_chunk["choices"][0]["delta"]["content"] = processed
                        flush_chunk["choices"][0]["finish_reason"] = None
                        yield f"data: {json.dumps(flush_chunk)}\n\n"
                        buffer = ""

                        finish_chunk = copy.deepcopy(chunk)
                        finish_chunk["choices"][0]["delta"] = {}
                        yield f"data: {json.dumps(finish_chunk)}\n\n"
                    else:
                        yield f"{line}\n\n"

    except httpx.ReadTimeout:
        logger.error("OpenAI stream read timed out")
        yield f"data: {json.dumps({'error': {'message': 'OpenAI stream timed out', 'type': 'timeout'}})}\n\n"
        yield "data: [DONE]\n\n"

    except httpx.ConnectError as exc:
        logger.error("OpenAI connection error: %s", exc)
        yield f"data: {json.dumps({'error': {'message': 'Could not connect to OpenAI', 'type': 'connection_error'}})}\n\n"
        yield "data: [DONE]\n\n"


@router.post("/api/chat/completions")
async def chat_completions(request: Request):
    secret = request.headers.get("x-vapi-secret")
    if secret != VAPI_SECRET:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    payload = await request.json()

    # Pre-process user message transcriptions to correct common STT spelling errors before LLM receives them
    if "messages" in payload and isinstance(payload["messages"], list):
        for msg in payload["messages"]:
            if isinstance(msg, dict) and msg.get("role") == "user" and "content" in msg:
                msg["content"] = preprocess_user_message(msg["content"])

        # Trim conversation history to the last 40 messages (20 user+assistant turns) to protect prompt cache bounds
        messages = payload["messages"]
        system_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
        convo_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]
        MAX_CONVO_MESSAGES = 40
        if len(convo_msgs) > MAX_CONVO_MESSAGES:
            convo_msgs = convo_msgs[-MAX_CONVO_MESSAGES:]
        payload["messages"] = system_msgs + convo_msgs

    return StreamingResponse(
        stream_openai_response(payload),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",  # Disables buffering in Nginx
            "Cache-Control": "no-cache, no-store, must-revalidate",  # Disables caching and CDN buffering
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )
