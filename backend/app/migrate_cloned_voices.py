"""
Add cloned_voices table to the database.
Run once: PYTHONPATH=backend .venv/bin/python backend/app/migrate_cloned_voices.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "app.db"


def migrate():
    conn = sqlite3.connect(str(DB_PATH))

    # Check if table already exists
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )]

    if "cloned_voices" in tables:
        print("cloned_voices table already exists. Skipping.")
        conn.close()
        return

    conn.execute("""
        CREATE TABLE cloned_voices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name VARCHAR(100) NOT NULL,
            reference_audio_path TEXT NOT NULL,
            preview_audio_path TEXT,
            status VARCHAR(30) DEFAULT 'ready',
            error_message TEXT,
            exaggeration FLOAT DEFAULT 0.5,
            cfg_weight FLOAT DEFAULT 0.5,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # Add cloned_voice_id column to video_jobs if not present
    cols = [r[1] for r in conn.execute("PRAGMA table_info(video_jobs)")]
    if "cloned_voice_id" not in cols:
        conn.execute(
            "ALTER TABLE video_jobs ADD COLUMN cloned_voice_id INTEGER"
        )
        print("Added cloned_voice_id to video_jobs")

    conn.commit()
    conn.close()
    print("cloned_voices table created successfully")


if __name__ == "__main__":
    migrate()
