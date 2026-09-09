import math
import os
import time
import re
import logging

from app.ai_provider import (
    call_gemini,
    call_deepseek_script_pro,
    call_deepseek_script_flash,
)
from app.script_validator import (
    deduplicate_cta_intents,
    find_duplicate_sentences,
    repeated_phrase_ratio,
    validate_script,
)

WORDS_PER_MINUTE = 145
MAX_EXPANSION_ATTEMPTS = 2
MAX_SHORTEN_ATTEMPTS = 1
MAX_CHUNK_WORDS = 350
SECTION_MIN_RATIO = 0.93
SECTION_MAX_RATIO = 1.10
CADENCE_LONG_SENTENCE_WORDS = 30
CADENCE_EXTREME_SENTENCE_WORDS = 40


from app.video_profiles import script_profile_instructions

logger = logging.getLogger(__name__)


def word_count(text):
    return len(re.findall(r"\b[\w'-]+\b", text))


def narration_sentences(text):
    return [sentence.strip() for sentence in re.split(
        r"(?<=[.!?])\s+", str(text or "").strip()
    ) if sentence.strip()]


def analyze_spoken_cadence(text):
    sentences = narration_sentences(text)
    lengths = [word_count(sentence) for sentence in sentences]
    long_count = sum(length >= CADENCE_LONG_SENTENCE_WORDS for length in lengths)
    extreme_count = sum(length >= CADENCE_EXTREME_SENTENCE_WORDS for length in lengths)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    total = len(sentences)
    long_ratio = long_count / total if total else 0.0
    poor = bool(word_count(text) >= 120 and (
        extreme_count >= 2
        or (long_count >= 3 and long_ratio >= 0.30)
        or (total <= 5 and word_count(text) >= 300)
        or (len(paragraphs) == 1 and total <= 8 and word_count(text) >= 300)
    ))
    return {
        "word_count": word_count(text),
        "sentence_count": total,
        "long_sentence_count": long_count,
        "extreme_sentence_count": extreme_count,
        "long_sentence_ratio": long_ratio,
        "paragraph_count": len(paragraphs),
        "poor": poor,
    }


def repair_spoken_cadence(text):
    """One conservative word-preserving punctuation and paragraph pass."""
    repaired = str(text or "").strip()
    patterns = (
        r";\s+(?=[A-Za-z])",
        r",\s+(?=(?:but|yet|while|although|however|meanwhile|instead)\b)",
        r",\s+(?=(?:and|so)\s+(?:this|that|these|those|it|they|we)\b)",
    )
    for pattern in patterns:
        repaired = re.sub(pattern, ". ", repaired, flags=re.IGNORECASE)
    repaired = re.sub(
        r"(?<=[.!?]\s)([a-z])",
        lambda match: match.group(1).upper(),
        repaired,
    )
    sentences = narration_sentences(repaired)
    if len(sentences) < 2:
        return repaired
    return "\n\n".join(
        " ".join(sentences[index:index + 3])
        for index in range(0, len(sentences), 3)
    )


def apply_spoken_cadence_repair(text):
    before = analyze_spoken_cadence(text)
    if not before["poor"]:
        return text, before, before, False
    candidate = repair_spoken_cadence(text)
    after = analyze_spoken_cadence(candidate)
    if word_count(candidate) != before["word_count"]:
        return text, before, before, False
    return candidate, before, after, candidate != text


def remove_sequential_word_markers(text):
    """Remove leaked sequential word indexes such as
    '(238) dreamed (239) of (240) a', while preserving isolated
    legitimate numbers such as '(1893)'.
    """
    matches = list(
        re.finditer(r"\(\s*(\d{1,6})\s*\)", text or "")
    )

    if len(matches) < 4:
        return text

    removable = set()
    run = [matches[0]]

    for previous, current in zip(matches, matches[1:]):
        if int(current.group(1)) == int(previous.group(1)) + 1:
            run.append(current)
        else:
            if len(run) >= 4:
                removable.update(
                    (item.start(), item.end())
                    for item in run
                )
            run = [current]

    if len(run) >= 4:
        removable.update(
            (item.start(), item.end())
            for item in run
        )

    if not removable:
        return text

    pieces = []
    cursor = 0

    for item in matches:
        span = (item.start(), item.end())

        if span not in removable:
            continue

        pieces.append(text[cursor:item.start()])
        cursor = item.end()

    pieces.append(text[cursor:])

    cleaned = "".join(pieces)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;!?])", r"\1", cleaned)

    return cleaned


def emit_progress(callback, status, progress):
    if callback:
        callback(status, progress)



