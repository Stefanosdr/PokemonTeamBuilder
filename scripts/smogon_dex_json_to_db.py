import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_int_maybe(value: str | None) -> int | None:
    """Parse an integer from a Smogon dex field, returning None when not numeric.

    Smogon move rows commonly contain placeholders like "—" for power/accuracy.

    Parameters
    ----------
    value:
        Raw string value, e.g. "120", "—", "100".

    Returns
    -------
    int | None
        Parsed integer or None if parsing fails.
    """

    if value is None:
        return None

    s = str(value).strip()
    if not s:
        return None

    try:
        return int(s)
    except ValueError:
        return None


def _normalize_stat_key(key: str) -> str:
    """Normalize a base stat key scraped from Smogon into a stable identifier.

    The scraper currently captures base stat labels as they appear on Smogon,
    e.g. "HP:", "Attack:", "Sp. Atk:".

    Parameters
    ----------
    key:
        Raw key from JSON.

    Returns
    -------
    str
        Normalized key in {hp, atk, def, spa, spd, spe} when recognized, else
        a lowercased sanitized string.
    """

    s = (key or "").strip()
    s = s.rstrip(":").strip()
    s = s.replace(" ", "").replace(".", "")
    s_lower = s.lower()

    mapping = {
        "hp": "hp",
        "attack": "atk",
        "atk": "atk",
        "defense": "def",
        "def": "def",
        "spatk": "spa",
        "spattack": "spa",
        "spdef": "spd",
        "spdefense": "spd",
        "speed": "spe",
        "spe": "spe",
    }

    return mapping.get(s_lower, s_lower)


def _ensure_schema(conn: sqlite3.Connection, *, reset: bool) -> None:
    """Create the Smogon dex schema (optionally dropping existing tables).

    Schema design
    -------------
    - `pokemon` stores per-Pokémon summary fields and base stats.
    - `moves` stores unique move definitions (Name, Type, Power, Accuracy, PP,
      Description) plus URL.
    - `pokemon_moves` is a many-to-many table mapping which Pokémon can learn
      which moves.

    This normalized approach prevents move duplication and makes it easy to
    query either direction:

    - all moves a Pokémon can learn
    - all Pokémon that can learn a move

    Parameters
    ----------
    conn:
        Open sqlite3 connection.
    reset:
        If True, drops the tables before re-creating them.
    """

    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON")

    if reset:
        cur.execute("DROP TABLE IF EXISTS pokemon_moves")
        cur.execute("DROP TABLE IF EXISTS moves")
        cur.execute("DROP TABLE IF EXISTS pokemon")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pokemon (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            slug TEXT,
            url TEXT,
            moves_url TEXT,
            type1 TEXT,
            type2 TEXT,
            hp INTEGER,
            atk INTEGER,
            def INTEGER,
            spa INTEGER,
            spd INTEGER,
            spe INTEGER
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moves (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            type TEXT,
            power_text TEXT,
            power_int INTEGER,
            accuracy_text TEXT,
            accuracy_int INTEGER,
            pp_text TEXT,
            pp_int INTEGER,
            description TEXT,
            url TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pokemon_moves (
            pokemon_id INTEGER NOT NULL,
            move_id INTEGER NOT NULL,
            PRIMARY KEY (pokemon_id, move_id),
            FOREIGN KEY (pokemon_id) REFERENCES pokemon(id) ON DELETE CASCADE,
            FOREIGN KEY (move_id) REFERENCES moves(id) ON DELETE CASCADE
        )
        """
    )

    cur.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_name ON pokemon(name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_moves_name ON moves(name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_moves_move_id ON pokemon_moves(move_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_moves_pokemon_id ON pokemon_moves(pokemon_id)")

    conn.commit()


def _upsert_pokemon(conn: sqlite3.Connection, pokemon: dict[str, Any]) -> int:
    """Insert/update a Pokémon row and return its primary key."""

    name = pokemon.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Pokemon entry missing 'name'")

    types = pokemon.get("types") or []
    type1 = types[0] if isinstance(types, list) and len(types) >= 1 else None
    type2 = types[1] if isinstance(types, list) and len(types) >= 2 else None

    base_stats = pokemon.get("base_stats") or {}
    stats_map: dict[str, int | None] = {"hp": None, "atk": None, "def": None, "spa": None, "spd": None, "spe": None}

    if isinstance(base_stats, dict):
        for k, v in base_stats.items():
            nk = _normalize_stat_key(str(k))
            if nk in stats_map:
                try:
                    stats_map[nk] = int(v)
                except Exception:
                    stats_map[nk] = None

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO pokemon (
            name, slug, url, moves_url, type1, type2,
            hp, atk, def, spa, spd, spe
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            slug=excluded.slug,
            url=excluded.url,
            moves_url=excluded.moves_url,
            type1=excluded.type1,
            type2=excluded.type2,
            hp=excluded.hp,
            atk=excluded.atk,
            def=excluded.def,
            spa=excluded.spa,
            spd=excluded.spd,
            spe=excluded.spe
        RETURNING id
        """,
        (
            name,
            pokemon.get("slug"),
            pokemon.get("url"),
            pokemon.get("moves_url"),
            type1,
            type2,
            stats_map["hp"],
            stats_map["atk"],
            stats_map["def"],
            stats_map["spa"],
            stats_map["spd"],
            stats_map["spe"],
        ),
    )

    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"Failed to upsert pokemon: {name}")
    return int(row[0])


