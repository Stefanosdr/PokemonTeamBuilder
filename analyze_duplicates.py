import sqlite3

TIER_ORDER = ["AG", "Uber", "OU", "UU", "RU", "NU", "PU", "ZU"]

conn = sqlite3.connect('pokemon_strategies.db')
cursor = conn.cursor()

cursor.execute("SELECT pokemon_name, tier FROM pokemon_builds")
rows = cursor.fetchall()

pokemon_tiers = {}
for name, tier in rows:
    if name not in pokemon_tiers:
        pokemon_tiers[name] = []
    pokemon_tiers[name].append(tier)

# Count duplicates based on UNIQUE tiers
duplicates = {k: v for k, v in pokemon_tiers.items() if len(set(v)) > 1}

print(f"Total Pokemon: {len(pokemon_tiers)}")
print(f"Pokemon with multiple UNIQUE tiers: {len(duplicates)}")

# Sample some duplicates
print("\nSample Duplicates:")
for name in list(duplicates.keys())[:5]:
    print(f"{name}: {duplicates[name]}")

conn.close()