def conservative_script_cleanup(text):
    """
    Repair only obvious narration artifacts.

    This deliberately does NOT reject a script for style preferences.
    It removes clear generation leakage while preserving normal prose.
    """
    cleaned = str(text or "").strip()

    # Remove leaked Markdown/code formatting but preserve its spoken text.
    cleaned = re.sub(
        r"```(?:[A-Za-z0-9_-]+)?",
        " ",
        cleaned,
    )
    cleaned = cleaned.replace("**", "")
    cleaned = cleaned.replace("__", "")
    cleaned = cleaned.replace("`", "")

    # Keep heading/list content, but remove non-spoken formatting markers.
    cleaned = re.sub(
        r"(?im)^\s{0,3}#{1,6}\s*",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?im)^\s*(?:[-*•]+\s+|\d{1,2}[.)]\s+)",
        "",
        cleaned,
    )

    # Remove obvious leaked generation labels such as:
    # "Additional Middle Section: The Role of Water in Baking"
    cleaned = re.sub(
        r"(?im)^\s*Additional\s+(?:Middle\s+)?Section"
        r"(?:\s*:\s*[^\n]{0,120})?\s*$",
        " ",
        cleaned,
    )

    # Remove isolated CJK generation contamination from English narration.
    # Do not reject the script and do not strip ordinary accented Latin names.
    cleaned = re.sub(
        r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF"
        r"\u3040-\u30FF\uAC00-\uD7AF]+",
        " ",
        cleaned,
    )

    # Repair obvious broken transition leakage, e.g. "Yet. While..."
    cleaned = re.sub(
        r"\b(Yet|But|And|So)\.\s+"
        r"(While|Although|Because|When|If)\b",
        lambda match: f"{match.group(1)}, {match.group(2).lower()}",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Remove only EXACT consecutive repeated sentences.
    # Similar/repeated ideas are left to the existing validator.
    for sentence in narration_sentences(cleaned):
        if word_count(sentence) < 3:
            continue

        duplicate_pattern = (
            re.escape(sentence)
            + r"\s+"
            + re.escape(sentence)
        )

        cleaned = re.sub(
            duplicate_pattern,
            sentence,
            cleaned,
            count=1,
            flags=re.IGNORECASE,
        )

    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]*\n[ \t]*", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"\s+([,.;!?])", r"\1", cleaned)

    return cleaned.strip()


def clean_script(text):
    text = text.strip()
    text = conservative_script_cleanup(text)
    text = remove_sequential_word_markers(text)

    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]

    text = re.sub(
        r"(?i)\s*\(\s*word\s*count\s*:\s*[\d,]+"
        r"\s*(?:words?)?\s*\)\s*",
        " ",
        text,
    )
    text = re.sub(
        r"(?im)^\s*word\s*count\s*:\s*[\d,]+"
        r"\s*(?:words?)?\s*$",
        "",
        text,
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = remove_sequential_word_markers(text)
    text = deduplicate_cta_intents(text)
    text = conservative_script_cleanup(text)

    return text.strip()



def _looks_like_generation_meta(text):
    lowered = " ".join(str(text or "").lower().split())
    suspicious = (
        "this ensures standard word counters",
        "let's review the content against instructions",
        "review the content against instructions",
        "required length:",
        "cover outline item",
        "section 1 mentioned:",
        "section 2 does",
        "against instructions:",
        "current draft length:",
        "previous repair length:",
        "word count is",
        "hmm, the user",
        "the user has given me",
        "the user has asked",
        "the user asked me",
        "they need me to",
        "looking at the requirements",
        "looking at these requirements",
        "looking at the instructions",
        "my task is to",
        "i need to adjust",
        "i need to revise",
        "i should revise",
        "i should write",
        "the current draft is",
        "the draft is exactly",
        "this is for a ",
        "let me review",
        "let me check",
        "i'll revise",
        "i will revise",
    )
    if any(value in lowered for value in suspicious):
        return True

    # Edit/reasoning notation should never appear in narration.
    if " -> " in str(text or "") and (
        "word" in lowered or "replace" in lowered or "count" in lowered
    ):
        return True

    return False


def _valid_outline(text, section_count):
    text = str(text or "").strip()

    if not text or _looks_like_generation_meta(text):
        return False

    # Accept normal, Markdown-bold and inline numbering:
    # 1. item
    # 1) item
    # **1.** item
    # **1)** item
    pattern = re.compile(
        r"(?:^|\n|\s)"
        r"(?:\*\*)?"
        r"(\d+)"
        r"[.)]"
        r"(?:\*\*)?"
        r"\s+",
        re.MULTILINE,
    )

    matches = list(pattern.finditer(text))
    numbers = [int(match.group(1)) for match in matches]

    if numbers != list(range(1, section_count + 1)):
        return False

    # Ensure every numbered item contains actual content.
    for index, match in enumerate(matches):
        content_start = match.end()
        content_end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(text)
        )

        if len(text[content_start:content_end].strip()) < 8:
            return False

    return True



def has_broken_opening(text):
    stripped = text.lstrip(" \t\r\n\"'([{")
    return bool(
        stripped
        and stripped[0].isalpha()
        and stripped[0].islower()
    )


