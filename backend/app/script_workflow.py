import re

from app.ai_provider import (
    call_gemini,
    call_ollama,
    call_together,
)
from app.script_validator import validate_script

WORDS_PER_MINUTE = 145


def word_count(text):
    return len(re.findall(r"\b[\w'-]+\b", text))


def clean_script(text):
    text = text.strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]

    return text.strip()


def first_success(prompt, providers):
    errors = []

    for name, provider in providers:
        try:
            result = provider(prompt, json_mode=False)
            text = clean_script(result.text)
            if text:
                return text, result.provider
        except Exception as error:
            errors.append(f"{name}: {error}")

    raise RuntimeError("All providers failed: " + " | ".join(errors))


def generate_initial_script(title, duration, style):
    target = duration * WORDS_PER_MINUTE

    prompt = f"""
Write a complete YouTube narration script.

Title: {title}
Style: {style}
Target length: approximately {target} words.

Requirements:
- Strong opening hook
- Clear beginning, middle and ending
- Natural spoken English
- Keep the narration engaging and conversational without forcing questions throughout
- End with a clear, topic-specific final takeaway
- End with one natural, topic-related question inviting viewers to comment
- Follow it with a brief, natural request to like the video and subscribe
- Keep the ending unique to the topic, not a generic repeated CTA
- No headings, labels or bullet points
- No repeated sentences or ideas
- No visual directions
- Return narration only
"""

    providers = [
        ("gemini", call_gemini),
        ("ollama", call_ollama),
        ("together", call_together),
    ]

    script, provider = first_success(prompt, providers)
    return script, provider, target


def repair_with_ollama(script, target, validation):
    current = word_count(script)

    prompt = f"""
Improve the narration script below.

Required word count: approximately {target} words.
Current word count: {current}.
Validation result: {validation}.

Rules:
- Keep the original topic and important information
- Extend naturally when it is too short
- Condense naturally when it is too long
- Remove repeated sentences, phrases and ideas
- Keep a strong hook and complete ending
- Keep the narration engaging and conversational without forcing questions throughout
- End with a clear, topic-specific final takeaway
- End with one natural, topic-related question inviting viewers to comment
- Follow it with a brief, natural request to like the video and subscribe
- Keep the ending unique to the topic, not a generic repeated CTA
- No headings, labels or bullet points
- Return only the complete revised narration

SCRIPT:
{script}
"""

    providers = [
        ("ollama", call_ollama),
        ("gemini", call_gemini),
        ("together", call_together),
    ]

    return first_success(prompt, providers)


def create_validated_script(title, duration, style):
    script, provider, target = generate_initial_script(
        title, duration, style
    )

    repairs = []

    for _ in range(2):
        validation = validate_script(script, duration)

        if validation.get("valid"):
            break

        script, repair_provider = repair_with_ollama(
            script, target, validation
        )
        repairs.append(repair_provider)

    final_validation = validate_script(script, duration)

    return {
        "script": script,
        "target_words": target,
        "actual_words": final_validation["word_count"],
        "initial_provider": provider,
        "repair_providers": repairs,
        "validation": final_validation,
    }
