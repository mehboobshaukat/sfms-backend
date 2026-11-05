"""ISO country and subdivision metadata + ISO 4217 currency for UI display.

Amounts are NOT converted when country changes: the application currently
stores numeric amounts without per-transaction currency identifiers. Display
symbols are metadata only; a real conversion requires an FX migration.
"""
from functools import lru_cache

from babel.numbers import get_territory_currencies, get_currency_symbol
from babel.core import UnknownLocaleError
import pycountry

DEFAULT_CURRENCY_CODE = "PKR"  # legacy accounts previously displayed PKR
DEFAULT_CURRENCY_SYMBOL = "Rs"
# CLDR's English locale sometimes returns an ISO code (e.g. AED) where a
# commonly used script-based sign is available. Prefer the local sign.
SYMBOL_OVERRIDES = {
    "PKR": "Rs",
    "AED": "د.إ",
    "SAR": "ر.س",
    "QAR": "ر.ق",
    "KWD": "د.ك",
    "BHD": "د.ب",
    "OMR": "ر.ع.",
}


@lru_cache(maxsize=300)
def country_metadata(country_code: str) -> dict | None:
    """Return a supported ISO 3166-1 alpha-2 country with an active currency."""
    if not isinstance(country_code, str):
        return None
    code = country_code.strip().upper()
    country = pycountry.countries.get(alpha_2=code)
    if not country:
        return None
    currencies = get_territory_currencies(code, tender=True)
    if not currencies:
        return None  # e.g. uninhabited territories without a legal currency
    currency_code = currencies[0]
    try:
        symbol = get_currency_symbol(currency_code, locale=f"en_{code}")
    except (ValueError, LookupError, UnknownLocaleError):
        symbol = get_currency_symbol(currency_code, locale="en")
    return {
        "code": code,
        "name": country.name,
        "currency_code": currency_code,
        "currency_symbol": SYMBOL_OVERRIDES.get(currency_code, symbol),
    }


def require_country(country_code: str) -> str:
    country = country_metadata(country_code)
    if not country:
        raise ValueError("Choose a valid two-letter ISO country code with an active currency (e.g. PK, US, GB).")
    return country["code"]


def countries_list() -> list[dict]:
    return sorted(
        (item for country in pycountry.countries if (item := country_metadata(country.alpha_2))),
        key=lambda item: item["name"],
    )


def states_for(country_code: str) -> list[dict]:
    """ISO 3166-2 subdivisions where published; free-text state stays valid."""
    code = require_country(country_code)
    return sorted(
        ({"code": entry.code, "name": entry.name, "type": entry.type}
         for entry in pycountry.subdivisions.get(country_code=code)),
        key=lambda item: item["name"],
    )
