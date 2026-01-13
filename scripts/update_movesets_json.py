import argparse
from pathlib import Path

import requests

from .fetch_showdown_movesets_json import fetch_and_write_latest_gen9_movesets_json, get_latest_month_url
from db import TIER_ORDER


def update_movesets_json(
    *,
    out_dir: Path,
    tiers: list[str] | None = None,
    timeout_seconds: float = 30.0,
    include_block_text: bool = False,
    force: bool = False,
) -> dict:
    tiers = tiers or list(TIER_ORDER)

    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "PokemonTeamBuilder/1.0"})

    month_url = get_latest_month_url(session, timeout_seconds=timeout_seconds)
    month = month_url.rstrip("/").rsplit("/", 1)[-1]

    month_out_dir = out_dir / month
    already_exists = month_out_dir.exists() and any(month_out_dir.iterdir())

    if already_exists and not force:
        return {
            "action": "skipped",
            "reason": "month folder already exists",
            "month": month,
            "month_url": month_url,
            "out_dir": str(month_out_dir),
        }

    report = fetch_and_write_latest_gen9_movesets_json(
        out_dir=out_dir,
        tiers=tiers,
        timeout_seconds=timeout_seconds,
        include_block_text=include_block_text,
    )
    report["action"] = "updated"
    report["out_dir"] = str(out_dir / report["month"])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Update movesets_json by fetching latest month only if missing")
    parser.add_argument("--out-dir", default=str(Path(__file__).resolve().parents[1] / "movesets_json"))
    parser.add_argument("--tiers", nargs="*", default=list(TIER_ORDER))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--include-block-text", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    report = update_movesets_json(
        out_dir=Path(args.out_dir),
        tiers=args.tiers,
        timeout_seconds=float(args.timeout),
        include_block_text=bool(args.include_block_text),
        force=bool(args.force),
    )

    if report.get("action") == "skipped":
        print(f"No update needed. Latest month {report['month']} already exists at {report['out_dir']}")
        return

    print(report["month_url"])
    for tier in args.tiers:
        info = report["tiers"].get(tier, {})
        print(f"{tier}: {info.get('files', 0)} files, {info.get('pokemon_blocks', 0)} pokemon blocks")


if __name__ == "__main__":
    main()
