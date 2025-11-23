import json
import random
import sqlite3
from pathlib import Path

import requests
import streamlit as st
from bs4 import BeautifulSoup

from pokepaste_uploader import showdown_to_pokepaste


TIER_ORDER = ["AG", "Uber", "OU", "UU", "RU", "NU", "PU", "ZU"]
EXCLUDED_TIERS = ["NFE", "LC"]  # Tiers to exclude from higher tier team generation


def _parse_showdown_team(team_text: str) -> list[dict]:
    """Parse the generated Showdown team text into per-Pokémon dicts.

    Each dict contains: name, item, ability, tera_type, evs, nature, moves.
    Assumes the format produced by build_random_ou_team.
    """
    blocks = [b for b in team_text.strip().split("\n\n") if b.strip()]
    team = []

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        entry: dict = {
            "name": "",
            "item": "",
            "ability": "",
            "tera_type": "",
            "evs": "",
            "nature": "",
            "moves": [],
        }

        # First line: Name @ Item (or just Name)
        first = lines[0]
        if "@" in first:
            name_part, item_part = first.split("@", 1)
            entry["name"] = name_part.strip()
            entry["item"] = item_part.strip()
        else:
            entry["name"] = first.strip()

        for line in lines[1:]:
            if line.startswith("Ability: "):
                entry["ability"] = line[len("Ability: ") :].strip()
            elif line.startswith("Tera Type: "):
                entry["tera_type"] = line[len("Tera Type: ") :].strip()
            elif line.startswith("EVs: "):
                entry["evs"] = line
            elif line.startswith("Nature: "):
                entry["nature"] = line
            elif line.startswith("- "):
                entry["moves"].append(line[2:].strip())

        team.append(entry)

    return team


def _fetch_pokemon_image_urls_from_pokepaste(paste_url: str) -> list[str]:
    """Fetch Pokémon sprite URLs from a Pokepaste page.

    Returns a list of absolute image URLs in team order.
    """
    try:
        resp = requests.get(paste_url, timeout=15)
        resp.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    imgs = soup.find_all("img", class_="img-pokemon")
    urls: list[str] = []
    for img in imgs:
        src = img.get("src")
        if not src:
            continue
        if src.startswith("http://") or src.startswith("https://"):
            urls.append(src)
        else:
            urls.append("https://pokepast.es" + src)
    return urls


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "pokemon_strategies.db"


def _get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _load_available_tiers() -> list[str]:
    """Return a list of tiers that exist in the database."""
    if not DB_PATH.exists():
        return ["OU"]
    
    conn = _get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT tier FROM pokemon_builds")
        tiers = [row["tier"] for row in cursor.fetchall()]
        
        # Sort tiers based on TIER_ORDER
        # Filter out any tiers not in TIER_ORDER and append them at the end if any exist
        known_tiers = [t for t in tiers if t in TIER_ORDER]
        unknown_tiers = [t for t in tiers if t not in TIER_ORDER]
        
        known_tiers.sort(key=lambda t: TIER_ORDER.index(t))
        unknown_tiers.sort()
        
        sorted_tiers = known_tiers + unknown_tiers
        return sorted_tiers if sorted_tiers else ["OU"]
    except Exception:
        return ["OU"]
    finally:
        conn.close()


def _build_showdown_set(name: str, build_data: dict) -> str:
    """Convert a build dictionary into a Showdown set string."""
    lines = []
    
    # Name @ Item
    item = build_data.get("item")
    if item:
        lines.append(f"{name} @ {item}")
    else:
        lines.append(name)
        
    # Ability
    ability = build_data.get("ability")
    if ability:
        lines.append(f"Ability: {ability}")
        
    # Tera Type
    tera = build_data.get("tera_type")
    if tera:
        lines.append(f"Tera Type: {tera}")
        
    # EVs
    evs = build_data.get("evs")
    if evs:
        lines.append(f"EVs: {evs}")
        
    # Nature
    nature = build_data.get("nature")
    if nature:
        lines.append(f"Nature: {nature}")
        
    # Moves
    for move in build_data.get("moves", []):
        lines.append(f"- {move}")
        
    return "\n".join(lines)


