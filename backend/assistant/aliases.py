"""Common aliases, unit mappings, honorific stripping and Hindi number words.

Supports Nidhi Masala spice naming conventions, packing units, and spoken Hindi numbers.
"""

from decimal import Decimal
from typing import Dict, List, Optional, Set

# Honorifics in English and Hindi script
HONORIFICS: Set[str] = {
    "ji",
    "sahab",
    "saheb",
    "bhai",
    "seth",
    "shree",
    "shri",
    "जी",
    "साहब",
    "साहेब",
    "भाई",
    "सेठ",
    "श्री",
}

# Spice item aliases (canonical term -> list of alias terms)
ITEM_ALIASES: Dict[str, List[str]] = {
    "haldi": ["turmeric", "haldi powder", "haldi sabut", "हल्दी"],
    "mirchi": ["chilli", "mirch", "red chilli", "lal mirch", "mirchi powder", "मिर्ची"],
    "dhaniya": ["coriander", "dhaniya powder", "dhaniya sabut", "धनिया"],
    "jeera": ["cumin", "jeera sabut", "zeera", "जीरा"],
    "rai": ["mustard", "rai sabut", "sarson", "राई"],
}

# Unit aliases mapping colloquial Hindi units to ERP UOM names
UNIT_ALIASES: Dict[str, str] = {
    "peti": "Box",
    "पेटी": "Box",
    "katta": "Bag",
    "कट्टा": "Bag",
    "bori": "Bag",
    "बोरी": "Bag",
    "kilo": "Kg",
    "kg": "Kg",
    "किलो": "Kg",
    "gram": "Gram",
    "gm": "Gram",
    "ग्राम": "Gram",
    "quintal": "Quintal",
    "क्विंटल": "Quintal",
    "packet": "Packet",
    "पैकेट": "Packet",
    "dozen": "Dozen",
    "दर्जन": "Dozen",
}

# Hindi number words
HINDI_NUMBER_WORDS: Dict[str, Decimal] = {
    "adha": Decimal("0.5"),
    "आधा": Decimal("0.5"),
    "paun": Decimal("0.75"),
    "paune": Decimal("0.75"),
    "पौने": Decimal("0.75"),
    "ek": Decimal("1"),
    "एक": Decimal("1"),
    "sava": Decimal("1.25"),
    "सवा": Decimal("1.25"),
    "dedh": Decimal("1.5"),
    "डेढ़": Decimal("1.5"),
    "do": Decimal("2"),
    "दो": Decimal("2"),
    "dhai": Decimal("2.5"),
    "ढाई": Decimal("2.5"),
    "teen": Decimal("3"),
    "तीन": Decimal("3"),
    "char": Decimal("4"),
    "चार": Decimal("4"),
    "paanch": Decimal("5"),
    "पांच": Decimal("5"),
    "sau": Decimal("100"),
    "सौ": Decimal("100"),
    "hazaar": Decimal("1000"),
    "hazar": Decimal("1000"),
    "हज़ार": Decimal("1000"),
    "lakh": Decimal("100000"),
    "लाख": Decimal("100000"),
}


def strip_honorifics(text: str) -> str:
    """Remove honorific suffixes/prefixes like ji, sahab, bhai, seth."""
    if not text:
        return ""
    words = text.split()
    cleaned = [w for w in words if w.lower().strip(".,:;!?") not in HONORIFICS]
    return " ".join(cleaned)


def parse_hindi_quantity(text: str) -> Optional[Decimal]:
    """Parse Hindi number words and compound phrases into a Decimal quantity.

    Examples:
    - "dedh" -> 1.5
    - "dhai" -> 2.5
    - "sava" -> 1.25
    - "paune do" -> 1.75
    - "sava do" -> 2.25
    - "dhai sau" -> 250
    - "dedh sau" -> 150
    - "dedh hazaar" -> 1500
    - "dhai hazaar" -> 2500
    - "adha" -> 0.5
    """
    if not text:
        return None
    text = text.lower().strip()

    # Exact single word match
    if text in HINDI_NUMBER_WORDS:
        return HINDI_NUMBER_WORDS[text]

    words = text.split()
    if not words:
        return None

    # Compound cases:
    # "dhai sau" = 2.5 * 100 = 250
    # "dedh sau" = 1.5 * 100 = 150
    # "dedh hazaar" = 1.5 * 1000 = 1500
    # "dhai hazaar" = 2.5 * 1000 = 2500
    if len(words) == 2:
        w1, w2 = words[0], words[1]
        if w1 in ("dedh", "डेढ़") and w2 in ("sau", "सौ"):
            return Decimal("150")
        if w1 in ("dhai", "ढाई") and w2 in ("sau", "सौ"):
            return Decimal("250")
        if w1 in ("dedh", "डेढ़") and w2 in ("hazaar", "hazar", "हज़ार"):
            return Decimal("1500")
        if w1 in ("dhai", "ढाई") and w2 in ("hazaar", "hazar", "हज़ार"):
            return Decimal("2500")

        # "sava do" = 2.25 (sava + 1 extra whole, or sava n = n + 0.25)
        # In Hindi: "sava do" = 2.25, "sava teen" = 3.25
        if w1 in ("sava", "सवा") and w2 in HINDI_NUMBER_WORDS:
            base = HINDI_NUMBER_WORDS[w2]
            return base + Decimal("0.25")

        # "paune do" = 1.75 (0.25 less than 2)
        # "paune teen" = 2.75 (0.25 less than 3)
        if w1 in ("paune", "paun", "पौने") and w2 in HINDI_NUMBER_WORDS:
            base = HINDI_NUMBER_WORDS[w2]
            return base - Decimal("0.25")

        # Standard multiplier: "do sau" = 200, "paanch hazaar" = 5000
        if w1 in HINDI_NUMBER_WORDS and w2 in ("sau", "सौ", "hazaar", "hazar", "हज़ार", "lakh", "लाख"):
            return HINDI_NUMBER_WORDS[w1] * HINDI_NUMBER_WORDS[w2]

    return None
