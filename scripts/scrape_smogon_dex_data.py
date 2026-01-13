import argparse
import concurrent.futures
import functools
import json
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SMOGON_DEX_SV_BASE = "https://www.smogon.com/dex/sv/pokemon/"


_THREAD_LOCAL = threading.local()
_THREAD_RESOURCES: list[dict[str, Any]] = []
_THREAD_RESOURCES_LOCK = threading.Lock()


class SmogonDexScrapeError(RuntimeError):
    """Raised when required data cannot be scraped from a Smogon dex page."""


_POKEMON_TYPES: tuple[str, ...] = (
    "Normal",
    "Fire",
    "Water",
    "Electric",
    "Grass",
    "Ice",
    "Fighting",
    "Poison",
    "Ground",
    "Flying",
    "Psychic",
    "Bug",
    "Rock",
    "Ghost",
    "Dragon",
    "Dark",
    "Steel",
    "Fairy",
)


def _split_concatenated_types(type_text: str) -> list[str]:
    """Split a concatenated dual-type string into separate type names.

    Smogon sometimes renders the two type badges without whitespace, resulting
    in strings like `"GrassNormal"` or `"ElectricFlying"` when scraping the
    container's `innerText`.

    This function attempts to parse the text into 1-2 valid Pokémon types.

    Parameters
    ----------
    type_text:
        Raw scraped type text, potentially concatenated.

    Returns
    -------
    list[str]
        A list of one or two type names. If parsing fails, returns `[type_text]`.
    """

    s = re.sub(r"[^A-Za-z]", "", type_text or "").strip()
    if not s:
        return []

    lower = s.lower()
    for t1 in _POKEMON_TYPES:
        t1l = t1.lower()
        if not lower.startswith(t1l):
            continue
        rem = s[len(t1) :]
        if not rem:
            return [t1]
        for t2 in _POKEMON_TYPES:
            if rem.lower() == t2.lower():
                return [t1, t2]

    return [type_text]


def _slugify_pokemon_name(pokemon_name: str) -> str:
    """Convert a Pokémon display name into a Smogon dex URL slug.

    Smogon SV dex Pokémon URLs generally follow this pattern:

    - `https://www.smogon.com/dex/sv/pokemon/<slug>/`

    Where `<slug>` is typically:

    - lowercased
    - spaces replaced by hyphens
    - apostrophes removed
    - most punctuation removed

    This function implements the same heuristic used elsewhere in this repo
    (e.g. in `ui_app.py`).

    Parameters
    ----------
    pokemon_name:
        The Pokémon name as stored in the local database, e.g. `"Iron Bundle"`.

    Returns
    -------
    str
        The Smogon slug, e.g. `"iron-bundle"`.
    """

    # Keep hyphens (forms like Rotom-Wash, Chi-Yu) but normalize whitespace.
    slug = pokemon_name.strip().lower()
    slug = slug.replace(" ", "-")
    slug = slug.replace("'", "")

    # Remove punctuation that commonly appears in names but not in the slug.
    # Note: we intentionally keep hyphens.
    slug = slug.replace(".", "")
    slug = slug.replace(":", "")
    slug = slug.replace("%", "")

    # Collapse accidental double-hyphens.
    slug = re.sub(r"-+", "-", slug)
    return slug


def _get_unique_pokemon_names(db_path: Path) -> list[str]:
    """Load the list of distinct Pokémon names from the strategies database.

    The Streamlit app (`ui_app.py`) uses `pokemon_strategies.db` and queries the
    `pokemon_builds` table via `pokemon_name`. This function follows the same
    assumption.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.

    Returns
    -------
    list[str]
        Sorted list of distinct Pokémon names.

    Raises
    ------
    FileNotFoundError
        If the database does not exist.
    sqlite3.Error
        If the database cannot be opened or queried.
    """

    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        rows = cur.execute("SELECT DISTINCT pokemon_name FROM pokemon_builds ORDER BY pokemon_name").fetchall()
        return [r[0] for r in rows if r and isinstance(r[0], str) and r[0].strip()]
    finally:
        conn.close()


