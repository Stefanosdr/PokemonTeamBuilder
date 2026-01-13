
import argparse
import gzip
import io
import re
import sqlite3
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

    if url.lower().endswith(".gz"):
        raw = resp.content
        data = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return data.decode("utf-8", errors="replace")

    resp.encoding = resp.encoding or "utf-8"
    return resp.text


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
        r"^(?P<target>.+?)\s+(?P<score>\d+\.\d+)\s+\((?P<win>\d+\.\d+)±(?P<margin>\d+\.\d+)\)$",
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
    m = re.search(r"\((?P<koed>\d+\.\d+)% KOed\s*/\s*(?P<sw>\d+\.\d+)% switched out\)", line)
    if not m:
        return {"detail": line, "koed": None, "switched_out": None}
    return {"detail": line, "koed": float(m.group("koed")), "switched_out": float(m.group("sw"))}


def init_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moveset_months (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT UNIQUE,
            month_url TEXT,
            fetched_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moveset_source_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month_id INTEGER,
            gen INTEGER,
            tier TEXT,
            format_slug TEXT,
            elo INTEGER,
            file_name TEXT,
            url TEXT UNIQUE,
            fetched_at TEXT,
            FOREIGN KEY (month_id) REFERENCES moveset_months (id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moveset_pokemon_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file_id INTEGER,
            pokemon_name TEXT,
            raw_count INTEGER,
            avg_weight REAL,
            viability_ceiling INTEGER,
            block_text TEXT,
            UNIQUE (source_file_id, pokemon_name),
            FOREIGN KEY (source_file_id) REFERENCES moveset_source_files (id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moveset_abilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pokemon_block_id INTEGER,
            ability_name TEXT,
            usage_pct REAL,
            UNIQUE (pokemon_block_id, ability_name),
            FOREIGN KEY (pokemon_block_id) REFERENCES moveset_pokemon_blocks (id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moveset_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pokemon_block_id INTEGER,
            item_name TEXT,
            usage_pct REAL,
            UNIQUE (pokemon_block_id, item_name),
            FOREIGN KEY (pokemon_block_id) REFERENCES moveset_pokemon_blocks (id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moveset_moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pokemon_block_id INTEGER,
            move_name TEXT,
            usage_pct REAL,
            UNIQUE (pokemon_block_id, move_name),
            FOREIGN KEY (pokemon_block_id) REFERENCES moveset_pokemon_blocks (id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moveset_tera_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pokemon_block_id INTEGER,
            tera_type TEXT,
            usage_pct REAL,
            UNIQUE (pokemon_block_id, tera_type),
            FOREIGN KEY (pokemon_block_id) REFERENCES moveset_pokemon_blocks (id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moveset_teammates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pokemon_block_id INTEGER,
            teammate_name TEXT,
            usage_pct REAL,
            UNIQUE (pokemon_block_id, teammate_name),
            FOREIGN KEY (pokemon_block_id) REFERENCES moveset_pokemon_blocks (id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moveset_spreads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pokemon_block_id INTEGER,
            spread TEXT,
            nature TEXT,
            hp INTEGER,
            atk INTEGER,
            def INTEGER,
            spa INTEGER,
            spd INTEGER,
            spe INTEGER,
            usage_pct REAL,
            UNIQUE (pokemon_block_id, spread),
            FOREIGN KEY (pokemon_block_id) REFERENCES moveset_pokemon_blocks (id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moveset_checks_counters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pokemon_block_id INTEGER,
            target_name TEXT,
            score REAL,
            win REAL,
            margin REAL,
            koed_pct REAL,
            switched_out_pct REAL,
            detail_text TEXT,
            UNIQUE (pokemon_block_id, target_name, score, win, margin),
            FOREIGN KEY (pokemon_block_id) REFERENCES moveset_pokemon_blocks (id)
        )
        """
    )

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_moveset_source_files_tier_elo ON moveset_source_files (tier, elo)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_moveset_blocks_source ON moveset_pokemon_blocks (source_file_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_moveset_blocks_pokemon ON moveset_pokemon_blocks (pokemon_name)"
    )
    conn.commit()


def _get_or_create_month(conn: sqlite3.Connection, month: str, month_url: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO moveset_months (month, month_url, fetched_at) VALUES (?, ?, ?)",
        (month, month_url, _utc_now_iso()),
    )
    cur.execute("SELECT id FROM moveset_months WHERE month = ?", (month,))
    row = cur.fetchone()
    if not row:
        raise SmogonStatsError("Failed to create or load moveset month")
    return int(row[0])


def _upsert_source_file(
    conn: sqlite3.Connection,
    *,
    month_id: int,
    gen: int,
    tier: str,
    format_slug: str,
    elo: int | None,
    file_name: str,
    url: str,
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO moveset_source_files (month_id, gen, tier, format_slug, elo, file_name, url, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            month_id=excluded.month_id,
            gen=excluded.gen,
            tier=excluded.tier,
            format_slug=excluded.format_slug,
            elo=excluded.elo,
            file_name=excluded.file_name,
            fetched_at=excluded.fetched_at
        """,
        (month_id, gen, tier, format_slug, elo, file_name, url, _utc_now_iso()),
    )
    cur.execute("SELECT id FROM moveset_source_files WHERE url = ?", (url,))
    row = cur.fetchone()
    if not row:
        raise SmogonStatsError("Failed to upsert source file")
    return int(row[0])


def _upsert_pokemon_block(
    conn: sqlite3.Connection,
    *,
    source_file_id: int,
    pokemon_name: str,
    raw_count: int | None,
    avg_weight: float | None,
    viability_ceiling: int | None,
    block_text: str,
) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO moveset_pokemon_blocks (
            source_file_id, pokemon_name, raw_count, avg_weight, viability_ceiling, block_text
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_file_id, pokemon_name) DO UPDATE SET
            raw_count=excluded.raw_count,
            avg_weight=excluded.avg_weight,
            viability_ceiling=excluded.viability_ceiling,
            block_text=excluded.block_text
        """,
        (source_file_id, pokemon_name, raw_count, avg_weight, viability_ceiling, block_text),
    )
    cur.execute(
        "SELECT id FROM moveset_pokemon_blocks WHERE source_file_id = ? AND pokemon_name = ?",
        (source_file_id, pokemon_name),
    )
    row = cur.fetchone()
    if not row:
        raise SmogonStatsError("Failed to upsert pokemon block")
    return int(row[0])


def _replace_sections(conn: sqlite3.Connection, pokemon_block_id: int, sections: dict[str, list[str]]) -> None:
    cur = conn.cursor()

    for table in [
        "moveset_abilities",
        "moveset_items",
        "moveset_moves",
        "moveset_tera_types",
        "moveset_teammates",
        "moveset_spreads",
        "moveset_checks_counters",
    ]:
        cur.execute(f"DELETE FROM {table} WHERE pokemon_block_id = ?", (pokemon_block_id,))

    def insert_pct_rows(table: str, col: str, rows: list[str]) -> None:
        parsed = []
        for line in rows:
            item = _parse_pct_entry(line)
            if not item:
                continue
            name, pct = item
            parsed.append((pokemon_block_id, name, pct))
        if not parsed:
            return
        cur.executemany(
            f"INSERT OR REPLACE INTO {table} (pokemon_block_id, {col}, usage_pct) VALUES (?, ?, ?)",
            parsed,
        )

    insert_pct_rows("moveset_abilities", "ability_name", sections.get("Abilities", []))
    insert_pct_rows("moveset_items", "item_name", sections.get("Items", []))
    insert_pct_rows("moveset_moves", "move_name", sections.get("Moves", []))
    insert_pct_rows("moveset_tera_types", "tera_type", sections.get("Tera Types", []))
    insert_pct_rows("moveset_teammates", "teammate_name", sections.get("Teammates", []))

    spreads = []
    for line in sections.get("Spreads", []):
        s = _parse_spread(line)
        if not s:
            continue
        spreads.append(
            (
                pokemon_block_id,
                s["spread"],
                s["nature"],
                s["hp"],
                s["atk"],
                s["def"],
                s["spa"],
                s["spd"],
                s["spe"],
                s["usage_pct"],
            )
        )
    if spreads:
        cur.executemany(
            """
            INSERT OR REPLACE INTO moveset_spreads (
                pokemon_block_id, spread, nature, hp, atk, def, spa, spd, spe, usage_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            spreads,
        )

    cc_lines = sections.get("Checks and Counters", [])
    checks: list[dict] = []
    i = 0
    while i < len(cc_lines):
        header = _parse_check_counter_header(cc_lines[i])
        if not header:
            i += 1
            continue
        if i + 1 < len(cc_lines):
            detail = _parse_check_counter_detail(cc_lines[i + 1])
            header["detail"] = detail.get("detail")
            header["koed"] = detail.get("koed")
            header["switched_out"] = detail.get("switched_out")
            i += 2
        else:
            i += 1
        checks.append(header)

    if checks:
        cur.executemany(
            """
            INSERT OR REPLACE INTO moveset_checks_counters (
                pokemon_block_id, target_name, score, win, margin, koed_pct, switched_out_pct, detail_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    pokemon_block_id,
                    x["target"],
                    x["score"],
                    x["win"],
                    x["margin"],
                    x["koed"],
                    x["switched_out"],
                    x["detail"],
                )
                for x in checks
            ],
        )


def fetch_and_store_latest_gen9_movesets(
    *,
    db_path: Path,
    tiers: list[str] | None = None,
    timeout_seconds: float = 30.0,
) -> dict:
    tiers = tiers or list(TIER_ORDER)

    session = requests.Session()
    session.headers.update({"User-Agent": "PokemonTeamBuilder/1.0"})

    month_url = get_latest_month_url(session, timeout_seconds=timeout_seconds)
    month = month_url.rstrip("/").rsplit("/", 1)[-1]

    files = list_moveset_files(session, month_url, timeout_seconds=timeout_seconds)

    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        month_id = _get_or_create_month(conn, month, month_url)

        report = {"month": month, "month_url": month_url, "tiers": {}}

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
                source_file_id = _upsert_source_file(
                    conn,
                    month_id=month_id,
                    gen=9,
                    tier=tier,
                    format_slug=slug,
                    elo=elo,
                    file_name=file_name,
                    url=url,
                )

                text = _download_text(session, url, timeout_seconds=timeout_seconds)
                for pokemon_name, block_text in iter_pokemon_blocks(text):
                    raw_count, avg_weight, viability_ceiling = _extract_simple_fields(block_text)
                    pokemon_block_id = _upsert_pokemon_block(
                        conn,
                        source_file_id=source_file_id,
                        pokemon_name=pokemon_name,
                        raw_count=raw_count,
                        avg_weight=avg_weight,
                        viability_ceiling=viability_ceiling,
                        block_text=block_text,
                    )

                    sections = parse_block_sections(block_text)
                    _replace_sections(conn, pokemon_block_id, sections)
                    pokemon_blocks_count += 1

                conn.commit()

            report["tiers"][tier] = {"files": len(tier_files), "pokemon_blocks": pokemon_blocks_count}

        return report
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        type=str,
        default=str(Path(__file__).resolve().parents[1] / "smogon_movesets_gen9.db"),
    )
    parser.add_argument("--tiers", nargs="*", default=list(TIER_ORDER))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    report = fetch_and_store_latest_gen9_movesets(
        db_path=Path(args.db),
        tiers=args.tiers,
        timeout_seconds=float(args.timeout),
    )

    print(report["month_url"])
    for tier in args.tiers:
        info = report["tiers"].get(tier, {})
        print(f"{tier}: {info.get('files', 0)} files, {info.get('pokemon_blocks', 0)} pokemon blocks")


if __name__ == "__main__":
    main()
