import argparse
import gzip
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import TIER_ORDER


STATS_INDEX_URL = "https://www.smogon.com/stats/"

FORMAT_SLUGS_BY_TIER: dict[str, str] = {
    "AG": "anythinggoes",
    "Uber": "ubers",
    "OU": "ou",
    "UUBL": "uubl",
    "UU": "uu",
    "RUBL": "rubl",
    "RU": "ru",
    "NUBL": "nubl",
    "NU": "nu",
    "PUBL": "publ",
    "PU": "pu",
    "ZUBL": "zubl",
    "ZU": "zu",
}

SECTION_NAMES = {
    "Abilities",
    "Items",
    "Spreads",
    "Moves",
    "Tera Types",
    "Teammates",
    "Checks and Counters",
}


class SmogonStatsError(RuntimeError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _get_soup(session: requests.Session, url: str, *, timeout_seconds: float) -> BeautifulSoup:
    resp = session.get(url, timeout=timeout_seconds)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def _month_sort_key(folder: str) -> tuple:
    name = folder.rstrip("/")
    if len(name) < 7:
        return ("", name)
    date_part = name[:7]
    suffix = name[7:]
    return (date_part, suffix)


def get_latest_month_url(
    session: requests.Session,
    *,
    stats_index_url: str = STATS_INDEX_URL,
    timeout_seconds: float = 30.0,
) -> str:
    soup = _get_soup(session, stats_index_url, timeout_seconds=timeout_seconds)

    month_folders: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if re.fullmatch(r"\d{4}-\d{2}[^/]*/", href):
            month_folders.append(href)

    if not month_folders:
        raise SmogonStatsError(f"No month folders found at {stats_index_url}")

    month_folders.sort(key=_month_sort_key)
    return urljoin(stats_index_url, month_folders[-1])


def list_moveset_files(
    session: requests.Session,
    month_url: str,
    *,
    timeout_seconds: float = 30.0,
) -> list[tuple[str, str]]:
    moveset_url = urljoin(month_url, "moveset/")
    soup = _get_soup(session, moveset_url, timeout_seconds=timeout_seconds)

    results: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        if href.lower().endswith(".txt") or href.lower().endswith(".txt.gz"):
            results.append((href, urljoin(moveset_url, href)))
    return results


def _download_text(session: requests.Session, url: str, *, timeout_seconds: float) -> str:
    resp = session.get(url, timeout=timeout_seconds)
    resp.raise_for_status()

    raw = resp.content
    if url.lower().endswith(".gz"):
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()

    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _try_download_text(session: requests.Session, url: str, *, timeout_seconds: float) -> str | None:
    try:
        return _download_text(session, url, timeout_seconds=timeout_seconds)
    except requests.HTTPError as e:
        resp = getattr(e, "response", None)
        if resp is not None and resp.status_code == 404:
            return None
        raise


def _parse_usage_real_pct(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip("\n")
        s = line.strip()
        if not s.startswith("|"):
            continue
        if s.startswith("| Rank"):
            continue
        if re.fullmatch(r"\+[-+ ]+\+", s):
            continue

        cols = [c.strip() for c in s.strip("|").split("|")]
        if len(cols) < 7:
            continue

        pokemon = cols[1]
        real_pct = cols[-1]
        if not pokemon or not real_pct.endswith("%"):
            continue
        if pokemon.lower() == "other":
            continue

        try:
            out[pokemon.lower()] = float(real_pct.rstrip("%"))
        except ValueError:
            continue

    return out


def _is_gen9_tier_file(filename: str, *, slug: str) -> bool:
    name = filename.lower().lstrip("/")
    return name.startswith(f"gen9{slug}-") and (name.endswith(".txt") or name.endswith(".txt.gz"))


def _prefer_plain_txt(files: list[tuple[str, str]]) -> list[tuple[str, str]]:
    by_stem: dict[str, tuple[str, str]] = {}
    for file_name, url in files:
        stem = re.sub(r"\.gz$", "", file_name, flags=re.IGNORECASE)
        existing = by_stem.get(stem)
        if existing is None:
            by_stem[stem] = (file_name, url)
            continue
        if existing[0].lower().endswith(".txt.gz") and file_name.lower().endswith(".txt"):
            by_stem[stem] = (file_name, url)
    return list(by_stem.values())


def _parse_elo_from_filename(filename: str) -> int | None:
    m = re.search(r"-(\d+)\.txt(?:\.gz)?$", filename, flags=re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1))


def iter_pokemon_blocks(text: str):
    lines = text.splitlines()

    def is_sep(line: str) -> bool:
        return bool(re.fullmatch(r"\+-+\+", line.strip()))

    starts: list[int] = []
    for i in range(len(lines) - 2):
        if not is_sep(lines[i]):
            continue
        if not is_sep(lines[i + 2]):
            continue
        mid = lines[i + 1].strip()
        if not (mid.startswith("|") and mid.endswith("|")):
            continue
        inner = mid.strip("|").strip()
        if not inner:
            continue
        starts.append(i)

    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        block_lines = lines[start:end]
        pokemon = lines[start + 1].strip().strip("|").strip()
        yield pokemon, "\n".join(block_lines).rstrip() + "\n"


def _extract_simple_fields(block_text: str) -> tuple[int | None, float | None, int | None]:
    raw_count = None
    avg_weight = None
    viability_ceiling = None

    m = re.search(r"Raw count:\s*(\d+)", block_text)
    if m:
        raw_count = int(m.group(1))

    m = re.search(r"Avg\. weight:\s*([0-9.]+)", block_text)
    if m:
        avg_weight = float(m.group(1))

    m = re.search(r"Viability Ceiling:\s*(\d+)", block_text)
    if m:
        viability_ceiling = int(m.group(1))

    return raw_count, avg_weight, viability_ceiling


def _iter_cell_text_lines(block_text: str) -> list[str]:
    out: list[str] = []
    for raw in block_text.splitlines():
        line = raw.rstrip("\n")
        if re.fullmatch(r"\+-+\+", line.strip()):
            continue
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        cell = s[1:-1].strip()
        if cell:
            out.append(cell)
    return out


def parse_block_sections(block_text: str) -> dict[str, list[str]]:
    lines = _iter_cell_text_lines(block_text)
    sections: dict[str, list[str]] = {}
    current: str | None = None

    for line in lines:
        if line in SECTION_NAMES:
            current = line
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        if line in SECTION_NAMES:
            current = line
            sections.setdefault(current, [])
            continue
        sections[current].append(line)

    return sections


def _parse_pct_entry(line: str) -> tuple[str, float] | None:
    m = re.match(r"^(?P<name>.+?)\s+(?P<pct>\d+\.\d+)%$", line)
    if not m:
        return None
    return m.group("name").strip(), float(m.group("pct"))


def _parse_spread(line: str) -> dict | None:
    m = re.match(r"^(?P<nature>[^:]+):(?P<evs>\d+/\d+/\d+/\d+/\d+/\d+)\s+(?P<pct>\d+\.\d+)%$", line)
    if not m:
        return None
    nature = m.group("nature").strip()
    evs = m.group("evs")
    parts = [int(x) for x in evs.split("/")]
    if len(parts) != 6:
        return None
    return {
        "nature": nature,
        "hp": parts[0],
        "atk": parts[1],
        "def": parts[2],
        "spa": parts[3],
        "spd": parts[4],
        "spe": parts[5],
        "usage_pct": float(m.group("pct")),
        "spread": f"{nature}:{evs}",
    }


def _parse_check_counter_header(line: str) -> dict | None:
    m = re.match(
        r"^(?P<target>.+?)\s+(?P<score>\d+\.\d+)\s+\((?P<win>\d+\.\d+)\s*(?:±|\+/-|\+-|\uFFFD)\s*(?P<margin>\d+\.\d+)\)\s*$",
        line,
    )
    if not m:
        return None
    return {
        "target": m.group("target").strip(),
        "score": float(m.group("score")),
        "win": float(m.group("win")),
        "margin": float(m.group("margin")),
        "detail": None,
        "koed": None,
        "switched_out": None,
    }


def _parse_check_counter_detail(line: str) -> dict:
    m = re.search(
        r"\((?P<koed>\d+(?:\.\d+)?)%\s*KOed\s*/\s*(?P<sw>\d+(?:\.\d+)?)%\s*switched out\)",
        line,
    )
    if not m:
        return {"detail": line, "koed": None, "switched_out": None}
    return {"detail": line, "koed": float(m.group("koed")), "switched_out": float(m.group("sw"))}


def _parse_pct_section(rows: list[str], *, field_name: str) -> list[dict]:
    out: list[dict] = []
    for line in rows:
        parsed = _parse_pct_entry(line)
        if not parsed:
            continue
        name, pct = parsed
        out.append({field_name: name, "usage_pct": pct})
    return out


def _parse_checks_counters(rows: list[str]) -> list[dict]:
    checks: list[dict] = []
    i = 0
    while i < len(rows):
        header = _parse_check_counter_header(rows[i])
        if not header:
            i += 1
            continue
        if i + 1 < len(rows):
            detail = _parse_check_counter_detail(rows[i + 1])
            header["detail"] = detail.get("detail")
            header["koed"] = detail.get("koed")
            header["switched_out"] = detail.get("switched_out")
            i += 2
        else:
            i += 1
        checks.append(
            {
                "target_name": header["target"],
                "score": header["score"],
                "win": header["win"],
                "margin": header["margin"],
                "koed_pct": header["koed"],
                "switched_out_pct": header["switched_out"],
                "detail_text": header["detail"],
            }
        )
    return checks


def _clean_sections(sections: dict[str, list[str]]) -> dict[str, list[dict]]:
    spreads: list[dict] = []
    for line in sections.get("Spreads", []):
        s = _parse_spread(line)
        if s:
            spreads.append(s)

    return {
        "abilities": _parse_pct_section(sections.get("Abilities", []), field_name="ability_name"),
        "items": _parse_pct_section(sections.get("Items", []), field_name="item_name"),
        "spreads": spreads,
        "moves": _parse_pct_section(sections.get("Moves", []), field_name="move_name"),
        "tera_types": _parse_pct_section(sections.get("Tera Types", []), field_name="tera_type"),
        "teammates": _parse_pct_section(sections.get("Teammates", []), field_name="teammate_name"),
        "checks_counters": _parse_checks_counters(sections.get("Checks and Counters", [])),
    }


def fetch_and_write_latest_gen9_movesets_json(
    *,
    out_dir: Path,
    tiers: list[str] | None = None,
    timeout_seconds: float = 30.0,
    include_block_text: bool = False,
) -> dict:
    tiers = tiers or list(TIER_ORDER)

    session = requests.Session()
    session.headers.update({"User-Agent": "PokemonTeamBuilder/1.0"})

    month_url = get_latest_month_url(session, timeout_seconds=timeout_seconds)
    month = month_url.rstrip("/").rsplit("/", 1)[-1]

    files = list_moveset_files(session, month_url, timeout_seconds=timeout_seconds)

    report = {"month": month, "month_url": month_url, "tiers": {}}

    month_out_dir = out_dir / month
    month_out_dir.mkdir(parents=True, exist_ok=True)

    for tier in tiers:
        slug = FORMAT_SLUGS_BY_TIER.get(tier)
        if not slug:
            report["tiers"][tier] = {"files": 0, "pokemon_blocks": 0}
            continue

        tier_files = [(fn, url) for fn, url in files if _is_gen9_tier_file(fn, slug=slug)]
        tier_files = _prefer_plain_txt(tier_files)

        pokemon_blocks_count = 0

        for file_name, url in tier_files:
            elo = _parse_elo_from_filename(file_name)
            text = _download_text(session, url, timeout_seconds=timeout_seconds)

            usage_url_txt = urljoin(month_url, file_name)
            usage_url_gz = urljoin(month_url, f"{file_name}.gz") if not file_name.lower().endswith(".gz") else usage_url_txt

            usage_text = _try_download_text(session, usage_url_txt, timeout_seconds=timeout_seconds)
            if usage_text is None and usage_url_gz != usage_url_txt:
                usage_text = _try_download_text(session, usage_url_gz, timeout_seconds=timeout_seconds)
            usage_by_pokemon = _parse_usage_real_pct(usage_text) if usage_text else {}

            payload: dict[str, object] = {
                "__meta__": {
                    "month": month,
                    "month_url": month_url,
                    "gen": 9,
                    "tier": tier,
                    "format_slug": slug,
                    "elo": elo,
                    "file_name": file_name,
                    "url": url,
                    "usage_url": usage_url_txt if usage_text is not None else None,
                    "fetched_at": _utc_now_iso(),
                }
            }

            for pokemon_name, block_text in iter_pokemon_blocks(text):
                raw_count, avg_weight, viability_ceiling = _extract_simple_fields(block_text)
                sections = parse_block_sections(block_text)
                cleaned = _clean_sections(sections)

                usage_pct = usage_by_pokemon.get(pokemon_name.lower())
                entry: dict[str, object] = {
                    "raw_count": raw_count,
                    "avg_weight": avg_weight,
                    "viability_ceiling": viability_ceiling,
                    "usage_pct": usage_pct,
                    **cleaned,
                }
                if include_block_text:
                    entry["block_text"] = block_text

                payload[pokemon_name] = entry
                pokemon_blocks_count += 1

            out_name = re.sub(r"\.txt(?:\.gz)?$", ".json", file_name, flags=re.IGNORECASE)
            out_path = month_out_dir / out_name
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        report["tiers"][tier] = {"files": len(tier_files), "pokemon_blocks": pokemon_blocks_count}

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch latest Smogon gen9 movesets and save each tier file as JSON")
    parser.add_argument("--out-dir", default=str(ROOT / "movesets_json"))
    parser.add_argument("--tiers", nargs="*", default=list(TIER_ORDER))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--include-block-text", action="store_true")
    args = parser.parse_args()

    report = fetch_and_write_latest_gen9_movesets_json(
        out_dir=Path(args.out_dir),
        tiers=args.tiers,
        timeout_seconds=float(args.timeout),
        include_block_text=bool(args.include_block_text),
    )

    print(report["month_url"])
    for tier in args.tiers:
        info = report["tiers"].get(tier, {})
        print(f"{tier}: {info.get('files', 0)} files, {info.get('pokemon_blocks', 0)} pokemon blocks")


if __name__ == "__main__":
    main()
