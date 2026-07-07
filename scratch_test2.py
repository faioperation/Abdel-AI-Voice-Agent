import difflib
import re

def normalize_street(s: str) -> str:
    s = s.lower().strip()
    return "".join(re.findall(r'[\w]', s))

db_streets = {"statenevej", "søndre ringvej"}

user_street = "Statenevej seksogfyre".lower()
user_street_norm = normalize_street(user_street)

for db_street in db_streets:
    db_street_norm = normalize_street(db_street)
    ratio_raw = difflib.SequenceMatcher(None, user_street, db_street).ratio()
    ratio_norm = difflib.SequenceMatcher(None, user_street_norm, db_street_norm).ratio()
    print(f"'{db_street}' -> raw: {ratio_raw:.2f}, norm: {ratio_norm:.2f}")

user_street = "statenevej forty-six".lower()
user_street_norm = normalize_street(user_street)
print("----")
for db_street in db_streets:
    db_street_norm = normalize_street(db_street)
    ratio_raw = difflib.SequenceMatcher(None, user_street, db_street).ratio()
    ratio_norm = difflib.SequenceMatcher(None, user_street_norm, db_street_norm).ratio()
    print(f"'{db_street}' -> raw: {ratio_raw:.2f}, norm: {ratio_norm:.2f}")
