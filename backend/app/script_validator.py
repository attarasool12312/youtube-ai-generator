import math
import re
from collections import Counter
from difflib import SequenceMatcher


WORD_COUNT_OVERFLOW_TOLERANCE = 20


def get_word_count_limits(duration_minutes):
    target = int(duration_minutes * 145)

    minimum_words = math.ceil(
        target * 0.93
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
    cta_counts = Counter(
        intent
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip())
        for intent in classify_cta_intents(sentence)
    )
    duplicated_cta = sorted(intent for intent, count in cta_counts.items() if count > 1)
    if duplicated_cta:
        issues.append("Repeated CTA intents: " + ", ".join(duplicated_cta) + ".")
    script_sentences = [
        part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()
    ]
    final_window_start = max(0, len(script_sentences) - 3)
    if any(
        classify_cta_intents(sentence)
        for sentence in script_sentences[:final_window_start]
    ):
        issues.append("CTA request appears outside the final section.")

    if not minimum_words <= word_count <= maximum_words:
        issues.append(
            f"Word count {word_count}; accepted range "
            f"{minimum_words}-{maximum_words} around "
            f"target {target}."
        )
    if re.search(r"(?i)\bword\s*count\s*:", text):
        issues.append(
            "Script contains word-count metadata."
        )

    if len(duplicates) >= 2:
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
    ending_intents = set().union(*(
        classify_cta_intents(sentence)
        for sentence in re.split(r"(?<=[.!?])\s+", ending)
    ))
    if "COMMENT" not in ending_intents:
        issues.append(
            "Ending is missing a natural invitation to comment."
        )
    if "LIKE" not in ending_intents:
        issues.append("Ending is missing a natural like CTA.")
    if "SUBSCRIBE" not in ending_intents:
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

_CTA_REQUEST_CUES = re.compile(
    r"(?i)(?:^|\b)(?:please|remember to|don['’]?t forget to|if you (?:enjoyed|liked|found)|"
    r"help (?:the|this|our) channel|support (?:the|this|our) channel)\b"
)


def classify_cta_intents(sentence):
    """Return request intents only; descriptive uses are deliberately ignored."""
    text = " ".join(str(sentence or "").split()).casefold()
    intents = set()
    request_cue = bool(_CTA_REQUEST_CUES.search(text))
    if re.search(r"\b(?:give (?:it|this|the video) a like|hit the like button|like (?:it|this|the video))\b", text) or (
        request_cue and re.search(r"\blike\b", text)
    ):
        intents.add("LIKE")
    if re.search(r"^(?:subscribe|follow)\b", text) or re.search(
        r"\b(?:subscribe for|follow (?:for|this|the|our))\b", text
    ) or (request_cue and re.search(r"\b(?:subscribe|follow)\b", text)):
        intents.add("SUBSCRIBE")
    if re.search(
        r"\b(?:comment below|in the comments|leave (?:a )?comment|share your (?:answer|thoughts)|"
        r"tell us|let us know|drop your answer)\b", text
    ):
        intents.add("COMMENT")
    if re.search(r"\b(?:support (?:the|this|our) channel|help (?:the|this|our) channel)\b", text):
        intents.add("SUPPORT_CHANNEL")
    return frozenset(intents)


def deduplicate_cta_intents(text):
    """Keep a concise set-cover of CTA intents and place it at the ending."""
    sentences = [
        part.strip() for part in re.split(r"(?<=[.!?])\s+", str(text or "").strip())
        if part.strip()
    ]
    cta = []
    narration = []
    for index, sentence in enumerate(sentences):
        intents = classify_cta_intents(sentence)
        if intents:
            cta.append((index, sentence, intents))
        else:
            narration.append(sentence)
    if not cta:
        return " ".join(sentences)
    selected = []
    covered = set()
    ranked = sorted(
        cta,
        key=lambda item: (-len(item[2]), len(get_words(item[1])), item[0]),
    )
    for item in ranked:
        if item[2] - covered and not item[2] & covered:
            selected.append(item)
            covered.update(item[2])
    selected.sort(key=lambda item: item[0])
    return " ".join([*narration, *(item[1] for item in selected)]).strip()
