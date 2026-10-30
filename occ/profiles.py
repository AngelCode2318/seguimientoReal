"""Validation and the shared identity rule for entry, edits and migrations."""
import json
import re
import time
import unicodedata


def clean_text(value):
    return " ".join(unicodedata.normalize("NFC", value).split())


def profile_fields(data):
    def text(key, maximum, required=False):
        value = data.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"Revisa el campo {key}.")
        value = clean_text(value)
        if len(value) > maximum:
            raise ValueError(f"Revisa el campo {key}.")
        if required and not value:
            raise ValueError("Completa tu alias y la identificación del auto.")
        return value

    alias = text("alias", 40, True)
    car = text("car", 80, True)
    color = text("color", 30)
    year = data.get("year")
    if isinstance(year, str):
        year = year.strip()
    if year in (None, ""):
        year = None
    else:
        if isinstance(year, bool) or not re.fullmatch(r"\d{4}", str(year)):
            raise ValueError("Escribe un año de cuatro dígitos.")
        year = int(year)
        if not 1886 <= year <= time.gmtime().tm_year + 1:
            raise ValueError("Revisa el año de tu auto.")
    return dict(alias=alias, car=car, year=year, color=color)


def identity_key(profile):
    # JSON avoids collisions between field boundaries; accents are preserved.
    return json.dumps([
        clean_text(profile["alias"]).casefold(),
        clean_text(profile["car"]).casefold(),
        profile["year"],
        clean_text(profile["color"]).casefold(),
    ], ensure_ascii=False, separators=(",", ":"))