def _load_existing_output(out_path: Path) -> dict[str, Any]:
    """Load existing JSON output if present.

    This enables *resume mode* by skipping Pokémon that already exist in the
    output JSON.

    Parameters
    ----------
    out_path:
        Path to an output JSON file.

    Returns
    -------
    dict[str, Any]
        Existing JSON object if readable, otherwise an empty dict.
    """

    if not out_path.is_file():
        return {}

    try:
        return json.loads(out_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_output(out_path: Path, data: dict[str, Any]) -> None:
    """Write the collected dex data to JSON.

    Parameters
    ----------
    out_path:
        Target JSON file.
    data:
        JSON-serializable object.
    """

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _scrape_types(page) -> list[str]:
    """Scrape the Pokémon's type(s) from the dex page.

    The user requested:

    - types under `PokemonSummary-types` div

    This function waits for that container and extracts the visible text.

    Parameters
    ----------
    page:
        A Playwright `Page` instance.

    Returns
    -------
    list[str]
        List of types, e.g. `["Dragon", "Ground"]`.

    Raises
    ------
    SmogonDexScrapeError
        If the types container cannot be found or yields no types.
    """

    page.wait_for_selector("div.PokemonSummary-types", timeout=30_000)
    container = page.locator("div.PokemonSummary-types")
    if container.count() == 0:
        raise SmogonDexScrapeError("PokemonSummary-types container not found")

    # Prefer extracting the child elements, which represent each badge.
    child_texts = [t.strip() for t in container.locator(":scope > *").all_inner_texts()]
    child_texts = [t for t in child_texts if t]

    candidates: list[str]
    if child_texts:
        candidates = child_texts
    else:
        # Fallback: sometimes the types are concatenated inside container text.
        txt = container.inner_text().strip()
        candidates = [t.strip() for t in re.split(r"\s+", txt) if t.strip()]

    out: list[str] = []
    for c in candidates:
        # Split concatenated dual types like GrassNormal -> Grass, Normal
        split = _split_concatenated_types(c)
        if split:
            out.extend(split)

    # De-duplicate while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for t in out:
        tl = t.lower()
        if tl in seen:
            continue
        seen.add(tl)
        deduped.append(t)

    if not deduped:
        raise SmogonDexScrapeError("No types extracted from PokemonSummary-types")

    return deduped


def _scrape_base_stats(page) -> dict[str, int]:
    """Scrape base stats from the dex page.

    The user requested:

    - base stats under `PokemonStats` table element

    Parameters
    ----------
    page:
        A Playwright `Page` instance.

    Returns
    -------
    dict[str, int]
        Mapping of stat name -> value, typically including:

        - HP
        - Atk
        - Def
        - SpA
        - SpD
        - Spe

    Raises
    ------
    SmogonDexScrapeError
        If the stats table cannot be found or parsed.
    """

    page.wait_for_selector("table.PokemonStats", timeout=30_000)
    table = page.locator("table.PokemonStats")

    rows = table.locator("tr")
    n = rows.count()
    out: dict[str, int] = {}

    for i in range(n):
        row = rows.nth(i)
        # Smogon tends to use th for label and td for value.
        label = row.locator("th").first.inner_text().strip() if row.locator("th").count() else ""
        value_txt = row.locator("td").first.inner_text().strip() if row.locator("td").count() else ""

        if not label or not value_txt:
            continue

        # Strip non-numeric characters.
        m = re.search(r"(\d+)", value_txt)
        if not m:
            continue

        out[label] = int(m.group(1))

    if not out:
        raise SmogonDexScrapeError("No stats extracted from PokemonStats table")

    return out


def _parse_dex_table(table) -> dict[str, Any]:
    """Parse a Smogon `DexTable` into structured rows.

    Smogon's dex uses a reusable component called `DexTable` for many tables.
    The user specifically mentioned:

    - move info under `DexTable is-even` div element

    We treat each `div.DexTable...` as one table, extracting:

    - headers (if found)
    - rows as list of dicts (when header count matches cell count)
    - otherwise rows as list of cell lists

    Parameters
    ----------
    table:
        A Playwright locator for the DexTable container.

    Returns
    -------
    dict[str, Any]
        Parsed table structure.
    """

    classes = (table.get_attribute("class") or "").strip()

    header_cells = []
    header_locator = table.locator(".DexTable-header")
    if header_locator.count():
        header_cells = [h.strip() for h in header_locator.first.locator(":scope > div").all_inner_texts()]
        header_cells = [h for h in header_cells if h]

    rows_out: list[Any] = []
    row_locator = table.locator(".DexTable-row")
    row_count = row_locator.count()

    for i in range(row_count):
        row = row_locator.nth(i)
        cells = [c.strip() for c in row.locator(":scope > div").all_inner_texts()]
        cells = [c for c in cells if c != ""]
        if not cells:
            continue

        if header_cells and len(header_cells) == len(cells):
            rows_out.append(dict(zip(header_cells, cells, strict=False)))
        else:
            rows_out.append(cells)

    return {
        "class": classes,
        "headers": header_cells,
        "rows": rows_out,
    }


def _parse_move_row(row) -> dict[str, Any]:
    """Parse a Smogon move row (`div.MoveRow`) into a structured payload.

    The user requested:

    - All move info under `div` with class `MoveRow`

    Smogon's dex is a dynamic React app; the exact internal markup can change.
    This function therefore focuses on capturing **all visible data** per row in
    a future-proof way.

    Captured fields
    --------------
    The caller requested a normalized shape where the row text maps to:

    - `Name`
    - `Type`
    - `Power`
    - `Accuracy`
    - `PP`
    - `Description`

    Values are derived from the row's visible text split by newlines.

    Additionally, we include:

    - `url`: Absolute URL to the move page when a link exists

    Parameters
    ----------
    row:
        Playwright locator for a single `div.MoveRow`.

    Returns
    -------
    dict[str, Any]
        JSON-serializable move row data.
    """

    def _first_nonempty_line(s: str) -> str | None:
        lines = [ln.strip() for ln in (s or "").splitlines()]
        for ln in lines:
            if ln:
                return ln
        return None

    def _last_nonempty_line(s: str) -> str | None:
        lines = [ln.strip() for ln in (s or "").splitlines()]
        for ln in reversed(lines):
            if ln:
                return ln
        return None

    move_name: str | None = None
    move_url: str | None = None

    link = row.locator("a")
    if link.count():
        try:
            move_name = link.first.inner_text().strip() or None
        except Exception:
            move_name = None
        try:
            href = link.first.get_attribute("href")
            if href:
                # The dex uses a <base href='/dex/'>, so links can be relative.
                if href.startswith("http"):
                    move_url = href
                else:
                    move_url = "https://www.smogon.com" + href
        except Exception:
            move_url = None

    # Try to capture per-column cell texts. Direct children are typically the
    # columns in the row.
    try:
        cells = [c.strip() for c in row.locator(":scope > *").all_inner_texts()]
    except Exception:
        cells = []

    # Fallback: if the DOM structure is flatter, at least capture something.
    if not cells:
        try:
            cells = [c.strip() for c in row.all_inner_texts()]
        except Exception:
            cells = []

    cells = [c for c in cells if c]

    # Smogon MoveRow layout is typically:
    #   0: Name
    #   1: Type
    #   2: Power (often rendered as "Power\n—" or "Power\n120")
    #   3: Accuracy
    #   4: PP
    #   5: Description
    # We parse using best-effort heuristics and keep `None` when missing.
    name_from_cells = _first_nonempty_line(cells[0]) if len(cells) > 0 else None
    type_from_cells = _first_nonempty_line(cells[1]) if len(cells) > 1 else None
    power_from_cells = _last_nonempty_line(cells[2]) if len(cells) > 2 else None
    accuracy_from_cells = _last_nonempty_line(cells[3]) if len(cells) > 3 else None
    pp_from_cells = _last_nonempty_line(cells[4]) if len(cells) > 4 else None
    desc_from_cells = (cells[5].strip() if len(cells) > 5 else None) or None

    # Prefer link-derived name, otherwise fall back to the first cell.
    final_name = move_name or name_from_cells

    return {
        "Name": final_name,
        "Type": type_from_cells,
        "Power": power_from_cells,
        "Accuracy": accuracy_from_cells,
        "PP": pp_from_cells,
        "Description": desc_from_cells,
        "url": move_url,
    }


def _scrape_moves(page) -> list[dict[str, Any]]:
    """Scrape all moves for a Pokémon from the dex "moves" subpage.

    The SV dex has a dedicated moves view:

    - `https://www.smogon.com/dex/sv/pokemon/<slug>/moves/`

    The user requested that moves be extracted from `div.MoveRow` elements.

    Parameters
    ----------
    page:
        A Playwright `Page` instance already navigated to the moves URL.

    Returns
    -------
    list[dict[str, Any]]
        List of moves with full row data.

    Raises
    ------
    SmogonDexScrapeError
        If no move rows can be found.
    """

    page.wait_for_selector("div.MoveRow", timeout=30_000)
    rows = page.locator("div.MoveRow")
    n = rows.count()

    out: list[dict[str, Any]] = []
    for i in range(n):
        out.append(_parse_move_row(rows.nth(i)))

    if not out:
        raise SmogonDexScrapeError("No MoveRow elements found on moves page")

    return out


def scrape_one_pokemon(page, pokemon_name: str) -> dict[str, Any]:
    """Scrape Smogon SV dex data for a single Pokémon.

    For each Pokémon we collect:

    - `types` from `div.PokemonSummary-types`
    - `base_stats` from `table.PokemonStats`
    - `moves_tables` from the moves subpage using `div.DexTable` containers

    Parameters
    ----------
    page:
        A Playwright `Page` instance.
    pokemon_name:
        Pokémon name as stored in the SQLite DB.

    Returns
    -------
    dict[str, Any]
        A JSON-serializable structure containing the scraped data.

    Raises
    ------
    SmogonDexScrapeError
        If the page structure is missing expected elements.
    """

    slug = _slugify_pokemon_name(pokemon_name)

    pokemon_url = f"{SMOGON_DEX_SV_BASE}{slug}/"
    moves_url = f"{SMOGON_DEX_SV_BASE}{slug}/moves/"

    page.goto(pokemon_url, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=30_000)

    types = _scrape_types(page)
    base_stats = _scrape_base_stats(page)

    page.goto(moves_url, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=30_000)

    moves = _scrape_moves(page)

    return {
        "name": pokemon_name,
        "slug": slug,
        "url": pokemon_url,
        "moves_url": moves_url,
        "types": types,
        "base_stats": base_stats,
        "moves": moves,
    }


def _thread_init_playwright(*, headless: bool) -> None:
    """Initialize Playwright resources for a worker thread.

    Playwright objects are not thread-safe when shared. To safely scrape in
    parallel, each worker thread creates its own Playwright driver, browser,
    context, and page, and stores them in thread-local storage.

    Parameters
    ----------
    headless:
        If True, Chromium runs headless. Set False to visually debug scraping.
    """

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=bool(headless))
    context = browser.new_context()
    page = context.new_page()

    _THREAD_LOCAL.playwright = pw
    _THREAD_LOCAL.browser = browser
    _THREAD_LOCAL.context = context
    _THREAD_LOCAL.page = page

    # Track resources for a clean shutdown at the end.
    with _THREAD_RESOURCES_LOCK:
        _THREAD_RESOURCES.append(
            {
                "playwright": pw,
                "browser": browser,
                "context": context,
            }
        )


