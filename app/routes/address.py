<<<<<<< HEAD
import os
import json
import difflib
import re
import logging
from typing import Optional
from functools import lru_cache
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/verify-address", tags=["Address Verification"])
logger = logging.getLogger(__name__)

ADDRESS_DATA_DIR = "Address_data"

# ── Pre-load valid postal codes at module load (O(1) lookup) ──────────────────
VALID_POSTAL_CODES: set[str] = set()

def _load_valid_postal_codes():
    if os.path.isdir(ADDRESS_DATA_DIR):
        for f in os.listdir(ADDRESS_DATA_DIR):
            code = f[:-4] if f.endswith(".txt") else ""
            if code.isdigit() and len(code) == 4:
                VALID_POSTAL_CODES.add(code)

_load_valid_postal_codes()


# ── Vapi envelope parsing ─────────────────────────────────────────────────────

def _extract_tool_call(body: dict):
    """
    Extract (toolCallId, arguments_dict) from Vapi's tool-call envelope.
    Handles both string and dict arguments.
    Falls back to flat body for manual curl testing.
    """
    msg = body.get("message", {}) if isinstance(body, dict) else {}
    raw = msg.get("toolCalls") or msg.get("toolCallList") or []
    for call in raw:
        cid = call.get("id") or call.get("toolCallId")
        fn = call.get("function", call)
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        return (cid, args or {})
    # Fallback: flat body for manual/curl testing
    if isinstance(body, dict) and ("postal_code" in body or "address" in body):
        return (body.get("toolCallId"), body)
    return (None, {})


def _make_vapi_response(call_id: Optional[str], deliverable: bool, suggestion: str):
    """Build the Vapi-compliant results envelope. Always returns HTTP 200."""
    result_obj = {"deliverable": deliverable, "suggestion": suggestion}
    return JSONResponse(status_code=200, content={
        "results": [{
            "toolCallId": call_id or "",
            "result": json.dumps(result_obj, ensure_ascii=False),
        }]
    })


# ── Postal code normalization ─────────────────────────────────────────────────

def resolve_postal_code(raw: str) -> Optional[str]:
    """
    Normalize a postal code input. Handles:
      "2860"          → "2860"
      "2860 Søborg"   → "2860"
      "28 60"         → "2860"
      "otto og tyve"  → None (unsupported — returns None)
    """
    if not raw:
        return None
    # Strip non-digits
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 4 and digits in VALID_POSTAL_CODES:
        return digits
    # Try fuzzy matching against valid codes (handles single-digit typos)
    if len(digits) == 4:
        matches = difflib.get_close_matches(digits, VALID_POSTAL_CODES, n=1, cutoff=0.75)
        if matches:
            return matches[0]
    return None


# ── Street name extraction & normalization (unchanged logic) ──────────────────

_NUM_WORDS = {
    # Danish digits and tens
    "en", "et", "to", "tre", "fire", "fem", "seks", "syv", "otte", "ni", "ti",
    "elleve", "tolv", "tretten", "fjorten", "femten", "seksten", "sytten", "atten", "nitten",
    "tyve", "tredive", "fyrre", "fyre", "halvtreds", "tres", "halvfjerds", "firs", "halvfems",
    # English digits and tens
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"
}

# Danish compound numbers like "seksogfyre" (46), "enogtres" (61)
_DK_COMPOUND_NUMBER_RE = re.compile(
    r'^(?:en|to|tre|fire|fem|seks|syv|otte|ni)og(?:tyve|tredive|fyrre|fyre|halvtreds|tres|halvfjerds|firs|halvfems)$',
    re.IGNORECASE
)

def is_number_word(token: str) -> bool:
    token_clean = token.lower().strip("-.,")
    if not token_clean:
        return False
    if token_clean.isdigit():
        return True
    if token_clean in _NUM_WORDS:
        return True
    if _DK_COMPOUND_NUMBER_RE.match(token_clean):
        return True
    # Handle hyphenated English numbers like "forty-six"
    if "-" in token_clean:
        parts = token_clean.split("-")
        if all(p in _NUM_WORDS for p in parts):
            return True
    return False