def _upsert_move(conn: sqlite3.Connection, move: dict[str, Any]) -> int:
    """Insert/update a move row and return its primary key."""

    name = move.get("Name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Move entry missing 'Name'")

    power_text = move.get("Power")
    accuracy_text = move.get("Accuracy")
    pp_text = move.get("PP")

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO moves (
            name, type,
            power_text, power_int,
            accuracy_text, accuracy_int,
            pp_text, pp_int,
            description,
            url
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            type=excluded.type,
            power_text=excluded.power_text,
            power_int=excluded.power_int,
            accuracy_text=excluded.accuracy_text,
            accuracy_int=excluded.accuracy_int,
            pp_text=excluded.pp_text,
            pp_int=excluded.pp_int,
            description=excluded.description,
            url=excluded.url
        RETURNING id
        """,
        (
            name,
            move.get("Type"),
            power_text,
            _parse_int_maybe(power_text),
            accuracy_text,
            _parse_int_maybe(accuracy_text),
            pp_text,
            _parse_int_maybe(pp_text),
            move.get("Description"),
            move.get("url"),
        ),
    )

    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"Failed to upsert move: {name}")
    return int(row[0])


def build_db_from_json(*, json_path: Path, db_path: Path, reset: bool) -> None:
    """Build a SQLite database from `smogon_dex_data.json`.

    Parameters
    ----------
    json_path:
        Path to the Smogon dex JSON file produced by `scrape_smogon_dex_data.py`.
    db_path:
        Target SQLite database path.
    reset:
        If True, drop and recreate all tables.
    """

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Expected top-level JSON object (dict)")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_schema(conn, reset=reset)

        move_id_cache: dict[str, int] = {}

        # Use a single transaction for speed.
        cur = conn.cursor()
        cur.execute("BEGIN")

        for pokemon_name, entry in payload.items():
            if not isinstance(entry, dict):
                continue
            if "error" in entry:
                continue

            pokemon_id = _upsert_pokemon(conn, entry)

            moves = entry.get("moves")
            if not isinstance(moves, list):
                continue

            for m in moves:
                if not isinstance(m, dict):
                    continue

                move_name = m.get("Name")
                if not isinstance(move_name, str) or not move_name.strip():
                    continue

                move_id = move_id_cache.get(move_name)
                if move_id is None:
                    move_id = _upsert_move(conn, m)
                    move_id_cache[move_name] = move_id

                cur.execute(
                    "INSERT OR IGNORE INTO pokemon_moves (pokemon_id, move_id) VALUES (?, ?)",
                    (pokemon_id, move_id),
                )

        conn.commit()

    finally:
        conn.close()


def main() -> None:
    """CLI entrypoint to convert Smogon dex JSON to SQLite."""

    parser = argparse.ArgumentParser(description="Convert smogon_dex_data.json into a normalized SQLite database")
    parser.add_argument("--json", dest="json_path", default=str(ROOT / "smogon_dex_data.json"))
    parser.add_argument("--out-db", dest="db_path", default=str(ROOT / "smogon_dex_data.db"))
    parser.add_argument("--reset", action="store_true", help="Drop and recreate tables")
    args = parser.parse_args()

    json_path = Path(args.json_path)
    db_path = Path(args.db_path)

    if not json_path.is_file():
        raise FileNotFoundError(f"JSON not found: {json_path}")

    build_db_from_json(json_path=json_path, db_path=db_path, reset=bool(args.reset))
    print(f"Wrote SQLite DB to {db_path}")


if __name__ == "__main__":
    main()
