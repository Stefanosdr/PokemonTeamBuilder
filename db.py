import sqlite3
from pathlib import Path
from datetime import datetime
import shutil


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "pokemon_strategies.db"

TIER_ORDER = [
    "AG",
    "Uber",
    "OU",
    "UUBL",
    "UU",
    "RUBL",
    "RU",
    "NUBL",
    "NU",
    "PUBL",
    "PU",
    "ZUBL",
    "ZU",
]
EXCLUDED_TIERS = ["NFE", "LC"]
BANLIST_TIERS = {"UUBL", "RUBL", "NUBL", "PUBL", "ZUBL"}


_BANLIST_TO_BASE_TIER: dict[str, str] = {
    "UUBL": "UU",
    "RUBL": "RU",
    "NUBL": "NU",
    "PUBL": "PU",
    "ZUBL": "ZU",
}


def is_user_selectable_tier(tier: str) -> bool:
    """Return whether `tier` should be shown as a selectable format to users.

    Notes
    -----
    - Smogon banlists (e.g. `UUBL`) are *not* standalone playable formats for this app.
      They exist in the strategies database as holding tiers.
    - LC/NFE are excluded from tier selection and also excluded when expanding pools.
    """

    if not tier:
        return False
    if tier in EXCLUDED_TIERS:
        return False
    if tier in BANLIST_TIERS:
        return False
    return True


def _get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_db_connection():
    return _get_db_connection()


def _choose_lowest_tier(tiers: list[str], consider_lc_nfe: bool) -> str:
    if not tiers:
        raise ValueError("No tiers provided")
    candidates = tiers
    if not consider_lc_nfe:
        non_excluded = [t for t in tiers if t not in EXCLUDED_TIERS]
        if non_excluded:
            candidates = non_excluded

    def rank(t: str) -> float:
        """Return a rank where *higher* means a *lower* (more permissive) native tier.

        We mostly follow `TIER_ORDER`, but we treat `*BL` tiers as being *slightly below*
        their corresponding base tier for native-tier selection. This prevents cleanup from
        incorrectly keeping e.g. `RU` when `RUBL` is present.
        """

        if t in BANLIST_TIERS:
            base = _BANLIST_TO_BASE_TIER.get(t)
            if base in TIER_ORDER:
                return float(TIER_ORDER.index(base)) + 0.1
        if t in TIER_ORDER:
            return float(TIER_ORDER.index(t))
        return -1.0

    return max(candidates, key=rank)


def cleanup_tiers(dry_run: bool = True, consider_lc_nfe: bool = False, create_backup: bool = True) -> dict:
    if not DB_PATH.exists():
        raise FileNotFoundError(str(DB_PATH))

    if create_backup and not dry_run:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = DB_PATH.with_name(f"{DB_PATH.stem}_backup_{ts}{DB_PATH.suffix}")
        shutil.copy2(DB_PATH, backup_path)

    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT pokemon_name
            FROM pokemon_builds
            GROUP BY pokemon_name
            HAVING COUNT(DISTINCT tier) > 1
            """
        )
        names = [r[0] for r in cur.fetchall()]

        report = []

        for name in names:
            cur.execute("SELECT DISTINCT tier FROM pokemon_builds WHERE pokemon_name = ?", (name,))
            tiers = [r[0] for r in cur.fetchall()]
            keep_tier = _choose_lowest_tier(tiers, consider_lc_nfe)

            cur.execute(
                "SELECT id, tier FROM pokemon_builds WHERE pokemon_name = ? AND tier != ?",
                (name, keep_tier),
            )
            rows = cur.fetchall()
            remove_ids = [r[0] for r in rows]
            removed_tiers = sorted({r[1] for r in rows})

            report.append(
                {
                    "pokemon_name": name,
                    "keep_tier": keep_tier,
                    "removed_tiers": removed_tiers,
                    "removed_build_count": len(remove_ids),
                }
            )

            if dry_run or not remove_ids:
                continue

            placeholders = ",".join(["?"] * len(remove_ids))
            for table in [
                "build_items",
                "build_abilities",
                "build_natures",
                "build_evs",
                "build_tera_types",
                "build_moves",
            ]:
                cur.execute(f"DELETE FROM {table} WHERE build_id IN ({placeholders})", remove_ids)

            cur.execute(
                f"DELETE FROM pokemon_builds WHERE id IN ({placeholders})",
                remove_ids,
            )

        if not dry_run:
            conn.commit()

        return {
            "dry_run": dry_run,
            "consider_lc_nfe": consider_lc_nfe,
            "affected_pokemon": len(report),
            "details": report,
        }
    finally:
        conn.close()


def _clean_item_name(value: str) -> str:
    if not value:
        return ""
    s = value.strip()
    for i in range(1, len(s)):
        if s[i - 1].islower() and s[i].isupper():
            return s[:i].strip()
    return s


def cleanup_build_items(dry_run: bool = True, create_backup: bool = True) -> dict:
    if not DB_PATH.exists():
        raise FileNotFoundError(str(DB_PATH))

    if create_backup and not dry_run:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = DB_PATH.with_name(f"{DB_PATH.stem}_backup_items_{ts}{DB_PATH.suffix}")
        shutil.copy2(DB_PATH, backup_path)

    conn = _get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT item_name FROM build_items")
        names = [r[0] for r in cur.fetchall()]

        updates: list[dict] = []
        for old in names:
            new = _clean_item_name(old)
            if not new or new == old:
                continue
            if not old.startswith(new):
                continue

            cur.execute("SELECT COUNT(*) FROM build_items WHERE item_name = ?", (old,))
            affected = int(cur.fetchone()[0])
            updates.append({"from": old, "to": new, "row_count": affected})

            if dry_run:
                continue
            cur.execute("UPDATE build_items SET item_name = ? WHERE item_name = ?", (new, old))

        if not dry_run:
            conn.commit()

        return {
            "dry_run": dry_run,
            "fixed_distinct_item_names": len(updates),
            "details": updates,
        }
    finally:
        conn.close()

