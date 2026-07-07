import math
import logging
from typing import Optional

import httpx

from app.config import GOOGLE_MAPS_API_KEY, GOOGLE_MAPS_REGION_CODE

logger = logging.getLogger(__name__)

# ── Google Address Validation API endpoint ────────────────────────────────────
_GOOGLE_VALIDATE_URL = (
    "https://addressvalidation.googleapis.com/v1:validateAddress"
)

# ── Timeout for external API calls (seconds) ──────────────────────────────────
_API_TIMEOUT = 8.0


# ─────────────────────────────────────────────────────────────────────────────
# Part 1: Google Address Validation API call
# ─────────────────────────────────────────────────────────────────────────────

async def validate_address(
    house_and_street: str,
    locality: str,
    postal_code: str,
) -> dict:
    """
    Send the parsed address to Google's Address Validation API and return
    a structured ValidationResult dict.

    Args:
        house_and_street: e.g. "Søborg Hovedgade 12"
        locality:         e.g. "Søborg"  (area / city name)
        postal_code:      e.g. "2860"

    Returns a dict:
    {
        "isValid": bool,
        "confidence": "high" | "medium" | "low",
        "formattedAddress": str,
        "postalCode": str,
        "lat": float,
        "lng": float,
        "unconfirmedFields": [str, ...],
        "rawApiResponse": dict,
    }
    On any error, isValid=False, confidence="low", lat=lng=0.
    """
    if not GOOGLE_MAPS_API_KEY:
        logger.error("[ADDRESS] GOOGLE_MAPS_API_KEY is not set — cannot validate address.")
        return _error_result("Server configuration error: address validation unavailable.")

    payload = {
        "address": {
            "regionCode": GOOGLE_MAPS_REGION_CODE,   # Hardcoded "DK"
            "locality": locality,
            "postalCode": postal_code,
            "addressLines": [house_and_street],
        }
    }

    try:
        async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
            resp = await client.post(
                _GOOGLE_VALIDATE_URL,
                params={"key": GOOGLE_MAPS_API_KEY},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        logger.warning("[ADDRESS] Google API timeout for address: %r", house_and_street)
        return _error_result("timeout")
    except httpx.HTTPStatusError as exc:
        logger.error("[ADDRESS] Google API HTTP error %s: %s", exc.response.status_code, exc.response.text)
        return _error_result("api_error")
    except Exception as exc:
        logger.error("[ADDRESS] Unexpected error calling Google API: %s", exc, exc_info=True)
        return _error_result("unknown_error")

    return _parse_api_response(data)


def _parse_api_response(data: dict) -> dict:
    """Extract and structure the fields we care about from the API response."""
    result = data.get("result", {})
    verdict = result.get("verdict", {})
    address = result.get("address", {})
    geocode = result.get("geocode", {})

    # ── Confidence mapping (tunable — see comments) ──────────────────────────
    next_action = verdict.get("possibleNextAction", "")
    unconfirmed_types = address.get("unconfirmedComponentTypes", [])
    missing_types = address.get("missingComponentTypes", [])

    # If Google wants FIX just because we didn't provide a city name (locality),
    # but it confirmed the street and number, we can treat it as ACCEPT.
    if next_action == "FIX" and not unconfirmed_types and set(missing_types) <= {"locality", "postal_town"}:
        next_action = "ACCEPT"

    # HIGH: Google fully accepts it with no unconfirmed parts
    if next_action == "ACCEPT" and not unconfirmed_types:
        confidence = "high"
    # MEDIUM: Accepted but some component (typically house number) not confirmed
    # Or FIX/CONFIRM where it's unconfirmed street_number
    elif next_action in ("ACCEPT", "FIX", "CONFIRM") and set(unconfirmed_types) <= {"street_number", "subpremise"}:
        confidence = "medium"
    # LOW: Google wants correction, can't complete, or flagged other fields
    else:
        confidence = "low"

    # ── Feature-size accuracy flag ───────────────────────────────────────────
    # If Google's geocode covers a large area (>300 m radius) precision is low
    feature_size_m = geocode.get("featureSizeMeters", 0)
    if feature_size_m > 300 and confidence == "high":
        confidence = "medium"   # Downgrade: centroid may be far from actual door

    # ── Extract geocode ───────────────────────────────────────────────────────
    location = geocode.get("location", {})
    lat = location.get("latitude", 0.0)
    lng = location.get("longitude", 0.0)

    # ── Formatted address & postal code ──────────────────────────────────────
    formatted = address.get("formattedAddress", "")
    postal_address = address.get("postalAddress", {})
    resolved_postal = postal_address.get("postalCode", "")

    is_valid = confidence in ("high", "medium") and bool(formatted)

    return {
        "isValid": is_valid,
        "confidence": confidence,
        "formattedAddress": formatted,
        "postalCode": resolved_postal,
        "lat": lat,
        "lng": lng,
        "unconfirmedFields": unconfirmed_types,
        "rawApiResponse": data,
    }


def _error_result(reason: str) -> dict:
    """Return a safe fallback ValidationResult on error."""
    return {
        "isValid": False,
        "confidence": "low",
        "formattedAddress": "",
        "postalCode": "",
        "lat": 0.0,
        "lng": 0.0,
        "unconfirmedFields": [],
        "rawApiResponse": {"_error": reason},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: Confidence-based bot action decision
# ─────────────────────────────────────────────────────────────────────────────

def get_next_bot_action(validation_result: dict) -> dict:
    """
    Decide what the bot should do next based on the validation result.

    Returns:
    {
        "action": "proceed" | "reconfirm_house" | "readback_address" | "fallback",
        "bot_reply": str   # Suggested text the bot should say
    }
    """
    confidence = validation_result.get("confidence", "low")
    unconfirmed = set(validation_result.get("unconfirmedFields", []))
    formatted = validation_result.get("formattedAddress", "")

    if confidence == "high":
        # Proceed silently to zone check — no extra confirmation needed
        return {
            "action": "proceed",
            "bot_reply": "",  # Bot will confirm address in the final order read-back
        }

    if confidence == "medium":
        if unconfirmed & {"street_number", "subpremise"}:
            # Only the house/unit number is uncertain — ask just for that
            return {
                "action": "reconfirm_house",
                "bot_reply": (
                    "Just to double check — could you confirm the house or flat number for me?"
                ),
            }
        else:
            # Another field (e.g. street spelling) was corrected — read back the corrected version
            return {
                "action": "readback_address",
                "bot_reply": (
                    f"Got it — I've got the address as {formatted}, is that right?"
                ),
            }

    # Low confidence — ask customer to repeat or offer alternative
    return {
        "action": "fallback",
        "bot_reply": (
            "I'm having a little trouble placing that address precisely. "
            "Could you describe a nearby landmark, or give me the full street name and number again?"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Part 3: Delivery zone check (Haversine, swappable to Distance Matrix API)
# ─────────────────────────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Straight-line distance in km between two lat/lng points using the
    Haversine formula.
    """
    R = 6371.0  # Earth radius in km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# ── Extension point: swap Haversine for Google Distance Matrix API later ──────
# To upgrade to road-distance accuracy:
#   1. Implement _get_road_distance_km(origin_latlng, dest_latlong) -> float
#      using the Distance Matrix API
#   2. Replace the _haversine_km call in check_delivery_zone() with
#      await _get_road_distance_km(...)
# The rest of the zone-check logic stays unchanged.
async def _get_road_distance_km(
    shop_lat: float, shop_lng: float, dest_lat: float, dest_lng: float
) -> float:
    """
    STUB — replace with Distance Matrix API call for road-distance accuracy.
    Currently falls back to Haversine.
    """
    return _haversine_km(shop_lat, shop_lng, dest_lat, dest_lng)


def check_delivery_zone(
    lat: float,
    lng: float,
    postal_code: str,
    shop_lat: float,
    shop_lng: float,
    delivery_radius_km: float,
    allowed_postal_codes: Optional[list[str]] = None,
) -> dict:
    """
    Check whether (lat, lng) is within the shop's delivery zone.

    Zone check order:
    1. Postal code allow-list (soft signal — not an instant reject if list
       is incomplete, just logged).
    2. Haversine distance vs. delivery_radius_km.

    Args:
        lat / lng:              Customer's geocoded coordinates
        postal_code:            Resolved postal code from Google API
        shop_lat / shop_lng:    Shop's coordinates (passed in from system prompt)
        delivery_radius_km:     Max delivery radius (from system prompt)
        allowed_postal_codes:   Optional allow-list (from system prompt, can be None)

    Returns:
    {
        "inZone": bool,
        "distanceKm": float,
        "method": "haversine",
        "postalCodeSignal": "allowed" | "unlisted" | "not_checked",
    }
    """
    # Soft postal code signal
    postal_signal = "not_checked"
    if allowed_postal_codes:
        clean_codes = [c.strip() for c in allowed_postal_codes if c.strip()]
        if clean_codes:
            postal_signal = "allowed" if postal_code in clean_codes else "unlisted"
            # Unlisted is a soft signal — we still do the distance check

    # Distance check
    distance_km = _haversine_km(shop_lat, shop_lng, lat, lng)
    in_zone = distance_km <= delivery_radius_km

    logger.info(
        "[ADDRESS] Zone check — dist=%.2f km, radius=%.1f km, inZone=%s, postalSignal=%s",
        distance_km, delivery_radius_km, in_zone, postal_signal,
    )

    return {
        "inZone": in_zone,
        "distanceKm": round(distance_km, 2),
        "method": "haversine",
        "postalCodeSignal": postal_signal,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Part 4: Top-level orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def process_delivery_address(
    raw_address: str,
    postal_code: str,
    shop_lat: float,
    shop_lng: float,
    delivery_radius_km: float,
    allowed_postal_codes: Optional[list[str]] = None,
) -> dict:
    """
    Full pipeline orchestrator. Called by the Vapi route handler.

    Args:
        raw_address:          Full address string from the customer (house + street)
        postal_code:          Postal code spoken by the customer
        shop_lat / shop_lng:  From the assistant's system prompt (per-assistant config)
        delivery_radius_km:   From the assistant's system prompt
        allowed_postal_codes: Optional list from the assistant's system prompt

    Returns:
    {
        "deliverable": bool,
        "suggestion": str,       # Formatted address on success, or bot reply on failure
        "confidence": str,
        "botAction": dict,
        "zoneResult": dict | None,
        "validationResult": dict,
    }
    """
    # ── Step 1: Validate inputs ───────────────────────────────────────────────
    if not raw_address or not postal_code:
        return {
            "deliverable": False,
            "suggestion": "Missing address or postal code. Please ask the customer to provide both.",
            "confidence": "low",
            "botAction": {"action": "fallback", "bot_reply": ""},
            "zoneResult": None,
            "validationResult": _error_result("missing_input"),
        }

    # ── Step 2: Google Address Validation ────────────────────────────────────
    # We pass the full raw_address as "house_and_street" and postal_code as both
    # locality hint and postalCode — Google will resolve the rest.
    validation = await validate_address(
        house_and_street=raw_address,
        locality="",          # Let Google infer locality from postal_code + addressLines
        postal_code=postal_code,
    )

    logger.info(
        "[ADDRESS] Validation result — confidence=%s, isValid=%s, formatted=%r",
        validation["confidence"], validation["isValid"], validation["formattedAddress"],
    )

    # ── Step 3: Bot action decision ───────────────────────────────────────────
    bot_action = get_next_bot_action(validation)

    # If confidence is low or address is invalid, return fallback immediately
    if not validation["isValid"] or bot_action["action"] == "fallback":
        fallback_msg = (
            bot_action["bot_reply"]
            or "I'm having trouble placing that address. Could you repeat it with the full street name?"
        )
        return {
            "deliverable": False,
            "suggestion": fallback_msg,
            "confidence": validation["confidence"],
            "botAction": bot_action,
            "zoneResult": None,
            "validationResult": validation,
        }

    # ── Step 4: Delivery zone check ───────────────────────────────────────────
    lat, lng = validation["lat"], validation["lng"]
    if lat == 0.0 and lng == 0.0:
        # No geocode returned — can't do zone check
        return {
            "deliverable": False,
            "suggestion": (
                "I wasn't able to pinpoint that address on the map. "
                "Could you give me the full street name and number again?"
            ),
            "confidence": "low",
            "botAction": {"action": "fallback", "bot_reply": ""},
            "zoneResult": None,
            "validationResult": validation,
        }

    zone = check_delivery_zone(
        lat=lat,
        lng=lng,
        postal_code=validation["postalCode"] or postal_code,
        shop_lat=shop_lat,
        shop_lng=shop_lng,
        delivery_radius_km=delivery_radius_km,
        allowed_postal_codes=allowed_postal_codes,
    )

    # ── Step 5: Build final response ──────────────────────────────────────────
    formatted = validation["formattedAddress"]

    if zone["inZone"]:
        # Re-confirmation or direct proceed
        if bot_action["action"] in ("reconfirm_house", "readback_address"):
            # Bot still needs to confirm one field — surface that message
            suggestion = bot_action["bot_reply"]
        else:
            suggestion = formatted  # Clean high-confidence address for order read-back
        deliverable = True
    else:
        suggestion = (
            f"Unfortunately we don't deliver to {formatted} — "
            f"it's about {zone['distanceKm']:.1f} km away, which is outside our delivery area. "
            "You're very welcome to pick it up from us instead — would that work for you?"
        )
        deliverable = False

    return {
        "deliverable": deliverable,
        "suggestion": suggestion,
        "confidence": validation["confidence"],
        "botAction": bot_action,
        "zoneResult": zone,
        "validationResult": validation,
    }
