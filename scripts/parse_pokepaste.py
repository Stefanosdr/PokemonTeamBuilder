import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pokepaste_parser import parse_pokepaste


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and parse a Pokepaste into structured JSON")
    parser.add_argument("url")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out", default="", help="Optional output file path")
    args = parser.parse_args()

    team = parse_pokepaste(args.url, timeout_seconds=float(args.timeout))
    txt = json.dumps(team, indent=2, ensure_ascii=False)

    if args.out:
        Path(args.out).write_text(txt, encoding="utf-8")
    else:
        print(txt)


if __name__ == "__main__":
    main()
