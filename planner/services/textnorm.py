"""Place-name normalisation shared by the gazetteer build script and runtime lookups.

Pure Python (no Django imports) so ``scripts/build_gazetteer.py`` can use it standalone.
"""
import re

# Census "legal/statistical area description" suffixes appended to place names,
# e.g. "Abbeville city", "Abanda CDP", "Nashville-Davidson metropolitan government (balance)".
_LSAD_SUFFIX = re.compile(
    r"\s+("
    r"city and borough|consolidated government|metropolitan government|metro government|"
    r"unified government|urban county|charter township|township|city|town|village|cdp|"
    r"borough|municipality|plantation|ccd|unorganized territory|gore|grant|location|"
    r"purchase|precinct|district|comunidad|zona urbana"
    r")$",
    re.IGNORECASE,
)
_PARENS = re.compile(r"\(.*?\)")

# Canonical abbreviations so "Saint Johns" == "St. Johns", "Mount Nebo" == "Mt Nebo".
_TOKEN_MAP = {"saint": "st", "sainte": "ste", "mount": "mt", "fort": "ft"}

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}


def normalize(name: str) -> str:
    """Collapse a place name to a comparison key: 'Mc Calla' and 'McCalla' -> 'mccalla'."""
    s = _PARENS.sub(" ", name.lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s.replace("'", ""))
    tokens = [_TOKEN_MAP.get(t, t) for t in s.split()]
    return "".join(tokens)


def clean_census_name(raw: str) -> str:
    """'Oklahoma City city' -> 'Oklahoma City'; strips exactly one LSAD suffix."""
    name = _PARENS.sub("", raw).strip()
    return _LSAD_SUFFIX.sub("", name).strip() or name


def alias_names(raw: str) -> list[str]:
    """Extra lookup names for consolidated city-counties: 'Macon-Bibb County' -> 'Macon'.

    Restricted to consolidated governments so ordinary hyphenated cities such as
    'Winston-Salem' do not gain a bogus 'Winston' alias.
    """
    lowered = raw.lower()
    if not any(marker in lowered for marker in ("county", "government", "(balance)")):
        return []
    head = re.split(r"[-/]", clean_census_name(raw))[0].strip()
    return [head] if head and head != clean_census_name(raw) else []


def state_code(value: str) -> str | None:
    """'TX', 'tx' or 'Texas' -> 'TX'. Returns None for anything that is not a US state/DC."""
    v = value.strip()
    if v.upper() in US_STATES:
        return v.upper()
    return _STATE_BY_NAME.get(v.lower())
