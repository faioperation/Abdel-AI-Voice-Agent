# Implementation Plan — Fix `verify_delivery_address` Vapi tool

**Project:** FoodVoice.ai / TV Byens Pizza voice ordering backend
**Symptom:** Vapi tool `verify_delivery_address` returns **"No result returned"** on every delivery call.
**Status of prior analysis:** The earlier "STT corrupts the postal code" theory is incorrect and must not be implemented first. See "Root cause" below.

---

## 0. Root cause (read before doing anything)

The endpoint does not speak Vapi's tool-call protocol. Two concrete defects:

1. **Wrong response shape (this is the actual bug).** Vapi requires the webhook to return
   `{"results":[{"toolCallId": "<id from request>", "result": "<string>"}]}` with HTTP 200.
   `address.py` returns `{"deliverable": bool, "suggestion": str}`. There is no `results`
   array and no `toolCallId`, so Vapi can never match a result → "No result returned."

2. **Wrong request parsing.** Vapi POSTs `{"message": {"toolCalls": [{"id", "function": {"name","arguments"}}]}}`.
   The current `AddressVerificationRequest` model expects top-level `postal_code`/`address`,
   so those fields parse as empty strings and the real arguments (nested under
   `message.toolCalls[0].function.arguments`) are dropped.

### Evidence the postal-code/STT theory is wrong
- Call transcript, 00:42 — the assistant reads back *"to tusind otte hundrede og tres Søborg"* = **2860**.
  The model held the correct postal code. Nothing garbled reached the tool as a postal code.
- The failing screenshot shows **"No result returned"**, which is Vapi's format/parse failure —
  not a `deliverable:false` business response. A genuine "not deliverable" answer would have come
  back as a normal result and the model would have said so cleanly.
- `2860.txt` exists; `"O Tingvej"` normalizes to `otingvej`, which matches `Otingvej` in the data.
  Once the envelope is fixed, this exact call should succeed.

### Priority correction vs the HTML bug report
| HTML report says | Reality |
|---|---|
| Root cause = STT corrupts postal code | Root cause = response/request format mismatch with Vapi |
| Endpoint "returns valid response shape" | It does not — this is the bug |
| Fix 1 = postal-code normalization (High) | Useful hardening, but **secondary**. Does not fix "No result returned" |
| Fuzzy street matching "works once reached" | True — but it is never reached, and even when reached the result is discarded |

---

## Task 1 — [P0] Make `address.py` speak Vapi's protocol *(the fix)*

**File:** `app/address.py`

**Do:**
- Read the request as the raw Vapi envelope; extract `(toolCallId, arguments)` from
  `message.toolCalls` (fall back to `message.toolCallList`, then to a flat body for manual testing).
- `arguments` may arrive as a dict **or** a JSON string — handle both.
- Run the existing exact + fuzzy street matching unchanged.
- Return `{"results":[{"toolCallId": <id>, "result": <json-string>}]}` with **HTTP 200 always**.
  Put `{"deliverable":..., "suggestion":...}` inside `result` as a JSON string so the system
  prompt's REGEL 15 keeps working.
- Register the route on `""` (and `"/"`) so the full path is exactly `/api/verify-address`,
  matching `server_url` in `vapi_client.py` (removes the 307-redirect risk).

**Reference parsing snippet:**
```python
def _extract_tool_calls(body: dict):
    msg = body.get("message", {}) if isinstance(body, dict) else {}
    raw = msg.get("toolCalls") or msg.get("toolCallList") or []
    out = []
    for call in raw:
        cid = call.get("id") or call.get("toolCallId")
        fn = call.get("function", call)
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try: args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError: args = {}
        out.append((cid, args or {}))
    if not out and isinstance(body, dict) and ("postal_code" in body or "address" in body):
        out.append((body.get("toolCallId"), body))
    return out
```

**Return shape:**
```python
return JSONResponse(status_code=200, content={
    "results": [{
        "toolCallId": call_id,
        "result": json.dumps({"deliverable": ..., "suggestion": ...}, ensure_ascii=False),
    }]
})
```

**Acceptance criteria:**
- `curl` with a simulated Vapi envelope (see Task 7) returns a `results[0].result` string and 200.
- In the Vapi dashboard, a live delivery call to `2860` + `Otingvej 13` shows a green tool result, not "No result returned."
- The model speaks back the confirmed address instead of looping on pickup.

> A ready-made corrected version of this file already exists at `address.py` from the prior step —
> use it as the starting point and merge Task 2's additions into it.

---

## Task 2 — [P1] Defensive postal-code normalization

**File:** `app/address.py`

This does **not** fix the live bug (Task 1 does), but it hardens against genuinely misheard input
like `"2860 Søborg"` or a future digit-by-digit miss.

**Do:**
- At startup, scan `Address_data/` once and cache valid 4-digit codes into a `set` for O(1) checks.
- Add `resolve_postal_code(raw)` with ordered strategies:
  1. Strip non-digits; if 4 digits and in the valid set → use it.
  2. (Optional) Danish compound-word → digit map (`otteogtyve`→`28`, `tres`→`60`, …), then re-extract digits.
  3. `difflib.get_close_matches(digits, VALID_CODES, n=1, cutoff=0.75)` as last resort.
  4. If all fail → return a friendly "please repeat the postal code digit by digit" result.