def _close_all_thread_resources() -> None:
    """Close Playwright resources created by all worker threads.

    This is called once after the executor finishes. We keep it best-effort so
    one failed close doesn't mask earlier scraping results.
    """

    with _THREAD_RESOURCES_LOCK:
        resources = list(_THREAD_RESOURCES)
        _THREAD_RESOURCES.clear()

    for r in resources:
        context = r.get("context")
        browser = r.get("browser")
        pw = r.get("playwright")

        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass


def _scrape_worker(pokemon_name: str, *, sleep_seconds: float) -> tuple[str, dict[str, Any]]:
    """Scrape one Pokémon using the current thread's Playwright page.

    Parameters
    ----------
    pokemon_name:
        Pokémon name as stored in the SQLite DB.
    sleep_seconds:
        Optional per-task delay, mainly to be polite to Smogon.

    Returns
    -------
    tuple[str, dict[str, Any]]
        `(pokemon_name, result)` where result is either the scraped payload or
        an `{error: ...}` object.
    """

    page = getattr(_THREAD_LOCAL, "page", None)
    if page is None:
        raise RuntimeError("Worker thread Playwright page not initialized")

    try:
        result = scrape_one_pokemon(page, pokemon_name)
    except Exception as exc:
        result = {
            "name": pokemon_name,
            "error": str(exc),
        }

    if sleep_seconds > 0:
        time.sleep(float(sleep_seconds))

    return pokemon_name, result


