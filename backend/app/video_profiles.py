from __future__ import annotations


VIDEO_PROFILES = {
    "auto": {
        "label": "Auto Detect",
        "description": (
            "Automatically choose the most suitable production profile."
        ),
        "script_instructions": "",
        "media_instructions": "",
        "historical": False,
    },

    "general_explainer": {
        "label": "General Explainer",
        "description": (
            "Clear explanations for broad informational topics."
        ),
        "script_instructions": (
            "Explain the subject clearly for a general audience. "
            "Build ideas in a logical order, explain causes and effects, "
            "use useful examples, avoid filler, and keep the narration "
            "engaging without exaggerating facts."
        ),
        "media_instructions": (
            "Use concrete visual concepts directly connected to each "
            "scene. Prefer specific objects, locations, actions and "
            "subjects over generic cinematic filler."
        ),
        "historical": False,
    },

    "tech_science": {
        "label": "Tech & Science",
        "description": (
            "Technology, engineering, computing, science and innovation."
        ),
        "script_instructions": (
            "Explain technical or scientific ideas accurately and in "
            "accessible language. Describe mechanisms, components, "
            "processes and cause-and-effect relationships. Distinguish "
            "established science from predictions or uncertainty."
        ),
        "media_instructions": (
            "Search for the exact technology, device, scientific object, "
            "experiment, component or phenomenon discussed in the scene. "
            "Avoid generic computers or laboratory footage when a more "
            "specific subject is available."
        ),
        "historical": False,
    },

    "what_if": {
        "label": "What-If / Fictional",
        "description": (
            "Hypothetical scenarios grounded in real-world mechanisms."
        ),
        "script_instructions": (
            "Clearly separate the factual starting conditions from "
            "hypothetical consequences. Keep mechanisms and timescales "
            "plausible. Do not present speculation as established fact "
            "and avoid unsupported chains of dramatic consequences."
        ),
        "media_instructions": (
            "Use real-world visual subjects that represent the factual "
            "basis of the hypothetical scenario. Use AI-oriented prompts "
            "for impossible or fictional visuals rather than unrelated "
            "stock footage."
        ),
        "historical": False,
    },

    "historical_person": {
        "label": "Historical Person / Biography",
        "description": (
            "Biographies of historical figures and notable people."
        ),
        "script_instructions": (
            "Build the biography around the actual person, chronology, "
            "major locations, documented achievements, conflicts and "
            "historical context. Keep names, relationships and chronology "
            "consistent. Avoid invented quotes or unsupported personal "
            "details."
        ),
        "media_instructions": (
            "Prioritize the exact historical person by full name. "
            "Search for portraits, photographs, paintings, documents, "
            "associated locations, institutions, events and period "
            "material. Keep proper names in search queries. Never replace "
            "a named historical person with generic modern people."
        ),
        "historical": True,
    },

    "historical_place": {
        "label": "Historical Place / Monument",
        "description": (
            "Historic buildings, monuments, archaeological sites and landmarks."
        ),
        "script_instructions": (
            "Focus on the exact place, its location, construction or "
            "historical period, architecture, important features, major "
            "events and historical significance. Keep similarly named "
            "places clearly distinguished."
        ),
        "media_instructions": (
            "Prioritize the exact monument or place name. Include its "
            "city, region or country when useful. Search for specific "
            "architectural features, courtyards, interiors, exterior "
            "views, historical photographs, maps and period material. "
            "Avoid substituting unrelated buildings of the same type."
        ),
        "historical": True,
    },

    "historical_event": {
        "label": "Historical Event / Era",
        "description": (
            "Wars, movements, civilizations, incidents and historical eras."
        ),
        "script_instructions": (
            "Present events in a clear chronology with the actual people, "
            "groups, locations, causes, developments and consequences. "
            "Distinguish evidence from disputed interpretations and avoid "
            "invented precision."
        ),
        "media_instructions": (
            "Prioritize the exact event, era, named participants, "
            "locations, maps, documents and period imagery. Include year "
            "or historical period in searches when it improves relevance. "
            "Avoid generic modern conflict or crowd footage."
        ),
        "historical": True,
    },

    "geography_travel": {
        "label": "Geography / Travel Documentary",
        "description": (
            "Countries, cities, landscapes, cultures and geographic topics."
        ),
        "script_instructions": (
            "Describe geography, locations, landscapes, culture and "
            "important landmarks accurately. Keep place names and regional "
            "relationships precise and avoid travel clichés."
        ),
        "media_instructions": (
            "Put the exact country, city, region, landmark or landscape "
            "name first in searches. Prefer recognizable location-specific "
            "visuals rather than generic travel footage."
        ),
        "historical": False,
    },

    "nature_wildlife": {
        "label": "Nature / Wildlife",
        "description": (
            "Animals, ecosystems, habitats and natural phenomena."
        ),
        "script_instructions": (
            "Use accurate species, habitat and ecological information. "
            "Explain behavior and natural processes without humanizing "
            "animals or exaggerating uncertain claims."
        ),
        "media_instructions": (
            "Search using the exact species, habitat, ecosystem or natural "
            "phenomenon. Prefer correct animals and environments over "
            "generic wildlife footage."
        ),
        "historical": False,
    },

    "space_astronomy": {
        "label": "Space / Astronomy",
        "description": (
            "Planets, stars, missions, cosmology and space exploration."
        ),
        "script_instructions": (
            "Explain astronomy and space science accurately. Clearly "
            "separate observations, accepted models, estimates and "
            "speculation. Keep scale and timescales scientifically plausible."
        ),
        "media_instructions": (
            "Search for the exact planet, moon, galaxy, telescope, "
            "spacecraft, mission or astronomical phenomenon. Prefer "
            "official or subject-specific imagery over generic stars."
        ),
        "historical": False,
    },

    "business_economics": {
        "label": "Business / Economics",
        "description": (
            "Companies, industries, markets, finance and economic systems."
        ),
        "script_instructions": (
            "Explain business and economic mechanisms clearly. Separate "
            "historical facts from current conditions and predictions. "
            "Avoid inventing financial figures, market data or certainty "
            "about future performance."
        ),
        "media_instructions": (
            "Use the exact company, industry, product, economic activity "
            "or institution when relevant. Avoid meaningless office and "
            "money footage when a specific visual subject exists."
        ),
        "historical": False,
    },

    "educational_how_it_works": {
        "label": "Educational / How It Works",
        "description": (
            "Step-by-step explanations of systems, processes and mechanisms."
        ),
        "script_instructions": (
            "Teach the topic progressively from fundamentals to the full "
            "process. Explain what each important component does and how "
            "the steps connect. Avoid skipping mechanisms that are needed "
            "to understand the result."
        ),
        "media_instructions": (
            "Search for the exact component, mechanism, process step, "
            "diagram subject or physical action being explained. Visuals "
            "should help teach the specific step rather than merely decorate it."
        ),
        "historical": False,
    },
}


