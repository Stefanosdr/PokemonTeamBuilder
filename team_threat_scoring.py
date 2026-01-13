from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from db import BANLIST_TIERS, TIER_ORDER, _BANLIST_TO_BASE_TIER
from pokepaste_parser import parse_pokepaste, parse_showdown_team_text
from team_generator import get_native_tier_for_pokemon, resolve_pokemon_name
from tier_threats import Threat, ThreatsError, find_latest_movesets_month_dir, get_top_threats_by_elo
from type_effectiveness import TypeEffectivenessError, type_multiplier


_FIXED_DAMAGE_MOVES: set[str] = {
    "night shade",
    "seismic toss",
}


@dataclass(frozen=True, slots=True)
class TeamPokemon:
    """A single team Pokémon and its known move list.

    Parameters
    ----------
    name:
        Species name.
    moves:
        List of known moves from the Pokepaste set.
    """

    name: str
    moves: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MatchupSideScore:
    """Aggregate score for one side of a matchup.

    Parameters
    ----------
    total:
        Sum of per-move scores.
    details:
        Optional per-move details for introspection.
    """

    total: float
    details: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class MatchupResult:
    """Computed matchup scores for a threat vs a single team Pokémon.

    Interpretation
    --------------
    - `threat_offense.total` is "how scary the threat is" into this defender.
      Larger is worse.
    - `team_offense.total` is "how much the team member threatens back".
      Larger is better.
    - `ratio` is `team_offense / threat_offense`.
      Larger is better for the team member.

    Parameters
    ----------
    threat:
        Threat Pokémon name.
    defender:
        Team Pokémon name.
    threat_offense:
        Threat -> Defender score.
    team_offense:
        Defender -> Threat score.
    ratio:
        team_offense / threat_offense, or None if threat_offense is 0.
    """

    threat: str
    defender: str
    threat_offense: MatchupSideScore
    team_offense: MatchupSideScore
    ratio: float | None


class TeamThreatScoringError(RuntimeError):
    """Raised when the team threat scoring pipeline cannot proceed."""


class DexTypeIndex:
    """Lightweight cached index for types from `smogon_dex_data.db`.

    This exists to avoid re-opening SQLite connections and re-querying the same
    Pokémon/move types for every matchup computation.

    Parameters
    ----------
    db_path:
        Path to `smogon_dex_data.db`.
    """

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._move_type_cache: dict[str, str | None] = {}
        self._move_power_cache: dict[str, int | None] = {}
        self._pokemon_types_cache: dict[str, tuple[str | None, str | None] | None] = {}

    def __enter__(self) -> "DexTypeIndex":
        if not self._db_path.is_file():
            raise FileNotFoundError(str(self._db_path))
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        self._conn = conn
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _norm_key(value: str) -> str:
        return value.strip().lower()

    def get_move_type(self, move_name: str) -> str | None:
        """Return the move type from DB, or None if not found/invalid."""

        key = self._norm_key(move_name)
        if key in self._move_type_cache:
            return self._move_type_cache[key]

        if self._conn is None:
            raise RuntimeError("DexTypeIndex used outside context manager")

        row = self._conn.execute(
            "SELECT type FROM moves WHERE lower(name) = lower(?)",
            (move_name.strip(),),
        ).fetchone()

        if row is None:
            self._move_type_cache[key] = None
            self._move_power_cache[key] = None
            return None

        move_type = row["type"]
        if not isinstance(move_type, str) or not move_type.strip():
            self._move_type_cache[key] = None
            self._move_power_cache[key] = None
            return None

        self._move_type_cache[key] = move_type.strip()
        return move_type.strip()

    def get_move_power_int(self, move_name: str) -> int | None:
        """Return integer base power from DB, or None when unknown.

        Notes
        -----
        This uses the `moves.power_int` field from `smogon_dex_data.db`, which is
        derived from the Smogon dex "Power" column.

        We use this as a practical filter for "non-damaging" moves.
        """

        key = self._norm_key(move_name)
        if key in self._move_power_cache:
            return self._move_power_cache[key]

        if self._conn is None:
            raise RuntimeError("DexTypeIndex used outside context manager")

        row = self._conn.execute(
            "SELECT power_int FROM moves WHERE lower(name) = lower(?)",
            (move_name.strip(),),
        ).fetchone()

        if row is None:
            self._move_power_cache[key] = None
            return None

        val = row["power_int"]
        if isinstance(val, int):
            self._move_power_cache[key] = val
            return val

        self._move_power_cache[key] = None
        return None

    def get_pokemon_types(self, pokemon_name: str) -> tuple[str | None, str | None] | None:
        """Return (type1, type2) for a Pokémon from DB, or None if not found."""

        key = self._norm_key(pokemon_name)
        if key in self._pokemon_types_cache:
            return self._pokemon_types_cache[key]

        if self._conn is None:
            raise RuntimeError("DexTypeIndex used outside context manager")

        row = self._conn.execute(
            "SELECT type1, type2 FROM pokemon WHERE lower(name) = lower(?)",
            (pokemon_name.strip(),),
        ).fetchone()

        if row is None:
            self._pokemon_types_cache[key] = None
            return None

        t1 = row["type1"] if isinstance(row["type1"], str) and row["type1"].strip() else None
        t2 = row["type2"] if isinstance(row["type2"], str) and row["type2"].strip() else None
        out = (t1, t2)
        self._pokemon_types_cache[key] = out
        return out


