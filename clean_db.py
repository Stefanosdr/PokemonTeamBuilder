import sqlite3

TIER_ORDER = ["AG", "Uber", "OU", "UU", "RU", "NU", "PU", "ZU"]

def get_tier_index(tier):
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return -1

conn = sqlite3.connect('pokemon_strategies.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("Starting database cleanup...")

# 1. Get all pokemon and their tiers
cursor.execute("SELECT id, pokemon_name, tier FROM pokemon_builds")
rows = cursor.fetchall()

pokemon_tiers = {}
for row in rows:
    p_name = row["pokemon_name"]
    if p_name not in pokemon_tiers:
        pokemon_tiers[p_name] = []
    pokemon_tiers[p_name].append(row)

ids_to_delete = []
deleted_count = 0

for p_name, entries in pokemon_tiers.items():
    if len(entries) <= 1:
        continue
        
    # Find the lowest tier (highest index)
    max_index = -1
    lowest_tier = None
    
    for entry in entries:
        idx = get_tier_index(entry["tier"])
        if idx > max_index:
            max_index = idx
            lowest_tier = entry["tier"]
            
    # Identify IDs to delete (those NOT in the lowest tier)
    # Note: If there are multiple entries in the SAME lowest tier, we keep all of them (different builds)
    for entry in entries:
        if entry["tier"] != lowest_tier:
            ids_to_delete.append(entry["id"])

print(f"Found {len(ids_to_delete)} entries to delete (non-native tiers).")

if ids_to_delete:
    # Delete in chunks to be safe
    chunk_size = 500
    for i in range(0, len(ids_to_delete), chunk_size):
        chunk = ids_to_delete[i:i+chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        
        # We also need to delete associated data in other tables
        # build_items, build_abilities, build_natures, build_evs, build_tera_types, build_moves
        tables = ["build_items", "build_abilities", "build_natures", "build_evs", "build_tera_types", "build_moves"]
        
        for table in tables:
            cursor.execute(f"DELETE FROM {table} WHERE build_id IN ({placeholders})", chunk)
            
        cursor.execute(f"DELETE FROM pokemon_builds WHERE id IN ({placeholders})", chunk)
        deleted_count += len(chunk)
        print(f"Deleted chunk {i//chunk_size + 1}...")

    conn.commit()
    print(f"Successfully deleted {deleted_count} entries.")
    
    # Vacuum to reclaim space
    print("Vacuuming database...")
    cursor.execute("VACUUM")
    
else:
    print("No duplicates found to delete.")

conn.close()
print("Cleanup complete.")
