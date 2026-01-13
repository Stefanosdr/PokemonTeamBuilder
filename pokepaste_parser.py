import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests


_STAT_ABBR_TO_KEY: dict[str, str] = {
    "HP": "hp",
    "Atk": "atk",
    "Def": "def",
    "SpA": "spa",
    "SpD": "spd",
    "Spe": "spe",
}


def pokepaste_to_raw_url(url: str) -> str:
    """Convert a Pokepaste URL to its /raw endpoint."""
    u = url.strip()
    if not u:
        raise ValueError("Empty URL")

    parsed = urlparse(u)
    if not parsed.scheme:
        parsed = urlparse("https://" + u)

    path = parsed.path.rstrip("/")
    if not path:
        raise ValueError(f"Invalid Pokepaste URL: {url}")

    if path.endswith("/raw"):
        raw_path = path
    else:
        raw_path = path + "/raw"

    return urlunparse((parsed.scheme, parsed.netloc, raw_path, "", "", ""))


def fetch_pokepaste_team_text(url: str, *, timeout_seconds: float = 30.0) -> str:
    raw_url = pokepaste_to_raw_url(url)
    resp = requests.get(raw_url, timeout=timeout_seconds)
    resp.raise_for_status()
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def parse_showdown_team_text(team_text: str) -> dict[str, list[dict[str, Any]]]:
    """Parse Pokemon Showdown export text into a dict keyed by species.

    If multiple sets share the same species, the value is a list of sets.
    """
    normalized = team_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return {}

    blocks = [b.strip() for b in re.split(r"\n\s*\n", normalized) if b.strip()]

    out: dict[str, list[dict[str, Any]]] = {}
    for block in blocks:
        s = parse_showdown_set(block)
        species = s.get("species")
        if not isinstance(species, str) or not species:
            continue

        for k in ["species", "nickname", "gender", "happiness", "ivs"]:
            s.pop(k, None)
        out.setdefault(species, []).append(s)

    return out


def _blank_stats() -> dict[str, int]:
    return {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}


def _parse_stat_line(text: str) -> dict[str, int]:
    # Example: "252 Atk / 4 SpD / 252 Spe"
    stats = _blank_stats()
    for part in text.split("/"):
        p = part.strip()
        m = re.match(r"^(\d+)\s+([A-Za-z]+)$", p)
        if not m:
            continue
        val = int(m.group(1))
        abbr = m.group(2)
        key = _STAT_ABBR_TO_KEY.get(abbr)
        if key is None:
            continue
        stats[key] = val
    return stats


def parse_showdown_set(set_text: str) -> dict[str, Any]:
    lines = [ln.rstrip() for ln in set_text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        raise ValueError("Empty set")

    header = lines[0]
    species, nickname, gender, item = _parse_header(header)

    set_data: dict[str, Any] = {
        "species": species,
        "nickname": nickname,
        "gender": gender,
        "item": item,
        "ability": None,
        "tera_type": None,
        "level": None,
        "shiny": None,
        "happiness": None,
        "evs": None,
        "ivs": None,
        "nature": None,
        "moves": [],
    }

    for ln in lines[1:]:
        if ln.startswith("-"):
            move = ln.lstrip("- ").strip()
            if move:
                set_data["moves"].append(move)
            continue

        if ln.startswith("Ability:"):
            set_data["ability"] = ln.split(":", 1)[1].strip() or None
            continue

        if ln.startswith("Tera Type:"):
            set_data["tera_type"] = ln.split(":", 1)[1].strip() or None
            continue

        if ln.startswith("Level:"):
            try:
                set_data["level"] = int(ln.split(":", 1)[1].strip())
            except ValueError:
                set_data["level"] = None
            continue

        if ln.startswith("Shiny:"):
            val = ln.split(":", 1)[1].strip().lower()
            set_data["shiny"] = val in {"yes", "true", "1"}
            continue

        if ln.startswith("Happiness:"):
            try:
                set_data["happiness"] = int(ln.split(":", 1)[1].strip())
            except ValueError:
                set_data["happiness"] = None
            continue

        if ln.startswith("EVs:"):
            set_data["evs"] = _parse_stat_line(ln.split(":", 1)[1].strip())
            continue

        if ln.startswith("IVs:"):
            set_data["ivs"] = _parse_stat_line(ln.split(":", 1)[1].strip())
            continue

        if ln.startswith("Nature:"):
            set_data["nature"] = ln.split(":", 1)[1].strip() or None
            continue

        if ln.endswith(" Nature"):
            set_data["nature"] = ln.replace(" Nature", "").strip() or None
            continue

    return set_data


def _parse_header(header: str) -> tuple[str, str | None, str | None, str | None]:
    # Header examples:
    #  - "Arbok @ Leftovers"
    #  - "Nickname (Arbok) @ Leftovers"
    #  - "Arbok (F) @ Leftovers"
    #  - "Nickname (Arbok) (F) @ Leftovers" (rare)

    item = None
    left = header
    if " @ " in header:
        left, item = header.split(" @ ", 1)
        item = item.strip() or None

    left = left.strip()

    gender = None
    m_gender = re.match(r"^(.*)\s+\((M|F)\)$", left)
    if m_gender:
        left = m_gender.group(1).strip()
        gender = m_gender.group(2)

    nickname = None
    species = left

    # If we have "Nickname (Species)", interpret parentheses as species.
    m = re.match(r"^(?P<nick>.+?)\s+\((?P<species>.+)\)$", left)
    if m:
        nickname = m.group("nick").strip() or None
        species = m.group("species").strip()

    return species, nickname, gender, item


def team_dict_to_json(team: dict[str, list[dict[str, Any]]], *, indent: int = 2) -> str:
    return json.dumps(team, indent=indent, ensure_ascii=False)


def parse_pokepaste(url: str, *, timeout_seconds: float = 30.0) -> dict[str, list[dict[str, Any]]]:
    text = fetch_pokepaste_team_text(url, timeout_seconds=timeout_seconds)
    return parse_showdown_team_text(text)
