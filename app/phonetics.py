import re

DANISH_PHONEME_DICT = {
    # REGEL 12 - ITALIENSK / FREMMED
    "mascarpone": "maskarpåne",
    "mozzarella": "motsarella",
    "gorgonzola": "gorgonsola",
    "parmesanost": "parmesaanost",
    "parmesan": "parmesaan",
    "margherita": "margerita",
    "bresaola": "bresola",
    "prosciutto": "prosjutto",
    "focacciabolle": "fokattsjabolle",
    "focaccia": "fokattsja",
    "quattro stagioni": "kvattro stasjoni",
    "funghi": "fungi",
    "calzone": "kaltsone",
    "diavola": "djavola",
    "pepperoni": "pepperroni",
    "pastrami": "pastrami",
    "penne": "penne",
    "spaghetti": "spaghetti",

    # SPANSK / MEXICANSK
    "jalapeños": "halapenyos",
    "jalapeño": "halapenyo",
    "chorizo": "tjorisso",
    "guacamole": "gvakamole",
    "salsa": "salsa",

    # FRANSK
    "bearnaisesovs": "bearnæsesovs",
    "bearnaisesauce": "bearnæsesovs",
    "bearnaise": "bearnæse",
    "creme fraiche": "kremfresh",
    "vinaigrette": "vinægrette",
    "remoulade": "remoulade",
    "salatmayonnaise": "salatmajonnæse",
    "mayonnaise": "majonnæse",

    # ENGELSK VARENAVN
    "mushroom": "masjrum",
    "chicken nuggets": "tjikken naggets",
    "hotwings": "hotvings",
    "snackboks": "snakboks",
    "icebergsalat": "ajsbergsalat",
    "cherrytomater": "tjerrytomater",
    "burger": "borger",
    "bacon": "bæjkon",
    "cheddar": "sjeddar",
    "coleslaw": "kålslå",
    "caesar": "seeser",
    "croutoner": "krutoner",
    "onion rings": "ånjonrings",
    "milkshakes": "milksjæjks",
    "milkshake": "milksjæjk",
    "sandwich": "sændvitsj",
    "rancher": "rantsjer",
    "sprite": "spræjt",
    "coca-cola": "koka kola",
    "cola zero": "kola siro",
    "veggie": "vedsji",
    "de luxe": "de lyks",
    "crispy": "krispi",
    "bbq": "bibi kjuh",
    "chilimayo": "tjilimajo",
    "chili": "tjili",

    # DANSK/FREMMED BLANDET
    "champignoner": "sjampinjonger",
    "champignon": "sjampinjong",
    "pommes frites": "pomfrit",
    "falafel": "falaffel",
    "aioli": "ajoli",
    "avokado": "avokado",
    "rucola": "rukola",
    "fiskefilet": "fiskefilæ",
    "trøffelflødesauce": "trøffelflødesovs",
    "tomatflødesauce": "tomatflødesovs",
    "basilikumsflødesauce": "basilikumsflødesovs",
    "basilikumspesto": "basilikumspesto",
    "hvidløgsdressing": "hvidløgsdresing",
    "gran biraghi": "gran biragi",
    "parmaskinke": "parmaskinke",
    "hjemmelavet": "hjemmelavet",
    "hvidløgssolie": "hvidløgsolie",
    "trøffelolie": "trøffelolie",
    "bådekartofler": "bådekartofler",
    "vildmosekartofler": "vildmosekartofler",
    "kødboller": "kødboller",
    "halvgrill": "halvgril",
    "peberfrugt": "peberfrugt",
    "kyllingenuggets": "kyllingnaggetz",

    # REGEL 12B - FORKORTELSER FORBUDT
    "kr.": "kroner",
    "pr.": "per",
    "stk.": "styk",
    "alm.": "almindelig",
    "ca.": "cirka",
    "bl.a.": "blandt andet",
    "inkl.": "inklusive",
    "ekskl.": "eksklusive",
    "tlf.": "telefonnummer",
    "nr.": "nummer",
    " cl ": " centiliter ",
    " l ": " liter ",
    " cm ": " centimeter ",
    " kg ": " kilo ",
    " g ": " gram ",
}

# ── Pre-compile all patterns once at module load ──────────────────────────────
# Sort longest-first so compound words match before their substrings.
# Compiling here instead of inside apply_phonemes() saves ~0.012ms per call —
# negligible alone but adds up across hundreds of streaming token chunks per call.
_COMPILED_PATTERNS: list[tuple[re.Pattern, str]] = []

for _word in sorted(DANISH_PHONEME_DICT.keys(), key=len, reverse=True):
    _replacement = DANISH_PHONEME_DICT[_word]
    if _word.startswith(" ") and _word.endswith(" "):
        # Unit abbreviations like " g " — match exact spacing
        _compiled_patterns_entry = (
            re.compile(re.escape(_word), flags=re.IGNORECASE),
            _replacement,
        )
    else:
        _compiled_patterns_entry = (
            re.compile(r"\b" + re.escape(_word) + r"\b", flags=re.IGNORECASE),
            _replacement,
        )
    _COMPILED_PATTERNS.append(_compiled_patterns_entry)


def apply_phonemes(text: str) -> str:
    """
    Applies phonetic plain-text substitution to the given string.
    Patterns are pre-compiled at module load; this function is just substitution.
    """
    # Pad with spaces so unit abbreviations like " g " match at string edges
    padded = f" {text} "
    for pattern, replacement in _COMPILED_PATTERNS:
        padded = pattern.sub(replacement, padded)
    return padded[1:-1]