def quality_rank(text):
    return (
        1 if has_broken_opening(text) else 0,
        len(find_duplicate_sentences(text)),
        repeated_phrase_ratio(text),
    )


def needs_chunk_retry(text):
    broken, duplicates, phrase_ratio = quality_rank(text)
    return (
        broken
        or duplicates >= 2
        or phrase_ratio > 0.025
    )


def needs_section_retry(existing_text, section_text):
    # Retry only when the NEW section itself is weak or introduces
    # additional repetition. Do not let an older issue cause every
    # later section to regenerate.
    if needs_chunk_retry(section_text):
        return True

    if not existing_text.strip():
        return False

    _, old_duplicates, old_phrase_ratio = quality_rank(existing_text)

    combined = existing_text + "\n\n" + section_text
    _, new_duplicates, new_phrase_ratio = quality_rank(combined)

    if new_duplicates > old_duplicates:
        return True

    return (
        new_phrase_ratio > 0.025
        and new_phrase_ratio > old_phrase_ratio
    )


def _script_api_delay():
    """Small pacing delay to avoid Together burst rate limits."""
    try:
        return max(
            0.0,
            float(os.getenv("SCRIPT_API_DELAY_SECONDS", "4")),
        )
    except (TypeError, ValueError):
        return 4.0


def _pace_script_api():
    delay = _script_api_delay()
    if delay > 0:
        time.sleep(delay)


def provider_chain():
    # Flash is the default long-form writer for speed and cost.
    # Pro is reserved as the higher-quality fallback when Flash fails.
    return [
        ("deepseek-v4-flash", call_deepseek_script_flash),
        ("deepseek-v4-pro", call_deepseek_script_pro),
        ("gemini", call_gemini),
    ]


def first_success(
    prompt,
    providers=None,
    require_complete_start=False,
    validator=None,
    validation_name="output",
    cleaner=clean_script,
):
    errors = []
    chain = providers or provider_chain()

    # Two complete passes make transient cloud/model failures recoverable
    # without weakening any narration quality gate.
    for round_number in range(2):
        if round_number:
            logger.warning(
                "SCRIPT_PROVIDER_CHAIN_RETRY round=%s",
                round_number + 1,
            )
            _pace_script_api()

        for name, provider in chain:
            try:
                result = provider(prompt, json_mode=False)
                text = cleaner(result.text)

                if text and require_complete_start and has_broken_opening(text):
                    message = (
                        f"{name}: output began with a broken "
                        "lowercase sentence fragment"
                    )
                    errors.append(message)
                    logger.warning("SCRIPT_PROVIDER_REJECTED %s", message)
                    continue

                if text and validator is not None and not validator(text):
                    message = f"{name}: invalid {validation_name}"
                    errors.append(message)
                    logger.warning("SCRIPT_PROVIDER_REJECTED %s", message)
                    continue

                if text:
                    return text, result.provider

                message = f"{name}: empty output"
                errors.append(message)
                logger.warning("SCRIPT_PROVIDER_REJECTED %s", message)

            except Exception as error:
                message = f"{name}: {error}"
                errors.append(message)
                logger.warning("SCRIPT_PROVIDER_FAILED %s", message)

    raise RuntimeError(
        "All providers failed after 2 rounds: " + " | ".join(errors)
    )



def calculate_section_count(target_words):
    if target_words <= 550:
        return 1

    return max(2, math.ceil(target_words / MAX_CHUNK_WORDS))


def calculate_section_targets(target_words, section_count):
    base = target_words // section_count
    remainder = target_words % section_count

    return [
        base + (1 if index < remainder else 0)
        for index in range(section_count)
    ]


def generate_outline(title, style, section_count):
    prompt = f"""
Create a concise continuity outline for a long-form YouTube narration.

Title: {title}
Style: {style}
Profile-specific instructions:
{script_profile_instructions(style)}
Number of sections: {section_count}

Rules:
- Return exactly {section_count} numbered lines using the format "1. ...", "2. ...", etc.
- Output no introduction, explanation, paragraph, heading, note or prose before/after those numbered lines
- Treat the outline as a strict coverage contract for the final script
- Each line must cover a clearly distinct, non-overlapping part of the topic
- Assign each major fact, mechanism, example, process, challenge or consequence to ONE section only
- Do not assign the same explanation or mechanism to multiple sections using different wording
- Make section boundaries specific enough that the writer knows exactly what belongs there
- Arrange sections in a logical narrative order: hook/context -> mechanisms/process -> consequences/application -> conclusion
- Later sections may briefly refer back to earlier information, but must never explain it again
- For factual topics, avoid unsupported claims, exaggerated consequences and false certainty
- The first section must contain the opening hook
- The final section must contain the conclusion and viewer CTA
- Keep each outline line concise
- Return the outline only
"""

    return first_success(
        prompt,
        require_complete_start=True,
        validator=lambda text: _valid_outline(text, section_count),
        validation_name=f"{section_count}-line numbered outline",
        cleaner=lambda text: str(text or "").strip(),
    )