def normalize_street(s: str) -> str:
    s = s.lower().strip()
    return "".join(re.findall(r'[\w]', s))


def extract_street_name(line: str) -> str:
    # Remove quotes
    line = line.strip().strip('"').strip()
    if not line:
        return ""

    # Street name and house number is before the first comma
    parts = line.split(",")
    street_and_number = parts[0].strip()

    # Split by whitespace
    tokens = street_and_number.split()
    if not tokens:
        return ""

    # Peel off number tokens from the end (leaves at least 1 token as the street name)
    while len(tokens) > 1:
        last_token = tokens[-1]
        if last_token[0].isdigit() or is_number_word(last_token):
            tokens.pop()
        else:
            break

    street_name = " ".join(tokens)
    return street_name.strip()


# ── Cached street data loading (avoids re-reading large files) ────────────────

@lru_cache(maxsize=64)
def _load_streets_for_postal_code(postal_code: str):
    """
    Load and cache the unique street names for a postal code.
    Returns (db_streets_set, db_street_display_map) or (None, None) if file not found.
    """
    file_path = os.path.join(ADDRESS_DATA_DIR, f"{postal_code}.txt")
    if not os.path.exists(file_path):
        return (None, None)

    db_streets = set()
    db_street_display_map = {}

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            street = extract_street_name(line)
            if street:
                street_lower = street.lower()
                db_streets.add(street_lower)
                db_street_display_map[street_lower] = street

    return (frozenset(db_streets), db_street_display_map)


# ── Core matching logic ───────────────────────────────────────────────────────

def _match_street(user_address: str, postal_code: str):
    """
    Run exact + fuzzy matching against the postal code's street data.
    Returns (deliverable: bool, suggestion: str).
    """
    result = _load_streets_for_postal_code(postal_code)
    if result[0] is None:
        return (False, "Delivery is not available for this postal code. Please offer pickup.")

    db_streets, db_street_display_map = result

    # Clean up postal code and city that LLMs might hallucinate into the address
    clean_addr = re.sub(r',?\s*\b\d{4}\b.*$', '', user_address.strip()).strip()
    if not clean_addr:
        clean_addr = user_address.strip()

    # Extract street name from the user's input
    user_street = extract_street_name(clean_addr)
    if not user_street:
        return (False, "Please provide a valid street name.")

    user_street_lower = user_street.lower()
    user_street_norm = normalize_street(user_street)

    # Extract house number from the cleaned address (part before the first comma)
    parts = clean_addr.split(",")
    street_and_number = parts[0].strip()
    house_suffix = street_and_number[len(user_street):].strip()
    house_number = (" " + house_suffix) if house_suffix else ""

    # 1. Exact Match Check (fast)
    if user_street_lower in db_streets:
        display_name = db_street_display_map[user_street_lower]
        return (True, f"{display_name}{house_number}")

    # 2. Fuzzy Match Check
    best_ratio = 0.0
    best_street_match = None

    for db_street in db_streets:
        db_street_norm = normalize_street(db_street)

        # Match raw lowercase
        ratio_raw = difflib.SequenceMatcher(None, user_street_lower, db_street).ratio()
        # Match normalized (no spaces/punctuation)
        ratio_norm = difflib.SequenceMatcher(None, user_street_norm, db_street_norm).ratio()

        max_ratio = max(ratio_raw, ratio_norm)

        # Check prefix match bonus
        is_prefix = False
        if user_street_lower.startswith(db_street) or user_street_norm.startswith(db_street_norm):
            is_prefix = True

        if is_prefix:
            # Assign prefix match ratio: 0.95 + length bonus to prefer longer matched street names
            prefix_ratio = 0.95 + (len(db_street) / 1000.0)
            max_ratio = max(max_ratio, prefix_ratio)

        if max_ratio > best_ratio:
            best_ratio = max_ratio
            best_street_match = db_street

    threshold = 0.65
    if best_ratio >= threshold and best_street_match:
        display_name = db_street_display_map[best_street_match]
        logger.info("[ADDRESS] Fuzzy match: '%s' → '%s' (ratio=%.2f)", user_street, display_name, best_ratio)
        return (True, f"{display_name}{house_number}")

    return (False, "Street not found in the delivery zone. Please offer pickup.")


