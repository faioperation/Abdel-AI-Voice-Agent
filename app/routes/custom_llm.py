import json
import copy
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from app.config import OPENAI_API_KEY, VAPI_SECRET
from app.phonetics import apply_phonemes

router = APIRouter()

async def stream_openai_response(payload: dict):
    # Filter payload to only include valid OpenAI parameters
    valid_keys = {
        "messages", "model", "frequency_penalty", "logit_bias", "logprobs", 
        "top_logprobs", "max_tokens", "n", "presence_penalty", "response_format", 
        "seed", "stop", "stream", "stream_options", "temperature", "top_p", 
        "tools", "tool_choice", "parallel_tool_calls", "user"
    }
    openai_payload = {k: v for k, v in payload.items() if k in valid_keys}
    
    # Ensure stream is true for our proxy
    openai_payload["stream"] = True
    
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    buffer = ""
    # We flush buffer on these boundary characters to ensure we evaluate complete words
    boundary_chars = set(" .,?!;\n\r-:")

    async with httpx.AsyncClient() as client:
        # Send the proxied request to OpenAI
        async with client.stream(
            "POST", 
            "https://api.openai.com/v1/chat/completions",
            json=openai_payload,
            headers=headers,
            timeout=60.0
        ) as response:
            
            # OpenAI SSE streams look like: data: {"id":...}\n\n
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                
                data_str = line[6:] # Strip "data: "
                
                if data_str == "[DONE]":
                    # Stream complete, flush any remaining buffer
                    if buffer:
                        processed = apply_phonemes(buffer)
                        chunk_json = {
                            "choices": [
                                {
                                    "delta": {"content": processed},
                                    "index": 0,
                                    "finish_reason": None
                                }
                            ]
                        }
                        yield f"data: {json.dumps(chunk_json)}\n\n"
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
                
                # If there are tool calls, flush text buffer first, then pass through
                if "tool_calls" in delta:
                    if buffer:
                        processed = apply_phonemes(buffer)
                        text_chunk = {
                            "choices": [
                                {
                                    "delta": {"content": processed},
                                    "index": choices[0].get("index", 0),
                                    "finish_reason": None
                                }
                            ]
                        }
                        yield f"data: {json.dumps(text_chunk)}\n\n"
                        buffer = ""
                    # Yield the tool_call chunk untouched
                    yield f"{line}\n\n"
                    continue

                if "content" in delta and delta["content"] is not None:
                    content = delta["content"]
                    buffer += content
                    
                    # Find the last boundary character to flush up to
                    last_boundary_idx = -1
                    for i in range(len(buffer) - 1, -1, -1):
                        if buffer[i] in boundary_chars:
                            last_boundary_idx = i
                            break
                    
                    if last_boundary_idx != -1:
                        # Extract everything up to the boundary (inclusive)
                        text_to_process = buffer[:last_boundary_idx + 1]
                        buffer = buffer[last_boundary_idx + 1:]
                        
                        processed = apply_phonemes(text_to_process)
                        
                        # Yield the processed text in the chunk
                        new_chunk = copy.deepcopy(chunk)
                        new_chunk["choices"][0]["delta"]["content"] = processed
                        yield f"data: {json.dumps(new_chunk)}\n\n"
                    else:
                        # Yield an empty chunk to preserve stream metadata (like role)
                        empty_chunk = copy.deepcopy(chunk)
                        empty_chunk["choices"][0]["delta"]["content"] = ""
                        yield f"data: {json.dumps(empty_chunk)}\n\n"
                    
                else:
                    # End of text chunk or just metadata chunk
                    if buffer and choices[0].get("finish_reason"):
                        processed = apply_phonemes(buffer)
                        
                        # First, yield the remaining text without finish_reason
                        new_chunk = copy.deepcopy(chunk)
                        new_chunk["choices"][0]["delta"]["content"] = processed
                        new_chunk["choices"][0]["finish_reason"] = None
                        yield f"data: {json.dumps(new_chunk)}\n\n"
                        buffer = ""
                        
                        # Then, yield the finish_reason chunk
                        new_chunk_finish = copy.deepcopy(chunk)
                        new_chunk_finish["choices"][0]["delta"] = {}
                        yield f"data: {json.dumps(new_chunk_finish)}\n\n"
                    else:
                        yield f"{line}\n\n"

@router.post("/api/chat/completions")
async def chat_completions(request: Request):
    secret = request.headers.get("x-vapi-secret")
    if secret != VAPI_SECRET:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    payload = await request.json()
    return StreamingResponse(stream_openai_response(payload), media_type="text/event-stream")
