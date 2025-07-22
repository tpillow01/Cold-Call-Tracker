import sqlite3

conn = sqlite3.connect('calls.db')

# Check if rep_id column exists
columns = [col[1] for col in conn.execute("PRAGMA table_info(cold_calls)").fetchall()]
if 'rep_id' not in columns:
    conn.execute('ALTER TABLE cold_calls ADD COLUMN rep_id INTEGER DEFAULT 1')
    conn.commit()
    print("✅ Added missing 'rep_id' column to cold_calls.")
else:
    print("✅ 'rep_id' column already exists.")

conn.close()
