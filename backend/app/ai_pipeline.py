import json
import logging
import math
import re

import httpx

from app.config import settings
from app.ai_provider import (
    call_ai, call_gemini, call_ollama, call_together,
    call_gemini_35_flash_lite, call_gemini_31_flash_lite,
    call_deepseek_metadata_flash,
)
from app.scene_ai import call_scene_json
from app.media_relevance import (
    build_media_topic_context,
    validate_scene_query,
)
from app.script_validator import validate_script
from app.script_workflow import (
    conservative_script_cleanup,
    create_validated_script,
)
from app.video_profiles import (
    MANUAL_PROFILE_KEYS,
    normalize_profile_key,
    profile_detection_prompt,
    media_profile_instructions,
)


logger = logging.getLogger(__name__)


def call_qwen(prompt: str) -> str:
    result = call_ai(prompt, json_mode=False)
    return result.text


def count_words(text: str) -> int:
    return len(text.split())


def trim_words(text: str, maximum: int) -> str:
    words = text.split()
    return " ".join(words[:maximum])



def _clean_narration(text: str) -> str:
    """Strip LLM review notes, word counts, and meta-commentary from narration."""
    import re
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip empty lines
        if not stripped:
            continue
        # Skip lines with markdown formatting (*bold*, _italic_)
        if stripped.startswith("*") and stripped.endswith("*"):
            continue
        # Skip word count notes like "(20 words) - Good"
        if re.search(r'\(\d+\s*words?\)', stripped):
            continue
        # Skip review markers
        if any(marker in stripped.lower() for marker in [
            "check.", "checked.", "- good", "- ok", "perfect.",
            "style/cadence", "sentence length", "word count",
            "finish with", "include one", "no headings",
            "no labels", "no bullet", "visual direction",
        ]):
            continue
        # Skip lines that are just labels like "Section:" or "Hook:"
        if re.match(r'^[A-Z][a-z]+:', stripped) and len(stripped) < 40:
            continue
        cleaned.append(stripped)
    return " ".join(cleaned)


def generate_section(
    title: str,
    video_style: str,
    section_name: str,
    target_words: int,
    previous_context: str = "",
) -> str:
    prompt = (
        f"Video title: {title}\n"
        f"Style: {video_style}\n"
        f"Section: {section_name}\n"
        f"Write exactly {target_words + 20} words of spoken narration.\n"
        "CRITICAL RULES:\n"
        "- Return ONLY the narration text that will be read aloud.\n"
        "- Do NOT include word counts, style notes, reviews, or analysis.\n"
        "- Do NOT include labels like (20 words), *Style*, Check, Good, Perfect.\n"
        "- Do NOT include any markdown formatting.\n"
        "- Write in a natural conversational documentary tone.\n"
        "- Just the spoken words, nothing else.\n"
        f"Previous context: {previous_context[-800:]}"
    )

    text = call_qwen(prompt)

    for _ in range(3):
        text = _clean_narration(text)
        if count_words(text) >= target_words:
            return trim_words(text, target_words)

        text = call_qwen(
            f"Expand this narration to at least {target_words + 20} words. "
            "Return only the complete revised narration:\n\n"
            f"{text}"
        )

    raise ValueError(f"Could not generate section: {section_name}")


def split_targets(total_words: int, section_count: int) -> list[int]:
    base = total_words // section_count
    remainder = total_words % section_count

    return [
        base + (1 if index < remainder else 0)
        for index in range(section_count)
    ]


def generate_script(title: str, duration_minutes: int, video_style: str) -> dict:
    target_words = duration_minutes * 145
    body_count = max(2, math.ceil(duration_minutes / 2))

    section_names = [
        "Strong opening hook",
        "Introduction and context",
        *[f"Main section {number}" for number in range(1, body_count + 1)],
        "Conclusion and final takeaway",
    ]

    targets = split_targets(target_words, len(section_names))

    sections = []
    previous_context = ""

    for section_name, section_target in zip(section_names, targets):
        narration = generate_section(
            title,
            video_style,
            section_name,
            section_target,
            previous_context,
        )

        sections.append({
            "name": section_name,
            "narration": narration,
            "word_count": count_words(narration),
        })

        previous_context += " " + narration

    script = " ".join(section["narration"] for section in sections)

    return {
        "script": script,
        "sections": sections,
        "word_count": count_words(script),
        "target_words": target_words,
    }


def _decode_ai_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]

    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object returned.")

    value, _ = json.JSONDecoder().raw_decode(text[start:])
    return value


