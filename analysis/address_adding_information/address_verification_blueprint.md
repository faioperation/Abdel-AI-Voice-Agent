# AI Voice Agent Address Verification Blueprint

This document provides a comprehensive, end-to-end guide on implementing a robust, LLM-driven address verification system using the Google Maps Address Validation API. It is designed to be highly resilient to Speech-to-Text (STT) errors and can be easily ported to other AI voice agent projects (e.g., using Vapi).

---

## 1. High-Level Architecture

The verification pipeline bridges the conversational AI and the backend logic seamlessly while keeping the backend stateless.

1. **Information Gathering:** The LLM prompts the user for their postal code, followed by their street name and house number.
2. **Stateless Tool Call:** The LLM invokes a backend webhook (`verify_delivery_address`), passing both the user's input and the shop's configuration (latitude, longitude, delivery radius) extracted from its system prompt.
3. **Google API Validation:** The backend queries the Google Address Validation API to standardize the address and retrieve exact GPS coordinates.
4. **Confidence Scoring:** The backend assesses Google's confidence (checking for unconfirmed components like missing house numbers).
5. **Zone Check:** The backend calculates the Haversine distance between the customer and the shop.
6. **Conversational Feedback:** The backend returns a conversational payload instructing the LLM to either proceed, ask for missing details, or reject the delivery politely.

---

## 2. LLM Configuration (System Prompt)

The success of this system relies heavily on strict LLM instructions. The LLM must know *how* to gather the data and *what* to do with the result.

### A. Shop Configuration Injection
Inject the store's configuration directly into the system prompt. This allows the backend to remain completely stateless and multi-tenant.

```text
# MENUDATA (STRICT SOURCE)
DELIVERY CONFIGURATION:
- shop_lat: 55.7295
- shop_lng: 12.3755
- delivery_radius_km: 7
- allowed_postal_codes: "2860, 2800, 2830"
```

### B. Strict Rules
Add explicit rules to govern the LLM's behavior:

> **RULE: DELIVERY VERIFICATION**
> If the customer wants delivery, you MUST verify their address using the `verify_delivery_address` tool.
> 1. Always ask for the 4-digit postal code FIRST. Convert spoken words to digits (e.g., "twenty eight sixty" -> "2860").
> 2. Then ask for the street name and house number.
> 3. Call the tool passing the address, postal code, and the shop configuration from the DELIVERY CONFIGURATION section.
> 4. If the tool returns `deliverable: true`, you MUST use the exact string provided in the `suggestion` field for the final order creation. Do not modify its spelling or casing.
> 5. If the tool asks you to reconfirm a field (e.g., house number), ask ONLY that question and call the tool again.
> 6. If `deliverable: false`, politely inform the customer and offer pickup instead.

---

## 3. Tool Schema Definition (e.g., Vapi)

Define the tool schema to accept all necessary parameters.

```json
{
  "name": "verify_delivery_address",
  "description": "Validates the customer's delivery address using Google Maps and checks the delivery zone.",
  "parameters": {
    "type": "object",
    "properties": {
      "address": {
        "type": "string",
        "description": "Customer's street name and house number ONLY. No postal code or city."
      },
      "postal_code": {
        "type": "string",
        "description": "The 4-digit postal code as digits only."
      },
      "shop_lat": { "type": "number", "description": "Shop latitude from prompt." },
      "shop_lng": { "type": "number", "description": "Shop longitude from prompt." },
      "delivery_radius_km": { "type": "number", "description": "Delivery radius in km from prompt." },
      "allowed_postal_codes": { "type": "string", "description": "Comma-separated list of allowed postal codes." }
    },
    "required": ["address", "postal_code", "shop_lat", "shop_lng", "delivery_radius_km"]
  }
}
```

---

## 4. Backend Implementation (FastAPI Example)

The backend handles the core logic. Below is the conceptual workflow.

### A. Google Address Validation API Call
Use the `https://addressvalidation.googleapis.com/v1:validateAddress` endpoint.

```python
payload = {
    "address": {
        "regionCode": "DK", # Adjust for your region
        "postalCode": postal_code,
        "addressLines": [street_and_house_number],
    }
}
# POST to Google API with your GOOGLE_MAPS_API_KEY
```

### B. Confidence Scoring (STT Resilience)
Voice STT often misses house numbers or misspells streets. Google's API identifies these via `unconfirmedComponentTypes`.

```python
next_action = verdict.get("possibleNextAction", "")
unconfirmed_types = address.get("unconfirmedComponentTypes", [])

# Example Logic:
if next_action == "ACCEPT" and not unconfirmed_types:
    confidence = "high"
elif next_action in ("ACCEPT", "FIX", "CONFIRM") and "street_number" in unconfirmed_types:
    confidence = "medium" # We found the street, but missing the house number
else:
    confidence = "low"
```
*Tip: If Google returns a large `featureSizeMeters` (>300m) despite high confidence, downgrade it to medium, as it likely geocoded the center of a long street rather than a specific door.*

### C. Determining the Bot's Next Action
Based on confidence, dictate the LLM's next conversational turn.

```python
if confidence == "high":
    action = "proceed"
    bot_reply = ""
elif confidence == "medium" and "street_number" in unconfirmed:
    action = "reconfirm_house"
    bot_reply = "Just to double check — could you confirm the house or flat number for me?"
elif confidence == "medium":
    action = "readback_address"
    bot_reply = f"Got it — I've got the address as {formatted_address}, is that right?"
else:
    action = "fallback"
    bot_reply = "I'm having a little trouble placing that address precisely. Could you describe a nearby landmark, or give me the full street name and number again?"
```

### D. Delivery Zone Check (Haversine)
If confidence is high or medium, check the distance.

```python
import math

def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0 # Earth radius in km
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng/2)**2
    return R * 2 * math.asin(math.sqrt(a))

distance = haversine_km(shop_lat, shop_lng, customer_lat, customer_lng)
in_zone = distance <= delivery_radius_km
```
*Optional: You can upgrade the Haversine calculation to road-distance using the Google Distance Matrix API for higher accuracy.*

### E. Constructing the Final Payload
Return a JSON payload that the LLM can easily interpret.

```python
if in_zone:
    if action in ["reconfirm_house", "readback_address"]:
        suggestion = bot_reply # Force the bot to ask the clarifying question
    else:
        suggestion = formatted_address # Clean address for the bot to use
    deliverable = True
else:
    suggestion = f"Unfortunately we don't deliver to {formatted_address} — it's about {distance:.1f} km away. You're very welcome to pick it up instead."
    deliverable = False

return {
    "results": [{
        "toolCallId": call_id,
        "result": json.dumps({"deliverable": deliverable, "suggestion": suggestion})
    }]
}
```

---

## 5. Portability Checklist for New Projects
To port this to a new Antigravity project, ensure you have:
1. **Google Maps API Key:** With Address Validation API enabled.
2. **Environment Variables:** Set up routing and API keys in your new `.env`.
3. **Stateless Tool Execution:** Ensure the new LLM prompt explicitly provides the shop coordinates and delivery radius.
4. **Vapi (or equivalent) Integration:** Register the tool schema and ensure webhooks route correctly to your backend endpoint.