def _get_random_build_from_db(conn, tier: str, pokemon_name: str) -> dict:
    """Fetch a random build for a specific pokemon in a tier from the database."""
    cursor = conn.cursor()
    
    # Get all build IDs for this pokemon and tier
    cursor.execute("SELECT id FROM pokemon_builds WHERE pokemon_name = ? AND tier = ?", (pokemon_name, tier))
    builds = cursor.fetchall()
    
    if not builds:
        return {}
        
    # Pick a random build ID
    build_id = random.choice(builds)["id"]
    
    build_data = {
        "item": None,
        "ability": None,
        "nature": None,
        "evs": None,
        "tera_type": None,
        "moves": []
    }
    
    # Fetch components
    # Item
    cursor.execute("SELECT item_name FROM build_items WHERE build_id = ?", (build_id,))
    items = cursor.fetchall()
    if items:
        build_data["item"] = random.choice(items)["item_name"]
        
    # Ability
    cursor.execute("SELECT ability_name FROM build_abilities WHERE build_id = ?", (build_id,))
    abilities = cursor.fetchall()
    if abilities:
        build_data["ability"] = random.choice(abilities)["ability_name"]
        
    # Nature
    cursor.execute("SELECT nature_name FROM build_natures WHERE build_id = ?", (build_id,))
    natures = cursor.fetchall()
    if natures:
        build_data["nature"] = random.choice(natures)["nature_name"]
        
    # EVs
    cursor.execute("SELECT ev_string FROM build_evs WHERE build_id = ? ORDER BY id", (build_id,))
    evs_list = cursor.fetchall()
    if evs_list:
        # EVs in the DB are stored as individual stats (e.g. "252 Atk", "4 SpD")
        # We need to join them to form the full EV string
        build_data["evs"] = " / ".join([row["ev_string"] for row in evs_list])
        
    # Tera Type
    cursor.execute("SELECT tera_type FROM build_tera_types WHERE build_id = ?", (build_id,))
    teras = cursor.fetchall()
    if teras:
        build_data["tera_type"] = random.choice(teras)["tera_type"]
        
    # Moves - we need 4 moves if possible
    # The DB structure has move_slot (Move1, Move2, etc.)
    # We should pick one move from each slot if available
    moves = []
    for i in range(1, 5):
        slot = f"Move{i}"
        cursor.execute("SELECT move_name FROM build_moves WHERE build_id = ? AND move_slot = ?", (build_id, slot))
        slot_moves = cursor.fetchall()
        if slot_moves:
            moves.append(random.choice(slot_moves)["move_name"])
            
    build_data["moves"] = moves
    
    return build_data