def call_qwen_json(prompt: str) -> dict:
    prompt += (
        "\nReturn only complete valid JSON. "
        "No Markdown. Keep every field concise."
    )
    errors = []

    attempts = [
        (call_gemini_35_flash_lite, 1),
        (call_gemini_31_flash_lite, 1),
        (call_deepseek_metadata_flash, 1),
    ]

    for provider, total in attempts:
        for attempt in range(total):
            try:
                result = provider(prompt, json_mode=True)
                return _decode_ai_json(result.text)
            except Exception as error:
                errors.append(
                    f"{provider.__name__} #{attempt + 1}: {error}"
                )

    raise ValueError("All JSON attempts failed: " + " | ".join(errors))


def _call_media_context_json(prompt: str) -> dict:
    """Ollama-first JSON call for media understanding.

    Scene/media planning intentionally prefers the local qwen model so the
    video does not become dependent on a remote provider.  Remote providers are
    fallbacks only, matching the existing scene-metadata policy.
    """
    prompt += (
        "\nReturn only complete valid JSON. No Markdown. "
        "Keep every field concise."
    )
    errors = []
    attempts = [
        (call_gemini_35_flash_lite, 1),
        (call_gemini_31_flash_lite, 1),
        (call_deepseek_metadata_flash, 1),
    ]
    for provider, total in attempts:
        for attempt in range(total):
            try:
                result = provider(prompt, json_mode=True)
                return _decode_ai_json(result.text)
            except Exception as error:
                errors.append(f"{provider.__name__} #{attempt + 1}: {error}")
    raise ValueError("All media-context JSON attempts failed: " + " | ".join(errors))


def _clean_media_list(value, limit=10, maximum=120):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        text = " ".join(str(item or "").split()).strip()[:maximum]
        if text and text.casefold() not in {entry.casefold() for entry in cleaned}:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _clean_media_script_context(data):
    if not isinstance(data, dict):
        return {}
    summary = " ".join(str(data.get("summary") or "").split()).strip()[:650]
    main_subject = " ".join(str(data.get("main_subject") or "").split()).strip()[:180]
    entities = _clean_media_list(data.get("entities"), 14)
    visual_terms = _clean_media_list(data.get("visual_terms"), 18)
    ambiguities = []
    for item in data.get("ambiguities") or []:
        if not isinstance(item, dict):
            continue
        term = " ".join(str(item.get("term") or "").split()).strip()[:90]
        meaning = " ".join(str(item.get("meaning") or "").split()).strip()[:180]
        avoid = _clean_media_list(item.get("avoid"), 8)
        if term and meaning:
            ambiguities.append({"term": term, "meaning": meaning, "avoid": avoid})
        if len(ambiguities) >= 10:
            break
    result = {
        "summary": summary,
        "main_subject": main_subject,
        "entities": entities,
        "visual_terms": visual_terms,
        "ambiguities": ambiguities,
    }
    return {key: value for key, value in result.items() if value}


def build_media_script_context(title, video_style, script):
    """Create one compact visual brief from the complete narration.

    Failure is deliberately non-fatal.  If every AI provider is unavailable,
    scene enrichment falls back to the existing title + narration behaviour.
    """
    script = " ".join(str(script or "").split()).strip()
    if not script:
        return {}
    prompt = (
        f"Video title: {title}\n"
        f"Style: {video_style}\n"
        "Read the complete narration and create a MEDIA CONTEXT BRIEF for "
        "finding accurate real-world visuals. This is context for later scene "
        "queries, not a list of queries itself.\n"
        "Return JSON with: summary (max 90 words), main_subject, entities, "
        "visual_terms, ambiguities.\n"
        "For ambiguities, use objects/words whose ordinary stock-search meaning "
        "could be wrong. Each ambiguity has term, intended meaning, and avoid "
        "(wrong meanings/subjects). Example: submarine cable should avoid cable "
        "car, bridge cable and electrical extension cable.\n"
        "Do not invent facts, brands, people, places, dates or objects not "
        "supported by the title or narration. Prefer concrete visible nouns.\n"
        f"Complete narration:\n{script}"
    )
    try:
        return _clean_media_script_context(_call_media_context_json(prompt))
    except Exception as error:
        logger.warning("MEDIA_CONTEXT_FAILED: %s", error)
        return {}

