from __future__ import annotations

import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""

    if result.returncode != 0:
        return ""

    return result.stdout.strip()


def _detect_environment() -> str:
    configured = (
        os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("ENV")
    )

    if configured:
        return configured.strip().title()

    try:
        hostname = (
            urlparse(settings.app_url).hostname or ""
        ).lower()
    except ValueError:
        hostname = ""

    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return "Development"

    return "Production"


def get_project_info() -> dict[str, str | int]:
    branch = (
        os.getenv("DEPLOYMENT_BRANCH")
        or _git("branch", "--show-current")
        or "Detached HEAD"
    )

    exact_tag = _git(
        "describe",
        "--tags",
        "--exact-match",
    )

    latest_tag = _git(
        "describe",
        "--tags",
        "--abbrev=0",
    )

    describe = _git(
        "describe",
        "--tags",
        "--always",
        "--dirty",
    )

    commit_hash = _git("rev-parse", "HEAD")
    commit_short = _git("rev-parse", "--short", "HEAD")

    commit_date = _git(
        "log",
        "-1",
        "--format=%ci",
    )

    commit_subject = _git(
        "log",
        "-1",
        "--format=%s",
    )

    app_version = (
        os.getenv("APP_VERSION")
        or exact_tag
        or describe
        or commit_short
        or "Unversioned"
    )

    production_tag = (
        os.getenv("PRODUCTION_TAG")
        or latest_tag
        or "Not tagged"
    )

    checkpoint_parts = [
        value
        for value in (
            commit_short,
            commit_date,
            commit_subject,
        )
        if value
    ]

    return {
        "application_name": settings.app_name,
        "version": app_version,
        "branch": branch,
        "production_tag": production_tag,
        "environment": _detect_environment(),
        "environment_status": "Running",
        "checkpoint": " · ".join(checkpoint_parts)
        or "Unavailable",
        "commit_hash": commit_hash or "Unavailable",
        "commit_short": commit_short or "Unavailable",
        "hostname": platform.node() or "Unknown host",
        "operating_system": (
            f"{platform.system()} {platform.release()}"
        ).strip(),
        "python_version": platform.python_version(),
        "architecture": platform.machine() or "Unknown",
        "current_year": datetime.now(
            timezone.utc
        ).year,
        "runtime": (
            f"Python {platform.python_version()} "
            f"on {platform.machine() or 'unknown architecture'}"
        ),
        "executable": sys.executable,
    }
