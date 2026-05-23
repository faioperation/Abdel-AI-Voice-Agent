import json
import copy
import logging
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from app.config import OPENAI_API_KEY, VAPI_SECRET
from app.phonetics import apply_phonemes
from app import http_client

router = APIRouter()
logger = logging.getLogger(__name__)

# Characters at which we consider a word complete and safe to flush.
# Flushing only at these boundaries guarantees apply_phonemes always
# receives complete words — compound phoneme entries like "chicken nuggets"
# or "creme fraiche" must never be split mid-phrase.
_BOUNDARY_CHARS = set(" .,?!;\n\r-:")


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

                    # Find the last boundary to flush up to — this guarantees
                    # multi-word phoneme entries are never split across chunks.
                    last_boundary_idx = -1
                    for i in range(len(buffer) - 1, -1, -1):
                        if buffer[i] in _BOUNDARY_CHARS:
                            last_boundary_idx = i
                            break

                    if last_boundary_idx != -1:
                        text_to_flush = buffer[:last_boundary_idx + 1]
                        buffer = buffer[last_boundary_idx + 1:]
                        processed = apply_phonemes(text_to_flush)
                        new_chunk = copy.deepcopy(chunk)
                        new_chunk["choices"][0]["delta"]["content"] = processed
                        yield f"data: {json.dumps(new_chunk)}\n\n"
                    else:
                        # No boundary yet — emit empty metadata chunk to keep
                        # the stream alive (preserves role/id fields Vapi needs)
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
    return StreamingResponse(stream_openai_response(payload), media_type="text/event-stream")