def generate_section(
    title,
    style,
    outline,
    section_number,
    section_count,
    target_words,
    previous_tail,
):
    is_final = section_number == section_count
    minimum_words = max(
        1,
        math.floor(target_words * SECTION_MIN_RATIO),
    )
    maximum_words = max(
        minimum_words,
        math.ceil(target_words * SECTION_MAX_RATIO),
    )

    if is_final:
        ending_rules = """
- Finish with a clear topic-specific takeaway
- Include one natural topic-related question
- Invite viewers to answer in the comments
- Include one brief natural request to like and subscribe
- Do not repeat the like, subscribe or comment request in another sentence
- Do not use a generic or repeated ending
"""
    else:
        ending_rules = """
- Do not conclude the full video
- Do not include a comment, like or subscribe CTA
- End with a natural transition toward the next section
"""

    continuity = previous_tail or (
        "This is the opening section, so begin with the main hook."
    )

    prompt = f"""
Write section {section_number} of {section_count} for a complete
long-form YouTube narration.

Title: {title}
Style: {style}
Profile-specific instructions:
{script_profile_instructions(style)}
Required length for this section: between {minimum_words} and
{maximum_words} words. Aim for {target_words} words.

Full outline:
{outline}

Previously written narration / continuity:
{continuity}

IMPORTANT: Everything in the previous narration above is already covered.
Use it for continuity and as a strict DO-NOT-REPEAT ledger.

Rules:
- Write only section {section_number}
- Stay within the required word range
- Never exceed {maximum_words} words
- Focus only on outline item {section_number}
- Do not cover later outline items early
- Start with a complete sentence beginning with a capital letter
- Keep the narration natural, detailed and conversational
- Write for spoken delivery with a conversational documentary cadence
- Prefer natural sentences of roughly 12-22 words where they fit the thought
- Split 30-plus-word sentences at logical boundaries when practical; longer sentences remain allowed when they sound natural
- Keep one major thought per sentence where reasonable, with natural commas where a narrator would pause
- Use paragraphs of roughly 2-4 sentences at meaningful topic transitions
- Vary sentence length naturally; do not make the narration robotic
- Keep factual claims accurate, proportionate and consistent with the available evidence
- Distinguish established facts, reasonable inference and speculation
- Keep cause-and-effect mechanisms and timescales plausible
- Do not invent unsupported chains of consequences or present uncertain outcomes as certain
- Do not invent precise figures, measurements, dates, quotes or probabilities
- Consider remaining forces, alternative causes and system inertia before claiming a complete stop or major failure
- Use cautious wording when evidence is limited, but do not weaken well-established facts with unnecessary hedging
- Frame predicted social, economic, technological or environmental reactions as possibilities when they are not established
- Preserve an engaging tone through explanation and meaningful stakes, not factual exaggeration
- Do not repeat information from earlier sections
- Never re-explain a fact, mechanism, analogy, statistic, process or example that appears in the previous narration, even using different wording
- If earlier information must be referenced for continuity, use only a short linking phrase and immediately move to genuinely new material
- Begin this section with the new material assigned to its own outline item; never restart the introduction or re-establish concepts already explained
- Before returning the section, silently check that its core sentences add new information rather than paraphrasing previous sections
- Do not use headings, labels, bullet points or visual directions
- Never output planning, analysis, self-review, instruction checks, editing notes, word-count calculations, substitutions, arrows, or commentary about the draft
- Do not mention the prompt, outline rules, section rules, required word range, or how you counted words
- Return ONLY the finished narration
{ending_rules}
"""

    return first_success(
        prompt,
        require_complete_start=True,
        validator=lambda text: not _looks_like_generation_meta(text),
        validation_name="narration-only section",
    )