# ── Route handlers (registered on both "" and "/" to avoid 307 redirect) ──────

async def _handle_verify(request: Request):
    """Shared handler for address verification — speaks Vapi's tool-call protocol."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    call_id, args = _extract_tool_call(body)
    raw_postal = str(args.get("postal_code", "")).strip()
    user_address = str(args.get("address", "")).strip()

    logger.info("[ADDRESS] Received — toolCallId=%s, raw_postal=%r, address=%r", call_id, raw_postal, user_address)

    # Validate inputs
    if not raw_postal or not user_address:
        logger.warning("[ADDRESS] Missing postal_code or address in args: %r", args)
        return _make_vapi_response(call_id, False,
            "Missing postal code or address. Please ask the customer to provide both.")

    # Resolve postal code
    postal_code = resolve_postal_code(raw_postal)
    if not postal_code:
        logger.warning("[ADDRESS] Could not resolve postal code: %r", raw_postal)
        return _make_vapi_response(call_id, False,
            "Delivery is not available for this postal code. Please offer pickup.")

    logger.info("[ADDRESS] Resolved postal_code: %s → %s", raw_postal, postal_code)

    # Run matching
    try:
        deliverable, suggestion = _match_street(user_address, postal_code)
        logger.info("[ADDRESS] Result — deliverable=%s, suggestion=%r", deliverable, suggestion)
        return _make_vapi_response(call_id, deliverable, suggestion)
    except Exception as e:
        logger.error("[ADDRESS] Error during matching: %s", e, exc_info=True)
        return _make_vapi_response(call_id, False,
            "Could not verify address due to server error. Please offer pickup.")


# Register on BOTH "" and "/" to avoid FastAPI 307 redirect (saves ~100-300ms)
@router.post("")
async def verify_address_no_slash(request: Request):
    return await _handle_verify(request)

@router.post("/")
async def verify_address_with_slash(request: Request):
    return await _handle_verify(request)
=======
from fastapi import APIRouter
from pydantic import BaseModel
import os
from typing import Optional

router = APIRouter(prefix="/api/verify-address", tags=["Address Verification"])

class AddressVerificationRequest(BaseModel):
    postal_code: str
    address: str

class AddressVerificationResponse(BaseModel):
    deliverable: bool
    suggestion: Optional[str] = None

@router.post("/", response_model=AddressVerificationResponse)
async def verify_address(data: AddressVerificationRequest):
    postal_code = data.postal_code.strip()
    address = data.address.strip().lower()
    
    # Path to the postal code file
    file_path = os.path.join("Address_data", f"{postal_code}.txt")
    
    if not os.path.exists(file_path):
        return AddressVerificationResponse(
            deliverable=False, 
            suggestion="Delivery is not available for this postal code. Please offer pickup."
        )
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        # 1. Exact substring match
        for line in lines:
            if address in line.lower():
                return AddressVerificationResponse(deliverable=True)
                
        # 2. First word match (loose matching for street name)
        address_words = address.split()
        if address_words:
            first_word = address_words[0]
            # Only match if the first word is substantial
            if len(first_word) >= 3:
                for line in lines:
                    if first_word in line.lower():
                        return AddressVerificationResponse(deliverable=True)

        return AddressVerificationResponse(
            deliverable=False,
            suggestion="Street not found in the delivery zone. Please offer pickup."
        )
        
    except Exception as e:
        print(f"Error reading address file: {e}")
        return AddressVerificationResponse(
            deliverable=False,
            suggestion="Could not verify address due to server error. Please offer pickup."
        )
>>>>>>> f18e905 (Update address validation and assistant logic)