def split_script_into_scenes(script, duration_minutes):
    words = script.strip().split()

    if not words:
        raise ValueError("Cannot create scenes from an empty script.")

    desired_count = max(1, duration_minutes * 8)
    minimum_words_per_scene = 12

    safe_count = max(
        1,
        len(words) // minimum_words_per_scene,
    )

    count = min(desired_count, safe_count)
    scenes = []
    start = 0

    for index in range(count):
        remaining_scenes = count - index

        if remaining_scenes == 1:
            end = len(words)
        else:
            remaining_words = len(words) - start
            ideal = start + round(
                remaining_words / remaining_scenes
            )

            minimum_end = start + minimum_words_per_scene
            maximum_end = (
                len(words)
                - minimum_words_per_scene
                * (remaining_scenes - 1)
            )

            target = min(
                max(ideal, minimum_end),
                maximum_end,
            )

            search_start = max(
                minimum_end,
                target - 6,
            )
            search_end = min(
                maximum_end,
                target + 6,
            )

            choices = range(
                search_start,
                search_end + 1,
            )

            endings = [
                position
                for position in choices
                if words[position - 1].endswith(
                    (".", "!", "?", ";", ":")
                )
            ]

            end = (
                min(
                    endings,
                    key=lambda position: abs(
                        position - target
                    ),
                )
                if endings
                else target
            )

        narration = " ".join(words[start:end]).strip()

        if not narration:
            raise ValueError(
                f"Scene {index + 1} has empty narration."
            )

        scenes.append({
            "scene_number": index + 1,
            "narration": narration,
        })

        start = end

    return scenes


def _scene_continuity_rows(all_scenes, batch):
    positions = {int(scene["scene_number"]): index for index, scene in enumerate(all_scenes)}
    rows = []
    for scene in batch:
        index = positions[int(scene["scene_number"])]
        previous = all_scenes[index - 1]["narration"] if index > 0 else ""
        following = all_scenes[index + 1]["narration"] if index + 1 < len(all_scenes) else ""
        rows.append({
            "scene_number": scene["scene_number"],
            "previous_narration": previous,
            "current_narration": scene["narration"],
            "next_narration": following,
        })
    return rows