def shorten_section(
    title,
    style,
    section_text,
    target_words,
    is_final,
    previous_word_count=None,
    source_text=None,
):
    minimum_words = max(
        1,
        math.floor(target_words * SECTION_MIN_RATIO),
    )
    maximum_words = max(
        minimum_words,
        math.ceil(target_words * SECTION_MAX_RATIO),
    )

    if is_final:
        ending_rules = """
- Keep or restore a clear topic-specific conclusion
- Ensure one natural topic-related viewer question
- Ensure a natural invitation to comment
- Ensure a brief natural request to like and subscribe
"""
    else:
        ending_rules = """
- Do not add a conclusion for the full video
- Do not add a comment, like or subscribe CTA
- Preserve a natural transition to the next section
"""

    source_text = source_text or section_text
    current_words = word_count(section_text)

    previous_note = (
        f"Previous repair length: {previous_word_count} words."
        if previous_word_count is not None
        else "This is the first repair attempt."
    )

    if current_words > maximum_words:
        task = (
            "Condense the current draft by removing repetition, "
            "filler and low-value wording."
        )
    elif current_words < minimum_words:
        task = (
            "Expand the current draft by restoring useful details "
            "that already exist in the original source narration."
        )
    else:
        task = (
            "Make only minimal wording adjustments while preserving "
            "the current length."
        )

    prompt = f"""
/no_think

Balance the current YouTube narration to fit the required word range.

Title: {title}
Style: {style}
Profile-specific instructions:
{script_profile_instructions(style)}
Current draft length: {current_words} words.
Required final length: between {minimum_words} and
{maximum_words} words.
Aim as closely as possible for {target_words} words.
{previous_note}

Task:
{task}

Original source narration:
{source_text}

Current draft to revise:
{section_text}

Rules:
- Preserve the original meaning and narrative continuity
- Use only facts and details present in the original source narration
- Do not introduce new facts, examples or claims
- Keep useful details while removing repetition and filler
- Keep the tone natural and conversational
- Preserve natural spoken sentence boundaries and meaningful paragraph breaks
- Do not use headings, labels, bullets or visual directions
- Do not produce fewer than {minimum_words} words
- Never exceed {maximum_words} words
- Silently check the word count before returning
- Return narration only
{ending_rules}
"""

    repaired, provider = first_success(
        prompt,
        providers=[
            ("deepseek-v4-pro", call_deepseek_script_pro),
            ("deepseek-v4-flash", call_deepseek_script_flash),
            ("gemini", call_gemini),
        ],
        require_complete_start=True,
        validator=lambda text: not _looks_like_generation_meta(text),
        validation_name="narration-only repair",
    )

    repaired = clean_script(repaired)

    if not repaired or _looks_like_generation_meta(repaired):
        raise RuntimeError(
            "Cloud script repair returned invalid narration."
        )

    return repaired, provider


def generate_expansion(
    title,
    style,
    outline,
    target_words,
    previous_tail,
    next_opening,
    existing_narration,
):
    prompt = f"""
Write one additional middle section for this long-form YouTube
narration.

Title: {title}
Style: {style}
Profile-specific instructions:
{script_profile_instructions(style)}
Target length: approximately {target_words} words.

Full outline:
{outline}

Previous section ending:
{previous_tail}

Following section opening:
{next_opening}

Existing narration already written:
{existing_narration}

Rules:
- Add only genuinely new material missing from the existing narration
- Do not repeat, summarize or rephrase ideas, examples or events already covered
- Add useful depth, examples, consequences or explanation
- Keep new factual material accurate, proportionate and supported by a plausible mechanism
- Clearly distinguish established effects, reasonable inference and speculation
- Preserve realistic timescales and do not invent unsupported downstream consequences
- Consider alternative causes, remaining influences and system inertia
- Frame uncertain social, economic, technological or environmental reactions as possibilities
- Connect naturally between the supplied surrounding sections
- Write for speech: use clear sentence boundaries, natural pauses, and meaningful paragraphs rather than dense run-ons
- Do not restart the introduction
- Do not conclude the full video
- Do not include a comment, like or subscribe CTA
- No headings, labels, bullet points or visual directions
- Return narration only
"""

    return first_success(prompt, require_complete_start=True)


def balance_expansion(
    title,
    style,
    expansion,
    provider,
    target_words,
):
    minimum_words = max(
        1,
        math.floor(target_words * 0.90),
    )
    maximum_words = max(
        minimum_words,
        math.ceil(target_words * 1.10),
    )

    current_words = word_count(expansion)

    if minimum_words <= current_words <= maximum_words:
        return expansion, provider

    original_expansion = expansion
    best_expansion = expansion
    best_provider = provider
    best_distance = abs(current_words - target_words)
    previous_word_count = None

    for _ in range(MAX_SHORTEN_ATTEMPTS):
        candidate, candidate_provider = shorten_section(
            title=title,
            style=style,
            section_text=expansion,
            target_words=target_words,
            is_final=False,
            previous_word_count=previous_word_count,
            source_text=original_expansion,
        )

        candidate_words = word_count(candidate)
        candidate_distance = abs(
            candidate_words - target_words
        )

        if candidate_distance < best_distance:
            best_expansion = candidate
            best_provider = candidate_provider
            best_distance = candidate_distance

        if minimum_words <= candidate_words <= maximum_words:
            return candidate, candidate_provider

        expansion = candidate
        previous_word_count = candidate_words

    return best_expansion, best_provider



def split_single_section_for_expansion(
    section,
    conclusion_target_words=140,
):
    section = section.strip()

    sentences = [
        sentence.strip()
        for sentence in re.split(
            r"(?<=[.!?])\s+",
            section,
        )
        if sentence.strip()
    ]

    if len(sentences) < 2:
        return [section] if section else []

    conclusion_sentences = []
    conclusion_words = 0

    while len(sentences) > 1:
        sentence = sentences.pop()
        conclusion_sentences.insert(0, sentence)
        conclusion_words += word_count(sentence)

        if conclusion_words >= conclusion_target_words:
            break

    body = " ".join(sentences).strip()
    conclusion = " ".join(
        conclusion_sentences
    ).strip()

    return [
        part
        for part in (body, conclusion)
        if part
    ]

