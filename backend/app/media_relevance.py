"""Deterministic topic anchoring for AI-generated scene media queries."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
import unicodedata

from app.video_profiles import normalize_profile_key

# Universal presentation wrappers. Editorial framing that surrounds a subject
# in almost any niche; the subject itself is what should anchor media search.
_LEADING_WRAPPERS = (
    r"who\s+(?:was|is|were|are)\s+",
    r"(?:the\s+)?(?:hidden\s+|secret\s+|untold\s+|true\s+|real\s+|dark\s+|forgotten\s+)?history\s+of\s+",
    r"(?:the\s+)?(?:untold\s+|true\s+|real\s+|secret\s+|dark\s+|incredible\s+)?(?:life|story)\s+of\s+",
    r"(?:the\s+)?(?:dark\s+|hidden\s+|untold\s+|shocking\s+|disturbing\s+|surprising\s+)?"
    r"(?:secrets?|truth|mystery|mysteries|science|physics|engineering)\s+(?:behind|of|about)\s+",
    r"(?:the\s+)?rise\s+and\s+fall\s+of\s+",
    r"(?:the\s+)?(?:fall|rise|collapse|birth|death|end|origin|origins|discovery|invention|making|building)\s+of\s+",
    r"what\s+(?:really\s+)?happened(?:\s+during|\s+to|\s+at|\s+in)?\s+",
    r"what\s+(?:really|actually)?\s*happens\s+(?:inside|in|when|if|to|at|during|during|after|before)?\s*",
    r"(?:a\s+)?(?:look|journey|trip|walk|step)\s+(?:inside|into|through|back\s+to)\s+",
    r"(?:deep\s+)?inside\s+",
    r"\d+\s+(?:\S+\s+){0,5}?(?:about|of|in|on|from)\s+",
    r"(?:everything|all)\s+(?:you\s+)?(?:need|should|must)\s+(?:to\s+)?know\s+about\s+",
    r"why\s+(?:you|we|people|everyone)\s+(?:should|must|need\s+to)\s+(?:care|know|worry)\s+about\s+",
)
_LEADING_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_TITLE_SPLIT = re.compile(r"\s*(?::|\s[-|\u2013\u2014]\s)\s*")
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
    # Per-scene: the scene's own query, so candidates found by a broadened
    # search variant are still judged against what the scene actually needs.
    scene_query: str = ""
    # Per-job: years named anywhere in the script's scene queries. Lets a
    # historical video down-rank present-day media even for undated scenes.
    era_years: tuple[str, ...] = ()

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


def _main_title_segment(topic: str) -> str:
    """Colon/dash titles usually carry the subject on the left ("Tesla Explained: ...")."""
    parts = [part.strip() for part in _TITLE_SPLIT.split(topic) if part.strip()]
    if len(parts) < 2:
        return topic
    for part in parts:
        if _meaningful_tokens(part):
            return part
    return parts[0]


def _strip_leading_wrappers(topic: str) -> str:
    for pattern in _LEADING_WRAPPERS:
        updated = re.sub(r"^" + pattern, "", topic, count=1, flags=re.IGNORECASE).strip()
        if updated and updated != topic:
            return updated
    return topic


def _primary_topic(title: str, profile: str) -> str:
    topic = unicodedata.normalize("NFKC", str(title or "")).strip()
    topic = topic.strip(" \t\r\n-:|?!.\u2013\u2014")
    topic = _main_title_segment(topic)
    match = _WHAT_IF_PREFIX.match(topic) or _QUESTION_PREFIX.match(topic)
    if match:
        topic = _trim_predicate(match.group("body"))
    else:
        topic = _strip_leading_wrappers(topic)
        # A wrapper may expose a question form ("The Truth About How X Works").
        match = _WHAT_IF_PREFIX.match(topic) or _QUESTION_PREFIX.match(topic)
        if match:
            topic = _trim_predicate(match.group("body"))
    topic = _LEADING_ARTICLE.sub("", topic).strip()
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
    """Soft anchor: the query names the title subject, in whole or by a subject noun."""
    normalized = _search_text(query)
    if not normalized:
        return False
    if not context.required_anchor_phrases:
        return True
    padded = f" {normalized} "
    if any(f" {phrase} " in padded for phrase in context.required_anchor_phrases):
        return True
    strong, _weak = _split_by_strength(
        token for token in media_text_tokens(context.primary_topic) if token not in _SCENE_GENERIC
    )
    query_forms = _token_forms(media_text_tokens(query))
    return any(forms & query_forms for forms in strong)


def query_is_concrete(query: str) -> bool:
    """A query naming at least one concrete word can stand on its own."""
    return bool(concrete_subject_words(query))


def validate_scene_query(query: str | None, context: MediaTopicContext) -> QueryValidation:
    """The scene query is the primary visual signal; the title is a soft anchor.

    A concrete scene query is left exactly as the model wrote it. Only an empty
    or purely generic query is replaced by the title subject, and even then by
    its short noun phrase rather than the whole title.
    """
    original = str(query or "").strip()
    if not context.primary_topic or query_is_concrete(original):
        return QueryValidation(original, original, False)
    concrete = concrete_subject_words(context.primary_topic)[:2]
    head = " ".join([*concrete[:-1], singular_form(concrete[-1])]) if concrete else context.primary_topic
    final = f"{original} {head}" if original else head
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
    # Non-photographable stand-ins a model may emit instead of a subject.
    "abstract", "component", "concept", "detail", "detailed", "element",
    "generic", "object", "thing", "things", "visual",
})


# Words that describe, frame, or locate a subject without identifying it.
# A metadata match on these alone is context, never proof of the right subject.
# Universal by construction: adjectives, editorial framing, generic settings,
# and nationality adjectives; no topic-specific vocabulary belongs here.
_WEAK_MODIFIERS = frozenset({
    # editorial / framing
    "amazing", "best", "biggest", "dark", "deadly", "dangerous", "facts", "famous",
    "forgotten", "greatest", "hidden", "incredible", "largest", "lost", "mysterious",
    "mystery", "mysteries", "real", "secret", "secrets", "shocking", "strange",
    "story", "surprising", "things", "top", "true", "truth", "untold", "weird", "worst",
    # time / age / size
    "ancient", "big", "early", "future", "giant", "great", "historic", "historical",
    "huge", "late", "little", "massive", "medieval", "modern", "new", "old", "past",
    "prehistoric", "small", "tiny", "young",
    # generic setting / medium
    "air", "city", "earth", "ground", "inside", "land", "nature", "natural", "ocean",
    "outside", "sea", "sky", "space", "underground", "underwater", "water", "world",
})
# Origin adjectives: useful search words ("roman empire"), but a metadata match
# on them alone does not prove the subject ("egyptian" desert fox).
_ORIGIN_MODIFIERS = frozenset({
    "african", "american", "arab", "asian", "australian", "british", "chinese",
    "egyptian", "english", "european", "french", "german", "greek", "indian",
    "italian", "japanese", "korean", "mexican", "persian", "roman", "russian",
    "spanish", "turkish", "viking",
})
_WEAK_EVIDENCE = _WEAK_MODIFIERS | _ORIGIN_MODIFIERS


# File-name and provider boilerplate that says nothing about the subject.
_FILE_NOISE = frozenset({
    "file", "jpg", "jpeg", "png", "gif", "webp", "tif", "tiff", "svg", "mp4", "webm",
    "commons", "wikimedia", "wikipedia", "img", "dsc", "stock", "video", "clip",
    "free", "royalty", "hd", "4k", "footage", "background", "wallpaper", "closeup",
    "www", "http", "https", "com", "org", "pexels", "pixabay", "coverr", "openverse",
})


# Small, profile-scoped domain vocabularies. A candidate whose title also
# names the video's domain gets a modest tie-breaking bonus, which is what
# separates "Planet Mars from orbit" from "Mars chocolate bar". These are
# domain words, never title-specific words.
_PROFILE_DOMAIN_HINTS = {
    "historical_place": {"ruins", "monument", "temple", "castle", "archaeological", "site", "museum", "heritage", "excavation"},
    "historical_event": {"museum", "ruins", "artifact", "artifacts", "archaeological", "painting", "engraving", "map", "manuscript", "reenactment", "monument"},
    "general_explainer": set(),
    "environment_climate": {"climate", "emissions", "renewable", "energy", "forest", "glacier", "pollution", "conservation", "wildlife"},
    "educational_how_it_works": {"mechanism", "machine", "engine", "gear", "cable", "motor", "circuit", "diagram", "cutaway", "component"},
    "sports": {"race", "stadium", "athlete", "team", "match", "championship", "track", "training", "player", "competition"},
    "tech_science": {"laboratory", "lab", "scientist", "research", "processor", "chip", "microscope", "experiment", "engineering", "prototype"},
    "automotive_transport": {"vehicle", "engine", "navy", "fleet", "highway", "railway", "aircraft", "vessel", "cockpit", "harbor", "ocean"},
    "culture_society": {"ceremony", "tradition", "festival", "ritual", "community", "costume", "temple", "village", "dance"},
    "health_wellness": {"patient", "doctor", "medical", "clinic", "hospital", "nurse", "glucose", "blood", "therapy", "diagnosis"},
    "nature_wildlife": {"wildlife", "habitat", "predator", "prey", "species", "forest", "wild", "hunting", "migration", "pack"},
    "what_if": {"planet", "earth", "simulation", "illustration", "concept", "orbit", "space"},
    "food_cooking": {"kitchen", "chef", "baking", "oven", "recipe", "ingredients", "flour", "cooking", "bakery"},
    "business_economics": {"company", "factory", "store", "market", "headquarters", "office", "stock", "brand", "product", "investors"},
    "geography_travel": {"landscape", "travel", "tourism", "coast", "mountain", "village", "island", "aerial", "capital"},
    "architecture_engineering": {"construction", "structure", "steel", "concrete", "architect", "skyline", "engineering", "span", "cables", "girder"},
    "space_astronomy": {"planet", "orbit", "galaxy", "nasa", "telescope", "astronaut", "spacecraft", "nebula", "solar", "rover", "surface"},
}


def _split_by_strength(tokens):
    """Return (strong, weak) as tuples of per-word form sets.

    Each word keeps its own spelling variants so one noun counts once however
    it is pluralised. A subject with no strong word keeps all its words as strong.
    """
    words = tuple(dict.fromkeys(tokens))
    strong = tuple(_token_forms((word,)) for word in words if word not in _WEAK_EVIDENCE)
    weak = tuple(_token_forms((word,)) for word in words if word in _WEAK_EVIDENCE)
    if not strong:
        return weak, ()
    return strong, weak


def _count_word_matches(word_forms, metadata_tokens, exclude=frozenset()):
    return sum(
        1 for forms in word_forms
        if not (forms & exclude) and forms & metadata_tokens
    )


def _token_forms(tokens):
    forms = set(tokens)
    for token in tuple(forms):
        if len(token) > 4 and token.endswith("ies"):
            forms.add(token[:-3] + "y")
        elif len(token) > 4 and token.endswith("ves"):
            forms.add(token[:-3] + "f")
            forms.add(token[:-3] + "fe")
        elif len(token) > 4 and token.endswith("es"):
            forms.add(token[:-2])
            forms.add(token[:-1])
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
    if context.scene_query:
        scene_query = context.scene_query
    query_years = set(re.findall(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", normalize_media_text(scene_query)))
    metadata_years = set(re.findall(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", metadata))
    if query_years and metadata_years and query_years.isdisjoint(metadata_years):
        return MediaCandidateRelevance(False, 0, "conflicting-named-date", False, 0)
    era_penalty = 0
    if not query_years and metadata_years and context.era_years:
        era = {int(year) for year in context.era_years}
        if all(abs(int(year) - nearest) > 15 for year in metadata_years for nearest in era):
            # Present-day anniversary/memorial photos for a 1960s-1980s story.
            era_penalty = 30
    subject_strong, subject_weak = _split_by_strength(
        token for token in media_text_tokens(context.primary_topic)
        if token not in _SCENE_GENERIC
    )
    subject_tokens = set().union(*subject_strong, *subject_weak) if (subject_strong or subject_weak) else set()
    query_strong, query_weak = _split_by_strength(
        token for token in media_text_tokens(scene_query)
        if token not in _SCENE_GENERIC
    )
    strong_matches = _count_word_matches(subject_strong, metadata_tokens)
    weak_matches = _count_word_matches(subject_weak, metadata_tokens)
    context_strong = _count_word_matches(query_strong, metadata_tokens, exclude=subject_tokens)
    context_weak = _count_word_matches(query_weak, metadata_tokens, exclude=subject_tokens)
    context_matches = context_strong + context_weak
    # Only a concrete subject noun proves the subject; "egyptian" or "ancient"
    # alone merely suggests the setting.
    entity_match = bool(strong_matches)
    subject_weight = 30
    if strong_matches == 1 and len(subject_strong) >= 2:
        # One noun of a multi-noun subject ("black" of "black hole") is real but
        # partial evidence; rank it under candidates naming the whole subject.
        subject_weight = 18
    score = strong_matches * subject_weight + weak_matches * 8 + context_strong * 6 + context_weak * 3
    padded = f" {metadata} "
    if any(
        f" {phrase} " in padded
        for phrase in context.required_anchor_phrases
        if phrase and " " in phrase
    ):
        score += 20

    if not metadata_tokens:
        return MediaCandidateRelevance(True, 5, "provider-result-sparse-metadata", False, 0)

    # The title names what the asset is; description and tags only surround it.
    title_tokens = _token_forms(
        token for token in media_text_tokens(title) if token not in _SCENE_GENERIC
    )
    if title_tokens:
        score += 6 * _count_word_matches(subject_strong, title_tokens)
        # Title words naming something outside both subject and scene are mild
        # evidence the asset is about something else ("desert fox" for mummies).
        known = subject_tokens | set().union(*query_strong, *query_weak) if (query_strong or query_weak) else subject_tokens
        foreign = [
            token for token in media_text_tokens(title)
            if token not in _SCENE_GENERIC and token not in _WEAK_EVIDENCE
            and token not in _PROVIDER_VISUAL_WORDS and token not in _FILE_NOISE
            and not token.isdigit() and not (_token_forms((token,)) & known)
        ]
        score -= min(len(foreign), 3) * 2
        hints = _PROFILE_DOMAIN_HINTS.get(context.profile, ())
        if hints:
            hint_forms = _token_forms(hints)
            score += min(len(title_tokens & hint_forms), 2) * 3
    score -= era_penalty
    if entity_match:
        reason = "subject-and-context" if context_matches else "main-subject-b-roll"
        return MediaCandidateRelevance(True, score, reason, True, context_matches)
    if context_strong:
        return MediaCandidateRelevance(True, max(score, 12), "contextual-b-roll", False, context_matches)
    if weak_matches or context_weak:
        # Setting-only overlap: keep as a last resort, ranked below real matches.
        return MediaCandidateRelevance(True, max(score, 6), "setting-only-b-roll", False, context_matches)

    # Search-provider inclusion is useful evidence, but populated metadata naming
    # no part of either the main subject or scene is strong evidence it is wrong.
    return MediaCandidateRelevance(False, 0, "clearly-unrelated-metadata", False, 0)

_PROVIDER_VISUAL_WORDS = _FILLER | frozenset({
    "archival", "archive", "black", "early", "famous", "historic", "historical",
    "image", "late", "old", "older", "photo", "photograph", "photography",
    "picture", "portrait", "white",
})


def singular_form(token: str) -> str:
    """Conservative singular form for search variants ("mummies" -> "mummy")."""
    token = str(token or "")
    if len(token) <= 3 or token.endswith(("ss", "us", "is", "ous")):
        return token
    if token.endswith("ies"):
        return token[:-3] + "y"
    if token.endswith("ves"):
        return token[:-3] + "f"
    if token.endswith(("ses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if token.endswith("s"):
        return token[:-1]
    return token


def concrete_subject_words(value: str) -> list[str]:
    """Words most likely to name the visible subject, in original order."""
    return [
        token for token in media_text_tokens(value)
        if token not in _SCENE_GENERIC and token not in _WEAK_MODIFIERS and not token.isdigit()
    ]


def practical_head_query(query: str, context: MediaTopicContext | None = None) -> str:
    """Short, searchable core of a scene query: the subject noun phrase itself.

    Prefers the title subject's own noun phrase (up to two words) when the scene
    query contains it, so "black hole" stays "black hole" rather than "hole".
    """
    query_words = concrete_subject_words(query)
    query_forms = _token_forms(query_words)
    topic_words = concrete_subject_words(getattr(context, "primary_topic", "") or "")
    for size in (2, 1):
        phrases = [topic_words[start:start + size] for start in range(0, max(len(topic_words) - size + 1, 0))]
        # Prefer pure noun phrases ("tea ceremony") over origin-led ones ("japanese tea").
        phrases.sort(key=lambda phrase: any(word in _ORIGIN_MODIFIERS for word in phrase))
        for phrase in phrases:
            if phrase[-1] in _ORIGIN_MODIFIERS:
                continue
            if all(_token_forms((word,)) & query_forms for word in phrase):
                # "egyptian mummies" -> "mummy": the origin word is a modifier.
                nouns = [word for word in phrase if word not in _ORIGIN_MODIFIERS]
                return " ".join([*nouns[:-1], singular_form(nouns[-1])])
    nouns = [word for word in query_words if word not in _ORIGIN_MODIFIERS] or query_words
    return singular_form(nouns[0]) if nouns else ""


def scene_context(context: MediaTopicContext | None, scene_query: str) -> MediaTopicContext | None:
    """Per-scene copy of the job context carrying the scene's own query."""
    if context is None:
        return None
    return replace(context, scene_query=str(scene_query or "").strip())


def with_era_years(context: MediaTopicContext | None, queries) -> MediaTopicContext | None:
    """Attach years named across the script so undated scenes keep their era."""
    if context is None:
        return context
    years = set()
    for query in queries:
        years.update(re.findall(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", normalize_media_text(str(query or ""))))
    return replace(context, era_years=tuple(sorted(years)))


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
