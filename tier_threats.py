from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_MOVESSETS_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_MOVESET_FILE_RE = re.compile(r"^gen9(?P<tier>[a-z0-9]+)-(?P<elo>\d+)\.json$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Threat:
    """A single usage-based threat entry for a tier at a given Elo.

    A "threat" is defined as a Pokémon with high usage within a tier and Elo bracket.
    This module derives threats from Smogon moveset JSON exports stored in
    `movesets_json/<month>/gen9<tier>-<elo>.json`.

    Parameters
    ----------
    name:
        The Pokémon name as it appears in the moveset JSON.
    usage_pct:
        Usage percentage within that tier+elo+month file, if present.
    raw_count:
        Raw usage count (number of teams/appearances), if present.
    """

    name: str
    usage_pct: float | None
    raw_count: int | None


class ThreatsError(RuntimeError):
    """Raised when threat extraction cannot proceed due to missing data or invalid inputs."""


def _coerce_int(value: Any) -> int | None:
    """Convert a value into an `int` if possible.

    Parameters
    ----------
    value:
        Value to convert.

    Returns
    -------
    int | None
        Integer if conversion succeeds; otherwise None.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit():
            return int(s)
    return None


def _coerce_float(value: Any) -> float | None:
    """Convert a value into a `float` if possible.

    Parameters
    ----------
    value:
        Value to convert.

    Returns
    -------
    float | None
        Float if conversion succeeds; otherwise None.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def find_latest_movesets_month_dir(movesets_root: str | Path | None = None) -> Path:
    """Find the latest month directory within `movesets_json/`.

    The repository stores movesets as month folders, e.g. `movesets_json/2025-11/`.
    This helper identifies the latest available month by comparing folder names.

    Parameters
    ----------
    movesets_root:
        Root directory containing month folders. If None, defaults to
        `<project_root>/movesets_json`.

    Returns
    -------
    pathlib.Path
        The path to the latest month folder.

    Raises
    ------
    ThreatsError
        If no valid month folders exist.
    """

    root = Path(movesets_root) if movesets_root is not None else Path(__file__).resolve().parent / "movesets_json"
    if not root.exists() or not root.is_dir():
        raise ThreatsError(f"movesets root not found: {root}")

    month_dirs = [
        p
        for p in root.iterdir()
        if p.is_dir() and _MOVESSETS_MONTH_RE.match(p.name) is not None
    ]
    if not month_dirs:
        raise ThreatsError(f"No month folders found under: {root}")

    # Lexicographic ordering works for YYYY-MM.
    return max(month_dirs, key=lambda p: p.name)


def get_moveset_json_files_by_elo(
    tier: str,
    *,
    movesets_root: str | Path | None = None,
    month_dir: str | Path | None = None,
) -> dict[str, list[Path]]:
    """Return a mapping of Elo bracket -> moveset JSON file paths for a tier.

    This is a convenience helper for UIs (e.g. Streamlit) that need to populate
    an Elo dropdown based on the available `movesets_json/<month>/gen9<tier>-<elo>.json` files.

    The mapping always contains:

    - key `"All"`: all matching tier files
    - keys like `"1500"`, `"1630"`, `"1760"`: a single JSON file for that bracket

    Parameters
    ----------
    tier:
        Tier name, e.g. `OU`, `NU`.
    movesets_root:
        Root `movesets_json/` folder. Defaults to `<project_root>/movesets_json`.
    month_dir:
        Optional month directory override. If None, uses latest month folder.

    Returns
    -------
    dict[str, list[pathlib.Path]]
        Mapping described above.

    Raises
    ------
    ThreatsError
        If the month folder cannot be resolved or no files exist for the tier.
    """

    if not tier or not tier.strip():
        raise ThreatsError("tier is required")

    root = Path(movesets_root) if movesets_root is not None else Path(__file__).resolve().parent / "movesets_json"

    if month_dir is None:
        month_path = find_latest_movesets_month_dir(root)
    else:
        md = Path(month_dir)
        if md.is_absolute() or md.exists():
            month_path = md
        else:
            month_path = root / str(month_dir)

    if not month_path.exists() or not month_path.is_dir():
        raise ThreatsError(f"month dir not found: {month_path}")

    tier_norm = tier.strip().lower()
    by_elo: dict[str, list[Path]] = {}
    all_files: list[Path] = []

    for p in month_path.iterdir():
        if not p.is_file() or p.suffix.lower() != ".json":
            continue
        m = _MOVESET_FILE_RE.match(p.name)
        if not m:
            continue
        if str(m.group("tier")).strip().lower() != tier_norm:
            continue
        elo_str = str(m.group("elo"))
        by_elo.setdefault(elo_str, []).append(p)
        all_files.append(p)

    if not all_files:
        raise ThreatsError(f"No moveset JSON files found for tier={tier.strip().upper()} under {month_path}")

    # Stable ordering.
    for k in list(by_elo.keys()):
        by_elo[k] = sorted(by_elo[k])

    by_elo = dict(sorted(by_elo.items(), key=lambda kv: int(kv[0])))
    return {"All": sorted(all_files), **by_elo}


def _iter_moveset_json_files_for_tier(month_dir: Path, tier: str) -> Iterable[Path]:
    """Yield JSON files within `month_dir` belonging to a given tier.

    Parameters
    ----------
    month_dir:
        Month directory, e.g. `movesets_json/2025-11`.
    tier:
        Tier name, e.g. `OU`, `UU`, `NU`.

    Yields
    ------
    pathlib.Path
        Paths to matching JSON files.
    """

    if not month_dir.exists() or not month_dir.is_dir():
        return

    normalized_tier = tier.strip().lower()
    # File name convention: gen9nu-1630.json
    prefix = f"gen9{normalized_tier}-"
    for p in month_dir.iterdir():
        if p.is_file() and p.suffix.lower() == ".json" and p.name.lower().startswith(prefix):
            yield p


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file from disk.

    Parameters
    ----------
    path:
        JSON file path.

    Returns
    -------
    dict[str, Any]
        Parsed JSON object.

    Raises
    ------
    ThreatsError
        If the file cannot be read or parsed.
    """

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ThreatsError(f"Failed to read JSON: {path}") from e


def get_top_threats_by_elo(
    tier: str,
    n: int,
    *,
    movesets_root: str | Path | None = None,
    month_dir: str | Path | None = None,
) -> dict[int, list[Threat]]:
    """Return the top `n` usage threats for `tier`, grouped by Elo.

    This function:
    - selects the latest month folder under `movesets_json/` by default
    - loads all files for the requested tier (one per Elo bracket)
    - extracts Pokémon usage values
    - returns the top N Pokémon per Elo bracket

    Parameters
    ----------
    tier:
        Tier name such as `OU`, `UU`, `RU`, `NU`, etc.
    n:
        Number of threats to return per Elo bracket.
    movesets_root:
        Root `movesets_json/` folder. Defaults to `<project_root>/movesets_json`.
    month_dir:
        If provided, use this month directory instead of auto-detecting the latest.
        Can be either a full path or a month name like `2025-11`.

    Returns
    -------
    dict[int, list[Threat]]
        Mapping from Elo (e.g. 1630) to a descending list of the top N threats.

    Raises
    ------
    ThreatsError
        If inputs are invalid or required data cannot be found.
    """

    if not tier or not tier.strip():
        raise ThreatsError("tier is required")

    if n <= 0:
        raise ThreatsError("n must be a positive integer")

    root = Path(movesets_root) if movesets_root is not None else Path(__file__).resolve().parent / "movesets_json"

    if month_dir is None:
        month_path = find_latest_movesets_month_dir(root)
    else:
        md = Path(month_dir)
        if md.is_absolute() or md.exists():
            month_path = md
        else:
            month_path = root / str(month_dir)

    if not month_path.exists() or not month_path.is_dir():
        raise ThreatsError(f"month dir not found: {month_path}")

    tier_upper = tier.strip().upper()
    results: dict[int, list[Threat]] = {}

    matched_files = list(_iter_moveset_json_files_for_tier(month_path, tier_upper))
    if not matched_files:
        raise ThreatsError(f"No moveset JSON files found for tier={tier_upper} under {month_path}")

    for json_path in matched_files:
        obj = _load_json(json_path)
        meta = obj.get("__meta__")
        if not isinstance(meta, dict):
            continue

        meta_tier = str(meta.get("tier", "")).strip().upper()
        if meta_tier != tier_upper:
            continue

        elo = _coerce_int(meta.get("elo"))
        if elo is None:
            continue

        threats: list[Threat] = []
        for pokemon_name, payload in obj.items():
            if pokemon_name == "__meta__":
                continue
            if not isinstance(payload, dict):
                continue

            usage_pct = _coerce_float(payload.get("usage_pct"))
            raw_count = _coerce_int(payload.get("raw_count"))

            # If neither exists, the entry is not helpful for "most used" ranking.
            if usage_pct is None and raw_count is None:
                continue

            threats.append(Threat(name=str(pokemon_name), usage_pct=usage_pct, raw_count=raw_count))

        def threat_sort_key(t: Threat) -> tuple[float, int]:
            # Prefer `usage_pct` when available; fall back to `raw_count`.
            up = t.usage_pct if t.usage_pct is not None else -1.0
            rc = t.raw_count if t.raw_count is not None else -1
            return (up, rc)

        threats.sort(key=threat_sort_key, reverse=True)
        results[elo] = threats[:n]

    if not results:
        raise ThreatsError(
            f"Found tier files for {tier_upper} under {month_path}, but none produced threats (unexpected schema?)"
        )

    return dict(sorted(results.items(), key=lambda kv: kv[0]))


def _format_threats_as_text(threats_by_elo: dict[int, list[Threat]]) -> str:
    """Format threats as a human-readable text report.

    Parameters
    ----------
    threats_by_elo:
        Mapping from Elo bracket to descending list of threats.

    Returns
    -------
    str
        A human-readable report suitable for printing to stdout.
    """

    lines: list[str] = []
    for elo, threats in threats_by_elo.items():
        lines.append(f"Elo {elo}:")
        for i, t in enumerate(threats, start=1):
            usage = "?" if t.usage_pct is None else f"{t.usage_pct:.3f}%"
            raw = "?" if t.raw_count is None else str(t.raw_count)
            lines.append(f"  {i:>2}. {t.name} (usage={usage}, raw_count={raw})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _serialize_threats(threats_by_elo: dict[int, list[Threat]]) -> dict[str, list[dict[str, Any]]]:
    """Serialize threats to a JSON-friendly dict.

    Parameters
    ----------
    threats_by_elo:
        Mapping from Elo bracket to descending list of threats.

    Returns
    -------
    dict[str, list[dict[str, Any]]]
        JSON-safe representation.
    """

    payload: dict[str, list[dict[str, Any]]] = {}
    for elo, threats in threats_by_elo.items():
        payload[str(elo)] = [
            {"name": t.name, "usage_pct": t.usage_pct, "raw_count": t.raw_count}
            for t in threats
        ]
    return payload


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for computing top usage threats by tier.

    Examples
    --------
    Print top 10 threats for NU across all Elo brackets (latest month):

    - `uv run python .\\tier_threats.py --tier NU --n 10`

    Print JSON output for OU (top 25):

    - `uv run python .\\tier_threats.py --tier OU --n 25 --json`

    Returns
    -------
    int
        Process exit code (0 for success).
    """

    parser = argparse.ArgumentParser(
        description=(
            "Compute the top N most-used Pokémon (threats) for a Smogon tier, "
            "split by Elo bracket, using movesets_json month exports."
        )
    )
    parser.add_argument("--tier", required=True, help="Tier name, e.g. OU, UU, RU, NU")
    parser.add_argument("--n", required=True, type=int, help="Top N threats to return per Elo")
    parser.add_argument(
        "--month",
        default="",
        help=(
            "Optional month folder name (e.g. 2025-11) or full path. "
            "If omitted, auto-detects the latest month under movesets_json."
        ),
    )
    parser.add_argument(
        "--movesets-root",
        default="",
        help="Optional movesets_json root path (default: <project_root>/movesets_json)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    movesets_root: str | Path | None = str(args.movesets_root) if args.movesets_root else None
    month_dir: str | Path | None = str(args.month) if args.month else None

    try:
        threats = get_top_threats_by_elo(
            str(args.tier),
            int(args.n),
            movesets_root=movesets_root,
            month_dir=month_dir,
        )
    except ThreatsError as e:
        raise SystemExit(str(e)) from e

    if bool(args.json):
        print(json.dumps(_serialize_threats(threats), indent=2, ensure_ascii=False))
    else:
        print(_format_threats_as_text(threats), end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
