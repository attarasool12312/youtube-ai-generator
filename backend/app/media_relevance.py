"""Deterministic topic anchoring for AI-generated scene media queries."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from app.video_profiles import normalize_profile_key

_LEADING_WRAPPERS = (
    r"who\s+(?:was|is)\s+", r"the\s+(?:hidden\s+)?history\s+of\s+",
    r"the\s+(?:life|story)\s+of\s+",
    r"what\s+(?:really\s+)?happened(?:\s+during|\s+to|\s+at)?\s+",
)
_TRAILING_WRAPPERS = (
    r"\s*[-:|]?\s*explained\s*[?!.]*$",
    r"\s*[-:|]?\s*how\s+it\s+works\s*[?!.]*$",
)
_QUESTION_PREFIX = re.compile(
    r"^(?:how|why)\s+(?:(?:does|do|did|is|are|was|were)\s+)?(?P<body>.+)$",
    re.IGNORECASE,
)
_WHAT_IF_PREFIX = re.compile(r"^what\s+if\s+(?P<body>.+)$", re.IGNORECASE)
# Conservative grammatical boundaries. Unknown constructions retain extra words.
_PREDICATES = frozenset({
    "become", "becomes", "became", "build", "builds", "built", "called", "carry", "carries",
    "change", "changes", "changed", "create", "creates", "created", "disappear",
    "disappears", "disappeared", "distort", "distorts", "doubled", "fail",
    "fails", "failed", "form", "forms", "formed", "happen", "happens",
    "happened", "have", "has", "had", "named", "organize", "organizes",
    "recover", "recovers", "remove", "removes", "removed", "so", "vanish", "vanishes", "vanished", "work",
    "works", "worked",
})
_PROFILE_SUFFIXES = {
    "nature_wildlife": frozenset({
        "behavior", "bleaching", "colony", "colonies", "conservation", "ecosystem", "ecosystems",
        "habitat", "migration",
    }),
}
_FILLER = frozenset({
    "a", "an", "and", "are", "at", "be", "by", "did", "do", "does",
    "during", "for", "from", "how", "in", "is", "it", "of", "on", "or",
    "the", "this", "to", "was", "were", "what", "when", "where", "which",
    "who", "why", "with", "works", "explained", "really", "hidden", "history",
    "happened",
})


@dataclass(frozen=True)
class MediaTopicContext:
    original_title: str
    profile: str
    primary_topic: str
    required_anchor_phrases: tuple[str, ...]
    optional_context_tokens: tuple[str, ...]
    anchor_kind: str = "topic"

    @property
    def anchor_phrases(self) -> tuple[str, ...]:
        return self.required_anchor_phrases

    @property
    def anchor_tokens(self) -> tuple[str, ...]:
        required = (
            token for phrase in self.required_anchor_phrases for token in phrase.split()
        )
        return tuple(dict.fromkeys((*required, *self.optional_context_tokens)))


@dataclass(frozen=True)
class QueryValidation:
    original_query: str
    final_query: str
    repaired: bool


def _search_text(value: str) -> str:
    """Normalize Unicode, punctuation, hyphens and possessives for matching."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"(?<=\w)[\u2019']s\b", "", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _meaningful_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token for token in _search_text(value).split()
        if len(token) > 1 and token not in _FILLER
    )


def _trim_predicate(body: str) -> str:
    words = body.split()
    for index, word in enumerate(words[1:], start=1):
        if _search_text(word) in _PREDICATES:
            return " ".join(words[:index])
    return body


def _primary_topic(title: str, profile: str) -> str:
    topic = unicodedata.normalize("NFKC", str(title or "")).strip()
    topic = topic.strip(" \t\r\n-:|?!.\u2013\u2014")
    match = _WHAT_IF_PREFIX.match(topic) or _QUESTION_PREFIX.match(topic)
    if match:
        topic = _trim_predicate(match.group("body"))
    else:
        for pattern in _LEADING_WRAPPERS:
            updated = re.sub(pattern, "", topic, count=1, flags=re.IGNORECASE)
            if updated != topic:
                topic = updated.strip()
                break
    for pattern in _TRAILING_WRAPPERS:
        topic = re.sub(pattern, "", topic, count=1, flags=re.IGNORECASE).strip()
    if profile == "nature_wildlife":
        topic = re.split(r"\s+of\s+", topic, maxsplit=1, flags=re.IGNORECASE)[0]
    suffixes = _PROFILE_SUFFIXES.get(profile, ())
    words = topic.split()
    while len(words) > 1 and _search_text(words[-1]) in suffixes:
        words.pop()
    topic = " ".join(words)
    topic = topic.strip(" \t\r\n-:|?!.\u2013\u2014")
    return topic or str(title or "").strip()


def build_media_topic_context(title: str, profile: str) -> MediaTopicContext:
    normalized_profile = normalize_profile_key(profile)
    primary = _primary_topic(title, normalized_profile)
    required = re.sub(r"^(?:the|a|an) ", "", _search_text(primary))
    required_tokens = set(required.split())
    optional = tuple(
        token for token in dict.fromkeys(_meaningful_tokens(title))
        if token not in required_tokens
    )
    anchor_kind = "topic"
    if normalized_profile == "nature_wildlife":
        nature_terms = set(required.split())
        ecosystems = {"desert", "ecosystem", "forest", "habitat", "mangrove", "ocean", "reef", "river", "wetland"}
        phenomena = {"aurora", "avalanche", "bioluminescence", "earthquake", "eruption", "hurricane", "lightning", "tornado", "volcano", "volcanoes"}
        anchor_kind = "ecosystem" if nature_terms & ecosystems else "phenomenon" if nature_terms & phenomena else "organism"
    elif normalized_profile == "educational_how_it_works":
        anchor_kind = "system"
    return MediaTopicContext(
        original_title=str(title or "").strip(), profile=normalized_profile,
        primary_topic=primary,
        required_anchor_phrases=(required,) if required else (),
        optional_context_tokens=optional,
        anchor_kind=anchor_kind,
    )


