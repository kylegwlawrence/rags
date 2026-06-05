"""Static data tables and small data-only helpers used across the render package."""

import re

IMAGE_FIELD_PREFIXES = frozenset(
    {
        "image",
        "img",
        "logo",
        "flag",
        "coat",
        "map",
        "photo",
        "picture",
        "banner",
        "seal",
        "shield",
        "emblem",
        "signature",
        "sound",
        "audio",
        "video",
    }
)

TAXONOMY_TEMPLATE_NAMES = frozenset({"speciesbox", "taxobox", "automatic taxobox"})

IMAGE_VALUE_RE = re.compile(
    r"^\s*\S+\.(jpe?g|png|svg|gif|webp|tiff?|ogg|ogv|oga|wav|mp[34]|flac|webm)\s*$",
    re.IGNORECASE,
)

MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

CITE_TEMPLATE_PREFIXES = ("cite ", "citation")

# Templates whose first positional arg is the content to display. Anything
# else they accept (author attribution, styling flags) is dropped — better
# to lose attribution than to lose the quoted text. Used by
# render.templates.convert_passthrough_first_arg_templates.
PASSTHROUGH_FIRST_ARG_TEMPLATES = frozenset(
    {
        "quote",
        "cquote",
        "bquote",
        "pull quote",
        "centered pull quote",
        "talk quote",
        "blockquote2",
        "respell",
    }
)

# HTML-rendered math: bodies are wikitext (apostrophe-italics, <sup>, {{=}} …)
# that MediaWiki wraps in <span class="texhtml">. NOT LaTeX.
HTML_MATH_TEMPLATE_NAMES = {"math", "math block", "bigmath"}
# Math-variable shorthand: body is always italic (single variable name).
MVAR_TEMPLATE_NAMES = {"mvar"}
# LaTeX-rendered math: body is raw LaTeX, suitable for KaTeX via <math>…</math>.
LATEX_MATH_TEMPLATE_NAMES = {"tmath", "tmath block"}
# Union used by the pre-pass regex in render.templates.
MATH_TEMPLATE_NAMES = HTML_MATH_TEMPLATE_NAMES | MVAR_TEMPLATE_NAMES | LATEX_MATH_TEMPLATE_NAMES

# Map of indicator template -> (display text, css class)
INDICATORS: dict[str, tuple[str, str]] = {
    "yes": ("Yes", "indicator-yes"),
    "y": ("Yes", "indicator-yes"),
    "tick": ("Yes", "indicator-yes"),
    "checked": ("Yes", "indicator-yes"),
    "no": ("No", "indicator-no"),
    "n": ("No", "indicator-no"),
    "x": ("No", "indicator-no"),
    "cross": ("No", "indicator-no"),
    "partial": ("Partial", "indicator-partial"),
    "some": ("Partial", "indicator-partial"),
    "dunno": ("Unknown", "indicator-unknown"),
    "unknown": ("Unknown", "indicator-unknown"),
    "?": ("Unknown", "indicator-unknown"),
    "n/a": ("N/A", "indicator-na"),
    "na": ("N/A", "indicator-na"),
    "included": ("Included", "indicator-yes"),
    "dropped": ("Dropped", "indicator-no"),
    "pending": ("Pending", "indicator-partial"),
    "increase": ("▲", "indicator-increase"),
    "decrease": ("▼", "indicator-decrease"),
    "steady": ("→", "indicator-steady"),
    "positive": ("▲", "indicator-increase"),
    "negative": ("▼", "indicator-decrease"),
    "increasenegative": ("▲", "indicator-increase-negative"),
    "decreasepositive": ("▼", "indicator-decrease-positive"),
}

