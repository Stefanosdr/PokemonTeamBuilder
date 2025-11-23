import sqlite3

TIER_ORDER = ["AG", "Uber", "OU", "UU", "RU", "NU", "PU", "ZU"]

def get_tier_index(tier):
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return -1

conn = sqlite3.connect('temp_debug.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

selected_tier = 'OU'
selected_tier_index = get_tier_index(selected_tier)

print(f"Testing Strict Mode for Tier: {selected_tier} (Index {selected_tier_index})")

# 1. Get all pokemon in the selected tier
cursor.execute("SELECT DISTINCT pokemon_name FROM pokemon_builds WHERE tier = ?", (selected_tier,))
candidates = [row['pokemon_name'] for row in cursor.fetchall()]

print(f"Found {len(candidates)} candidates in {selected_tier} (including lower tiers)")

# Check specific problematic ones
problematic = ['Gothitelle', 'Lapras', 'Copperajah', 'Weavile', 'Revavroom', 'Dudunsparce', 'Garchomp', 'Great Tusk']
for p in problematic:
    if p in candidates:
        # Get all tiers for this pokemon
        cursor.execute("SELECT DISTINCT tier FROM pokemon_builds WHERE pokemon_name = ?", (p,))
        tiers = [row['tier'] for row in cursor.fetchall()]
        
        # Find the lowest tier (highest index)
        max_index = -1
        lowest_tier = None
        
        for t in tiers:
            idx = get_tier_index(t)
            if idx > max_index:
                max_index = idx
                lowest_tier = t
                
        is_native = (max_index == selected_tier_index)
        print(f"{p}: Tiers={tiers}, Lowest={lowest_tier}, Native to {selected_tier}? {is_native}")

conn.close()