def _tier_rank(tier: str) -> int:
    """Rank tiers so that smaller numbers represent "higher" tiers."""

    if not tier:
        return 10**9
    t = tier.strip()

    if t in BANLIST_TIERS:
        base = _BANLIST_TO_BASE_TIER.get(t)
        if base:
            t = base

    if t in TIER_ORDER:
        return TIER_ORDER.index(t)
    return 10**8


def infer_team_tier_from_pokepaste_team(team: dict[str, list[dict[str, Any]]]) -> str:
    """Infer the team tier by selecting the highest-tier Pokémon in the team.

    This uses the existing `pokemon_strategies.db` tier assignments via
    `team_generator.get_native_tier_for_pokemon`.

    Parameters
    ----------
    team:
        Parsed Pokepaste team structure from `pokepaste_parser.parse_pokepaste`.

    Returns
    -------
    str
        Inferred tier.

    Raises
    ------
    TeamThreatScoringError
        If no Pokémon tiers can be resolved.
    """

    resolved_tiers: list[str] = []
    for species in team.keys():
        resolved = resolve_pokemon_name(species)
        if resolved is None:
            continue
        tier = get_native_tier_for_pokemon(resolved)
        if tier is None:
            continue
        if tier in BANLIST_TIERS:
            tier = _BANLIST_TO_BASE_TIER.get(tier, tier)
        resolved_tiers.append(tier)

    if not resolved_tiers:
        raise TeamThreatScoringError("Could not resolve a tier for any Pokémon in the Pokepaste team")

    return min(resolved_tiers, key=_tier_rank)


