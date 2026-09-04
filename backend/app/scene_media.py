import json
import logging
from pathlib import Path
import shutil

from app.media_selector import normalize_media_source, select_media
from app.video_profiles import is_historical_profile
from app.media_relevance import build_media_topic_context, scene_context, with_era_years
from app.stock_providers import normalized_query
from app.paths import DATA_ROOT

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
REUSE_MIN_SCENE_GAP = 4
REUSE_MAX_USES = 2
# Backward-compatible names for older callers/tests.
NATURE_REUSE_MIN_SCENE_GAP = REUSE_MIN_SCENE_GAP
NATURE_REUSE_MAX_USES = REUSE_MAX_USES


def emergency_max_uses(duration_minutes):
    if duration_minutes >= 25:
        return 4
    if duration_minutes >= 15:
        return 3
    return 2


def reuse_eligible_keys(asset_usage, scene_number):
    return {
        key for key, state in asset_usage.items()
        if scene_number - state["last_scene"] >= REUSE_MIN_SCENE_GAP
        and state["uses"] < REUSE_MAX_USES
    }


def nature_reuse_eligible_keys(asset_usage, scene_number):
    return reuse_eligible_keys(asset_usage, scene_number)


def _record_asset(asset_usage, result, scene_number):
    previous_scene = None
    for key in (result.get("source"), result.get("asset_key")):
        if not key:
            continue
        previous = asset_usage.get(key, {"last_scene": -1000, "uses": 0})
        if previous["uses"]:
            previous_scene = previous["last_scene"]
        asset_usage[key] = {"last_scene": scene_number, "uses": previous["uses"] + 1, "result": dict(result)}
    return previous_scene


def _record_nature_asset(asset_usage, result, scene_number):
    return _record_asset(asset_usage, result, scene_number)


def emergency_topic_queries(topic_context):
    if topic_context is None:
        return []
    words = normalized_query(topic_context.primary_topic).split()
    candidates = [" ".join(words[:4]), " ".join(words[:2])]
    return [value for index, value in enumerate(candidates)
            if value and value not in candidates[:index]][:2]


def _emergency_local_reuse(asset_usage, media_dir, number, duration_minutes):
    candidates = {}
    for state in asset_usage.values():
        result = state.get("result") or {}
        media_file = result.get("media_file")
        if not media_file or media_file in candidates:
            continue
        if state["uses"] >= emergency_max_uses(duration_minutes):
            continue
        if number - state["last_scene"] < REUSE_MIN_SCENE_GAP:
            continue
        if not Path(media_file).is_file():
            continue
        candidates[media_file] = state
    if not candidates:
        return None
    state = min(candidates.values(), key=lambda item: (item["uses"], item["last_scene"]))
    original = state["result"]
    source_path = Path(original["media_file"])
    target = Path(media_dir) / f"scene-{number:03d}{source_path.suffix}"
    shutil.copy2(source_path, target)
    result = dict(original)
    result.update({
        "media_file": str(target), "reused": True, "emergency": True,
        "previous_scene": state["last_scene"],
    })
    logger.info(
        "MEDIA scene=%d emergency=true result=reuse provider=%s previous_scene=%s use_count=%s",
        number, result.get("provider", ""), state["last_scene"], state["uses"] + 1,
    )
    return result


def _select_scene_media(query, media_dir, number, requested, orientation,
                        used_sources, ai_prompt, media_source, prefer_wikimedia,
                        topic_context, history_sources, asset_usage, duration_minutes=10):
    media_source = normalize_media_source(media_source)
    try:
        return select_media(
            query, media_dir, number, requested, orientation, used_sources,
            ai_prompt, "stock_only", prefer_wikimedia=prefer_wikimedia,
            topic_context=topic_context, history_sources=history_sources,
        )
    except RuntimeError:
        for broad_query in emergency_topic_queries(topic_context):
            logger.info("MEDIA scene=%d emergency=true query=%r", number, broad_query)
            try:
                result = select_media(
                    broad_query, media_dir, number, requested, orientation,
                    used_sources, broad_query, "stock_only",
                    prefer_wikimedia=False, topic_context=topic_context, history_sources=history_sources,
                )
                result["emergency"] = True
                return result
            except RuntimeError:
                continue
        # Stock exhaustion is not fatal.  Hand the scene to the isolated
        # Z-Image batch instead of aborting the whole video before the AI
        # fallback stage gets a chance to run.
        logger.info(
            "MEDIA scene=%d result=ai_fallback reason=stock_exhausted query=%r",
            number, query,
        )
        return {
            "provider": "stock-exhausted",
            "source": None,
            "media_file": "",
            "media_type": "image",
            "asset_key": None,
            "matched_query": query,
            "relevance_score": 0,
            "relevance_confidence": "none",
            "relevance_reason": (
                "No usable unique stock candidate remained after normal and broad-topic searches."
            ),
            "relevance_penalties": ["stock_exhausted"],
            "subject_coverage": 0.0,
            "descriptive_coverage": 0.0,
            "required_matches": [],
            "needs_ai_fallback": True,
            "stock_decision": "ai_fallback_pending_no_stock",
        }