VIDEO_PROFILE_KEYS = frozenset(VIDEO_PROFILES)

MANUAL_PROFILE_KEYS = tuple(
    key
    for key in VIDEO_PROFILES
    if key != "auto"
)


def normalize_profile_key(value: str | None) -> str:
    key = (value or "auto").strip().lower()
    return key if key in VIDEO_PROFILE_KEYS else "auto"


def get_profile(value: str | None) -> dict:
    key = normalize_profile_key(value)

    if key == "auto":
        key = "general_explainer"

    return VIDEO_PROFILES[key]


def profile_label(value: str | None) -> str:
    key = normalize_profile_key(value)
    return VIDEO_PROFILES[key]["label"]


def script_profile_instructions(value: str | None) -> str:
    return get_profile(value)["script_instructions"]


def media_profile_instructions(value: str | None) -> str:
    return get_profile(value)["media_instructions"]


def is_historical_profile(value: str | None) -> bool:
    return bool(get_profile(value)["historical"])


def profile_detection_prompt(title: str) -> str:
    choices = "\n".join(
        f"- {key}: {VIDEO_PROFILES[key]['description']}"
        for key in MANUAL_PROFILE_KEYS
    )

    return f"""Choose the single best video production profile for this title.

Title: {title}

Available profiles:
{choices}

Classification rules:
- Base the decision on the main subject and the visuals required.
- A named historical person normally uses historical_person.
- A historic building, monument or archaeological site normally uses historical_place.
- A war, revolution, dynasty, civilization, incident or historical era normally uses historical_event.
- Countries, cities and modern travel topics normally use geography_travel.
- Hypothetical or "what if" topics normally use what_if.
- Scientific and technology topics use tech_science.
- Animals and ecosystems use nature_wildlife.
- Space topics use space_astronomy.
- Companies, markets, finance and economics use business_economics.
- Process explanations use educational_how_it_works.
- Use general_explainer only when no more specific profile clearly fits.

Return exactly one profile key and nothing else.
"""
