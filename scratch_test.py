import sys
import difflib
import re

VALID_POSTAL_CODES = {"2600"}

def normalize_street(s: str) -> str:
    s = s.lower().strip()
    return "".join(re.findall(r'[\w]', s))

def extract_street_name(line: str) -> str:
    line = line.strip().strip('"').strip()
    if not line: return ""
    parts = line.split(",")
    street_and_number = parts[0].strip()
    tokens = street_and_number.split()
    if not tokens: return ""
    if len(tokens) > 1 and tokens[-1][0].isdigit():
        street_name = " ".join(tokens[:-1])
    else:
        street_name = " ".join(tokens)
    return street_name.strip()

db_streets = {"statenevej", "søndre ringvej"}
db_street_display_map = {"statenevej": "Statenevej", "søndre ringvej": "Søndre Ringvej"}

def test_match(user_address):
    clean_addr = re.sub(r',?\s*\b\d{4}\b.*$', '', user_address.strip()).strip()
    user_street = extract_street_name(clean_addr)
    user_street_lower = user_street.lower()
    user_street_norm = normalize_street(user_street)

    parts = clean_addr.split(",")
    street_and_number = parts[0].strip()
    user_tokens = street_and_number.split()
    house_number = ""
    if len(user_tokens) > 1 and user_tokens[-1][0].isdigit():
        house_number = " " + user_tokens[-1]

    print(f"[{user_address}] user_street: '{user_street}', house_number: '{house_number}'")

    if user_street_lower in db_streets:
        return (True, f"{db_street_display_map[user_street_lower]}{house_number}")

    best_ratio = 0.0
    best_street_match = None

    for db_street in db_streets:
        db_street_norm = normalize_street(db_street)
        ratio_raw = difflib.SequenceMatcher(None, user_street_lower, db_street).ratio()
        ratio_norm = difflib.SequenceMatcher(None, user_street_norm, db_street_norm).ratio()
        max_ratio = max(ratio_raw, ratio_norm)
        print(f"  vs '{db_street}' -> raw: {ratio_raw:.2f}, norm: {ratio_norm:.2f}")
        if max_ratio > best_ratio:
            best_ratio = max_ratio
            best_street_match = db_street

    if best_ratio >= 0.65 and best_street_match:
        return (True, f"{db_street_display_map[best_street_match]}{house_number}")
    return False, "Not found"

print(test_match("Statenevej 46"))
print(test_match("Statene vej 46"))
print(test_match("Søndre Ringvej 46"))
print(test_match("statenevej 46"))