def _build_random_team_for_tier(tier: str, num_pokemon: int = 6, include_lower_tiers: bool = True) -> str:
    """Build a random team for the given tier using the database.
    
    If include_lower_tiers is True, allows Pokemon from the selected tier and any lower tiers.
    """
    conn = _get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Determine allowed tiers
        allowed_tiers = [tier]
        if include_lower_tiers and tier in TIER_ORDER:
            start_idx = TIER_ORDER.index(tier)
            allowed_tiers = TIER_ORDER[start_idx:]
            # Remove excluded tiers (NFE, LC) from allowed tiers
            allowed_tiers = [t for t in allowed_tiers if t not in EXCLUDED_TIERS]
            
        # Get all unique pokemon names in these tiers
        placeholders = ",".join("?" for _ in allowed_tiers)
        query = f"SELECT DISTINCT pokemon_name FROM pokemon_builds WHERE tier IN ({placeholders})"
        cursor.execute(query, allowed_tiers)
        
        all_pokemon = [row["pokemon_name"] for row in cursor.fetchall()]
        
        # If strict matching (include_lower_tiers=False), we don't need extra filtering anymore
        # because the database has been cleaned to only contain the native tier for each Pokemon.
        # So if we query for tier='OU', we will only get Pokemon whose native tier is OU.
        
        if len(all_pokemon) < num_pokemon:
             # Fallback if not enough pokemon
             if not all_pokemon:
                 raise ValueError(f"No Pokemon found in tiers {allowed_tiers}")
             chosen_names = all_pokemon # Take all if less than 6
        else:
            chosen_names = random.sample(all_pokemon, num_pokemon)
            
        team_sets: list[str] = []
        for name in chosen_names:
            # For each chosen pokemon, we need to pick a build.
            # If we are including lower tiers, the pokemon might exist in multiple allowed tiers.
            # We should probably pick a build from the highest available tier for that pokemon, 
            # or just random across allowed tiers. Random across allowed tiers is simpler and adds variety.
            
            # We need to find which tiers this pokemon has builds for, within our allowed list
            cursor.execute(f"SELECT DISTINCT tier FROM pokemon_builds WHERE pokemon_name = ? AND tier IN ({placeholders})", 
                           (name, *allowed_tiers))
            available_tiers_for_mon = [row["tier"] for row in cursor.fetchall()]
            
            if not available_tiers_for_mon:
                continue # Should not happen given previous query
                
            # Pick a random tier for this specific pokemon
            chosen_tier = random.choice(available_tiers_for_mon)
            
            build_data = _get_random_build_from_db(conn, chosen_tier, name)
            if build_data:
                team_sets.append(_build_showdown_set(name, build_data))
                
        return "\n\n".join(team_sets)
        
    finally:
        conn.close()


def generate_random_team_for_tier(tier: str, include_lower_tiers: bool = True) -> tuple[str, str]:
    """Generate a random team for the given tier and upload to Pokepaste.

    Returns a tuple of (team_text, pokepaste_url).
    """
    team_text = _build_random_team_for_tier(tier, include_lower_tiers=include_lower_tiers)
    url = showdown_to_pokepaste(
        team_text=team_text,
        title=f"Random {tier} Team",
        author="PokemonTeamBuilder",
        notes=f"Randomly generated {tier} team",
        public=True,
    )
    return team_text, url