def download_scene_images(scenes, output_dir, orientation="landscape",
                          media_source="stock_only", video_profile="general_explainer",
                          video_title="", duration_minutes=10,
                          progress_callback=None):
    media_dir = Path(output_dir) / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    attributions = []
    manifest_path = Path(output_dir) / "media-manifest.jsonl"
    history_path = DATA_ROOT / "media_history.json"
    prefer_wikimedia = is_historical_profile(video_profile)
    topic_context = build_media_topic_context(video_title, video_profile) if video_title else None
    if topic_context is not None and is_historical_profile(video_profile):
        topic_context = with_era_years(
            topic_context, (scene.get("search_query") or "" for scene in scenes),
        )
    try:
        history = json.loads(history_path.read_text())
    except Exception:
        history = []
    history_sources = set(history[-500:])
    used_sources, new_sources, asset_usage = set(), [], {}

    total_scenes = len(scenes)

    for scene_index, scene in enumerate(scenes, start=1):
        number = int(scene["scene_number"])
        keywords = scene.get("stock_keywords") or []
        if isinstance(keywords, str):
            keywords = [keywords]
        query = str(scene.get("search_query") or "").strip()
        if not query:
            query = " ".join(str(word).strip() for word in keywords[:4] if str(word).strip())
        query = query or scene["narration"][:100]
        try:
            result = _select_scene_media(
                query, media_dir, number, scene.get("media_type", "video"),
                orientation, used_sources, scene.get("image_prompt") or query,
                media_source, prefer_wikimedia,
                scene_context(
                    topic_context,
                    query,
                    visual_subject=scene.get("visual_subject") or "",
                    positive_terms=keywords,
                    negative_terms=scene.get("negative_keywords") or (),
                    required_terms=scene.get("required_terms") or (),
                    search_variants=scene.get("search_variants") or (),
                ),
                history_sources,
                asset_usage, duration_minutes,
            )
        except Exception as error:
            raise RuntimeError(f"Scene {number} media generation failed: {error}") from error
        scene["media_file"] = result["media_file"]
        scene["media_provider"] = result["provider"]
        scene["media_type"] = result["media_type"]
        scene.pop("media_error", None)
        result["scene_number"] = number
        previous_scene = _record_asset(asset_usage, result, number)
        if result.get("reused"):
            result["previous_scene"] = previous_scene
            logger.info(
                "MEDIA scene=%d result=reuse provider=%s source=%s previous_scene=%s",
                number, result.get("provider", ""), result.get("source", ""), previous_scene,
            )
        result["query"] = query
        scene.update(result)
        # Placeholder AI-fallback rows have no licensable stock source and
        # therefore must not leak into publishing attributions.
        if result.get("source"):
            attributions.append(result)
        manifest_entry = {
            "scene_number": number,
            "narration": scene.get("narration"),
            "visual_subject": scene.get("visual_subject"),
            "search_query": query,
            "required_terms": scene.get("required_terms") or [],
            "search_variants": scene.get("search_variants") or [],
            "negative_keywords": scene.get("negative_keywords") or [],
            "provider": result.get("provider"),
            "source": result.get("source"),
            "matched_query": result.get("matched_query") or query,
            "media_type": result.get("media_type"),
            "reused": bool(result.get("reused")),
            "previous_scene": result.get("previous_scene"),
            "emergency": bool(result.get("emergency")),
            "asset_key": result.get("asset_key"),
            "relevance_score": result.get("relevance_score"),
            "relevance_confidence": result.get("relevance_confidence"),
            "relevance_reason": result.get("relevance_reason"),
            "relevance_penalties": result.get("relevance_penalties") or [],
            "subject_coverage": result.get("subject_coverage"),
            "descriptive_coverage": result.get("descriptive_coverage"),
            "required_matches": result.get("required_matches"),
            "needs_ai_fallback": bool(result.get("needs_ai_fallback")),
            "stock_decision": result.get("stock_decision"),
            "author": result.get("author"),
            "license": result.get("license"),
            "license_url": result.get("license_url"),
        }
        with manifest_path.open("a", encoding="utf-8") as manifest:
            manifest.write(json.dumps(manifest_entry, ensure_ascii=False) + "\n")
        for key in (result.get("asset_key"), result.get("source")):
            if key:
                used_sources.add(key)
        if result.get("source"):
            new_sources.append(result["source"])

        if progress_callback:
            progress_callback(scene_index, total_scenes)

    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps((history + new_sources)[-1000:], indent=2))
    (Path(output_dir) / "attributions.json").write_text(json.dumps(attributions, indent=2))
    return scenes
