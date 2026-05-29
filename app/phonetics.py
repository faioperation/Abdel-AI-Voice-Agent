import re

DANISH_PHONEME_DICT = {
    # Pronunciation fixes requested by client
    "pommes frites": "pomfritter",
    "kebab": "kebæb",                 # Forces the correct flat Danish vowel
    "champignoner": "sjampinjoner",   # Avoids a hard G sound (from "sjampinjonger")
    "champignon": "sjampinjon",

    # Cartesia MFA-IPA Danish inline overrides for Pepperoni (matching all possible spellings)
    "pepperronipizzaen": "<<p|ɛ|p|ə|ɾ|o|n|i>>pizzaen",
    "pepperonipizzaen": "<<p|ɛ|p|ə|ɾ|o|n|i>>pizzaen",
    "peperonipizzaen": "<<p|ɛ|p|ə|ɾ|o|n|i>>pizzaen",
    "pepperronipizza": "<<p|ɛ|p|ə|ɾ|o|n|i>>pizza",
    "pepperonipizza": "<<p|ɛ|p|ə|ɾ|o|n|i>>pizza",
    "peperonipizza": "<<p|ɛ|p|ə|ɾ|o|n|i>>pizza",
    "pepperroni": "<<p|ɛ|p|ə|ɾ|o|n|i>>",
    "pepperoni": "<<p|ɛ|p|ə|ɾ|o|n|i>>",
    "peperoni": "<<p|ɛ|p|ə|ɾ|o|n|i>>",

    # Soft open-mid front vowel and silent 't' for fiskefilet/fiskefillet (pronounced like "fiske-fil-æ")
    "fiskefilet": "fiskefilæ",
    "fiskefillet": "fiskefilæ",
    "fiskefileter": "fiskefilæer",
    "fiskefilleter": "fiskefilæer",
    "fiskefileten": "fiskefilæen",
    "fiskefilleten": "fiskefilæen",

    # Brand name streaming normalization
    "foodvoice.ai": "FoodVoice punktum A I",
    "foodvoice.dk": "FoodVoice punktum D K",

    # Abbreviation expansions
    "kr.": "kroner", "kr": "kroner",
    "pr.": "per", "pr": "per",
    "stk.": "styk", "stk": "styk",
    "alm.": "almindelig", "alm": "almindelig",
    "ca.": "cirka", "ca": "cirka",
    "bl.a.": "blandt andet",
    "inkl.": "inklusive", "inkl": "inklusive",
    "ekskl.": "eksklusive", "ekskl": "eksklusive",
    "tlf.": "telefonnummer", "tlf": "telefonnummer",
    "nr.": "nummer", "nr": "nummer",
}

# Unit Map for Number+Unit normalization
UNIT_MAP = {
    "g": "gram",
    "kg": "kilo",
    "cl": "centiliter",
    "l": "liter",
    "cm": "centimeter",
    "kr": "kroner",
    "kr.": "kroner",
    "stk": "styk",
    "stk.": "styk",
}

# Pre-compile the phonetic dictionary patterns
_COMPILED_PATTERNS: list[tuple[re.Pattern, str]] = []
for _word in sorted(DANISH_PHONEME_DICT.keys(), key=len, reverse=True):
    _replacement = DANISH_PHONEME_DICT[_word]
    _compiled_patterns_entry = (
        re.compile(r"\b" + re.escape(_word) + r"\b", flags=re.IGNORECASE),
        _replacement,
    )
    _COMPILED_PATTERNS.append(_compiled_patterns_entry)

# Pre-compile number normalizer regexes
# 1. Matches digits (including decimals) followed optionally by whitespace and a unit (e.g. 150g, 30 cm, 0,5L)
_RE_NUM_UNIT = re.compile(r"\b(\d+[\.,]\d+|\d+)\s*(g|kg|cl|l|cm|kr\.?|stk\.?)\b", flags=re.IGNORECASE)

# 2. Matches standalone decimals (e.g. 0,5 or 0.33)
_RE_DECIMAL = re.compile(r"\b(\d+)([\.,])(\d+)\b")

# 3. Matches standalone integers
_RE_INTEGER = re.compile(r"\b\d+\b")

# Master regex to quickly check if the text contains digits or any word from the Danish phonetic dictionary
_PHONETIC_KEYS_OR_DIGIT = re.compile(
    r"\d|" + "|".join(r"\b" + re.escape(_word) + r"\b" for _word in DANISH_PHONEME_DICT.keys()),
    flags=re.IGNORECASE,
)



