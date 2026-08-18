from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from statistics import median
from urllib.parse import urlparse

from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_provider import call_gemini
from app.models import YouTubeConnection
from app.video_profiles import VIDEO_PROFILES
from app.youtube_connections import refresh_channel_credentials


ALLOWED_DURATIONS = {3, 5, 8, 10, 15, 20, 25, 30}
TITLE_RESEARCH_DURATION = 25


def _json(text: str) -> dict:
    raw = (text or "").strip()

    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\\s*", "", raw)
        raw = re.sub(r"\\s*```$", "", raw)

    start = raw.find("{")
    end = raw.rfind("}")

    if start < 0 or end < start:
        raise ValueError("AI returned invalid JSON.")

    payload = raw[start:end + 1]

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        repaired = call_gemini(
            f"""
Repair the malformed JSON below.

Rules:
- Preserve all original information and wording.
- Do not add new ideas, titles, scores or facts.
- Only fix JSON syntax.
- Return one valid JSON object only.

MALFORMED JSON:
{payload}
""".strip(),
            json_mode=True,
        )

        repaired_text = (repaired.text or "").strip()

        if repaired_text.startswith("```"):
            repaired_text = re.sub(
                r"^```(?:json)?\\s*",
                "",
                repaired_text,
            )
            repaired_text = re.sub(
                r"\\s*```$",
                "",
                repaired_text,
            )

        repair_start = repaired_text.find("{")
        repair_end = repaired_text.rfind("}")

        if repair_start < 0 or repair_end < repair_start:
            raise ValueError("AI returned invalid JSON.")

        data = json.loads(
            repaired_text[repair_start:repair_end + 1]
        )

    if not isinstance(data, dict):
        raise ValueError("AI response must be a JSON object.")

    return data


def _youtube(
    db: Session,
    connection_id: int | None = None,
):
    connection = None

    if connection_id:
        connection = db.get(
            YouTubeConnection,
            int(connection_id),
        )

        if (
            connection is None
            or not connection.is_active
            or not connection.encrypted_refresh_token
        ):
            raise RuntimeError(
                "Selected YouTube channel is unavailable."
            )

    if connection is None:
        connection = db.scalar(
            select(YouTubeConnection)
            .where(
                YouTubeConnection.is_active.is_(True),
                YouTubeConnection.encrypted_refresh_token.is_not(None),
            )
            .order_by(YouTubeConnection.id.asc())
        )

    if not connection:
        raise RuntimeError("Connect a YouTube channel first.")

    credentials = refresh_channel_credentials(
        db,
        connection,
    )

    return build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )



def _num(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _age_days(published_at: str) -> float:
    try:
        dt = datetime.fromisoformat(
            published_at.replace("Z", "+00:00")
        )
        return max(
            1.0,
            (datetime.now(timezone.utc) - dt).total_seconds() / 86400,
        )
    except Exception:
        return 3650.0


def _tokens(value: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9]+", (value or "").casefold())
        if len(word) > 2
        and word not in {
            "the", "and", "for", "with", "that", "this",
            "why", "how", "what", "when", "where", "from",
        }
    }


def _similarity(a: str, b: str) -> float:
    left = _tokens(a)
    right = _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _percentile(value: float, values: list[float]) -> float:
    if not values:
        return 50.0
    below = sum(item < value for item in values)
    equal = sum(item == value for item in values)
    return 100.0 * (below + 0.5 * equal) / len(values)


