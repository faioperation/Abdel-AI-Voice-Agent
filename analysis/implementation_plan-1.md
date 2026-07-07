# Fix Address Verification — Accurate STT Capture & Postal Code Lookup

## Problem Summary

When a customer provides their delivery address over the phone, the STT (Speech-to-Text) model frequently **mishears street names** — especially Danish street names which are long, compound words (e.g., "Vesterbrogade" might get transcribed as "Vester bro gade" or "Wester pro gate"). This causes the address verification tool to fail because the garbled transcription doesn't match any entry in the `Address_data/{postal_code}.txt` files.

The current system has **two weaknesses**:

1. **STT Capture (Input)**: Danish street names are misheard by the transcriber, and unlike menu items, there's no fuzzy-matching correction for addresses in the `custom_llm.py` middleware.
2. **Address Matching (Backend)**: The current [address.py](file:///D:/Spartacus-bubble-AI/Abdel-AI-Voice-Agent/app/routes/address.py) does only naive `substring` and `first_word` matching — no fuzzy/phonetic matching, so even minor STT errors cause a false negative.

## Proposed Solution — Three-Layer Defense

### Layer 1: System Prompt — Force Structured Collection & Spell-Back
### Layer 2: Backend Fuzzy Matching — Tolerate STT Errors  
### Layer 3: Tool Design — Collect Postal Code First, Match Streets from Data

The key insight: **The postal code is always just 4 digits and is almost always transcribed perfectly by STT**. Once we have the postal code, we can extract the unique street names from that postal code's data file and use fuzzy matching to find the best match, even if the STT mangled the street name.

---

## Proposed Changes

### Component 1: System Prompt (Address Collection Strategy)

#### [MODIFY] [system_prompt.txt](file:///D:/Spartacus-bubble-AI/Abdel-AI-Voice-Agent/system_prompt.txt)
#### [MODIFY] [new agent prompt after creation.txt](file:///D:/Spartacus-bubble-AI/Abdel-AI-Voice-Agent/new%20agent%20prompt%20after%20creation.txt)

Update **REGEL 15** in both prompts to instruct the AI to:

1. **Ask for postal code FIRST** (4-digit number — STT handles this perfectly)
2. **Ask for street name and house number SEPARATELY** after postal code
3. **Always read back the full address** and ask for confirmation before calling the tool
4. **If verification fails, ask the customer to SPELL the street name** letter by letter (e.g., "Kan du stave gadenavnet for mig?")
5. **On second failure, offer pickup** instead of trying a third time

New REGEL 15 text (Danish):

```
REGEL 15 — LEVERINGSADRESSE (KUN VED LEVERING):
Hvis kunden ønsker levering, SKAL du indsamle adressen trinvist:

TRIN 1 — POSTNUMMER:
  Spørg: "Hvad er dit postnummer?"
  Postnummeret er altid 4 cifre. Gentag det for at bekræfte: "Postnummer [XXXX], er det korrekt?"

TRIN 2 — GADENAVN OG HUSNUMMER:
  Spørg: "Hvad er dit gadenavn og husnummer?"
  Gentag altid det fulde gadenavn og husnummer: "Du sagde [gadenavn] [husnummer], er det rigtigt?"
  Hvis kunden siger "nej" eller retter, spørg igen.

TRIN 3 — VERIFIKATION:
  Kald `verify_delivery_address` med postnummeret og adressen.
  - Hvis værktøjet returnerer `deliverable: true` med et matchet gadenavn, brug det matchede gadenavn videre.
  - Hvis `deliverable: false`, sig: "Jeg kunne desværre ikke finde den adresse i vores leveringsområde. Kan du stave gadenavnet for mig bogstav for bogstav?"
  - Ved andet fejlet forsøg: Tilbyd afhentning ("pickup") i stedet.

VIGTIGT:
  - Hvis ordren er "pickup", spørg IKKE om adresse.
  - Saml ALTID postnummer og gadenavn separat — sig ALDRIG "Hvad er din fulde adresse?"
  - Afslut ALDRIG opkaldet, fordi adressen ikke kunne verificeres.
```

---

### Component 2: Backend Address Verification (Fuzzy Matching)

#### [MODIFY] [address.py](file:///D:/Spartacus-bubble-AI/Abdel-AI-Voice-Agent/app/routes/address.py)

Complete rewrite of the verification logic:

1. **Extract unique street names** from the postal code file (parse `"StreetName HouseNum, ..."` → extract just the street name portion)
2. **Normalize** both the user input and the file data (lowercase, strip accents, remove punctuation)
3. **Fuzzy match** using `difflib.SequenceMatcher` (no external dependencies) with a configurable threshold (e.g., 0.70 similarity)
4. **Return the matched street name** in the response so the AI can confirm with the customer: `{"deliverable": true, "matched_street": "Vesterbrogade"}`
5. **Also try token-based matching**: split both the user input and file street names into word tokens and check if enough tokens overlap — this handles cases where STT inserts spaces into compound words ("Vester bro gade" → match "Vesterbrogade")

Updated response model:
```python
class AddressVerificationResponse(BaseModel):
    deliverable: bool
    matched_street: Optional[str] = None  # The canonical street name found in the data
    suggestion: Optional[str] = None      # Explanation for the AI
```

Key matching strategy:
```
Input: "wester bro gade 85" + postal_code "1620"
→ Load 1620.txt
→ Extract unique streets: {"Vesterbrogade"}  
→ Normalize input: "westerbrogade" (join tokens, strip spaces)
→ Compare to "vesterbrogade" → ratio 0.86 → MATCH
→ Return: {deliverable: true, matched_street: "Vesterbrogade 85"}
```

---

### Component 3: Vapi Tool Definition (Enhanced)

#### [MODIFY] [vapi_client.py](file:///D:/Spartacus-bubble-AI/Abdel-AI-Voice-Agent/app/vapi_client.py)

Update the `create_address_verification_tool` function to:

1. Improve the tool description to guide the LLM on how to format the arguments
2. Add a `house_number` optional parameter so the AI can send it separately
3. Update the tool description to clarify that the `address` field should be just the **street name** (not the full formatted address)

Updated tool schema:
```python
{
    "name": "verify_delivery_address",
    "description": "Verifies if the customer's street is within the delivery zone for their postal code. Call this after collecting postal code and street name separately. The address should be just the street name and house number.",
    "parameters": {
        "type": "object",
        "properties": {
            "postal_code": {"type": "string", "description": "The 4-digit Danish postal code."},
            "address": {"type": "string", "description": "The street name and house number (e.g. 'Vesterbrogade 85'). Do NOT include postal code or city name."}
        },
        "required": ["postal_code", "address"]
    }
}
```

---

### Component 4: Address Fuzzy Matching in STT Preprocessing (Optional Enhancement)

#### [NO CHANGE] [custom_llm.py](file:///D:/Spartacus-bubble-AI/Abdel-AI-Voice-Agent/app/routes/custom_llm.py)

We will **NOT** add address-specific fuzzy mapping to `custom_llm.py`. The reason: street names are too numerous and varied to maintain a static dictionary. Instead, the backend `address.py` will do the fuzzy matching dynamically against the postal code file data. This is the correct architectural approach because:
- Street names are context-dependent (only valid within a postal code)
- The postal code files already contain all valid streets
- Fuzzy matching on the backend is more maintainable than a static dictionary

---

## Open Questions

> [!IMPORTANT]
> **Similarity Threshold**: I plan to use a 0.65 similarity ratio as the fuzzy match threshold. This is deliberately generous because Danish compound street names can get heavily mangled by STT (e.g., "Vesterbrogade" → "Wester pro gate"). Should I make this configurable via `.env`, or is a hard-coded value acceptable?

> [!IMPORTANT]
> **House Number Handling**: When the customer says "Vesterbrogade 85", should the verification check if house number 85 specifically exists in the postal code file, or just verify that the street name "Vesterbrogade" exists in that postal zone? Checking the exact house number is stricter but might cause false negatives if the STT mishears "85" as "58". I recommend checking **only the street name** (+ postal code) for deliverability, since any house on that street within that postal zone should be deliverable. The house number is still collected for the delivery driver.

---

## Verification Plan

### Automated Tests
- Add a test endpoint `GET /api/verify-address/test` that runs a suite of test cases:
  - Exact match: `postal_code="1620"`, `address="Vesterbrogade 85"` → `deliverable: true`
  - Fuzzy match (STT error): `postal_code="1620"`, `address="Wester bro gade 85"` → `deliverable: true`
  - Invalid postal code: `postal_code="9999"` → `deliverable: false`
  - Invalid street: `postal_code="1620"`, `address="Nonexistent Street 1"` → `deliverable: false`

### Manual Verification
- After deployment, create a new assistant via the dashboard (this attaches the updated tool and prompt)
- Make a test call ordering delivery and provide address with intentional mispronunciation
- Verify the AI collects postal code first, then street name, reads it back, and successfully verifies

---

## Summary of Files Changed

| File | Change Type | Description |
|------|------------|-------------|
| [address.py](file:///D:/Spartacus-bubble-AI/Abdel-AI-Voice-Agent/app/routes/address.py) | MODIFY | Complete rewrite with fuzzy matching, street name extraction, normalization |
| [vapi_client.py](file:///D:/Spartacus-bubble-AI/Abdel-AI-Voice-Agent/app/vapi_client.py) | MODIFY | Updated tool description and parameter docs |
| [system_prompt.txt](file:///D:/Spartacus-bubble-AI/Abdel-AI-Voice-Agent/system_prompt.txt) | MODIFY | Rewrite REGEL 15 for step-by-step address collection |
| [new agent prompt after creation.txt](file:///D:/Spartacus-bubble-AI/Abdel-AI-Voice-Agent/new%20agent%20prompt%20after%20creation.txt) | MODIFY | Same REGEL 15 update |