def num_to_danish_words(n: int) -> str:
    """Converts an integer from 0 to 9999 to its Danish word equivalent."""
    if n == 0:
        return "nul"

    ones = ["", "en", "to", "tre", "fire", "fem", "seks", "syv", "otte", "ni",
            "ti", "elleve", "tolv", "tretten", "fjorten", "femten", "seksten",
            "sytten", "atten", "nitten"]

    tens = ["", "", "tyve", "tredive", "fyrre", "halvtreds", "tres", "halvfjerds", "firs", "halvfems"]

    def _under_100(val: int) -> str:
        if val < 20:
            return ones[val]
        t = val // 10
        o = val % 10
        if o == 0:
            return tens[t]
        o_str = "en" if o == 1 else ones[o]
        return f"{o_str}og{tens[t]}"

    if n < 100:
        return _under_100(n)

    if n < 1000:
        h = n // 100
        rem = n % 100
        h_str = "hundrede" if h == 1 else f"{ones[h]} hundrede"
        if rem == 0:
            return h_str
        return f"{h_str} og {_under_100(rem)}"

    if n < 10000:
        th = n // 1000
        rem = n % 1000
        th_str = "tusind" if th == 1 else f"{ones[th]} tusind"
        if rem == 0:
            return th_str
        if rem < 100:
            return f"{th_str} og {_under_100(rem)}"
        return f"{th_str} {num_to_danish_words(rem)}"

    return str(n)


def integer_to_danish_words(num_str: str) -> str:
    """Converts a string of digits to Danish words, handling long numbers as digit-by-digit."""
    if len(num_str) >= 5:
        digit_names = ["nul", "en", "to", "tre", "fire", "fem", "seks", "syv", "otte", "ni"]
        return " ".join(digit_names[int(d)] for d in num_str)
    return num_to_danish_words(int(num_str))


def normalize_number_unit(match: re.Match) -> str:
    """Callback to normalize matched number+unit pairs to Danish words."""
    num_part = match.group(1)
    unit_part = match.group(2).lower()

    if "." in num_part or "," in num_part:
        # Decimal number
        num_part = num_part.replace(",", ".")
        parts = num_part.split(".")
        whole = parts[0]
        decimal = parts[1]
        digit_names = ["nul", "en", "to", "tre", "fire", "fem", "seks", "syv", "otte", "ni"]
        whole_words = integer_to_danish_words(whole)
        decimal_words = " ".join(digit_names[int(d)] for d in decimal)
        num_words = f"{whole_words} komma {decimal_words}"
    else:
        num_words = integer_to_danish_words(num_part)

    unit_words = UNIT_MAP.get(unit_part, UNIT_MAP.get(unit_part + ".", unit_part))
    if unit_words == "styk" and num_words == "en":
        num_words = "et"
    return f"{num_words} {unit_words}"


def normalize_decimal(match: re.Match) -> str:
    """Callback to normalize standalone decimals to Danish words."""
    whole = match.group(1)
    decimal = match.group(3)
    digit_names = ["nul", "en", "to", "tre", "fire", "fem", "seks", "syv", "otte", "ni"]
    whole_words = integer_to_danish_words(whole)
    decimal_words = " ".join(digit_names[int(d)] for d in decimal)
    return f"{whole_words} komma {decimal_words}"


def normalize_integer(match: re.Match) -> str:
    """Callback to normalize standalone integers to Danish words."""
    return integer_to_danish_words(match.group(0))


def apply_phonemes(text: str) -> str:
    """
    Applies number normalization and phonetic plain-text substitution to the given string.
    """
    # Fast-path: if the text doesn't contain any digits or phonetic dictionary words, return immediately
    if not _PHONETIC_KEYS_OR_DIGIT.search(text):
        return text

    # 1. Normalize numbers with units first (e.g. 150g, 30 cm)
    text = _RE_NUM_UNIT.sub(normalize_number_unit, text)

    # 2. Normalize standalone decimals (e.g. 0,5)
    text = _RE_DECIMAL.sub(normalize_decimal, text)

    # 3. Normalize standalone integers (e.g. 2, 3)
    text = _RE_INTEGER.sub(normalize_integer, text)

    # 4. Apply general phonetic / abbreviation substitutions
    for pattern, replacement in _COMPILED_PATTERNS:
        text = pattern.sub(replacement, text)

    return text