def _enrich_scene_batch_once(
    title,
    video_style,
    scenes,
    topic_context=None,
    media_script_context=None,
    all_scenes=None,
    recent_visuals=None,
):
    topic_context = topic_context or build_media_topic_context(
        title, video_style
    )
    media_script_context = media_script_context or {}
    all_scenes = all_scenes or scenes
    recent_visuals = recent_visuals or []
    continuity = _scene_continuity_rows(all_scenes, scenes)
    prompt = (
        f"Video title: {title}\n"
        f"Style: {video_style}\n"
        "Whole-video media context (use for disambiguation, never to override "
        "what the current scene actually says):\n"
        f"{json.dumps(media_script_context, ensure_ascii=False)}\n"
        "Profile-specific media instructions:\n"
        f"{media_profile_instructions(video_style)}\n"
        "Create media metadata for the supplied scenes.\n"
        "Do not rewrite narration.\n"
        "Return JSON with key scenes.\n"
        "Each scene needs scene_number, visual_subject, action, environment, search_query, "
        "required_terms, search_variants, stock_keywords, negative_keywords, media_type, "
        "shot_type, camera_angle, focal_detail, visual_distinction and zimage_prompt.\n"
        "visual_subject is the exact visible real-world subject the viewer should "
        "see for THIS scene, normally 2-8 words. The current narration has higher "
        "priority than the whole-video topic.\n"
        "action is the concrete physical action visible in the shot, normally 2-8 words. "
        "Do not describe an idea, emotion, implication or editorial meaning as an action.\n"
        "environment is the concrete visible location or physical setting surrounding "
        "the subject, normally 2-8 words.\n"
        "visual_subject + action + environment must describe one coherent photographable shot.\n"
        "search_query must be one concise search phrase describing the "
        "most visually important real subject in that scene.\n"
        "When a scene involves a named person, place, monument, event, "
        "species, company, technology or spacecraft, keep that exact "
        "proper name in search_query.\n"
        "Do not replace specific subjects with generic concepts such as "
        "man, woman, building, office, crowd, technology or history.\n"
        "Rules for search_query, because it is sent to stock-footage search:\n"
        "- Use only concrete, photographable nouns: the exact object, "
        "component, place, action, material or period shown in the scene.\n"
        "- Never use editorial or mood words such as dark, secret, mysterious, "
        "shocking, hidden, amazing, untold or truth unless they are literal.\n"
        "- Do not mention an animal unless the narration of that scene is "
        "actually about that animal.\n"
        "- Do not add a country, era or setting word on its own; pair it with "
        "the subject noun (\"roman aqueduct\", not \"roman\").\n"
        "- For historical or archaeological scenes prefer artifacts, ruins, "
        "monuments, maps, documents, paintings, museum objects or period "
        "photographs over generic modern footage.\n"
        "- Never search for an abstract concept, metaphor, emotion or business idea.\n"
        "- When narration describes a consequence, forecast, implication, or abstract effect, keep the visual anchored to the video core physical subject, mechanism, location, or entity unless the narration explicitly makes a different concrete place or object the main subject.\n"
        "- Do not jump to generic illustrative B-roll such as random streets, crowds, offices, people walking, traffic, skylines, or unrelated environments merely because they symbolize an effect.\n"
        "- Never invent a specific physical action, animal behavior, person, vehicle, instrument, experiment, meeting, conference, or event unless it is stated in the current narration or clearly established by the immediately adjacent scene context.\n"
        "- For broad conclusions such as an ecosystem surviving, a discovery changing scientific understanding, or a process influencing an environment, show the actual physical subject or system itself. Do not invent feeding, grazing, meetings, presentations, or symbolic activity.\n"
        "- Preserve physical plausibility. For deep-ocean seafloor sampling or inspection, never place an unprotected researcher or scientist physically on the seafloor. Use the ROV, submersible, robotic sampler, sampling arm, or instrument established by the narration/context.\n"
        "- When narration says researchers study, measure, sample, observe, or investigate something remotely, make the visible subject the real specimen, process, instrument, ROV, sample, or physical environment rather than inventing a human performing the action at an impossible location.\n"
        "- Do not invent a research vessel, laboratory, conference, control room, display, or scientific meeting merely to visualize research or an implication. Use one only when the current narration or adjacent context actually establishes it.\n"
        "- Example: if Antarctic ice flow causes future sea-level rise, prefer Antarctic glacier flow, ice shelf movement, grounding-line retreat, or a physically relevant Antarctic visualization rather than a generic flooded city street.\n"
        "- Two to five words is ideal; the video title is context, not the query.\n"
        "- Resolve ambiguous words before searching. Add the distinguishing noun "
        "or qualifier needed to force the intended meaning (for example submarine "
        "fiber optic cable, not just cable; network data traffic, not traffic).\n"
        "- required_terms: 1-3 discriminating concrete terms or short phrases that "
        "prove the intended subject. Choose the distinctive mechanism/entity, not "
        "generic colors/settings. For example hemolysis, antibody, agglutination, "
        "submarine fiber optic, cable landing station.\n"
        "- search_variants: give at most 2 alternative VIDEO searches for the SAME visual "
        "subject. The first variant should be the best practical synonym/component query. "
        "Never change the subject or word sense.\n"
        "- negative_keywords: 0-6 short wrong subjects/meanings that a stock "
        "search could confuse with this scene. Use only real ambiguity risks, not "
        "a huge rejection list. NEVER include the requested subject, its required "
        "entity, action, environment, or a direct synonym of them in negative_keywords.\n"
        "- For like, subscribe, comment, or viewer-engagement narration, do NOT create "
        "a visual subject involving buttons, screens, social-media UI, laptops, phones, "
        "advertising graphics or call-to-action text. Keep showing the real video topic.\n"
        "media_type MUST be video for normal scenes; use image when the scene "
        "specifically needs a map, document, portrait, archival photograph, labeled "
        "diagram, or artifact where moving stock footage would be misleading. Even for "
        "history, prefer relevant video of the actual ruins, site, museum object, excavation, "
        "or reconstruction before choosing an archival still.\n"
        "stock_keywords should support the same exact subject and context.\n"
        "shot_type must choose a concrete composition such as wide establishing, medium, close-up, macro detail, overhead, aerial, tracking or low-angle.\n"
        "camera_angle must describe the actual viewpoint and should vary from nearby scenes.\n"
        "focal_detail must state the exact visible physical detail that distinguishes this scene.\n"
        "visual_distinction must explain how this scene should look different from adjacent visuals without inventing new facts.\n"
        "zimage_prompt must be a concise photorealistic documentary-still prompt grounded only in this scene's subject, action and environment. Never request text, labels, captions, logos, screens, UI, posters, fake maps, title cards or written diagrams.\n"
        "Never output placeholder values such as none, n/a, unknown or not applicable for visual planning fields.\n"
        "Across adjacent scenes, vary framing, viewpoint and focal detail while preserving factual accuracy.\n"
        "Recent visual plans already used earlier in this SAME video:\n"
        f"{json.dumps(recent_visuals, ensure_ascii=False)}\n"
        "Cross-batch visual diversity rules:\n"
        "- Treat those recent visuals as already used. Do not casually reuse the same visual concept.\n"
        "- A different camera angle alone does NOT make a repeated subject into a new visual idea.\n"
        "- If the same named entity must appear again because narration directly requires it, show a different factual component, physical action, environment, scale, stage, or meaningful focal detail.\n"
        "- Avoid repeatedly using generic hero views such as a spacecraft floating in space, telescope portrait, rocket on launch pad, isolated planet against stars, or the same object in a cleanroom.\n"
        "- Prefer the most specific physical mechanism, component, observation target, surface feature, instrument, environment, or event actually supported by the current narration.\n"
        "- Never invent unrelated variety just to be different; narration accuracy remains mandatory.\n"
        "Local continuity context (previous/current/next narration): "
        f"{json.dumps(continuity, ensure_ascii=False)}\n"
        f"Scenes: {json.dumps(scenes)}"
    )

    result = call_scene_json(prompt)

    metadata = {
        int(item["scene_number"]): item
        for item in result.get("scenes", [])
    }

    enriched = []

    for scene in scenes:
        item = metadata.get(scene["scene_number"])

        if not item:
            raise ValueError(
                f"Missing metadata for scene {scene['scene_number']}."
            )

        validation = validate_scene_query(
            item.get("search_query", ""), topic_context
        )
        if validation.repaired:
            logger.debug(
                "Scene media query repaired profile=%s topic=%r original=%r final=%r",
                topic_context.profile,
                topic_context.primary_topic,
                validation.original_query,
                validation.final_query,
            )

        final_query = validation.final_query
        visual_subject = " ".join(
            str(item.get("visual_subject") or final_query).split()
        ).strip()[:180]
        action = " ".join(str(item.get("action") or "").split()).strip()[:160]
        environment = " ".join(
            str(item.get("environment") or "").split()
        ).strip()[:160]

        required_terms = _clean_media_list(item.get("required_terms"), 3, 80)
        search_variants = _clean_media_list(item.get("search_variants"), 3, 100)
        stock_keywords = _clean_media_list(item.get("stock_keywords"), 10, 80)
        negative_keywords = _clean_media_list(item.get("negative_keywords"), 8, 100)

        def clean_visual_field(value, maximum):
            text = " ".join(str(value or "").split()).strip()
            if text.casefold() in {
                "none", "n/a", "na", "unknown",
                "not applicable", "null",
            }:
                return ""
            return text[:maximum]

        shot_type = clean_visual_field(
            item.get("shot_type"), 100
        )
        camera_angle = clean_visual_field(
            item.get("camera_angle"), 100
        )
        focal_detail = clean_visual_field(
            item.get("focal_detail"), 180
        )
        visual_distinction = clean_visual_field(
            item.get("visual_distinction"), 220
        )
        zimage_prompt = clean_visual_field(
            item.get("zimage_prompt"), 700
        )

        # A model may turn a broad conclusion into the literal video
        # title, a digital globe, map, infographic, or other generated
        # editorial graphic. Keep AI imagery grounded in a physical
        # subject instead; Remotion will handle maps/UI/text later.
        if (
            _looks_like_title_visual(visual_subject, title)
            or _looks_like_title_visual(final_query, title)
            or _looks_like_generated_graphic(zimage_prompt)
        ):
            anchor = _scene_topic_anchor(topic_context)

            if anchor:
                anchor_validation = validate_scene_query(
                    anchor,
                    topic_context,
                )
                final_query = anchor_validation.final_query
                visual_subject = final_query

            action = ""
            environment = ""
            focal_detail = ""
            visual_distinction = (
                "topic-focused documentary closing visual"
            )
            zimage_prompt = ""

            if _looks_like_generated_graphic(camera_angle):
                camera_angle = ""

        # CTA narration/metadata must stay on the real video topic.
        # Remotion will later add the actual subscribe/comment graphics.
        if (
            _narration_is_cta(scene.get("narration"))
            or _looks_like_cta_visual(visual_subject)
            or _looks_like_cta_visual(final_query)
        ):
            anchor = _scene_topic_anchor(topic_context)

            previous_environment = ""
            if enriched:
                previous_environment = " ".join(
                    str(
                        enriched[-1].get("environment")
                        or ""
                    ).split()
                ).strip()

            if anchor:
                visual_subject = anchor

                # Keep broad conclusion/CTA visuals physical rather than
                # turning them into maps, globes, dashboards or fake UI.
                if previous_environment:
                    environment = previous_environment

                query_parts = [anchor]
                if (
                    environment
                    and environment.casefold()
                    not in anchor.casefold()
                ):
                    query_parts.append(environment)

                final_query = " ".join(query_parts)[:180]

                action = ""
                focal_detail = (
                    f"physical details of {anchor}"
                )[:180]
                visual_distinction = (
                    "clean physical documentary closing shot "
                    "focused on the real subject"
                )

                # Ignore any AI-generated CTA/map/display image plan.
                zimage_prompt = ""

                # Conclusion/CTA fallback must remain physically coherent.
                # Do not preserve a map-style/aerial angle from the rejected plan.
                shot_type = "wide documentary"
                camera_angle = "eye-level"

                if _narration_is_cta(scene.get("narration")):
                    negative_keywords = []

        # Abstract AI subjects are unsuitable for stock retrieval or
        # text-to-image generation. Prefer the concrete validated query;
        # if that is abstract too, fall back to the video's physical topic.
        if _looks_like_abstract_visual(visual_subject):
            if not _looks_like_abstract_visual(final_query):
                visual_subject = final_query
            else:
                anchor = _scene_topic_anchor(topic_context)
                if anchor:
                    anchor_validation = validate_scene_query(
                        anchor, topic_context
                    )
                    final_query = anchor_validation.final_query
                    visual_subject = final_query

        narration_text = _normalized_visual_text(scene.get("narration"))

        # Reject invented editorial scenes unless the narration actually
        # mentions scientists/research/labs/conferences.
        if _looks_like_editorial_drift(action, environment):
            narration_allows_editorial = any(
                term in narration_text
                for term in (
                    "scientist",
                    "researcher",
                    "research team",
                    "laboratory",
                    "lab ",
                    "conference",
                )
            )
            if not narration_allows_editorial:
                anchor = _scene_topic_anchor(topic_context)
                if anchor:
                    anchor_validation = validate_scene_query(
                        anchor, topic_context
                    )
                    final_query = anchor_validation.final_query
                    visual_subject = final_query
                    action = ""
                    environment = ""

        visual_subject = _strengthen_generic_subject(
            visual_subject,
            final_query,
            environment,
        )

        final_query = _concrete_scene_query(
            visual_subject,
            environment,
            final_query,
        )

        negative_keywords = _remove_conflicting_negative_keywords(
            negative_keywords,
            visual_subject,
            final_query,
            action,
            environment,
            *required_terms,
        )

        media_type = str(
            item.get("media_type") or "video"
        ).strip().lower()
        if media_type not in {
            "video", "stock_video", "image", "stock_image"
        }:
            media_type = "video"

        enriched.append({
            **scene,
            "search_query": final_query,
            "visual_subject": visual_subject,
            "action": action,
            "environment": environment,
            "required_terms": required_terms,
            "search_variants": search_variants,
            "stock_keywords": stock_keywords,
            "negative_keywords": negative_keywords,
            "media_type": media_type,
            "shot_type": shot_type,
            "camera_angle": camera_angle,
            "focal_detail": focal_detail,
            "visual_distinction": visual_distinction,
            "zimage_prompt": zimage_prompt,
        })

    return enriched



