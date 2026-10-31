"""Transliteration and phonetic normalization for Hindi (Devanagari) to Latin.

Converts Devanagari script into Latin letters and applies phonetic normalization:
1. Lowercase
2. Collapse 'aa' -> 'a'
3. Collapse 'ee' -> 'i'
4. Collapse 'sh' -> 's'
5. Collapse 'v' -> 'w'
6. Drop trailing 'a' on words (Hindi schwa deletion)
"""

import re
from typing import Dict

# Independent vowels
INDEPENDENT_VOWELS: Dict[str, str] = {
    "\u0905": "a",   # अ
    "\u0906": "aa",  # आ
    "\u0907": "i",   # इ
    "\u0908": "ee",  # ई
    "\u0909": "u",   # उ
    "\u090a": "oo",  # ऊ
    "\u090b": "ri",  # ऋ
    "\u090e": "e",   # ऎ
    "\u090f": "e",   # ए
    "\u0910": "ai",  # ऐ
    "\u0912": "o",   # ऒ
    "\u0913": "o",   # ओ
    "\u0914": "au",  # औ
}

# Dependent vowel signs (matras)
MATRAS: Dict[str, str] = {
    "\u093e": "aa",  # ा
    "\u093f": "i",   # ि
    "\u0940": "ee",  # ी
    "\u0941": "u",   # ु
    "\u0942": "oo",  # ू
    "\u0943": "ri",  # ृ
    "\u0946": "e",   # ॆ
    "\u0947": "e",   # े
    "\u0948": "ai",  # ै
    "\u094a": "o",   # ॊ
    "\u094b": "o",   # ो
    "\u094c": "au",  # ौ
}

# Consonants (base consonant without vowel)
CONSONANTS: Dict[str, str] = {
    "\u0915": "k",   # क
    "\u0916": "kh",  # ख
    "\u0917": "g",   # ग
    "\u0918": "gh",  # घ
    "\u0919": "ng",  # ङ
    "\u091a": "ch",  # च
    "\u091b": "chh", # छ
    "\u091c": "j",   # ज
    "\u091d": "jh",  # झ
    "\u091e": "ny",  # ञ
    "\u091f": "t",   # ट
    "\u0920": "th",  # ठ
    "\u0921": "d",   # ड
    "\u0922": "dh",  # ढ
    "\u0923": "n",   # ण
    "\u0924": "t",   # त
    "\u0925": "th",  # थ
    "\u0926": "d",   # द
    "\u0927": "dh",  # ध
    "\u0928": "n",   # न
    "\u092a": "p",   # प
    "\u092b": "ph",  # फ
    "\u092c": "b",   # ब
    "\u092d": "bh",  # भ
    "\u092e": "m",   # म
    "\u092f": "y",   # य
    "\u0930": "r",   # र
    "\u0932": "l",   # ल
    "\u0933": "l",   # ळ
    "\u0935": "v",   # व
    "\u0936": "sh",  # श
    "\u0937": "sh",  # ष
    "\u0938": "s",   # स
    "\u0939": "h",   # ह
    "\u0958": "q",   # क़
    "\u0959": "kh",  # ख़
    "\u095a": "g",   # ग़
    "\u095b": "z",   # ज़
    "\u095c": "d",   # ड़
    "\u095d": "rh",  # ढ़
    "\u095e": "f",   # फ़
}

VIRAMA = "\u094d"  # ् (halant)
NUKTA = "\u093c"   # ़

# Nukta consonant mappings
NUKTA_CONSONANTS: Dict[str, str] = {
    "\u0915": "q",   # क + ़
    "\u0916": "kh",  # ख + ़
    "\u0917": "g",   # ग + ़
    "\u091c": "z",   # ज + ़
    "\u0921": "d",   # ड + ़
    "\u0922": "rh",  # ढ + ़
    "\u092b": "f",   # फ + ़
}

SPECIAL: Dict[str, str] = {
    "\u0902": "n",   # Anusvara ं
    "\u0901": "n",   # Chandrabindu ँ
    "\u0903": "h",   # Visarga ः
}


def devanagari_to_latin(text: str) -> str:
    """Transliterate Devanagari characters to Latin letters."""
    if not text:
        return ""
    result = []
    i = 0
    n = len(text)
    while i < n:
        char = text[i]

        if char in INDEPENDENT_VOWELS:
            result.append(INDEPENDENT_VOWELS[char])
            i += 1
        elif char in CONSONANTS:
            # Check for nukta
            cons_sound = CONSONANTS[char]
            if i + 1 < n and text[i + 1] == NUKTA:
                cons_sound = NUKTA_CONSONANTS.get(char, cons_sound)
                i += 1

            # Check next character for virama or matra
            if i + 1 < n and text[i + 1] == VIRAMA:
                result.append(cons_sound)
                i += 2  # consume consonant and virama
            elif i + 1 < n and text[i + 1] in MATRAS:
                result.append(cons_sound + MATRAS[text[i + 1]])
                i += 2  # consume consonant and matra
            else:
                # Default inherent 'a'
                result.append(cons_sound + "a")
                i += 1
        elif char in MATRAS:
            result.append(MATRAS[char])
            i += 1
        elif char in SPECIAL:
            result.append(SPECIAL[char])
            i += 1
        elif char == VIRAMA:
            # Standalone virama (already handled above if after consonant)
            i += 1
        else:
            result.append(char)
            i += 1

    return "".join(result)


def normalize_latin(text: str) -> str:
    """Normalize Latin string phonetically:

    - Lowercase
    - Collapse 'aa' to 'a'
    - Collapse 'ee' to 'i'
    - Collapse 'sh' to 's'
    - Collapse 'v' to 'w'
    - Drop trailing 'a' on words
    """
    if not text:
        return ""
    text = text.lower()
    # Collapse aa -> a
    text = re.sub(r"aa+", "a", text)
    # Collapse ee -> i
    text = re.sub(r"ee+", "i", text)
    # Collapse oo -> u
    text = re.sub(r"oo+", "u", text)
    # Collapse ou / ow -> au
    text = re.sub(r"o[uw]", "au", text)
    # Collapse sh -> s
    text = re.sub(r"sh", "s", text)
    # Collapse v -> w
    text = re.sub(r"v", "w", text)
    # Drop trailing 'a' on words (e.g. sharma -> sharm, haldi -> haldi, ram -> ram)
    text = re.sub(r"\b([a-z0-9]+?)a\b", r"\1", text)
    return text.strip()


def to_latin(text: str) -> str:
    """Convert Devanagari to Latin and phonetically normalise."""
    if not text:
        return ""
    raw_latin = devanagari_to_latin(text)
    return normalize_latin(raw_latin)
