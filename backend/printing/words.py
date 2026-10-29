"""Amount in words with Indian digit grouping (thousand, lakh, crore).

``amount_in_words(Decimal("12345678.50"))`` ->
``"Rupees One Crore Twenty Three Lakh Forty Five Thousand Six Hundred Seventy Eight
and Fifty Paise Only"``.

English and Hindi are supported (``lang="en"`` / ``"hi"``); every other language falls
back to English, because spelled-out numbers are not regular enough in the other ten
languages to generate without a per-language table.
"""

from decimal import ROUND_HALF_UP, Decimal

LANGS = ("en", "hi")

_EN_ONES = (
    "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen "
    "Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen"
).split()
_EN_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()

# Hindi has an irregular word for every number below 100.
_HI_BELOW_100 = (
    "शून्य एक दो तीन चार पाँच छह सात आठ नौ दस "
    "ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस बीस "
    "इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस "
    "इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस "
    "इकतालीस बयालीस तैंतालीस चौवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास "
    "इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ "
    "इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर सत्तर "
    "इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उनासी अस्सी "
    "इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे "
    "इक्यानवे बानवे तिरानवे चौरानवे पंचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे"
).split()

_WORDS = {
    "en": {
        "below_100": lambda n: (
            _EN_ONES[n]
            if n < 20
            else _EN_TENS[n // 10] + (f" {_EN_ONES[n % 10]}" if n % 10 else "")
        ),
        "hundred": "Hundred",
        "thousand": "Thousand",
        "lakh": "Lakh",
        "crore": "Crore",
        "rupees": "Rupees",
        "paise": "Paise",
        "and": "and",
        "only": "Only",
        "minus": "Minus",
        "rupees_first": True,
    },
    "hi": {
        "below_100": lambda n: _HI_BELOW_100[n],
        "hundred": "सौ",
        "thousand": "हज़ार",
        "lakh": "लाख",
        "crore": "करोड़",
        "rupees": "रुपये",
        "paise": "पैसे",
        "and": "और",
        "only": "मात्र",
        "minus": "ऋण",
        "rupees_first": False,
    },
}


def _below_100(n, w):
    return w["below_100"](n)


def _below_1000(n, w):
    """Words for 0 < n < 1000 ("" for 0)."""
    parts = []
    hundreds, rest = divmod(n, 100)
    if hundreds:
        parts.append(f"{_below_100(hundreds, w)} {w['hundred']}")
    if rest:
        parts.append(_below_100(rest, w))
    return " ".join(parts)


def integer_in_words(n, lang="en"):
    """Words for a non-negative integer in the Indian system (crore recurses, so
    ``10**9`` is "One Hundred Crore")."""
    w = _WORDS.get(lang, _WORDS["en"])
    n = int(n)
    if n < 0:
        raise ValueError("integer_in_words expects a non-negative integer")
    if n == 0:
        return _below_100(0, w)
    parts = []
    crores, n = divmod(n, 10_000_000)
    if crores:
        parts.append(f"{integer_in_words(crores, lang)} {w['crore']}")
    lakhs, n = divmod(n, 100_000)
    if lakhs:
        parts.append(f"{_below_100(lakhs, w)} {w['lakh']}")
    thousands, n = divmod(n, 1000)
    if thousands:
        parts.append(f"{_below_100(thousands, w)} {w['thousand']}")
    if n:
        parts.append(_below_1000(n, w))
    return " ".join(parts)


def amount_in_words(amount, lang="en"):
    """Rupees-and-paise words of ``amount`` (rounded half-up to 2 dp).

    * ``0`` -> "Rupees Zero Only"; paise only (``0.50``) -> "Fifty Paise Only".
    * A negative amount (a credit / debit note) is prefixed "Minus".
    """
    w = _WORDS.get(lang, _WORDS["en"])
    value = Decimal(str(amount if amount is not None else 0)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    negative = value < 0
    rupees, paise = divmod(int(abs(value) * 100), 100)

    segments = []
    if rupees or not paise:
        rupee_words = integer_in_words(rupees, lang)
        segments.append(
            f"{w['rupees']} {rupee_words}"
            if w["rupees_first"]
            else f"{rupee_words} {w['rupees']}"
        )
    if paise:
        paise_words = f"{integer_in_words(paise, lang)} {w['paise']}"
        segments.append(f"{w['and']} {paise_words}" if segments else paise_words)
    text = " ".join(segments + [w["only"]])
    if negative:
        text = f"{w['minus']} {text}"
    return text
