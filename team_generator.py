import random
import sqlite3

from db import BANLIST_TIERS, DB_PATH, EXCLUDED_TIERS, TIER_ORDER, is_user_selectable_tier
from pokepaste_uploader import showdown_to_pokepaste


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_pokemon_name(name: str) -> str:
    if not name:
        return ""
    return "".join(ch for ch in name.strip().lower() if ch.isalnum())


def resolve_pokemon_name(user_pokemon_name: str) -> str | None:
    if not user_pokemon_name:
        return None
    if not DB_PATH.exists():
        return None

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT pokemon_name FROM pokemon_builds WHERE LOWER(pokemon_name) = LOWER(?) LIMIT 1",
            (user_pokemon_name.strip(),),
        )
        row = cursor.fetchone()
        if row:
            return row["pokemon_name"]

        normalized_user = _normalize_pokemon_name(user_pokemon_name)
        if not normalized_user:
            return None

        cursor.execute("SELECT DISTINCT pokemon_name FROM pokemon_builds")
        all_names = [r["pokemon_name"] for r in cursor.fetchall()]

        normalized_map: dict[str, str] = {}
        for name in all_names:
            norm = _normalize_pokemon_name(name)
            if norm and norm not in normalized_map:
                normalized_map[norm] = name

        return normalized_map.get(normalized_user)
    finally:
        conn.close()


def _choose_native_tier(tiers: list[str], consider_lc_nfe: bool = False) -> str:
    if not tiers:
        raise ValueError("No tiers provided")

    candidates = tiers
    if not consider_lc_nfe:
        non_excluded = [t for t in tiers if t not in EXCLUDED_TIERS]
        if non_excluded:
            candidates = non_excluded

    banlist_to_base = {
        "UUBL": "UU",
        "RUBL": "RU",
        "NUBL": "NU",
        "PUBL": "PU",
        "ZUBL": "ZU",
    }

    def rank(t: str) -> float:
        if t in BANLIST_TIERS:
            base = banlist_to_base.get(t)
            if base in TIER_ORDER:
                return float(TIER_ORDER.index(base)) + 0.1
        if t in TIER_ORDER:
            return float(TIER_ORDER.index(t))
        return -1.0

    return max(candidates, key=rank)


def get_native_tier_for_pokemon(pokemon_name: str, consider_lc_nfe: bool = False) -> str | None:
    if not pokemon_name:
        return None
    if not DB_PATH.exists():
        return None

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT tier FROM pokemon_builds WHERE pokemon_name = ?",
            (pokemon_name,),
        )
        tiers = [row["tier"] for row in cursor.fetchall()]
        if not tiers:
            return None
        return _choose_native_tier(tiers, consider_lc_nfe=consider_lc_nfe)
    finally:
        conn.close()


def load_available_tiers() -> list[str]:
    if not DB_PATH.exists():
        return ["OU"]

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT tier FROM pokemon_builds")
        tiers = [row["tier"] for row in cursor.fetchall()]

        known_tiers = [t for t in tiers if t in TIER_ORDER]
        unknown_tiers = [t for t in tiers if t not in TIER_ORDER]

        known_tiers.sort(key=lambda t: TIER_ORDER.index(t))
        unknown_tiers.sort()

        sorted_tiers = known_tiers + unknown_tiers
        sorted_tiers = [t for t in sorted_tiers if is_user_selectable_tier(t)]
        return sorted_tiers if sorted_tiers else ["OU"]
    except Exception:
        return ["OU"]
    finally:
        conn.close()


def _build_showdown_set(name: str, build_data: dict) -> str:
    lines = []

    item = build_data.get("item")
    if item:
        lines.append(f"{name} @ {item}")
    else:
        lines.append(name)

    ability = build_data.get("ability")
    if ability:
        lines.append(f"Ability: {ability}")

    tera = build_data.get("tera_type")
    if tera:
        lines.append(f"Tera Type: {tera}")

    evs = build_data.get("evs")
    if evs:
        lines.append(f"EVs: {evs}")

    nature = build_data.get("nature")
    if nature:
        lines.append(f"Nature: {nature}")

    for move in build_data.get("moves", []):
        lines.append(f"- {move}")

    return "\n".join(lines)


