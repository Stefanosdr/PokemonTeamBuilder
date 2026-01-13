import sqlite3
from pathlib import Path
from typing import Any
from pprint import pprint as pp


DEFAULT_MOVESET_DB_PATH = Path(__file__).resolve().parent / "smogon_movesets_gen9_sections.db"


def get_pokemon_moveset_data(pokemon_name: str, db_path: str | Path | None = None) -> dict[str, Any]:
    db_path = Path(db_path) if db_path is not None else DEFAULT_MOVESET_DB_PATH
    if not db_path.exists():
        raise FileNotFoundError(str(db_path))

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()

        block_rows = cur.execute(
            """
            SELECT
                b.id AS pokemon_block_id,
                b.source_file_id,
                b.pokemon_name,
                b.raw_count,
                b.avg_weight,
                b.viability_ceiling,
                b.block_text,
                f.id AS source_file_id,
                f.month_id,
                f.gen,
                f.tier,
                f.format_slug,
                f.elo,
                f.file_name,
                f.url,
                f.fetched_at AS source_fetched_at,
                m.id AS month_id,
                m.month,
                m.month_url,
                m.fetched_at AS month_fetched_at
            FROM moveset_pokemon_blocks b
            JOIN moveset_source_files f ON f.id = b.source_file_id
            JOIN moveset_months m ON m.id = f.month_id
            WHERE LOWER(b.pokemon_name) = LOWER(?)
            ORDER BY m.month DESC, f.tier, f.elo
            """,
            (pokemon_name,),
        ).fetchall()

        if not block_rows:
            return {"pokemon_name": pokemon_name, "blocks": []}

        block_ids = [int(r["pokemon_block_id"]) for r in block_rows]

        def fetch_section_rows(table: str) -> dict[int, list[dict[str, Any]]]:
            placeholders = ",".join(["?"] * len(block_ids))
            rows = cur.execute(
                f"SELECT * FROM {table} WHERE pokemon_block_id IN ({placeholders}) ORDER BY pokemon_block_id",
                block_ids,
            ).fetchall()
            grouped: dict[int, list[dict[str, Any]]] = {}
            for row in rows:
                d = dict(row)
                bid = int(d["pokemon_block_id"])
                grouped.setdefault(bid, []).append(d)
            return grouped

        abilities_by_block = fetch_section_rows("moveset_abilities")
        items_by_block = fetch_section_rows("moveset_items")
        spreads_by_block = fetch_section_rows("moveset_spreads")
        moves_by_block = fetch_section_rows("moveset_moves")
        tera_by_block = fetch_section_rows("moveset_tera_types")
        teammates_by_block = fetch_section_rows("moveset_teammates")
        checks_by_block = fetch_section_rows("moveset_checks_counters")

        blocks: list[dict[str, Any]] = []
        for r in block_rows:
            bid = int(r["pokemon_block_id"])
            blocks.append(
                {
                    "pokemon_block": {
                        "id": bid,
                        "source_file_id": int(r["source_file_id"]),
                        "pokemon_name": r["pokemon_name"],
                        "raw_count": r["raw_count"],
                        "avg_weight": r["avg_weight"],
                        "viability_ceiling": r["viability_ceiling"],
                        "block_text": r["block_text"],
                    },
                    "source_file": {
                        "id": int(r["source_file_id"]),
                        "month_id": int(r["month_id"]),
                        "gen": r["gen"],
                        "tier": r["tier"],
                        "format_slug": r["format_slug"],
                        "elo": r["elo"],
                        "file_name": r["file_name"],
                        "url": r["url"],
                        "fetched_at": r["source_fetched_at"],
                    },
                    "month": {
                        "id": int(r["month_id"]),
                        "month": r["month"],
                        "month_url": r["month_url"],
                        "fetched_at": r["month_fetched_at"],
                    },
                    "sections": {
                        "abilities": abilities_by_block.get(bid, []),
                        "items": items_by_block.get(bid, []),
                        "spreads": spreads_by_block.get(bid, []),
                        "moves": moves_by_block.get(bid, []),
                        "tera_types": tera_by_block.get(bid, []),
                        "teammates": teammates_by_block.get(bid, []),
                        "checks_counters": checks_by_block.get(bid, []),
                    },
                }
            )

        return {"pokemon_name": block_rows[0]["pokemon_name"], "blocks": blocks}
    finally:
        conn.close()

if __name__ == "__main__":
    pp(get_pokemon_moveset_data("Flygon"))