def score_team_dict_vs_top_threats(
    *,
    team: dict[str, list[dict[str, Any]]],
    n: int,
    dex_db_path: str | Path | None = None,
    movesets_root: str | Path | None = None,
    month_dir: str | Path | None = None,
    elo: int | None = None,
    threat_move_limit: int = 20,
    insights_k: int = 3,
    include_details: bool = False,
) -> dict[str, Any]:
    """Score a parsed team dict against the top N threats in its inferred tier.

    Parameters
    ----------
    team:
        Parsed team dict keyed by species.
    n:
        Top N threats to evaluate per Elo bracket.

    Returns
    -------
    dict[str, Any]
        JSON-serializable output.
    """

    if n <= 0:
        raise TeamThreatScoringError("n must be positive")

    if insights_k < 0:
        raise TeamThreatScoringError("insights_k must be >= 0")

    team_list = _extract_team_pokemon(team)
    inferred_tier = infer_team_tier_from_pokepaste_team(team)

    root = Path(movesets_root) if movesets_root is not None else Path(__file__).resolve().parent / "movesets_json"
    month_path = (
        Path(month_dir)
        if month_dir is not None
        else find_latest_movesets_month_dir(root)
    )

    threats_by_elo = get_top_threats_by_elo(
        inferred_tier,
        n,
        movesets_root=root,
        month_dir=month_path,
    )

    if elo is not None:
        threats_by_elo = {int(elo): threats_by_elo.get(int(elo), [])}

    dex_path = Path(dex_db_path) if dex_db_path is not None else Path(__file__).resolve().parent / "smogon_dex_data.db"

    result: dict[str, Any] = {
        "inferred_tier": inferred_tier,
        "month_dir": str(month_path),
        "team": [{"name": p.name, "moves": list(p.moves)} for p in team_list],
        "by_elo": {},
    }

    with DexTypeIndex(dex_path) as dex:
        for elo_key, threats in threats_by_elo.items():
            tier_file = _load_tier_moveset_file(month_dir=month_path, tier=inferred_tier, elo=int(elo_key))

            defender_ratio_sum: dict[str, float] = {p.name: 0.0 for p in team_list}
            defender_ratio_count: dict[str, int] = {p.name: 0 for p in team_list}
            defender_ratios: dict[str, list[tuple[float, str]]] = {p.name: [] for p in team_list}

            per_threat: list[dict[str, Any]] = []
            for threat in threats:
                threat_payload = tier_file.get(threat.name)
                threat_moves_raw = []
                if isinstance(threat_payload, dict):
                    moves_list = threat_payload.get("moves")
                    if isinstance(moves_list, list):
                        for m in moves_list:
                            if isinstance(m, dict):
                                mn = m.get("move_name")
                                up = m.get("usage_pct")
                                if isinstance(mn, str) and mn.strip():
                                    threat_moves_raw.append((mn.strip(), float(up) if isinstance(up, (int, float)) else None))

                matchups: list[dict[str, Any]] = []
                for defender in team_list:
                    threat_off = _compute_side_score(
                        dex=dex,
                        attacker_name=threat.name,
                        defender_name=defender.name,
                        moves=threat_moves_raw,
                        include_details=include_details,
                        assume_probability=True,
                        move_limit=int(threat_move_limit),
                    )

                    team_moves = [(m, None) for m in defender.moves]
                    team_off = _compute_side_score(
                        dex=dex,
                        attacker_name=defender.name,
                        defender_name=threat.name,
                        moves=team_moves,
                        include_details=include_details,
                        assume_probability=False,
                        move_limit=0,
                    )

                    ratio = None
                    if threat_off.total > 0:
                        ratio = float(team_off.total / threat_off.total)

                    if ratio is not None and math.isfinite(ratio):
                        defender_ratio_sum[defender.name] += float(ratio)
                        defender_ratio_count[defender.name] += 1
                        defender_ratios[defender.name].append((float(ratio), str(threat.name)))

                    matchups.append(
                        {
                            "defender": defender.name,
                            "threat_offense": {"total": threat_off.total, "details": threat_off.details},
                            "team_offense": {"total": team_off.total, "details": team_off.details},
                            "ratio": ratio,
                        }
                    )

                if include_details:
                    per_threat.append(
                        {
                            "threat": {"name": threat.name, "usage_pct": threat.usage_pct, "raw_count": threat.raw_count},
                            "matchups": matchups,
                        }
                    )

            team_scores: list[dict[str, Any]] = []
            for p in team_list:
                cnt = int(defender_ratio_count[p.name])
                avg = (float(defender_ratio_sum[p.name]) / cnt) if cnt > 0 else None
                avg_rounded = round(avg, 1) if isinstance(avg, (int, float)) else None

                worst_threats: list[dict[str, Any]] = []
                if insights_k > 0:
                    pairs = defender_ratios.get(p.name, [])
                    pairs_sorted = sorted(pairs, key=lambda t: t[0])
                    for r, tn in pairs_sorted[: int(insights_k)]:
                        worst_threats.append({"threat": tn, "ratio": round(float(r), 2)})

                team_scores.append({"pokemon": p.name, "score": avg_rounded, "matchups": cnt, "worst_threats": worst_threats})

            team_scores.sort(key=lambda d: (float(d["score"]) if isinstance(d.get("score"), (int, float)) else -1.0, str(d.get("pokemon", ""))))
            numeric_scores = [d["score"] for d in team_scores if isinstance(d.get("score"), (int, float))]
            team_score = round(float(sum(numeric_scores) / len(numeric_scores)), 1) if numeric_scores else None

            per_elo_payload: dict[str, Any] = {"team_score": team_score, "team_scores": team_scores}
            if include_details:
                per_elo_payload["top_threats"] = per_threat

            result["by_elo"][str(int(elo_key))] = per_elo_payload

    return result