def _get_random_build_from_db(conn: sqlite3.Connection, tier: str, pokemon_name: str) -> dict:
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id FROM pokemon_builds WHERE pokemon_name = ? AND tier = ?",
        (pokemon_name, tier),
    )
    builds = cursor.fetchall()

    if not builds:
        return {}

    build_id = random.choice(builds)["id"]

    build_data = {
        "item": None,
        "ability": None,
        "nature": None,
        "evs": None,
        "tera_type": None,
        "moves": [],
    }

    cursor.execute("SELECT item_name FROM build_items WHERE build_id = ?", (build_id,))
    items = cursor.fetchall()
    if items:
        build_data["item"] = random.choice(items)["item_name"]

    cursor.execute(
        "SELECT ability_name FROM build_abilities WHERE build_id = ?", (build_id,)
    )
    abilities = cursor.fetchall()
    if abilities:
        build_data["ability"] = random.choice(abilities)["ability_name"]

    cursor.execute("SELECT nature_name FROM build_natures WHERE build_id = ?", (build_id,))
    natures = cursor.fetchall()
    if natures:
        build_data["nature"] = random.choice(natures)["nature_name"]

    cursor.execute(
        "SELECT ev_string FROM build_evs WHERE build_id = ? ORDER BY id", (build_id,)
    )
    evs_list = cursor.fetchall()
    if evs_list:
        build_data["evs"] = " / ".join([row["ev_string"] for row in evs_list])

    cursor.execute(
        "SELECT tera_type FROM build_tera_types WHERE build_id = ?", (build_id,)
    )
    teras = cursor.fetchall()
    if teras:
        build_data["tera_type"] = random.choice(teras)["tera_type"]

    moves = []
    for i in range(1, 5):
        slot = f"Move{i}"
        cursor.execute(
            "SELECT move_name FROM build_moves WHERE build_id = ? AND move_slot = ?",
            (build_id, slot),
        )
        slot_moves = cursor.fetchall()
        if slot_moves:
            moves.append(random.choice(slot_moves)["move_name"])

    build_data["moves"] = moves

    return build_data


def build_random_team_for_tier(
    tier: str,
    num_pokemon: int = 6,
    include_lower_tiers: bool = False,
    force_pokemon_name: str | None = None,
) -> str:
    if tier in BANLIST_TIERS:
        raise ValueError(f"Banlist tiers are not selectable formats: {tier}")

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        allowed_tiers = [tier]
        if include_lower_tiers and tier in TIER_ORDER:
            start_idx = TIER_ORDER.index(tier)
            allowed_tiers = TIER_ORDER[start_idx:]
            allowed_tiers = [t for t in allowed_tiers if t not in EXCLUDED_TIERS]

        placeholders = ",".join("?" for _ in allowed_tiers)
        query = (
            f"SELECT DISTINCT pokemon_name FROM pokemon_builds WHERE tier IN ({placeholders})"
        )
        cursor.execute(query, allowed_tiers)
        all_pokemon = [row["pokemon_name"] for row in cursor.fetchall()]

        if not all_pokemon:
            raise ValueError(f"No Pokemon found in tiers {allowed_tiers}")

        chosen_names: list[str]
        if force_pokemon_name:
            cursor.execute(
                f"SELECT 1 FROM pokemon_builds WHERE pokemon_name = ? AND tier IN ({placeholders}) LIMIT 1",
                (force_pokemon_name, *allowed_tiers),
            )
            if cursor.fetchone() is None:
                raise ValueError(
                    f"Pokemon `{force_pokemon_name}` not found in tiers {allowed_tiers}"
                )

            remaining = [p for p in all_pokemon if p != force_pokemon_name]
            remaining_needed = max(0, num_pokemon - 1)
            remaining_chosen = (
                remaining
                if len(remaining) <= remaining_needed
                else random.sample(remaining, remaining_needed)
            )
            chosen_names = [force_pokemon_name, *remaining_chosen]
        else:
            chosen_names = (
                all_pokemon
                if len(all_pokemon) < num_pokemon
                else random.sample(all_pokemon, num_pokemon)
            )

        team_sets: list[str] = []
        for name in chosen_names:
            cursor.execute(
                f"SELECT DISTINCT tier FROM pokemon_builds WHERE pokemon_name = ? AND tier IN ({placeholders})",
                (name, *allowed_tiers),
            )
            available_tiers_for_mon = [row["tier"] for row in cursor.fetchall()]
            if not available_tiers_for_mon:
                continue

            chosen_tier = random.choice(available_tiers_for_mon)

            build_data = _get_random_build_from_db(conn, chosen_tier, name)
            if build_data:
                team_sets.append(_build_showdown_set(name, build_data))

        return "\n\n".join(team_sets)

    finally:
        conn.close()


def generate_random_team_for_tier(
    tier: str, include_lower_tiers: bool = False
) -> tuple[str, str]:
    team_text = build_random_team_for_tier(tier, include_lower_tiers=include_lower_tiers)
    url = showdown_to_pokepaste(
        team_text=team_text,
        title=f"Random {tier} Team",
        author="PokemonTeamBuilder",
        notes=f"Randomly generated {tier} team",
        public=True,
    )
    return team_text, url


def generate_random_team_for_pokemon(
    pokemon_name: str, include_lower_tiers: bool = False
) -> tuple[str, str, str, str]:
    resolved_name = resolve_pokemon_name(pokemon_name)
    if resolved_name is None:
        raise ValueError(f"Unknown Pokemon `{pokemon_name}`")

    tier = get_native_tier_for_pokemon(resolved_name)
    if tier is None:
        raise ValueError(f"No tier found for Pokemon `{resolved_name}`")

    team_text = build_random_team_for_tier(
        tier,
        include_lower_tiers=include_lower_tiers,
        force_pokemon_name=resolved_name,
    )
    url = showdown_to_pokepaste(
        team_text=team_text,
        title=f"Random {tier} Team ({resolved_name})",
        author="PokemonTeamBuilder",
        notes=f"Randomly generated {tier} team featuring {resolved_name}",
        public=True,
    )
    return resolved_name, tier, team_text, url