def main() -> None:
    """CLI entrypoint.

    This script:

    1) Reads all distinct Pokémon names from `pokemon_strategies.db`
    2) Scrapes Smogon SV dex pages using Playwright
    3) Writes a JSON file that can be resumed between runs

    Notes
    -----
    - Playwright requires a one-time browser install:

      `python -m playwright install chromium`

    - Use `--headless false` for debugging.
    - Use `--limit` to test on a smaller subset.
    """

    parser = argparse.ArgumentParser(description="Scrape Smogon SV dex data for all Pokémon in pokemon_strategies.db")
    parser.add_argument("--db-path", default=str(ROOT / "pokemon_strategies.db"))
    parser.add_argument("--out", default=str(ROOT / "smogon_dex_data.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, min(4, (os.cpu_count() or 4))),
        help="Number of parallel worker threads (each owns a Playwright browser)",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sleep-seconds", type=float, default=0.3, help="Delay between Pokémon to be polite")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    out_path = Path(args.out)

    names = _get_unique_pokemon_names(db_path)
    if args.limit and args.limit > 0:
        names = names[: int(args.limit)]

    existing = _load_existing_output(out_path)
    if not isinstance(existing, dict):
        existing = {}

    # Lazy import so the module can be imported without Playwright installed.
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Playwright is required. Install it with `pip install playwright` "
            "and run `python -m playwright install chromium`."
        ) from exc

    # Silence unused import warning; we only import here to validate dependency.
    _ = sync_playwright

    start = time.time()

    # Filter early so we do not schedule tasks we already have.
    pending_names = [n for n in names if args.force or n not in existing]

    if not pending_names:
        _write_output(out_path, existing)
        print(f"Nothing to do: all Pokémon already present in {out_path}")
        return

    # Each worker thread owns its own Playwright browser and page.
    # We use executor.map as requested, and write results sequentially in the
    # main thread to keep the output JSON consistent.
    worker_fn = functools.partial(_scrape_worker, sleep_seconds=float(args.sleep_seconds))

    try:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, int(args.workers)),
            initializer=lambda: _thread_init_playwright(headless=bool(args.headless)),
        ) as executor:
            for name, result in executor.map(worker_fn, pending_names):
                existing[name] = result
                _write_output(out_path, existing)
    finally:
        _close_all_thread_resources()

    elapsed = time.time() - start
    print(f"Wrote {out_path} in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
