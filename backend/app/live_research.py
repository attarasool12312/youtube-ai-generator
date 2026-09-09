import logging
import re
from datetime import datetime, timezone

from app.gemini_router import generate_grounded


logger = logging.getLogger(__name__)


CURRENT_PHRASES = (
    "breaking",
    "latest",
    "today",
    "tonight",
    "this week",
    "this month",
    "right now",
    "new study",
    "new research",
    "new discovery",
    "newly discovered",
    "recent discovery",
    "recently discovered",
    "recent findings",
    "latest findings",
    "just announced",
    "just discovered",
    "just confirmed",
    "just released",
    "new report",
    "latest report",
    "latest update",
    "new update",
    "current situation",
    "current data",
    "market today",
    "stocks today",
    "scientists just",
    "nasa just",
    "spacex just",
)


def needs_live_research(title, style=""):
    text = " ".join(
        str(title or "").lower().split()
    )

    if not text:
        return False

    if any(
        phrase in text
        for phrase in CURRENT_PHRASES
    ):
        return True

    year = datetime.now(timezone.utc).year

    # A current-year title usually promises current facts.
    if re.search(
        rf"\b(?:{year}|{year - 1})\b",
        text,
    ):
        return True

    return False


def prepare_live_research(title, style=""):
    if not needs_live_research(title, style):
        print(
            "LIVE_RESEARCH|skip|evergreen",
            flush=True,
        )
        return None

    now = datetime.now(timezone.utc)

    prompt = f"""
You are the factual research layer for a YouTube documentary/explainer.

CURRENT UTC DATE:
{now.date().isoformat()}

VIDEO TITLE:
{title}

VIDEO PROFILE:
{style or "auto"}

Use live Google Search.

Build a concise factual research pack for the writer.

Requirements:
- Research what is actually true as of the current date.
- Prefer primary, official, scientific, government, company, institutional,
  or highly reputable reporting sources.
- Verify important dates, names, numbers, announcements and scientific claims.
- Distinguish confirmed facts from estimates, speculation or disputed claims.
- If sources disagree, state the disagreement.
- Never treat another YouTube title as factual proof.
- Never invent a breaking event, discovery, NASA confirmation or study.
- Prioritize the newest trustworthy information when the topic is time-sensitive.
- Include enough background context to prevent misleading narration.
- Keep it concise: approximately 8-14 high-value factual points.
- Do NOT write the video script.
- Do NOT add a generic introduction or conclusion.

The next AI writer will use this as internal factual context.
""".strip()

    try:
        result = generate_grounded(prompt)
    except Exception as error:
        # Research failure must never kill a video.
        logger.warning(
            "LIVE_RESEARCH_FAILED: %s",
            error,
        )
        print(
            "LIVE_RESEARCH|fallback|normal-script",
            flush=True,
        )
        return None

    sources = result.get("sources") or []

    source_text = "\n".join(
        (
            f"- {item.get('title', '').strip()}: "
            f"{item.get('url', '').strip()}"
        )
        for item in sources
        if item.get("url")
    )

    grounded_title = f"""
{title}

INTERNAL LIVE-RESEARCH CONTEXT
Current date: {now.date().isoformat()}

{result["text"]}

TRUSTED SEARCH SOURCES
{source_text or "Grounded by Google Search; no source URLs returned."}

WRITER RULES:
- Treat the research above as internal factual grounding.
- Do not mention this research block, URLs, search process or AI in narration.
- Do not invent facts beyond the evidence.
- For time-sensitive claims, preserve dates and uncertainty accurately.
- Keep the original video title/topic and write a natural standalone script.
""".strip()

    research = {
        "enabled": True,
        "generated_at_utc": now.isoformat(),
        "provider": "gemini:" + str(
            result.get("model") or ""
        ),
        "text": result["text"],
        "sources": sources,
        "search_queries": (
            result.get("search_queries")
            or []
        ),
        "input_tokens": result.get(
            "input_tokens",
            0,
        ),
        "output_tokens": result.get(
            "output_tokens",
            0,
        ),
        "grounded_title": grounded_title,
    }

    print(
        "LIVE_RESEARCH|enabled|"
        f"provider={research['provider']}|"
        f"sources={len(sources)}",
        flush=True,
    )

    return research
