import argparse
import sys
from typing import Optional

import requests


def showdown_to_pokepaste(
    team_text: str,
    title: str = "My Showdown Team",
    author: str = "",
    notes: str = "",
    public: bool = True,
) -> str:
    """Upload a Pokémon Showdown team text to Pokepaste and return the paste URL.

    Parameters
    ----------
    team_text: str
        The raw Pokémon Showdown team export text.
    title: str
        Title that will be shown on Pokepaste.
    author: str
        Optional author name.
    notes: str
        Optional notes / description.
    public: bool
        If True, paste is public; if False, it is unlisted.
    """
    url = "https://pokepast.es/create"

    # Normalise line endings to CRLF with a trailing newline to better
    # match browser form submissions, which Pokepaste's parser expects.
    normalized_team = team_text.replace("\r\n", "\n").replace("\r", "\n")
    normalized_team = normalized_team.replace("\n", "\r\n")
    if not normalized_team.endswith("\r\n"):
        normalized_team += "\r\n"

    data = {
        "paste": normalized_team,
        "title": title,
        "author": author,
        "notes": notes,
        "visibility": "public" if public else "unlisted",
    }

    # Pokepaste returns a redirect (303 See Other in practice) to the new paste;
    # we do not follow redirects so we can grab the Location header directly.
    response = requests.post(url, data=data, allow_redirects=False, timeout=30)

    if response.status_code in (301, 302, 303) and "Location" in response.headers:
        paste_url = response.headers["Location"]
        if paste_url.startswith("/"):
            paste_url = "https://pokepast.es" + paste_url
        return paste_url

    raise RuntimeError(
        f"Failed to create pokepaste: status {response.status_code}, "
        f"response: {response.text[:500]}"
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload a Pokémon Showdown team to Pokepaste and print the URL.",
    )
    parser.add_argument(
        "--file",
        "-f",
        type=str,
        help="Path to a text file containing the Showdown team export. If omitted, reads from stdin.",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="My Showdown Team",
        help="Title for the Pokepaste.",
    )
    parser.add_argument(
        "--author",
        type=str,
        default="",
        help="Author name.",
    )
    parser.add_argument(
        "--notes",
        type=str,
        default="",
        help="Optional notes/description.",
    )
    parser.add_argument(
        "--unlisted",
        action="store_true",
        help="Create an unlisted paste instead of a public one.",
    )
    return parser


def _read_team_from_source(path: Optional[str]) -> str:
    if path:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    # Read from stdin if no file given
    return sys.stdin.read()


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    team_text = _read_team_from_source(args.file).strip()
    if not team_text:
        print("No team text provided.", file=sys.stderr)
        return 1

    try:
        url = showdown_to_pokepaste(
            team_text=team_text,
            title=args.title,
            author=args.author,
            notes=args.notes,
            public=not args.unlisted,
        )
    except Exception as e:  # pragma: no cover - simple CLI error path
        print(f"Error creating Pokepaste: {e}", file=sys.stderr)
        return 1

    print(url)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
