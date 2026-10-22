"""One-off demo cleanup: keep only the five benchmark cases and no uploads."""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "careflow.db")
UPLOADS = os.path.join(BASE, "data", "uploads")
BACKUP_DIR = os.path.join(os.environ.get("TEMP", BASE), "opencode")
os.makedirs(BACKUP_DIR, exist_ok=True)
backup = os.path.join(BACKUP_DIR, f"careflow-before-clean-{datetime.now():%Y%m%d-%H%M%S}.db")
shutil.copy2(DB, backup)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
before = conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"]
for table in ("clinician_actions", "ai_outputs", "intakes"):
    conn.execute(f"DELETE FROM {table} WHERE case_id NOT LIKE 'demo_%'")
conn.execute("DELETE FROM cases WHERE id NOT LIKE 'demo_%'")
conn.commit()
conn.execute("VACUUM")
after = conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"]
remaining = [r["id"] for r in conn.execute("SELECT id FROM cases ORDER BY id")]
conn.close()

shutil.rmtree(UPLOADS, ignore_errors=True)
os.makedirs(UPLOADS, exist_ok=True)

print(f"backup: {backup}")
print(f"cases before: {before}, after: {after}")
print("remaining:", remaining)