def _resolve_competitor(youtube, value: str) -> dict | None:
    value = (value or "").strip()
    if not value:
        return None

    channel_id = ""
    handle = ""

    if "youtube.com" in value:
        parsed = urlparse(
            value if "://" in value else f"https://{value}"
        )
        parts = [part for part in parsed.path.split("/") if part]

        if len(parts) >= 2 and parts[0] == "channel":
            channel_id = parts[1]
        elif parts and parts[0].startswith("@"):
            handle = parts[0][1:]
    elif value.startswith("UC"):
        channel_id = value
    elif value.startswith("@"):
        handle = value[1:]

    kwargs = {
        "part": "snippet,contentDetails,statistics",
        "maxResults": 1,
    }

    if channel_id:
        kwargs["id"] = channel_id
    elif handle:
        kwargs["forHandle"] = handle
    else:
        result = youtube.search().list(
            part="snippet",
            type="channel",
            q=value,
            maxResults=1,
        ).execute()

        items = result.get("items", [])
        if not items:
            return None

        kwargs["id"] = items[0]["id"]["channelId"]

    response = youtube.channels().list(**kwargs).execute()
    items = response.get("items", [])
    if not items:
        return None

    channel = items[0]
    uploads = (
        channel.get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads")
    )

    video_ids = []
    if uploads:
        playlist = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads,
            maxResults=25,
        ).execute()
        video_ids = [
            item.get("contentDetails", {}).get("videoId")
            for item in playlist.get("items", [])
        ]
        video_ids = [item for item in video_ids if item]

    videos = []
    if video_ids:
        response = youtube.videos().list(
            part="snippet,statistics",
            id=",".join(video_ids),
        ).execute()

        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            age = _age_days(snippet.get("publishedAt", ""))
            views = _num(stats.get("viewCount"))

            videos.append({
                "title": snippet.get("title", ""),
                "views": views,
                "views_per_day": round(views / age, 2),
                "published_at": snippet.get("publishedAt", ""),
            })

    videos.sort(
        key=lambda item: item["views_per_day"],
        reverse=True,
    )

    return {
        "channel_id": channel.get("id"),
        "channel_title": channel.get("snippet", {}).get("title", ""),
        "subscribers": _num(
            channel.get("statistics", {}).get("subscriberCount")
        ),
        "recent_videos": videos,
        "top_outliers": videos[:8],
    }


def _research_queries(
    prompt: str,
    niche: str,
    competitor: dict | None,
) -> list[str]:
    competitor_context = ""
    if competitor:
        competitor_context = "\nCompetitor top videos:\n" + "\n".join(
            f"- {item['title']}"
            for item in competitor.get("top_outliers", [])[:8]
        )

    result = call_gemini(
        f"""
Turn this user's YouTube title research request into 3 concise
YouTube search queries that will reveal real demand and competition.

USER PROMPT:
{prompt}

NICHE/TOPIC:
{niche or "Not specified"}
{competitor_context}

Do not create titles yet.
Return JSON only:
{{
  "queries": ["query 1", "query 2", "query 3"]
}}
""".strip(),
        json_mode=True,
    )

    data = _json(result.text)
    queries = []

    for item in data.get("queries", []):
        query = " ".join(str(item).split()).strip()
        if query and query.casefold() not in {
            existing.casefold() for existing in queries
        }:
            queries.append(query)

    return queries[:3]