def tail_words(text, limit=180):
    return " ".join(text.split()[-limit:])


def head_words(text, limit=120):
    return " ".join(text.split()[:limit])



def profile_ending_question(style):
    questions = {
        "auto": (
            "Which part of this topic would you like "
            "to explore next?"
        ),
        "general_explainer": (
            "Which part of this topic would you like "
            "to explore next?"
        ),
        "tech_science": (
            "Which part of this technology or science "
            "topic should we explore next?"
        ),
        "what_if": (
            "Which consequence of this scenario do you "
            "think would happen first?"
        ),
        "historical_place": (
            "Which part of this place's history interests "
            "you most?"
        ),
        "historical_event": (
            "Which moment or consequence of this event "
            "do you think mattered most?"
        ),
        "geography_travel": (
            "Which place or feature would you like to "
            "explore further?"
        ),
        "nature_wildlife": (
            "Which behavior or natural feature surprised "
            "you most?"
        ),
        "space_astronomy": (
            "Which part of this space topic should we "
            "explore next?"
        ),
        "business_economics": (
            "Which part of this business or economic "
            "story should we break down next?"
        ),
        "educational_how_it_works": (
            "Which step or mechanism would you like "
            "explained further?"
        ),
        "health_wellness": "Which part of health and wellbeing should we explore next?",
        "sports": "Which technique or tactic should we break down next?",
        "automotive_transport": "Which vehicle system should we explain next?",
        "food_cooking": "Which ingredient or cooking process should we explore next?",
        "environment_climate": "Which environmental solution should we examine next?",
        "culture_society": "Which cultural pattern should we explore next?",
        "architecture_engineering": "Which structural feature should we explain next?",
    }

    return questions.get(
        style,
        questions["general_explainer"],
    )


def ensure_ending_requirements(script, duration, style="general_explainer"):
    script = clean_script(script)
    validation = validate_script(script, duration)
    issues = validation.get("issues", [])
    required_question = profile_ending_question(style)
    missing_profile_question = required_question not in script

    ending_issues = [
        issue
        for issue in issues
        if issue.startswith("Ending is missing")
    ]

    if not ending_issues and not missing_profile_question:
        return script

    missing_question = any(
        "viewer question" in issue
        for issue in ending_issues
    )
    missing_comment = any(
        "invitation to comment" in issue
        for issue in ending_issues
    )
    missing_like = any(
        "like CTA" in issue
        for issue in ending_issues
    )
    missing_subscribe = any(
        "subscribe CTA" in issue
        for issue in ending_issues
    )

    additions = []

    if missing_question or missing_profile_question:
        additions.append(required_question)

    if missing_comment:
        additions.append(
            "Share your answer in the comments."
        )

    if missing_like and missing_subscribe:
        additions.append(
            "If you enjoyed this video, like it and subscribe for more."
        )
    elif missing_like:
        additions.append(
            "If you enjoyed this video, give it a like."
        )
    elif missing_subscribe:
        additions.append(
            "Subscribe for more videos like this."
        )

    if not additions:
        return script

    sentences = [
        sentence.strip()
        for sentence in re.split(
            r"(?<=[.!?])\s+",
            script.strip(),
        )
        if sentence.strip()
    ]

    if not sentences:
        return script

    cta = " ".join(additions)

    def build_candidate():
        return (
            " ".join(sentences).strip()
            + " "
            + cta
        ).strip()

    candidate = build_candidate()
    candidate_validation = validate_script(
        candidate,
        duration,
    )

    maximum_words = candidate_validation.get(
        "maximum_words",
        math.floor(duration * WORDS_PER_MINUTE * 1.10),
    )

    while (
        candidate_validation["word_count"] > maximum_words
        and len(sentences) > 3
    ):
        overflow = (
            candidate_validation["word_count"]
            - maximum_words
        )

        removable = list(
            range(1, len(sentences) - 2)
        )

        large_enough = [
            index
            for index in removable
            if word_count(sentences[index]) >= overflow
        ]

        if large_enough:
            remove_index = min(
                large_enough,
                key=lambda index: word_count(
                    sentences[index]
                ),
            )
        else:
            remove_index = min(
                removable,
                key=lambda index: word_count(
                    sentences[index]
                ),
            )

        sentences.pop(remove_index)
        candidate = build_candidate()
        candidate_validation = validate_script(
            candidate,
            duration,
        )

    return clean_script(candidate)

