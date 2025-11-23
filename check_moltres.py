import sqlite3

try:
    conn = sqlite3.connect('temp_check.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT pb.tier, bi.item_name 
        FROM pokemon_builds pb
        JOIN build_items bi ON pb.id = bi.build_id
        WHERE pb.pokemon_name = 'Moltres'
    """)
    rows = cursor.fetchall()
    print(f"Moltres Builds: {rows}")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