LANG_NAMES: dict[str, str] = {
    "af": "Afrikaans",
    "ar": "Arabic",
    "az": "Azerbaijani",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "bs": "Bosnian",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "eo": "Esperanto",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "ga": "Irish",
    "gl": "Galician",
    "gu": "Gujarati",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "hy": "Armenian",
    "id": "Indonesian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "ka": "Georgian",
    "kk": "Kazakh",
    "km": "Khmer",
    "kn": "Kannada",
    "ko": "Korean",
    "ku": "Kurdish",
    "ky": "Kyrgyz",
    "la": "Latin",
    "lb": "Luxembourgish",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mk": "Macedonian",
    "ml": "Malayalam",
    "mn": "Mongolian",
    "mr": "Marathi",
    "ms": "Malay",
    "mt": "Maltese",
    "my": "Burmese",
    "nb": "Norwegian Bokmål",
    "ne": "Nepali",
    "nl": "Dutch",
    "nn": "Norwegian Nynorsk",
    "no": "Norwegian",
    "pa": "Punjabi",
    "pl": "Polish",
    "ps": "Pashto",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sc": "Sardinian",
    "sd": "Sindhi",
    "si": "Sinhala",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sq": "Albanian",
    "sr": "Serbian",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "tg": "Tajik",
    "th": "Thai",
    "tk": "Turkmen",
    "tl": "Filipino",
    "tr": "Turkish",
    "tt": "Tatar",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "uz": "Uzbek",
    "vi": "Vietnamese",
    "yi": "Yiddish",
    "zh": "Chinese",
    "zu": "Zulu",
}


UNIT_NAMES: dict[str, str] = {
    # Length
    "m": "m",
    "km": "km",
    "mi": "mi",
    "ft": "ft",
    "in": "in",
    "cm": "cm",
    "mm": "mm",
    "nm": "nm",
    "um": "µm",
    "yd": "yd",
    "nmi": "nmi",
    "ly": "ly",
    "au": "AU",
    "pc": "pc",
    # Area
    "sqmi": "sq mi",
    "sqkm": "sq km",
    "km2": "km²",
    "m2": "m²",
    "ft2": "sq ft",
    "sqft": "sq ft",
    "sqin": "sq in",
    "sqyd": "sq yd",
    "ha": "ha",
    "acre": "acres",
    "sqm": "m²",
    "sqnmi": "sq nmi",
    # Mass
    "kg": "kg",
    "lb": "lb",
    "lbs": "lb",
    "oz": "oz",
    "g": "g",
    "mg": "mg",
    "t": "t",
    "st": "st",
    "ton": "tons",
    "LT": "long tons",
    "ST": "short tons",
    "MT": "Mt",
    # Speed
    "mph": "mph",
    "kph": "km/h",
    "km/h": "km/h",
    "kn": "kn",
    "knot": "kn",
    "knots": "kn",
    "mps": "m/s",
    "m/s": "m/s",
    "fps": "ft/s",
    # Temperature
    "C": "°C",
    "F": "°F",
    "K": "K",
    # Volume
    "l": "L",
    "L": "L",
    "ml": "mL",
    "mL": "mL",
    "cl": "cL",
    "dl": "dL",
    "gal": "gal",
    "impgal": "imp gal",
    "usgal": "US gal",
    "floz": "fl oz",
    "cuft": "cu ft",
    "cuin": "cu in",
    "cum": "m³",
    "km3": "km³",
    "m3": "m³",
    "ft3": "cu ft",
    # Power / Energy
    "W": "W",
    "kW": "kW",
    "MW": "MW",
    "GW": "GW",
    "hp": "hp",
    "J": "J",
    "kJ": "kJ",
    "MJ": "MJ",
    "GJ": "GJ",
    "cal": "cal",
    "kcal": "kcal",
    "Wh": "Wh",
    "kWh": "kWh",
    "MWh": "MWh",
    # Force / Pressure
    "N": "N",
    "kN": "kN",
    "Pa": "Pa",
    "hPa": "hPa",
    "kPa": "kPa",
    "MPa": "MPa",
    "GPa": "GPa",
    "Torr": "Torr",
    "atm": "atm",
    "bar": "bar",
    "psi": "psi",
    # Density
    "g/cm3": "g/cm³",
    "kg/m3": "kg/m³",
    "lb/ft3": "lb/cu ft",
    "mg/L": "mg/L",
    # Additional area
    "mm2": "mm²",
    "cm2": "cm²",
    # Additional speed
    "kt": "kt",
    # Population density (special compound units)
    "/sqmi": "/sq mi",
    "PD/sqmi": "/sq mi",
    "/mi2": "/sq mi",
    "/sqkm": "/km²",
    "/km2": "/km²",
    # Time
    "s": "s",
    "ms": "ms",
    "min": "min",
    "h": "h",
}


def lang_code_to_name(code: str) -> str:
    """Return the English name for an ISO 639 language code, or the code itself."""
    base = code.lower().split("-")[0].split("_")[0]
    return LANG_NAMES.get(base, code)