_ABSTRACT_QUERY_TERMS = (
    "chemistry",
    "resilience",
    "importance",
    "implication",
    "implications",
    "engagement",
    "call-to-action",
    "call to action",
    "supporting life",
    "scientific significance",
    "global impact",
    "social impact",
    "economic impact",
)

_EDITORIAL_DRIFT_TERMS = (
    "conference",
    "presentation",
    "meeting",
    "seminar",
    "panel discussion",
    "research implications",
    "scientific implications",
    "concept",
)


def _query_has_abstract_terms(value):
    text = _normalized_visual_text(value)
    return bool(text and any(term in text for term in _ABSTRACT_QUERY_TERMS))


def _looks_like_editorial_drift(action, environment):
    text = _normalized_visual_text(f"{action} {environment}")
    return bool(text and any(term in text for term in _EDITORIAL_DRIFT_TERMS))


def _strengthen_generic_subject(subject, search_query, environment):
    subject = " ".join(str(subject or "").split()).strip()
    query = " ".join(str(search_query or "").split()).strip()
    environment = " ".join(str(environment or "").split()).strip()

    if len(subject.split()) > 1:
        return subject

    # Single nouns such as "larvae", "scientists", "plume" are too ambiguous.
    # Prefer the existing concrete query, otherwise attach the real setting.
    if query and len(query.split()) >= 2 and not _query_has_abstract_terms(query):
        return query[:180]

    if environment:
        return f"{subject} in {environment}"[:180]

    return subject