def _extract_team_pokemon(team: dict[str, list[dict[str, Any]]]) -> tuple[TeamPokemon, ...]:
    """Convert parsed Pokepaste structure into a normalized team list."""

    out: list[TeamPokemon] = []
    for species, sets in team.items():
        if not isinstance(species, str) or not species.strip():
            continue
        if not isinstance(sets, list) or not sets:
            continue

        moves: list[str] = []
        for s in sets:
            if not isinstance(s, dict):
                continue
            ms = s.get("moves")
            if isinstance(ms, list):
                for m in ms:
                    if isinstance(m, str) and m.strip():
                        moves.append(m.strip())

        uniq = tuple(dict.fromkeys(moves).keys())
        out.append(TeamPokemon(name=species.strip(), moves=uniq))

    return tuple(out)


def _safe_probability_from_usage_pct(usage_pct: float | None) -> float:
    if usage_pct is None or not math.isfinite(float(usage_pct)):
        return 0.0
    return max(0.0, min(1.0, float(usage_pct) / 100.0))


def _is_stab(*, move_type: str | None, attacker_types: Iterable[str | None]) -> bool:
    if move_type is None:
        return False
    mt = move_type.strip().lower()
    for t in attacker_types:
        if isinstance(t, str) and t.strip().lower() == mt:
            return True
    return False


def _compute_side_score(
    *,
    dex: DexTypeIndex,
    attacker_name: str,
    defender_name: str,
    moves: Iterable[tuple[str, float | None]],
    include_details: bool,
    assume_probability: bool,
    move_limit: int,
) -> MatchupSideScore:
    attacker_types = dex.get_pokemon_types(attacker_name)
    defender_types = dex.get_pokemon_types(defender_name)

    if attacker_types is None or defender_types is None:
        return MatchupSideScore(total=0.0, details=tuple())

    def_types = [t for t in defender_types if isinstance(t, str) and t.strip()]

    total = 0.0
    details: list[dict[str, Any]] = []

    for i, (move_name, usage_pct) in enumerate(moves):
        if move_limit > 0 and i >= move_limit:
            break

        if not isinstance(move_name, str) or not move_name.strip():
            continue
        if move_name.strip().lower() == "other":
            continue

        move_type = dex.get_move_type(move_name)
        if move_type is None:
            continue

        power_int = dex.get_move_power_int(move_name)
        is_fixed_damage = move_name.strip().lower() in _FIXED_DAMAGE_MOVES
        if (power_int is None or int(power_int) <= 0) and not is_fixed_damage:
            # For now, ignore non-damaging moves.
            # Note: fixed-damage moves (e.g. Night Shade) often have no `power_int`.
            continue

        try:
            eff = float(type_multiplier(move_type, def_types))
        except TypeEffectivenessError:
            continue

        prob = _safe_probability_from_usage_pct(usage_pct) if assume_probability else 1.0
        stab = 1.5 if _is_stab(move_type=move_type, attacker_types=attacker_types) else 1.0
        score = prob * eff * stab

        total += score

        if include_details:
            details.append(
                {
                    "move": move_name,
                    "move_type": move_type,
                    "power": int(power_int) if isinstance(power_int, int) else None,
                    "probability": prob,
                    "effectiveness": eff,
                    "stab": stab,
                    "score": score,
                }
            )

    return MatchupSideScore(total=float(total), details=tuple(details))


