import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse


@dataclass(frozen=True)
class BanlistPokemonLink:
    """A resolved Pokémon link extracted from a Smogon banlist format page.

    Attributes
    ----------
    name:
        Display name as shown on the page (best-effort). This is typically the
        Smogon dex display name for the Pokémon/form.
    url:
        Absolute URL pointing to the Pokémon page.
    slug:
        Best-effort slug extracted from the URL (e.g. ``"iron-bundle"``).
        If it cannot be parsed from the URL, it is set to ``None``.
    """

    name: str
    url: str
    slug: str | None


class BanlistScrapeError(RuntimeError):
    """Raised when the banlist page cannot be scraped reliably."""


def _safe_text(value: Any) -> str:
    """Return a stripped string if possible, otherwise an empty string."""

    if not isinstance(value, str):
        return ""
    return value.strip()


def _infer_slug_from_url(pokemon_url: str) -> str | None:
    """Infer a Pokémon slug from a Smogon dex Pokémon URL.

    Parameters
    ----------
    pokemon_url:
        An absolute URL expected to look like:

        - ``https://www.smogon.com/dex/sv/pokemon/<slug>/``

    Returns
    -------
    str | None
        The extracted slug, or ``None`` if the URL does not match.
    """

    if not pokemon_url:
        return None

    parsed = urlparse(pokemon_url)
    path = parsed.path or ""
    m = re.search(r"/dex/sv/pokemon/([^/]+)/?", path)
    if not m:
        return None
    return m.group(1).strip() or None


def scrape_missing_pokemon_links(
    *,
    banlist_url: str,
    headless: bool = True,
    timeout_seconds: float = 30.0,
    limit: int = 0,
    include_non_pokemon_links: bool = False,
) -> list[BanlistPokemonLink]:
    """Scrape missing Pokémon links from a Smogon banlist format page.

    The caller provides a banlist URL (commonly containing ``uubl``, ``rubl``, etc.).
    The page is loaded with Playwright and we extract Pokémon links located under
    ``div.PokemonAltRow`` elements.

    Parameters
    ----------
    banlist_url:
        The banlist page URL.
    headless:
        Whether to run the browser in headless mode.
    timeout_seconds:
        Maximum time to wait for the page and required selectors.
    limit:
        If > 0, only return the first N links (useful for testing).
    include_non_pokemon_links:
        If True, returns all links under `div.PokemonAltRow` (including type/ability links).
        By default this is False and only Pokémon page links are returned.

    Returns
    -------
    list[BanlistPokemonLink]
        Extracted Pokémon links.

    Raises
    ------
    BanlistScrapeError
        If the page cannot be loaded or the expected elements are missing.
    RuntimeError
        If Playwright is not installed.
    """

    banlist_url = _safe_text(banlist_url)
    if not banlist_url:
        raise ValueError("banlist_url is required")

    # Lazy import so this module can be imported without Playwright installed.
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Playwright is required. Install it with `pip install playwright` and run "
            "`python -m playwright install chromium`."
        ) from exc

    timeout_ms = int(max(1.0, float(timeout_seconds)) * 1000)

    results: list[BanlistPokemonLink] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=bool(headless))
        context = browser.new_context(user_agent="PokemonTeamBuilder/1.0")
        page = context.new_page()

        try:
            page.goto(banlist_url, wait_until="domcontentloaded", timeout=timeout_ms)

            # Smogon pages may keep background requests open, so `networkidle` can time out.
            # Instead, wait on the actual content we need.
            page.wait_for_selector("div.PokemonAltRow", timeout=timeout_ms)
            page.wait_for_selector("div.PokemonAltRow a[href]", timeout=timeout_ms)

            # Collect anchor tags inside each PokemonAltRow.
            anchors = page.query_selector_all("div.PokemonAltRow a[href]")
            if not anchors:
                raise BanlistScrapeError(
                    "No links found under div.PokemonAltRow. "
                    "Page structure may have changed, or the page did not fully render."
                )

            seen_urls: set[str] = set()
            for a in anchors:
                href = _safe_text(a.get_attribute("href"))
                if not href:
                    continue

                abs_url = urljoin(banlist_url, href)
                if abs_url in seen_urls:
                    continue
                seen_urls.add(abs_url)

                text = _safe_text(a.inner_text())
                slug = _infer_slug_from_url(abs_url)

                if not include_non_pokemon_links and slug is None:
                    continue

                # Some anchors may be icons or empty; keep a stable placeholder.
                name = text or (slug or abs_url)

                results.append(BanlistPokemonLink(name=name, url=abs_url, slug=slug))

                if limit and len(results) >= int(limit):
                    break

        finally:
            context.close()
            browser.close()

    return results


def _serialize_links(links: list[BanlistPokemonLink]) -> list[dict[str, Any]]:
    """Convert dataclass instances into a JSON-serializable list."""

    return [{"name": x.name, "url": x.url, "slug": x.slug} for x in links]


def main() -> None:
    """CLI entrypoint for scraping missing Pokémon links from a banlist page."""

    parser = argparse.ArgumentParser(
        description=(
            "Scrape missing Pokemon links from a Smogon banlist format page "
            "(extracts links under div.PokemonAltRow)."
        )
    )
    parser.add_argument("url", help="Banlist page URL (e.g. a page containing 'uubl')")
    parser.add_argument("--out", default="", help="Optional JSON output path")
    parser.add_argument("--limit", type=int, default=0, help="Only return first N links")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--include-non-pokemon-links",
        action="store_true",
        help="Include type/ability/format links found inside PokemonAltRow blocks (default: pokemon-only)",
    )
    args = parser.parse_args()

    links = scrape_missing_pokemon_links(
        banlist_url=str(args.url),
        headless=bool(args.headless),
        timeout_seconds=float(args.timeout),
        limit=int(args.limit),
        include_non_pokemon_links=bool(args.include_non_pokemon_links),
    )

    payload = _serialize_links(links)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
