import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "app.db"

def migrate():
    conn = sqlite3.connect(str(DB_PATH))

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cloned_voices (
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tts_generations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            voice_name TEXT NOT NULL,
            voice_type TEXT NOT NULL,
            text_preview TEXT,
            word_count INTEGER DEFAULT 0,
            duration_seconds FLOAT DEFAULT 0,
            audio_path TEXT,
            format TEXT DEFAULT 'mp3',
            title TEXT,
            status TEXT DEFAULT 'completed',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cols = [r[1] for r in conn.execute("PRAGMA table_info(video_jobs)")]
    if "cloned_voice_id" not in cols:
        conn.execute("ALTER TABLE video_jobs ADD COLUMN cloned_voice_id INTEGER")

    conn.commit()
    conn.close()
    print("VOICE_DATABASE_MIGRATION_OK")

if __name__ == "__main__":
    migrate()
