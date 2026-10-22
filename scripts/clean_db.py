import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), "..", "careflow.db")
conn = sqlite3.connect(db_path)

conn.execute("UPDATE users SET name = 'Dr. Sharma' WHERE id = 'doctor_sharma'")
conn.execute("UPDATE users SET name = 'Sunita Sharma' WHERE id LIKE '%nurse_sunita%'")
conn.execute("UPDATE users SET name = 'Priya Patel' WHERE id LIKE '%patient_demo%' OR id LIKE '%patient_priya%'")
conn.execute("UPDATE users SET name = 'Admin' WHERE id = 'admin_user'")

# If nurse took it, patient_name should NOT be the nurse's name
conn.execute("UPDATE cases SET patient_name = 'Assisted Patient' WHERE created_by LIKE '%nurse%' AND (patient_name IS NULL OR patient_name LIKE '%Sunita%' OR patient_name LIKE '%Nurse%')")
conn.execute("UPDATE cases SET patient_name = 'Priya Patel' WHERE id LIKE 'demo_%' OR patient_name LIKE '%Priya%'")
conn.commit()

print("Cleaned successfully!")
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT id, pseudonym_code, patient_name, created_by FROM cases").fetchall()
for r in rows:
    print(r["pseudonym_code"], "-> Patient:", r["patient_name"], "| CreatedBy:", r["created_by"])
