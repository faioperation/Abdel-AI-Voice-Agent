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

def apply_phonemes(text: str) -> str:
    """
    Applies phonetic plain text substitution to the given string.
    Matches longer compound words before shorter substrings.
    """
    # Sort dictionary by length descending to match compound words first
    sorted_keys = sorted(DANISH_PHONEME_DICT.keys(), key=len, reverse=True)
    
    # Pad text with spaces to match single letter units properly
    padded_text = f" {text} "
    
    for word in sorted_keys:
        replacement = DANISH_PHONEME_DICT[word]
        if word.startswith(" ") and word.endswith(" "):
            # For exact matches with spaces (e.g., ' g ')
            pattern = re.compile(re.escape(word), flags=re.IGNORECASE)
            padded_text = pattern.sub(replacement, padded_text)
        else:
            # Word boundaries for normal words
            pattern = re.compile(r'\b' + re.escape(word) + r'\b', flags=re.IGNORECASE)
            padded_text = pattern.sub(replacement, padded_text)
            
    # Remove the artificial padding spaces
    return padded_text[1:-1]