def _load_tier_moveset_file(*, month_dir: Path, tier: str, elo: int) -> dict[str, Any]:
    path = month_dir / f"gen9{tier.strip().lower()}-{int(elo)}.json"
    if not path.is_file():
        raise TeamThreatScoringError(f"Moveset JSON file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise TeamThreatScoringError(f"Failed to load JSON: {path}") from e


def score_team_vs_top_threats(
    *,
    pokepaste_url: str,
    n: int,
    dex_db_path: str | Path | None = None,
    movesets_root: str | Path | None = None,
    month_dir: str | Path | None = None,
    elo: int | None = None,
    threat_move_limit: int = 20,
    insights_k: int = 3,
    include_details: bool = False,
) -> dict[str, Any]:
    """Score a Pokepaste team against the top N threats in its inferred tier.

    Parameters
    ----------
    pokepaste_url:
        Pokepaste link for the team.
    n:
        Top N threats to evaluate per Elo bracket.
    dex_db_path:
        Optional path to `smogon_dex_data.db`. Defaults to `<project_root>/smogon_dex_data.db`.
    movesets_root:
        Optional movesets root. Defaults to `<project_root>/movesets_json`.
    month_dir:
        Optional month dir override.
    elo:
        If provided, only evaluate this Elo bracket.
    threat_move_limit:
        Maximum number of threat moves to consider (sorted by usage_pct as stored in JSON).
    insights_k:
        Number of worst (lowest-ratio) threats to report per team Pokémon.
    include_details:
        If True, include per-move contribution details.

    Returns
    -------
    dict[str, Any]
        JSON-serializable result containing inferred tier, month, threats, and matchup matrices.
    """

    team = parse_pokepaste(pokepaste_url)
    result = score_team_dict_vs_top_threats(
        team=team,
        n=n,
        dex_db_path=dex_db_path,
        movesets_root=movesets_root,
        month_dir=month_dir,
        elo=elo,
        threat_move_limit=threat_move_limit,
        insights_k=insights_k,
        include_details=include_details,
    )
    result["pokepaste_url"] = pokepaste_url
    return result


def score_showdown_team_text_vs_top_threats(
    *,
    team_text: str,
    n: int,
    dex_db_path: str | Path | None = None,
    movesets_root: str | Path | None = None,
    month_dir: str | Path | None = None,
    elo: int | None = None,
    threat_move_limit: int = 20,
    insights_k: int = 3,
    include_details: bool = False,
) -> dict[str, Any]:
    """Score pasted Showdown team text against the top N threats in the inferred tier."""

    team = parse_showdown_team_text(team_text)
    result = score_team_dict_vs_top_threats(
        team=team,
        n=n,
        dex_db_path=dex_db_path,
        movesets_root=movesets_root,
        month_dir=month_dir,
        elo=elo,
        threat_move_limit=threat_move_limit,
        insights_k=insights_k,
        include_details=include_details,
    )
    result["showdown_team_text"] = team_text
    return result


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for scoring a team vs top threats."""

    parser = argparse.ArgumentParser(
        description=(
            "Parse a Pokepaste team, infer its tier (highest-tier member), then score matchups "
            "against the top N usage threats for that tier (split by Elo)."
        )
    )
    parser.add_argument("url", help="Pokepaste URL")
    parser.add_argument("--n", type=int, default=15, help="Top N threats per Elo bracket")
    parser.add_argument("--elo", type=int, default=None, help="Optional Elo bracket to score (e.g. 1630)")
    parser.add_argument("--month", default="", help="Optional month override (e.g. 2025-11) or full path")
    parser.add_argument("--movesets-root", default="", help="Optional movesets_json root path")
    parser.add_argument("--dex-db", default="", help="Optional smogon_dex_data.db path")
    parser.add_argument(
        "--threat-move-limit",
        type=int,
        default=20,
        help="Max number of threat moves to consider (highest usage first)",
    )
    parser.add_argument(
        "--insights-k",
        type=int,
        default=3,
        help="How many worst threats to show per team Pokemon (0 disables)",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Include per-move scoring details in output (very verbose)",
    )
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args(argv)

    try:
        payload = score_team_vs_top_threats(
            pokepaste_url=str(args.url),
            n=int(args.n),
            dex_db_path=str(args.dex_db) if args.dex_db else None,
            movesets_root=str(args.movesets_root) if args.movesets_root else None,
            month_dir=str(args.month) if args.month else None,
            elo=int(args.elo) if args.elo is not None else None,
            threat_move_limit=int(args.threat_move_limit),
            insights_k=int(args.insights_k),
            include_details=bool(args.details),
        )
    except (ThreatsError, TeamThreatScoringError, FileNotFoundError) as e:
        raise SystemExit(str(e)) from e

    txt = json.dumps(payload, indent=2, ensure_ascii=False)

    if args.out:
        Path(args.out).write_text(txt, encoding="utf-8")
    else:
        print(txt)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