def _concrete_scene_query(subject, environment, original):
    subject = " ".join(str(subject or "").split()).strip()
    environment = " ".join(str(environment or "").split()).strip()
    original = " ".join(str(original or "").split()).strip()

    if original and not _query_has_abstract_terms(original):
        return original

    words = []
    for value in (subject, environment):
        for word in value.split():
            if word.casefold() not in {x.casefold() for x in words}:
                words.append(word)

    return " ".join(words[:9]) or subject or original


_ABSTRACT_VISUAL_PHRASES = (
    "viewer engagement",
    "call-to-action",
    "call to action",
    "subscription call",
    "engagement prompt",
    "global ocean chemistry",
    "nutrient distribution",
    "ecosystem resilience",
    "organism-environment interaction",
)

_CTA_VISUAL_PHRASES = (
    "subscribe",
    "subscription",
    "like button",
    "comment prompt",
    "viewer engagement",
    "call-to-action",
    "call to action",
)


def _normalized_visual_text(value):
    return " ".join(str(value or "").lower().replace("_", " ").split())


def _looks_like_cta_visual(value):
    text = _normalized_visual_text(value)
    return bool(text and any(term in text for term in _CTA_VISUAL_PHRASES))


def _narration_is_cta(value):
    text = _normalized_visual_text(value)
    if not text:
        return False
    terms = (
        "subscribe",
        "share your thoughts",
        "comment below",
        "let us know below",
        "like this video",
        "follow for more",
        "hit the bell",
    )
    return any(term in text for term in terms)


