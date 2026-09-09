# Backup & Restore

ATTA includes a lightweight portable backup system.

## Included

- `backend/data/app.db`
- `.env`
- `backend/data/gemini_router_state.json`
- `backend/data/media_history.json`
- `backend/data/video_presets.json` when present
- manifest/checksum metadata

## Excluded

- generated videos
- output media
- model caches
- virtual environments
- avatars
- cloned-voice media

## Create from dashboard

Open **Backup & Restore** and click **Create Backup**.

## Create from terminal

```bash
bash scripts/backup_user_data.sh
```

Expected:

```text
BACKUP_OK /path/to/atta-user-data-YYYYMMDD-HHMMSS.tar.gz
```

## Verify

```bash
sha256sum -c backups/atta-user-data-YYYYMMDD-HHMMSS.tar.gz.sha256
tar -tzf backups/atta-user-data-YYYYMMDD-HHMMSS.tar.gz
```

## Restore from dashboard

1. Upload the backup archive.
2. Select it under Available Backups.
3. Start Restore.
4. The app will temporarily restart.
5. Refresh after completion.

Restore log:

```text
backups/restore.log
```

## Restore from terminal

```bash
bash scripts/restore_user_data.sh /path/to/atta-user-data-YYYYMMDD-HHMMSS.tar.gz
```

Before replacing current state, restore keeps a safety copy under:

```text
backend/data/pre-restore-YYYYMMDD-HHMMSS/
```

## Presets

Saved presets are mirrored to:

```text
backend/data/video_presets.json
```

This allows presets to survive a new VM/browser restore.

## Security

The archive can contain secrets and protected credentials. Treat it like a password file and never upload it publicly.
