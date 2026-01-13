import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from type_effectiveness import TypeEffectivenessError, type_multiplier


class DexLookupError(RuntimeError):
    """Raised when a Pokémon or move cannot be found in the dex database."""


def _open_db(db_path: Path) -> sqlite3.Connection:
    """Open the Smogon dex SQLite database.

    Parameters
    ----------
    db_path:
        Path to `smogon_dex_data.db`.

    Returns
    -------
    sqlite3.Connection
        Connection with row_factory set to sqlite3.Row.
    """

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_pokemon_types(conn: sqlite3.Connection, pokemon_name: str) -> tuple[str | None, str | None]:
    """Lookup a Pokémon's defensive types from the database.

    Parameters
    ----------
    conn:
        Open sqlite connection.
    pokemon_name:
        Pokémon name, e.g. "Abomasnow".

    Returns
    -------
    tuple[str | None, str | None]
        (type1, type2)

    Raises
    ------
    DexLookupError
        If the Pokémon isn't found.
    """

    row = conn.execute(
        "SELECT type1, type2 FROM pokemon WHERE lower(name) = lower(?)",
        (pokemon_name.strip(),),
    ).fetchone()

    if row is None:
        raise DexLookupError(f"Pokemon not found in DB: {pokemon_name!r}")

    return row["type1"], row["type2"]


def _get_move_type(conn: sqlite3.Connection, move_name: str) -> str:
    """Lookup a move's type from the database.

    Parameters
    ----------
    conn:
        Open sqlite connection.
    move_name:
        Move name, e.g. "Flamethrower".

    Returns
    -------
    str
        Canonical move type name as stored in DB (e.g. "Fire").

    Raises
    ------
    DexLookupError
        If the move isn't found or has no type.
    """

    row = conn.execute(
        "SELECT type FROM moves WHERE lower(name) = lower(?)",
        (move_name.strip(),),
    ).fetchone()

    if row is None:
        raise DexLookupError(f"Move not found in DB: {move_name!r}")

    move_type = row["type"]
    if not isinstance(move_type, str) or not move_type.strip():
        raise DexLookupError(f"Move has no type in DB: {move_name!r}")

    return move_type.strip()


def compute_move_effectiveness(*, db_path: Path, move_name: str, pokemon_name: str) -> float:
    """Compute type effectiveness of a move against a Pokémon.

    This function:

    1) Looks up `moves.type` for `move_name`
    2) Looks up `pokemon.type1/type2` for `pokemon_name`
    3) Uses `type_effectiveness.type_multiplier()` to compute the final multiplier.

    Parameters
    ----------
    db_path:
        Path to `smogon_dex_data.db`.
    move_name:
        Attack move name to evaluate.
    pokemon_name:
        Defending Pokémon name.

    Returns
    -------
    float
        The effectiveness multiplier (0, 0.5, 1, 2, 4, ...).
    """

    conn = _open_db(db_path)
    try:
        move_type = _get_move_type(conn, move_name)
        type1, type2 = _get_pokemon_types(conn, pokemon_name)

        defender_types = [t for t in (type1, type2) if isinstance(t, str) and t.strip()]
        return float(type_multiplier(move_type, defender_types))
    finally:
        conn.close()


def main() -> None:
    """CLI entrypoint.

    Examples
    --------
    Compute effectiveness of Flamethrower vs Abomasnow:

    - `python .\\scripts\\move_effectiveness_vs_pokemon.py --move Flamethrower --pokemon Abomasnow`

    Notes
    -----
    - The script matches move and Pokémon names case-insensitively.
    - It uses `smogon_dex_data.db` created earlier by `smogon_dex_json_to_db.py`.
    """

    parser = argparse.ArgumentParser(description="Compute move type effectiveness vs a Pokemon using smogon_dex_data.db")
    parser.add_argument("--db", dest="db_path", default=str(ROOT / "smogon_dex_data.db"))
    parser.add_argument("--move", required=True, help="Attack move name (e.g. 'Flamethrower')")
    parser.add_argument("--pokemon", required=True, help="Defending Pokemon name (e.g. 'Abomasnow')")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"DB not found: {db_path}")

    try:
        mult = compute_move_effectiveness(db_path=db_path, move_name=args.move, pokemon_name=args.pokemon)
    except DexLookupError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(2)
    except TypeEffectivenessError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(3)

    print(mult)


if __name__ == "__main__":
    main()
