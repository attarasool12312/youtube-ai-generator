import re
from collections import Counter
from difflib import SequenceMatcher


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
    target = int(duration_minutes * 145)
    word_count = len(get_words(text))
    duplicates = find_duplicate_sentences(text)
    phrase_ratio = repeated_phrase_ratio(text)
    issues = []

    if not target * 0.90 <= word_count <= target * 1.10:
        issues.append(
            f"Word count {word_count}; target approximately {target}."
        )
    if duplicates:
        issues.append(
            f"Found {len(duplicates)} repeated sentences."
        )
    if phrase_ratio > 0.025:
        issues.append(
            f"Repeated phrase ratio is {phrase_ratio:.1%}."
        )

    return {
        "valid": not issues,
        "issues": issues,
        "word_count": word_count,
        "target_words": target,
        "duplicate_sentences": duplicates,
        "repeated_phrase_ratio": round(phrase_ratio, 4),
    }