def _looks_like_abstract_visual(value):
    text = _normalized_visual_text(value)
    if not text:
        return True
    if any(term in text for term in _ABSTRACT_VISUAL_PHRASES):
        return True
    endings = (
        " resilience",
        " distribution",
        " interaction",
        " importance",
        " implication",
        " engagement",
        " concept",
        " prompt",
    )
    return any(text.endswith(term) for term in endings)


def _scene_topic_anchor(topic_context):
    value = " ".join(
        str(getattr(topic_context, "primary_topic", "") or "").split()
    ).strip()

    # Turn explanatory/video-title wording into a concrete physical
    # stock/image subject, especially for CTA and abstract fallback scenes.
    lowered = value.casefold()

    for prefix in ("how ", "why ", "what is ", "what are "):
        if lowered.startswith(prefix):
            value = value[len(prefix):].strip()
            lowered = value.casefold()
            break

    for suffix in (
        " support life",
        " supports life",
        " supporting life",
        " connect the world",
        " connects the world",
        " connecting the world",
        " explained",
        " explanation",
        " why it matters",
        " what it means",
    ):
        if lowered.endswith(suffix):
            value = value[:-len(suffix)].strip()
            lowered = value.casefold()
            break

    return value[:180]


def _looks_like_title_visual(value, title):
    value_text = _normalized_visual_text(value)
    title_text = _normalized_visual_text(title)

    if not value_text or not title_text:
        return False

    return value_text == title_text


def _looks_like_generated_graphic(value):
    text = _normalized_visual_text(value)

    risky = (
        "digital globe",
        "world map",
        "interactive map",
        "global scale map",
        "glowing routes",
        "glowing lines tracing",
        "control room display",
        "display showing",
        "network lines spanning",
        "routes connecting continents",
        "global transoceanic",
        "title card",
        "subscribe button",
        "social media interface",
        "fake interface",
        "infographic",
        "poster layout",
    )

    return bool(text and any(term in text for term in risky))


def _remove_conflicting_negative_keywords(values, *protected_values):
    protected = [
        _normalized_visual_text(value)
        for value in protected_values
        if _normalized_visual_text(value)
    ]

    cleaned = []
    for value in values:
        candidate = _normalized_visual_text(value)
        if not candidate:
            continue

        candidate_tokens = set(candidate.split())
        conflicts = False

        for text in protected:
            protected_tokens = set(text.split())

            # Exact phrase or a complete negative phrase embedded in the
            # requested subject/query is a direct contradiction.
            if candidate == text or candidate in text:
                conflicts = True
                break

            # If every meaningful token in the negative is already part of
            # the requested subject, it is also contradictory.
            meaningful = {
                token for token in candidate_tokens
                if len(token) >= 4
            }
            if meaningful and meaningful.issubset(protected_tokens):
                conflicts = True
                break

        if not conflicts:
            cleaned.append(value)

    return cleaned


def detect_video_style(title, requested_style):
    requested_style = normalize_profile_key(requested_style)

    if requested_style != "auto":
        return requested_style

    result = call_qwen(
        profile_detection_prompt(title)
    ).lower().strip()

    result = result.replace("`", "").strip()

    if "\n" in result:
        result = result.splitlines()[0].strip()

    if ":" in result:
        candidate = result.split(":", 1)[0].strip()
        if candidate in MANUAL_PROFILE_KEYS:
            result = candidate

    return (
        result
        if result in MANUAL_PROFILE_KEYS
        else "general_explainer"
    )



def enrich_scene_batch(title, video_style, scenes, script=None):
    enriched = []
    topic_context = build_media_topic_context(title, video_style)
    complete_script = script or " ".join(scene.get("narration", "") for scene in scenes)
    media_script_context = build_media_script_context(title, video_style, complete_script)

    for start in range(0, len(scenes), 8):
        batch = scenes[start:start + 8]

        recent_visuals = [
            {
                "scene_number": item.get("scene_number"),
                "visual_subject": item.get("visual_subject"),
                "action": item.get("action"),
                "environment": item.get("environment"),
                "shot_type": item.get("shot_type"),
                "camera_angle": item.get("camera_angle"),
                "focal_detail": item.get("focal_detail"),
                "visual_distinction": item.get("visual_distinction"),
            }
            for item in enriched[-16:]
        ]

        enriched.extend(
            _enrich_scene_batch_once(
                title, video_style, batch, topic_context,
                media_script_context, scenes,
                recent_visuals=recent_visuals,
            )
        )

    return enriched


