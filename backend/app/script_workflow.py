import math
import re

from app.ai_provider import (
    call_gemini,
    call_ollama,
    call_ollama_length_repair,
    call_together,
)
from app.script_validator import validate_script

WORDS_PER_MINUTE = 145
MAX_EXPANSION_ATTEMPTS = 5
MAX_SHORTEN_ATTEMPTS = 3
MAX_CHUNK_WORDS = 800
SECTION_MIN_RATIO = 0.95
SECTION_MAX_RATIO = 1.05


def word_count(text):
    return len(re.findall(r"\b[\w'-]+\b", text))


def clean_script(text):
    text = text.strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]

    return text.strip()


def provider_chain():
    return [
        ("gemini", call_gemini),
        ("ollama", call_ollama),
        ("together", call_together),
    ]


def first_success(prompt, providers=None):
    errors = []

    for name, provider in providers or provider_chain():
        try:
            result = provider(prompt, json_mode=False)
            text = clean_script(result.text)

            if text:
                return text, result.provider
        except Exception as error:
            errors.append(f"{name}: {error}")

    raise RuntimeError(
        "All providers failed: " + " | ".join(errors)
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
Number of sections: {section_count}

Rules:
- Return exactly {section_count} numbered lines
- Each line must cover a distinct part of the topic
- Arrange sections in a logical narrative order
- Avoid repeating the same information
- The first section must contain the opening hook
- The final section must contain the conclusion and viewer CTA
- Keep each outline line concise
- Return the outline only
"""

    return first_success(prompt)


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
- Include a brief natural request to like and subscribe
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
Required length for this section: between {minimum_words} and
{maximum_words} words. Aim for {target_words} words.

Full outline:
{outline}

Continuity from the previous section:
{continuity}

Rules:
- Write only section {section_number}
- Stay within the required word range
- Never exceed {maximum_words} words
- Follow the matching part of the outline
- Keep the narration natural, detailed and conversational
- Do not repeat information from earlier sections
- Do not use headings, labels, bullet points or visual directions
- Return narration only
{ending_rules}
"""

    return first_success(prompt)


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
- Do not use headings, labels, bullets or visual directions
- Do not produce fewer than {minimum_words} words
- Never exceed {maximum_words} words
- Silently check the word count before returning
- Return narration only
{ending_rules}
"""

    result = call_ollama_length_repair(
        prompt,
        maximum_words=maximum_words,
    )
    repaired = clean_script(result.text)

    if not repaired:
        raise RuntimeError(
            "Ollama length repair returned empty output."
        )

    return repaired, result.provider


def generate_expansion(
    title,
    style,
    outline,
    target_words,
    previous_tail,
    next_opening,
):
    prompt = f"""
Write one additional middle section for this long-form YouTube
narration.

Title: {title}
Style: {style}
Target length: approximately {target_words} words.

Full outline:
{outline}

Previous section ending:
{previous_tail}

Following section opening:
{next_opening}

Rules:
- Add useful depth, examples, consequences or explanation
- Cover material not already repeated in the existing narration
- Connect naturally between the supplied surrounding sections
- Do not restart the introduction
- Do not conclude the full video
- Do not include a comment, like or subscribe CTA
- No headings, labels, bullet points or visual directions
- Return narration only
"""

    return first_success(prompt)


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



def ensure_ending_requirements(script, duration):
    validation = validate_script(script, duration)
    issues = validation.get("issues", [])

    ending_issues = [
        issue
        for issue in issues
        if issue.startswith("Ending is missing")
    ]

    if not ending_issues:
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

    if missing_question:
        additions.append(
            "What will you do first?"
        )

    if missing_comment:
        additions.append(
            "Share your answer in the comments."
        )

    if missing_like and missing_subscribe:
        additions.append(
            "Like this video and subscribe for more."
        )
    elif missing_like:
        additions.append(
            "Like this video."
        )
    elif missing_subscribe:
        additions.append(
            "Subscribe for more."
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

    return candidate

def generate_initial_script(title, duration, style):
    target = int(duration * WORDS_PER_MINUTE)
    section_count = calculate_section_count(target)

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

    for index, target_words in enumerate(targets, start=1):
        section, provider = generate_section(
            title=title,
            style=style,
            outline=outline,
            section_number=index,
            section_count=section_count,
            target_words=target_words,
            previous_tail=previous_tail,
        )

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
        previous_tail = tail_words(section)

    return {
        "sections": sections,
        "target_words": target,
        "outline": outline,
        "outline_provider": outline_provider,
        "section_providers": section_providers,
    }


def create_validated_script(title, duration, style):
    generated = generate_initial_script(
        title,
        duration,
        style,
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
            )

            expansion, provider = balance_expansion(
                title=title,
                style=style,
                expansion=expansion,
                provider=provider,
                target_words=expansion_target,
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

    final_validation = validate_script(script, duration)

    ending_issues = [
        issue
        for issue in final_validation.get("issues", [])
        if issue.startswith("Ending is missing")
    ]

    if ending_issues:
        script = ensure_ending_requirements(
            script,
            duration,
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
    }
