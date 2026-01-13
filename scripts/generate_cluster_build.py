import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


STAT_ORDER = ["hp", "atk", "def", "spa", "spd", "spe"]
STAT_LABELS = {
    "hp": "HP",
    "atk": "Atk",
    "def": "Def",
    "spa": "SpA",
    "spd": "SpD",
    "spe": "Spe",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_name_map(d: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in d.keys():
        if k == "__meta__":
            continue
        out[k.lower()] = k
    return out


def _pick_top_pct(rows: list[dict[str, Any]], name_field: str) -> str | None:
    best_name: str | None = None
    best_pct = -1.0
    for r in rows:
        name = r.get(name_field)
        if not isinstance(name, str) or not name:
            continue
        if name.strip().lower() == "other":
            continue
        pct = float(r.get("usage_pct") or 0.0)
        if pct > best_pct:
            best_pct = pct
            best_name = name
    return best_name


def _cluster_weights(cluster_features: dict[str, Any], key: str) -> dict[str, float]:
    raw = cluster_features.get(key) or []
    weights: dict[str, float] = {}
    mx = 0.0
    for item in raw:
        if not (isinstance(item, list) or isinstance(item, tuple)) or len(item) != 2:
            continue
        name, w = item
        if not isinstance(name, str):
            continue
        try:
            wv = float(w)
        except Exception:
            continue
        if wv > mx:
            mx = wv
        weights[name] = wv

    if mx <= 0.0:
        return {k: 0.0 for k in weights.keys()}
    return {k: v / mx for k, v in weights.items()}


def _target_ev_means(cluster_features: dict[str, Any], cluster_size: int) -> dict[str, float]:
    evs = cluster_features.get("ev_means") or []
    out: dict[str, float] = {}
    if cluster_size <= 0:
        return out

    for item in evs:
        if not (isinstance(item, list) or isinstance(item, tuple)) or len(item) != 2:
            continue
        stat, total = item
        if stat not in STAT_ORDER:
            continue
        try:
            mean = float(total) / float(cluster_size)
        except Exception:
            continue
        out[stat] = max(0.0, min(1.0, mean))
    return out


def _format_evs(spread: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for stat in STAT_ORDER:
        v = int(spread.get(stat) or 0)
        if v <= 0:
            continue
        parts.append(f"{v} {STAT_LABELS[stat]}")
    if not parts:
        return None
    return " / ".join(parts)


def _choose_moves(
    pokemon_entry: dict[str, Any],
    *,
    cluster_move_weights: dict[str, float],
    alpha: float = 0.75,
    cluster_move_slots: int = 2,
) -> list[str]:
    rows = pokemon_entry.get("moves") or []

    pokemon_move_pct: dict[str, float] = {}
    for r in rows:
        name = r.get("move_name")
        if not isinstance(name, str) or not name:
            continue
        if name.strip().lower() == "other":
            continue
        pokemon_move_pct[name] = float(r.get("usage_pct") or 0.0) / 100.0

    scored: list[tuple[float, str]] = []

    # Force-inject a few cluster-defining moves IF the Pokemon has them.
    cluster_candidates = [
        (cluster_move_weights.get(m, 0.0), m)
        for m in cluster_move_weights.keys()
        if m in pokemon_move_pct
    ]
    cluster_candidates.sort(key=lambda x: x[0], reverse=True)
    forced = [m for _, m in cluster_candidates[: max(0, int(cluster_move_slots))]]

    for name, pct in pokemon_move_pct.items():
        cw = cluster_move_weights.get(name, 0.0)
        score = pct + alpha * cw
        scored.append((score, name))

    scored.sort(key=lambda x: x[0], reverse=True)

    moves: list[str] = []
    seen: set[str] = set()

    for name in forced:
        if name in seen:
            continue
        moves.append(name)
        seen.add(name)
        if len(moves) >= 4:
            return moves

    for _, name in scored:
        if name in seen:
            continue
        moves.append(name)
        seen.add(name)
        if len(moves) >= 4:
            break

    return moves


def _choose_spread(
    pokemon_entry: dict[str, Any],
    *,
    cluster_nature_weights: dict[str, float],
    target_ev: dict[str, float],
    cluster_size: int,
    usage_weight: float = 0.35,
    nature_weight: float = 0.35,
    ev_weight: float = 0.30,
    top_n: int = 1,
    rng: random.Random | None = None,
) -> dict[str, Any] | None:
    spreads = pokemon_entry.get("spreads") or []
    if not spreads:
        return None

    scored: list[tuple[float, dict[str, Any]]] = []

    for s in spreads:
        try:
            pct = float(s.get("usage_pct") or 0.0) / 100.0
        except Exception:
            pct = 0.0

        nature = s.get("nature")
        nature_bonus = 0.0
        if isinstance(nature, str) and nature:
            nature_bonus = cluster_nature_weights.get(nature, 0.0)

        ev_dist = 0.0
        if target_ev:
            for stat, t in target_ev.items():
                sv = float(s.get(stat) or 0.0) / 252.0
                ev_dist += (sv - t) * (sv - t)
            ev_dist = math.sqrt(ev_dist)

        score = usage_weight * pct + nature_weight * nature_bonus - ev_weight * ev_dist
        scored.append((score, s))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    top_n = max(1, int(top_n))
    pool = [s for _, s in scored[:top_n]]
    if rng is None or len(pool) == 1:
        return pool[0]
    return rng.choice(pool)


def build_showdown_set_from_cluster(
    *,
    clusters_path: str | Path,
    cluster_id: int,
    pokemon_name: str,
    move_alpha: float = 0.75,
    cluster_move_slots: int = 2,
    spread_usage_weight: float = 0.35,
    spread_nature_weight: float = 0.35,
    spread_ev_weight: float = 0.30,
    spread_top_n: int = 1,
    random_seed: int | None = None,
) -> str:
    clusters_path = Path(clusters_path)
    cluster_export = _load_json(clusters_path)

    src = cluster_export.get("source") or {}
    tier_file = src.get("file_path")
    if not isinstance(tier_file, str) or not tier_file:
        raise ValueError("Cluster export missing source.file_path")

    tier_path = Path(tier_file)
    if not tier_path.is_absolute():
        tier_path = (clusters_path.parent / tier_path).resolve()

    tier_json = _load_json(tier_path)
    name_map = _normalize_name_map(tier_json)
    key = name_map.get(pokemon_name.lower())
    if key is None:
        raise KeyError(f"Pokemon not found in tier file: {pokemon_name}")

    pokemon_entry = tier_json[key]
    if not isinstance(pokemon_entry, dict):
        raise ValueError("Pokemon entry is not a dict")

    clusters = cluster_export.get("clusters") or {}
    members = clusters.get(str(cluster_id))
    if not isinstance(members, list):
        raise KeyError(f"Cluster {cluster_id} not found")

    if key not in members:
        # Not an error: you may intentionally ask for a set that mimics a cluster even if the mon isn't in it.
        # But it's useful feedback when results look identical.
        print(
            f"[warn] {key} is not a member of cluster {cluster_id} (build may fall back to the mon's default usage profile)",
            file=sys.stderr,
        )

    cluster_size = len(members)
    cluster_features = (cluster_export.get("cluster_top_features") or {}).get(str(cluster_id))
    if not isinstance(cluster_features, dict):
        raise KeyError(f"Missing cluster_top_features for cluster {cluster_id}")

    move_w = _cluster_weights(cluster_features, "moves")
    nature_w = _cluster_weights(cluster_features, "natures")
    target_ev = _target_ev_means(cluster_features, cluster_size)

    rng = random.Random(int(random_seed)) if random_seed is not None else None

    overlap_moves = 0
    pokemon_move_names = {
        r.get("move_name")
        for r in (pokemon_entry.get("moves") or [])
        if isinstance(r, dict) and isinstance(r.get("move_name"), str) and r.get("move_name")
    }
    for cm in move_w.keys():
        if cm in pokemon_move_names:
            overlap_moves += 1
    if overlap_moves == 0:
        print(
            f"[warn] No overlap between cluster {cluster_id} top-moves and {key}'s move list; moves will be mostly default usage",
            file=sys.stderr,
        )

    moves = _choose_moves(
        pokemon_entry,
        cluster_move_weights=move_w,
        alpha=float(move_alpha),
        cluster_move_slots=int(cluster_move_slots),
    )
    spread = _choose_spread(
        pokemon_entry,
        cluster_nature_weights=nature_w,
        target_ev=target_ev,
        cluster_size=cluster_size,
        usage_weight=float(spread_usage_weight),
        nature_weight=float(spread_nature_weight),
        ev_weight=float(spread_ev_weight),
        top_n=int(spread_top_n),
        rng=rng,
    )

    item = _pick_top_pct(pokemon_entry.get("items") or [], "item_name")
    ability = _pick_top_pct(pokemon_entry.get("abilities") or [], "ability_name")
    tera = _pick_top_pct(pokemon_entry.get("tera_types") or [], "tera_type")

    header = key
    if item:
        header = f"{header} @ {item}"

    lines: list[str] = [header]
    if ability:
        lines.append(f"Ability: {ability}")
    if tera:
        lines.append(f"Tera Type: {tera}")

    if spread:
        evs = _format_evs(spread)
        if evs:
            lines.append(f"EVs: {evs}")
        nature = spread.get("nature")
        if isinstance(nature, str) and nature:
            lines.append(f"{nature} Nature")

    for m in moves:
        lines.append(f"- {m}")

    return "\n".join(lines).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a Showdown/Pokepaste set for a Pokemon that matches a chosen cluster"
    )
    parser.add_argument("--clusters", required=True, help="Path to cluster export JSON (e.g. zu_cluster.json)")
    parser.add_argument("--cluster", required=True, type=int, help="Cluster id")
    parser.add_argument("--pokemon", required=True, help="Pokemon name (must exist in tier JSON)")
    parser.add_argument("--move-alpha", type=float, default=0.75, help="How strongly to bias moves toward cluster top-moves")
    parser.add_argument(
        "--cluster-move-slots",
        type=int,
        default=2,
        help="Try to force this many moves from the cluster's top moves (if the Pokemon has them)",
    )
    parser.add_argument("--spread-usage-weight", type=float, default=0.35)
    parser.add_argument("--spread-nature-weight", type=float, default=0.35)
    parser.add_argument("--spread-ev-weight", type=float, default=0.30)
    parser.add_argument(
        "--spread-top-n",
        type=int,
        default=1,
        help="Choose randomly among the top N spreads by score (use with --seed for variety)",
    )
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    txt = build_showdown_set_from_cluster(
        clusters_path=Path(args.clusters),
        cluster_id=int(args.cluster),
        pokemon_name=str(args.pokemon),
        move_alpha=float(args.move_alpha),
        cluster_move_slots=int(args.cluster_move_slots),
        spread_usage_weight=float(args.spread_usage_weight),
        spread_nature_weight=float(args.spread_nature_weight),
        spread_ev_weight=float(args.spread_ev_weight),
        spread_top_n=int(args.spread_top_n),
        random_seed=(int(args.seed) if args.seed is not None else None),
    )
    print(txt)


if __name__ == "__main__":
    main()