def trim_script_to_max_words(script, maximum_words):
    """Deterministically remove excess middle sentences while
    preserving the opening and final CTA/conclusion.
    """
    script = clean_script(script)

    if word_count(script) <= maximum_words:
        return script

    sentences = [
        sentence.strip()
        for sentence in re.split(
            r"(?<=[.!?])\s+",
            script,
        )
        if sentence.strip()
    ]

    if len(sentences) <= 3:
        return script

    while word_count(" ".join(sentences)) > maximum_words:
        overflow = (
            word_count(" ".join(sentences))
            - maximum_words
        )

        # Preserve opening and last two conclusion/CTA sentences.
        removable = list(
            range(1, max(1, len(sentences) - 2))
        )

        if not removable:
            break

        large_enough = [
            index
            for index in removable
            if word_count(sentences[index]) >= overflow
        ]

        if large_enough:
            remove_index = min(
                large_enough,
                key=lambda index: word_count(
                    sentences[index]
                ),
            )
        else:
            remove_index = max(
                removable,
                key=lambda index: word_count(
                    sentences[index]
                ),
            )

        sentences.pop(remove_index)

    return clean_script(" ".join(sentences))


def generate_initial_script(title, duration, style, progress_callback=None):
    from app.live_research import prepare_live_research

    original_title = title
    live_research = prepare_live_research(
        title,
        style,
    )

    if live_research:
        title = live_research["grounded_title"]

    target = int(duration * WORDS_PER_MINUTE)
    section_count = calculate_section_count(target)

    emit_progress(
        progress_callback,
        "outline_generation",
        5,
    )

    outline, outline_provider = generate_outline(
        title,
        style,
        section_count,
    )

    targets = calculate_section_targets(
        target,
        section_count,
    )

    sections = []
    section_providers = []
    previous_tail = ""

    emit_progress(
        progress_callback,
        "script_sections",
        8,
    )

    for index, target_words in enumerate(targets, start=1):
        _pace_script_api()

        section, provider = generate_section(
            title=title,
            style=style,
            outline=outline,
            section_number=index,
            section_count=section_count,
            target_words=target_words,
            previous_tail=previous_tail,
        )

        existing_text = "\n\n".join(sections)
        combined = "\n\n".join(sections + [section])

        if needs_section_retry(existing_text, section):
            _pace_script_api()

            retry_section, retry_provider = generate_section(
                title=title,
                style=style,
                outline=outline,
                section_number=index,
                section_count=section_count,
                target_words=target_words,
                previous_tail=previous_tail,
            )

            retry_combined = "\n\n".join(
                sections + [retry_section]
            )

            if quality_rank(retry_combined) < quality_rank(combined):
                section = retry_section
                provider = retry_provider

        maximum_words = math.ceil(
            target_words * 1.10
        )

        minimum_words = math.floor(
            target_words * 0.90
        )

        if word_count(section) > maximum_words:
            original_section = section
            previous_word_count = None
            best_section = section
            best_provider = provider
            best_distance = abs(
                word_count(section) - target_words
            )

            for _ in range(MAX_SHORTEN_ATTEMPTS):
                candidate, candidate_provider = shorten_section(
                    title=title,
                    style=style,
                    section_text=section,
                    target_words=target_words,
                    is_final=index == section_count,
                    previous_word_count=previous_word_count,
                    source_text=original_section,
                )

                candidate_words = word_count(candidate)
                candidate_distance = abs(
                    candidate_words - target_words
                )

                if candidate_distance < best_distance:
                    best_section = candidate
                    best_provider = candidate_provider
                    best_distance = candidate_distance

                section = candidate
                previous_word_count = candidate_words

                if minimum_words <= candidate_words <= maximum_words:
                    break

            section = best_section
            provider = best_provider

        sections.append(section)
        section_providers.append(provider)
        previous_tail = tail_words(
            "\n\n".join(sections),
            limit=900,
        )

        emit_progress(
            progress_callback,
            "script_sections",
            8 + round((index / section_count) * 12),
        )

    return {
        "sections": sections,
        "target_words": target,
        "outline": outline,
        "outline_provider": outline_provider,
        "section_providers": section_providers,
        "live_research": live_research,
        "original_title": original_title,
    }