def query_has_topic_anchor(query: str, context: MediaTopicContext) -> bool:
    normalized = _search_text(query)
    if not normalized:
        return False
    if not context.required_anchor_phrases:
        return True
    padded = f" {normalized} "
    return any(f" {phrase} " in padded for phrase in context.required_anchor_phrases)


def validate_scene_query(query: str | None, context: MediaTopicContext) -> QueryValidation:
    original = str(query or "").strip()
    if not context.primary_topic or query_has_topic_anchor(original, context):
        return QueryValidation(original, original, False)
    final = f"{context.primary_topic} {original}" if original else context.primary_topic
    return QueryValidation(original, " ".join(final.split()), True)

# Public provider-facing normalization keeps entity matching consistent across
# licensed media adapters without exposing display-query mutation.
def normalize_media_text(value: str) -> str:
    return _search_text(value)


def media_text_tokens(value: str) -> tuple[str, ...]:
    return tuple(token for token in normalize_media_text(value).split() if len(token) > 1)

@dataclass(frozen=True)
class MediaCandidateRelevance:
    accepted: bool
    score: int
    reason: str
    entity_match: bool
    context_matches: int


_SCENE_GENERIC = _FILLER | frozenset({
    "archival", "close", "diagram", "footage", "historical", "image", "photo",
    "photograph", "portrait", "scene", "schematic", "view", "wide",
})


def _token_forms(tokens):
    forms = set(tokens)
    for token in tuple(forms):
        if len(token) > 4 and token.endswith("ies"):
            forms.add(token[:-3] + "y")
        elif len(token) > 4 and token.endswith("es"):
            forms.add(token[:-2])
        elif len(token) > 3 and token.endswith("s"):
            forms.add(token[:-1])
    return forms


def score_media_candidate(
    context: MediaTopicContext,
    scene_query: str,
    *,
    title: str = "",
    description: str = "",
    tags=(),
    categories=(),
) -> MediaCandidateRelevance:
    """Rank like a stock editor; reject only clear evidence of a wrong subject."""
    tag_text = " ".join(
        str(item.get("name") or "") if isinstance(item, dict) else str(item)
        for item in (tags or ())
    )
    category_text = " ".join(
        str(item.get("title") or "") if isinstance(item, dict) else str(item)
        for item in (categories or ())
    )
    metadata = normalize_media_text(" ".join((title, description, tag_text, category_text)))
    metadata_tokens = _token_forms(metadata.split())
    query_years = set(re.findall(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", normalize_media_text(scene_query)))
    metadata_years = set(re.findall(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", metadata))
    if query_years and metadata_years and query_years.isdisjoint(metadata_years):
        return MediaCandidateRelevance(False, 0, "conflicting-named-date", False, 0)
    subject_tokens = _token_forms(
        token for token in media_text_tokens(context.primary_topic)
        if token not in _SCENE_GENERIC
    )
    query_tokens = _token_forms(
        token for token in media_text_tokens(scene_query)
        if token not in _SCENE_GENERIC
    )
    subject_matches = len(subject_tokens & metadata_tokens)
    context_matches = len((query_tokens - subject_tokens) & metadata_tokens)
    entity_match = bool(subject_matches)
    score = subject_matches * 30 + context_matches * 6

    if not metadata_tokens:
        return MediaCandidateRelevance(True, 5, "provider-result-sparse-metadata", False, 0)
    if entity_match:
        reason = "subject-and-context" if context_matches else "main-subject-b-roll"
        return MediaCandidateRelevance(True, score, reason, True, context_matches)
    if context_matches:
        return MediaCandidateRelevance(True, max(score, 12), "contextual-b-roll", False, context_matches)

    # Search-provider inclusion is useful evidence, but populated metadata naming
    # no part of either the main subject or scene is strong evidence it is wrong.
    return MediaCandidateRelevance(False, 0, "clearly-unrelated-metadata", False, 0)

_PROVIDER_VISUAL_WORDS = _FILLER | frozenset({
    "archival", "archive", "black", "early", "famous", "historic", "historical",
    "image", "late", "old", "older", "photo", "photograph", "photography",
    "picture", "portrait", "white",
})


def build_media_query_candidates(
    query: str,
    context: MediaTopicContext | None,
    limit: int = 4,
) -> list[str]:
    """Build bounded variants while retaining the required primary subject."""
    variants: list[str] = []

    def append(value: str) -> None:
        value = " ".join(str(value or "").split()).strip()
        normalized = normalize_media_text(value)
        if value and normalized and all(
            normalize_media_text(item) != normalized for item in variants
        ):
            variants.append(value)

    full = " ".join(str(query or "").split()).strip()
    append(full)
    primary = " ".join(str(getattr(context, "primary_topic", "") or "").split())
    if not primary:
        return variants[:limit]
    full_tokens = list(media_text_tokens(full))
    primary_tokens = list(media_text_tokens(primary))
    if full_tokens[:len(primary_tokens)] == primary_tokens:
        full_tokens = full_tokens[len(primary_tokens):]
    useful_words = [token for token in full_tokens if token not in _PROVIDER_VISUAL_WORDS]
    useful_context = " ".join(useful_words[:4])
    append(f"{primary} {useful_context}")
    append(primary)
    optional = list(getattr(context, "optional_context_tokens", ()) or ())
    broad = next((token for token in optional if token not in _PROVIDER_VISUAL_WORDS), "")
    if broad:
        append(f"{primary} {broad}")
    return variants[:limit]
