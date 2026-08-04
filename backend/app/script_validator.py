import math
import re
from collections import Counter
from difflib import SequenceMatcher


WORD_COUNT_OVERFLOW_TOLERANCE = 20


def get_word_count_limits(duration_minutes):
    target = int(duration_minutes * 145)

    minimum_words = math.ceil(
        target * 0.90
    )

    maximum_words = (
        math.floor(target * 1.10)
        + WORD_COUNT_OVERFLOW_TOLERANCE
    )

    return target, minimum_words, maximum_words


def get_words(text):
    return re.findall(r"[A-Za-z0-9']+", text.lower())


def get_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [x.strip() for x in parts if len(get_words(x)) >= 6]


def find_duplicate_sentences(text):
    sentences = get_sentences(text)
    duplicates = []

    for index, current in enumerate(sentences):
        for earlier in sentences[:index]:
            score = SequenceMatcher(
                None, current.lower(), earlier.lower()
            ).ratio()
            if score >= 0.88:
                duplicates.append(current[:160])
                break

    return duplicates


def repeated_phrase_ratio(text, size=8):
    words = get_words(text)
    if len(words) < size:
        return 0.0

    phrases = [
        tuple(words[i:i + size])
        for i in range(len(words) - size + 1)
    ]
    counts = Counter(phrases)
    repeated = sum(count - 1 for count in counts.values())
    return repeated / max(1, len(phrases))


def validate_script(text, duration_minutes):
    (
        target,
        minimum_words,
        maximum_words,
    ) = get_word_count_limits(duration_minutes)

    word_count = len(get_words(text))
    duplicates = find_duplicate_sentences(text)
    phrase_ratio = repeated_phrase_ratio(text)
    ending = " ".join(text.strip().split()[-140:])
    ending_lower = ending.lower()
    issues = []

    if not minimum_words <= word_count <= maximum_words:
        issues.append(
            f"Word count {word_count}; accepted range "
            f"{minimum_words}-{maximum_words} around "
            f"target {target}."
        )
    if duplicates:
        issues.append(
            f"Found {len(duplicates)} repeated sentences."
        )
    if phrase_ratio > 0.025:
        issues.append(
            f"Repeated phrase ratio is {phrase_ratio:.1%}."
        )

    if "?" not in ending:
        issues.append(
            "Ending is missing a topic-related viewer question."
        )
    if not any(phrase in ending_lower for phrase in (
        "comment", "comments", "let us know", "let me know",
        "tell us", "share your thoughts", "leave your thoughts",
        "drop your answer",
    )):
        issues.append(
            "Ending is missing a natural invitation to comment."
        )
    if not re.search(r"\b(?:like|liked)\b", ending_lower):
        issues.append("Ending is missing a natural like CTA.")
    if not re.search(r"\bsubscrib(?:e|es|ed|ing)\b", ending_lower):
        issues.append("Ending is missing a natural subscribe CTA.")

    return {
        "valid": not issues,
        "issues": issues,
        "word_count": word_count,
        "target_words": target,
        "minimum_words": minimum_words,
        "maximum_words": maximum_words,
        "duplicate_sentences": duplicates,
        "repeated_phrase_ratio": round(phrase_ratio, 4),
    }