def main() -> None:
    st.set_page_config(page_title="Pokemon Team Generator", layout="wide")

    # Load custom CSS for prettier team grid styling
    css_path = Path(__file__).with_name("streamlit_styles.css")
    if css_path.is_file():
        css = css_path.read_text(encoding="utf-8")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

    st.title("Pokemon Team Generator")
    st.markdown(
        """
        Welcome to the **Pokemon Team Generator**! 🚀

        Instantly create competitive teams for any tier. Simply select your desired tier, choose whether to include lower-tier Pokemon, and click **Generate**. Your team will be ready in seconds, complete with a Pokepaste link for easy export to Showdown.
        """
    )

    # Tier selector
    tiers = _load_available_tiers()
    tier_index = 0
    if "OU" in tiers:
        tier_index = tiers.index("OU")
        
    tier = st.selectbox("Select tier", options=tiers, index=tier_index)
    
    include_lower = st.checkbox("Include Pokemon from lower tiers", value=True)

    if st.button("Generate random team"):
        with st.spinner("Generating team and uploading to Pokepaste..."):
            try:
                team_text, paste_url = generate_random_team_for_tier(tier, include_lower_tiers=include_lower)
            except Exception as exc:  # pragma: no cover - UI error path
                st.error(f"Failed to generate team or upload to Pokepaste: {exc}")
                return

        # Success banner with inline Pokepaste link, constrained width
        banner_left, banner_center, banner_right = st.columns([1, 3, 1])
        with banner_center:
            st.success(f"Team generated!  [Open in Pokepaste]({paste_url})")

        # Parse the team and fetch sprite URLs from Pokepaste
        team_entries = _parse_showdown_team(team_text)
        image_urls = _fetch_pokemon_image_urls_from_pokepaste(paste_url)

        # 2x3 grid layout for the team, wrapped in a styled container
        st.markdown('<div class="team-grid">', unsafe_allow_html=True)
        rows = 2
        cols_per_row = 3
        idx = 0
        for _ in range(rows):
            cols = st.columns(cols_per_row)
            for col in cols:
                if idx >= len(team_entries):
                    break
                entry = team_entries[idx]
                img_url = image_urls[idx] if idx < len(image_urls) else None
                with col:
                    moves_html = "".join(
                        f"<span class='move-pill'>{m}</span>" for m in entry["moves"]
                    )

                    # Build a Showdown-inspired card layout
                    card_html = "<div class='team-card'>"
                    card_html += "<div class='team-card-header'>"
                    card_html += f"<span class='team-name'>{entry['name']}</span>"
                    card_html += "</div>"  # header

                    card_html += "<div class='team-card-body'>"

                    # LEFT COLUMN: sprite + basic info
                    card_html += "<div>"  # left col wrapper
                    if img_url:
                        card_html += "<div class='team-sprite'>"
                        card_html += f"<img src='{img_url}' alt='{entry['name']}'>"
                        card_html += "</div>"
                    if any([entry["item"], entry["ability"], entry["tera_type"], entry["nature"]]):
                        card_html += "<div class='sprite-info'>"
                        if entry["item"]:
                            card_html += (
                                "<div class='sprite-info-row'><span class='detail-label'>Item:</span>"
                                f"<span>{entry['item']}</span></div>"
                            )
                        if entry["ability"]:
                            card_html += (
                                "<div class='sprite-info-row'><span class='detail-label'>Ability:</span>"
                                f"<span>{entry['ability']}</span></div>"
                            )
                        if entry["tera_type"]:
                            card_html += (
                                "<div class='sprite-info-row'><span class='detail-label'>Tera:</span>"
                                f"<span>{entry['tera_type']}</span></div>"
                            )
                        if entry["nature"]:
                            card_html += (
                                "<div class='sprite-info-row'><span class='detail-label'>Nature:</span>"
                                f"<span>{entry['nature'].replace('Nature: ', '')}</span></div>"
                            )
                        card_html += "</div>"  # sprite-info
                    card_html += "</div>"  # left col

                    # MIDDLE COLUMN: moves
                    card_html += "<div class='team-moves-col'>"
                    card_html += "<div class='team-moves-title'>Moves</div>"
                    if moves_html:
                        card_html += f"<div class='team-moves'>{moves_html}</div>"
                    card_html += "</div>"  # moves col

                    # RIGHT COLUMN: EVs as labeled rows
                    card_html += "<div class='team-evs-col'>"
                    card_html += "<div class='team-evs-title'>EVs</div>"
                    if entry["evs"]:
                        raw = entry["evs"].replace("EVs: ", "")
                        parts = [p for p in raw.split(" / ") if p]
                        evs_map = {"HP": 0, "Atk": 0, "Def": 0, "SpA": 0, "SpD": 0, "Spe": 0}
                        for p in parts:
                            sub = p.split()
                            if len(sub) != 2:
                                continue
                            try:
                                val = int(sub[0])
                            except ValueError:
                                continue
                            stat = sub[1]
                            if stat in evs_map:
                                evs_map[stat] = val

                        ordered_stats = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]
                        rows = []
                        for stat in ordered_stats:
                            val = evs_map[stat]
                            rows.append(f"<div class='team-evs-row'>{stat}: {val}</div>")
                        card_html += "<div class='team-evs-table'>" + "".join(rows) + "</div>"
                    card_html += "</div>"  # evs col

                    card_html += "</div>"  # team-card-body

                    card_html += "</div>"  # team-card

                    st.markdown(card_html, unsafe_allow_html=True)
                idx += 1
        st.markdown('</div>', unsafe_allow_html=True)

        # Raw Showdown text hidden by default inside an expander
        with st.expander("Show raw Showdown team"):
            st.code(team_text, language="text")


if __name__ == "__main__":
    main()