def create_validated_script(title, duration, style, progress_callback=None):
    generated = generate_initial_script(
        title,
        duration,
        style,
        progress_callback=progress_callback,
    )

    emit_progress(
        progress_callback,
        "script_validation",
        22,
    )

    sections = generated["sections"]
    target = generated["target_words"]
    outline = generated["outline"]
    expansion_providers = []

    for _ in range(MAX_EXPANSION_ATTEMPTS):
        script = "\n\n".join(
            section.strip()
            for section in sections
            if section.strip()
        )

        validation = validate_script(script, duration)

        if validation.get("valid"):
            break

        current = validation["word_count"]
        minimum = validation.get(
            "minimum_words",
            math.ceil(target * 0.90),
        )
        maximum = validation.get(
            "maximum_words",
            math.floor(target * 1.10),
        )

        if current < minimum:
            emit_progress(
                progress_callback,
                "script_refinement",
                25,
            )

            missing = max(1, target - current)
            expansion_target = min(
                MAX_CHUNK_WORDS,
                max(60, missing),
            )

            if len(sections) == 1:
                sections = split_single_section_for_expansion(
                    sections[0],
                    conclusion_target_words=140,
                )

            insert_at = max(1, len(sections) - 1)

            previous_section = sections[insert_at - 1]
            next_section = sections[insert_at]

            expansion, provider = generate_expansion(
                title=title,
                style=style,
                outline=outline,
                target_words=expansion_target,
                previous_tail=tail_words(previous_section),
                next_opening=head_words(next_section),
                existing_narration=script,
            )

            expansion, provider = balance_expansion(
                title=title,
                style=style,
                expansion=expansion,
                provider=provider,
                target_words=expansion_target,
            )

            candidate_sections = (
                sections[:insert_at]
                + [expansion]
                + sections[insert_at:]
            )
            candidate_script = "\n\n".join(candidate_sections)

            if needs_chunk_retry(candidate_script):
                retry_expansion, retry_provider = generate_expansion(
                    title=title,
                    style=style,
                    outline=outline,
                    target_words=expansion_target,
                    previous_tail=tail_words(previous_section),
                    next_opening=head_words(next_section),
                    existing_narration=script,
                )

                retry_expansion, retry_provider = balance_expansion(
                    title=title,
                    style=style,
                    expansion=retry_expansion,
                    provider=retry_provider,
                    target_words=expansion_target,
                )

                retry_sections = (
                    sections[:insert_at]
                    + [retry_expansion]
                    + sections[insert_at:]
                )
                retry_script = "\n\n".join(retry_sections)

                if quality_rank(retry_script) < quality_rank(candidate_script):
                    expansion = retry_expansion
                    provider = retry_provider
                    candidate_script = retry_script

            if needs_chunk_retry(candidate_script):
                raise RuntimeError(
                    "Expansion rejected because it repeats "
                    "existing narration."
                )

            sections.insert(insert_at, expansion)
            expansion_providers.append(provider)
            continue

        ending_issues = [
            issue
            for issue in validation.get("issues", [])
            if issue.startswith("Ending is missing")
        ]

        if current <= maximum and ending_issues:
            emit_progress(
                progress_callback,
                "script_refinement",
                25,
            )

            final_index = len(sections) - 1
            final_section = sections[final_index]
            final_target = max(
                1,
                word_count(final_section),
            )

            repaired, provider = shorten_section(
                title=title,
                style=style,
                section_text=final_section,
                target_words=final_target,
                is_final=True,
                source_text=final_section,
            )

            if repaired != final_section:
                sections[final_index] = repaired
                expansion_providers.append(provider)
                continue

        break

    script = "\n\n".join(
        section.strip()
        for section in sections
        if section.strip()
    )

    script = clean_script(script)

    if _looks_like_generation_meta(script):
        raise RuntimeError(
            "Script rejected: AI planning/reasoning leaked into narration."
        )

    final_validation = validate_script(script, duration)

    ending_issues = [
        issue
        for issue in final_validation.get("issues", [])
        if issue.startswith("Ending is missing")
    ]

    if ending_issues or profile_ending_question(style) not in script:
        emit_progress(
            progress_callback,
            "script_refinement",
            27,
        )

        script = ensure_ending_requirements(
            script,
            duration,
            style,
        )
        script = clean_script(script)
        final_validation = validate_script(
            script,
            duration,
        )

    # Final deterministic length safety gate. This prevents a
    # good script from failing simply because an LLM shortening
    # attempt remains slightly above the allowed maximum.
    maximum_words = final_validation.get(
        "maximum_words",
        math.floor(duration * WORDS_PER_MINUTE * 1.10),
    )

    if final_validation["word_count"] > maximum_words:
        script = trim_script_to_max_words(
            script,
            maximum_words,
        )

        final_validation = validate_script(
            script,
            duration,
        )

    if not final_validation.get("valid"):
        issues = " | ".join(final_validation.get("issues", []))

        raise RuntimeError(
            "Script validation failed after chunked generation: "
            + issues
        )

    script, cadence_before, cadence_after, cadence_repaired = (
        apply_spoken_cadence_repair(script)
    )
    final_validation = validate_script(script, duration)

    section_providers = generated["section_providers"]

    return {
        "script": script,
        "target_words": target,
        "actual_words": final_validation["word_count"],
        "initial_provider": (
            section_providers[0]
            if section_providers
            else generated["outline_provider"]
        ),
        "repair_providers": expansion_providers,
        "outline_provider": generated["outline_provider"],
        "chunk_providers": section_providers,
        "validation": final_validation,
        "cadence": {
            "before": cadence_before,
            "after": cadence_after,
            "repaired": cadence_repaired,
        },
        "live_research": generated.get("live_research"),
    }