```python
VALID_POSTAL_CODES: set[str] = set()
def _load_valid_postal_codes():
    if os.path.isdir(ADDRESS_DATA_DIR):
        for f in os.listdir(ADDRESS_DATA_DIR):
            code = f[:-4] if f.endswith(".txt") else ""
            if code.isdigit() and len(code) == 4:
                VALID_POSTAL_CODES.add(code)
_load_valid_postal_codes()
```

**Acceptance criteria:** unit calls with `"2860"`, `"2860 Søborg"`, `"28 60"` all resolve to `2860`.
Gibberish returns the retry prompt, not a crash.

> Scope note: TV Byens Pizza only delivers to `2860` per the menu data. The strategy-2 Danish
> word map is optional for a single-postcode client — strategy 1 + 3 cover it. Keep the map only
> if you reuse this backend across many postcodes.

---

## Task 3 — [P1] Tolerate STT-split street names

**File:** `app/address.py`

The live call mangled `Otingvej` → `"O Tingvej"`. Your `normalize_street()` already strips spaces,
so `"O Tingvej"` → `otingvej` matches. Just confirm the fuzzy branch compares the **normalized**
forms (it does) and consider lowering the threshold from `0.65` to `0.60` only if testing shows misses.

**Acceptance criteria:** `"O Tingvej 13"` and `"Otingvej 13"` both return `deliverable:true` with
suggestion `Otingvej 13`.

---

## Task 4 — [P2] Tool description + system prompt nudge

**Files:** `app/vapi_client.py`, `system_prompt.txt`

- In `create_address_verification_tool()`, set the `postal_code` description to:
  *"The 4-digit postal code as digits only (e.g. '2860'). Convert spoken Danish like 'otteogtyve tres' → '2860' before calling. Never pass words or the city name."*
- Under REGEL 15 in `system_prompt.txt`, append the Danish instruction:
  *"VIGTIGT: Postnummeret SKAL sendes som 4 cifre (f.eks. '2860'), ALDRIG som ord. Omdan 'otteogtyve tres' → '2860' FØR du kalder verify_delivery_address."*

These are belt-and-suspenders; GPT-4o already converted correctly in the recording, so treat as low priority.

---

## Task 5 — [P2] Structured logging

**File:** `app/address.py`

Log on every request: the raw arguments received, the resolved postal code, the extracted street,
and the match path taken (exact / fuzzy / none). Example:
`logger.info("[ADDRESS] args=%r postal=%r street=%r match=%s", args, postal, street, path)`

This turns any future STT edge case into a one-line log lookup instead of a call repro.

---

## Task 6 — [P0-adjacent] Audit `save_order` for the same defect

**Files:** whatever handles `/api/webhook/call` (not in the uploaded set — likely `chat.py` or `custom_llm.py`)

`save_order`'s `server.url` is `/api/webhook/call`. If that handler was written like the old
`address.py` (returning a bare object instead of `{"results":[{"toolCallId","result"}]}`), it has
the identical bug and orders may be silently failing too. Verify it returns the Vapi envelope.

**Acceptance criteria:** a simulated `save_order` tool-call payload returns `results[].toolCallId`
matching the request and HTTP 200.

---

## Task 7 — Verification harness (use before/after each task)

Simulate exactly what Vapi sends. Run against your deployed endpoint:

```bash
curl -i -X POST https://test24.fireai.agency/api/verify-address \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "type": "tool-calls",
      "toolCalls": [{
        "id": "call_test_123",
        "type": "function",
        "function": {
          "name": "verify_delivery_address",
          "arguments": "{\"postal_code\": \"2860\", \"address\": \"O Tingvej 13\"}"
        }
      }]
    }
  }'
```

**Pass =** HTTP 200 and body:
```json
{"results":[{"toolCallId":"call_test_123","result":"{\"deliverable\": true, \"suggestion\": \"Otingvej 13\"}"}]}
```

Repeat with `"arguments"` as a raw object (not a string) and with a wrong postcode `"9999"`
(expect `deliverable:false`, still 200, still a matching `toolCallId`).

---

## Definition of done
- [ ] Task 1 merged; Task 7 curl returns the `results`/`toolCallId` envelope with 200.
- [ ] Live Vapi delivery call to `2860` + `Otingvej 13` shows a successful tool result (no red error).
- [ ] Model confirms the address and proceeds to order instead of looping on pickup.
- [ ] Task 6 audit complete — `save_order` confirmed returning the correct envelope.
- [ ] Tasks 2–5 landed as hardening; logging visible in server output.

## Suggested commit order for the agent
1. Task 1 (envelope) — deploy, run Task 7, confirm live call works.
2. Task 6 (audit save_order) — same envelope check.
3. Task 2 + Task 3 (input hardening) + Task 5 (logging).
4. Task 4 (prompt/description nudges).