def assign_scene_durations(scenes, total_seconds):
    weights = [
        max(1, len(scene["narration"].split()))
        for scene in scenes
    ]

    total_weight = sum(weights)

    durations = [
        max(3, round(total_seconds * weight / total_weight))
        for weight in weights
    ]

    difference = total_seconds - sum(durations)
    index = 0

    while difference != 0:
        position = index % len(durations)

        if difference > 0:
            durations[position] += 1
            difference -= 1
        elif durations[position] > 3:
            durations[position] -= 1
            difference += 1

        index += 1

    for scene, duration in zip(scenes, durations):
        scene["duration_seconds"] = duration

    return scenes


def generate_video_package_from_script(
    title,
    script,
    duration_minutes,
    video_style,
    progress_callback=None,
):
    """Build the normal video package from a user-supplied script.

    This deliberately skips AI script generation.
    """
    detected_style = detect_video_style(title, video_style)

    cleaned_script = conservative_script_cleanup(script or "").strip()

    if not cleaned_script:
        raise ValueError("The supplied script is empty.")

    if progress_callback:
        progress_callback("script_validation", 22)

    validation = validate_script(
        cleaned_script,
        duration_minutes,
    )

    if progress_callback:
        progress_callback("scene_splitting", 30)

    scenes = split_script_into_scenes(
        cleaned_script,
        duration_minutes,
    )

    if progress_callback:
        progress_callback("scene_enrichment", 32)

    scenes = enrich_scene_batch(
        title,
        detected_style,
        scenes,
        cleaned_script,
    )

    scenes = assign_scene_durations(
        scenes,
        duration_minutes * 60,
    )

    if progress_callback:
        progress_callback("scene_planning", 35)

    return {
        "title": title,
        "requested_style": video_style,
        "detected_style": detected_style,
        "duration_minutes": duration_minutes,
        "script": cleaned_script,
        "word_count": count_words(cleaned_script),
        "script_initial_provider": "user_supplied",
        "script_repair_providers": [],
        "script_validation": validation,
        "live_research": None,
        "sections": [],
        "scenes": scenes,
    }


def generate_video_package(title, duration_minutes, video_style, progress_callback=None):
    detected_style = detect_video_style(title, video_style)

    script_result = create_validated_script(
        title,
        duration_minutes,
        detected_style,
        progress_callback=progress_callback,
    )
    validation = script_result["validation"]
    print("SCRIPT_INITIAL_PROVIDER:", script_result["initial_provider"], flush=True)
    print("SCRIPT_REPAIR_PROVIDERS:", script_result["repair_providers"] or ["none"], flush=True)

    if progress_callback:
        progress_callback("scene_splitting", 30)

    scenes = split_script_into_scenes(
        script_result["script"],
        duration_minutes,
    )

    if progress_callback:
        progress_callback("scene_enrichment", 32)

    scenes = enrich_scene_batch(
        title,
        detected_style,
        scenes,
        script_result["script"],
    )

    scenes = assign_scene_durations(
        scenes,
        duration_minutes * 60,
    )

    if progress_callback:
        progress_callback("scene_planning", 35)

    return {
        "title": title,
        "requested_style": video_style,
        "detected_style": detected_style,
        "duration_minutes": duration_minutes,
        "script": script_result["script"],
        "word_count": script_result["actual_words"],
        "script_initial_provider": script_result["initial_provider"],
        "script_repair_providers": script_result["repair_providers"],
        "script_validation": validation,
        "sections": [],
        "scenes": scenes,
    }


def repair_script(title, minutes, style, original, report):
    target = minutes * 145
    prompt = (
        f"Rewrite this YouTube narration about: {title}\n"
        f"Style: {style}. Target exactly about {target} words.\n"
        "Remove all repeated sentences, paragraphs and ideas. "
        "Use a clear intro, progressive body and strong conclusion.\n"
        "Return narration only, without headings or notes.\n"
        f"Validation issues: {report['issues']}\n"
        f"Original script:\n{original}"
    )
    result = call_ai(prompt)
    text = result.text.strip()

    return {
        "script": text,
        "sections": [{
            "name": "Repaired Script",
            "narration": text,
            "word_count": count_words(text),
        }],
        "word_count": count_words(text),
        "target_words": target,
        "ai_provider": result.provider,
    }