def _collect_query_data(youtube, query: str) -> dict:
    search = youtube.search().list(
        part="snippet",
        type="video",
        q=query,
        order="relevance",
        maxResults=15,
    ).execute()

    ids = [
        item.get("id", {}).get("videoId")
        for item in search.get("items", [])
    ]
    ids = [item for item in ids if item]

    if not ids:
        return {
            "query": query,
            "videos": [],
            "median_views": 0,
            "median_views_per_day": 0,
            "median_engagement": 0,
            "fresh_ratio": 0,
        }

    response = youtube.videos().list(
        part="snippet,statistics",
        id=",".join(ids),
    ).execute()

    videos = []

    for item in response.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})

        views = _num(stats.get("viewCount"))
        likes = _num(stats.get("likeCount"))
        comments = _num(stats.get("commentCount"))
        age = _age_days(snippet.get("publishedAt", ""))

        engagement = (
            ((likes + comments) / views) * 100
            if views else 0.0
        )

        videos.append({
            "title": snippet.get("title", ""),
            "channel_id": snippet.get("channelId", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "published_at": snippet.get("publishedAt", ""),
            "age_days": round(age, 1),
            "views": views,
            "views_per_day": round(views / age, 2),
            "engagement": round(engagement, 4),
        })

    return {
        "query": query,
        "videos": videos,
        "median_views": median(
            [item["views"] for item in videos]
        ) if videos else 0,
        "median_views_per_day": median(
            [item["views_per_day"] for item in videos]
        ) if videos else 0,
        "median_engagement": median(
            [item["engagement"] for item in videos]
        ) if videos else 0,
        "fresh_ratio": (
            sum(item["age_days"] <= 365 for item in videos)
            / len(videos)
            if videos else 0
        ),
    }


def _generate_candidates(
    prompt: str,
    niche: str,
    research: list[dict],
    competitor: dict | None,
) -> list[dict]:
    research_summary = []

    for item in research:
        research_summary.append({
            "query": item["query"],
            "median_views": round(item["median_views"]),
            "median_views_per_day": round(
                item["median_views_per_day"], 2
            ),
            "median_engagement": round(
                item["median_engagement"], 3
            ),
            "fresh_ratio": round(item["fresh_ratio"], 3),
            "existing_titles": [
                video["title"]
                for video in item["videos"][:10]
            ],
        })

    competitor_summary = None
    if competitor:
        competitor_summary = {
            "channel": competitor["channel_title"],
            "subscribers": competitor["subscribers"],
            "top_outliers": competitor["top_outliers"],
        }

    profile_keys = list(VIDEO_PROFILES.keys())

    result = call_gemini(
        f"""
Generate 12 strong, truthful YouTube video title opportunities from
the user's request and the REAL YouTube research data below.

USER PROMPT:
{prompt}

NICHE:
{niche or "Not specified"}

RESEARCH:
{json.dumps(research_summary, ensure_ascii=False)}

COMPETITOR:
{json.dumps(competitor_summary, ensure_ascii=False)}

Rules:
- Titles must match the user's actual request.
- Do not simply copy existing titles.
- Learn the competitor's topic selection and curiosity structure, not
  unsupported factual claims.
- Prefer clear curiosity, useful viewer intent and strong specificity.
- YouTube search titles are research signals, not proof that their claims
  are factually true.
- Do not fabricate timestamps such as "3 MINUTES AGO", breaking-news
  wording, "NASA CONFIRMED", new signals, discoveries or scientific
  findings merely because competitor titles use those phrases.
- Titles may be dramatic, but must not present an unverified event as fact.
- Do not invent search volume or statistics.
- source_query must exactly match one RESEARCH query.
- Choose one existing profile key only.
- recommended_duration must always be 25.
- Keep reasons short and evidence-aware.

Existing profile keys:
{json.dumps(profile_keys)}

Supported durations:
{sorted(ALLOWED_DURATIONS)}

Return JSON only:
{{
  "titles": [
    {{
      "title": "...",
      "source_query": "...",
      "intent": "...",
      "reason": "...",
      "recommended_profile": "...",
      "recommended_duration": 10
    }}
  ]
}}
""".strip(),
        json_mode=True,
    )

    data = _json(result.text)
    candidates = []

    query_names = {item["query"] for item in research}

    for item in data.get("titles", []):
        title = " ".join(str(item.get("title", "")).split()).strip()
        source_query = str(item.get("source_query", "")).strip()
        profile = str(
            item.get("recommended_profile", "auto")
        ).strip()
        duration = TITLE_RESEARCH_DURATION

        if not title:
            continue

        if source_query not in query_names:
            source_query = research[0]["query"] if research else ""

        if profile not in VIDEO_PROFILES:
            profile = "auto"

        candidates.append({
            "title": title,
            "source_query": source_query,
            "intent": str(item.get("intent", "")).strip(),
            "reason": str(item.get("reason", "")).strip(),
            "recommended_profile": profile,
            "recommended_duration": duration,
        })

    return candidates[:12]


def _strength_label(score: float) -> str:
    if score >= 75:
        return "Excellent Pick"
    if score >= 65:
        return "Strong Pick"
    if score >= 55:
        return "Good Opportunity"
    if score >= 45:
        return "Consider"
    return "Low Priority"


def _score_candidates(
    candidates: list[dict],
    research: list[dict],
) -> list[dict]:
    if not candidates:
        return []

    query_map = {
        item["query"]: item
        for item in research
    }

    all_videos = [
        video
        for group in research
        for video in group.get("videos", [])
    ]

    if not all_videos:
        return []

    velocity_values = [
        math.log1p(video.get("views_per_day", 0))
        for video in all_videos
    ]

    engagement_values = [
        video.get("engagement", 0)
        for video in all_videos
    ]

    freshness_values = [
        math.exp(
            -float(video.get("age_days", 3650)) / 365.0
        )
        for video in all_videos
    ]

    existing_titles = [
        video.get("title", "")
        for video in all_videos
    ]

    scored = []

    for candidate in candidates:
        candidate_title = candidate["title"]

        related = []

        for video in all_videos:
            similarity = _similarity(
                candidate_title,
                video.get("title", ""),
            )

            if similarity > 0:
                related.append(
                    (similarity, video)
                )

        related.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        related = related[:6]

        # If wording is very original, use the source-query
        # neighbourhood rather than giving the title no evidence.
        if not related:
            source = query_map.get(
                candidate["source_query"],
                {},
            )

            related = [
                (
                    0.15,
                    video,
                )
                for video in source.get("videos", [])[:6]
            ]

        def weighted_average(getter, fallback=0.0):
            total_weight = 0.0
            total_value = 0.0

            for similarity, video in related:
                weight = max(0.10, similarity)
                total_weight += weight
                total_value += getter(video) * weight

            if not total_weight:
                return fallback

            return total_value / total_weight

        demand_raw = weighted_average(
            lambda video: math.log1p(
                video.get("views_per_day", 0)
            )
        )

        engagement_raw = weighted_average(
            lambda video: video.get(
                "engagement",
                0,
            )
        )

        freshness_raw = weighted_average(
            lambda video: math.exp(
                -float(
                    video.get(
                        "age_days",
                        3650,
                    )
                ) / 365.0
            )
        )

        audience_interest = _percentile(
            demand_raw,
            velocity_values,
        )

        viewer_response = _percentile(
            engagement_raw,
            engagement_values,
        )

        trend_activity = _percentile(
            freshness_raw,
            freshness_values,
        )

        max_similarity = max(
            (
                _similarity(
                    candidate_title,
                    existing,
                )
                for existing in existing_titles
            ),
            default=0.0,
        )

        originality = max(
            0.0,
            100.0 * (1.0 - max_similarity),
        )

        competitor_strength = 50.0

        if related:
            competitor_strength = max(
                _percentile(
                    math.log1p(
                        video.get(
                            "views_per_day",
                            0,
                        )
                    ),
                    velocity_values,
                )
                for _, video in related[:3]
            )

        ranking_chance = (
            originality * 0.50
            + (100.0 - competitor_strength) * 0.25
            + audience_interest * 0.25
        )

        video_potential = round(
            audience_interest * 0.35
            + viewer_response * 0.10
            + trend_activity * 0.15
            + ranking_chance * 0.25
            + originality * 0.15
        )

        signals = {
            "audience_interest": round(
                audience_interest
            ),
            "viewer_response": round(
                viewer_response
            ),
            "trend_activity": round(
                trend_activity
            ),
            "ranking_chance": round(
                ranking_chance
            ),
            "originality": round(
                originality
            ),
        }

        item = dict(candidate)

        item.update({
            "opportunity_score": max(
                0,
                min(100, video_potential),
            ),
            "potential_label": _strength_label(
                video_potential
            ),
            "signals": signals,
            "signal_labels": {
                key: _strength_label(value)
                for key, value in signals.items()
            },
        })

        scored.append(item)

    scored.sort(
        key=lambda item: item["opportunity_score"],
        reverse=True,
    )

    return scored

def research_titles(
    db: Session,
    prompt: str,
    niche: str = "",
    competitor: str = "",
    youtube_connection_id: int | None = None,
) -> dict:
    prompt = " ".join((prompt or "").split()).strip()
    niche = " ".join((niche or "").split()).strip()
    competitor = " ".join((competitor or "").split()).strip()

    if not prompt and not niche and not competitor:
        raise ValueError(
            "Enter a research prompt, niche/topic, or competitor."
        )

    youtube = _youtube(db, youtube_connection_id)
    competitor_data = _resolve_competitor(
        youtube,
        competitor,
    )

    queries = _research_queries(
        prompt,
        niche,
        competitor_data,
    )

    if not queries:
        raise RuntimeError("Could not generate research queries.")

    research = [
        _collect_query_data(youtube, query)
        for query in queries
    ]

    candidates = _generate_candidates(
        prompt,
        niche,
        research,
        competitor_data,
    )

    ranked = _score_candidates(candidates, research)

    return {
        "prompt": prompt,
        "niche": niche,
        "competitor": competitor_data,
        "queries": queries,
        "results": ranked,
    }
