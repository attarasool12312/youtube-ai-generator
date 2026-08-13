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

    "health_wellness": {
        "label": "Health & Wellness", "description": "Health, sleep, fitness, nutrition and wellbeing topics.",
        "script_instructions": "Explain health and wellbeing evidence clearly, distinguish general education from medical advice, and avoid overstating benefits, risks or certainty.",
        "media_instructions": "Prefer the relevant body system, healthy activity, care setting or everyday behavior.", "historical": False,
    },
    "sports": {
        "label": "Sports", "description": "Sports, athletes, tactics, training and competition.",
        "script_instructions": "Explain rules, technique, tactics and performance clearly, using accurate sporting terms without assuming expert knowledge.",
        "media_instructions": "Prefer the correct sport, action, equipment, venue and useful training footage.", "historical": False,
    },
    "automotive_transport": {
        "label": "Automotive & Transport", "description": "Cars, electric vehicles, aviation, rail and transport systems.",
        "script_instructions": "Explain the vehicle or transport system, its components, operation and tradeoffs accurately, with practical examples and clear cause and effect.",
        "media_instructions": "Prefer the vehicle, transport mode, component or related movement discussed.", "historical": False,
    },
    "food_cooking": {
        "label": "Food & Cooking", "description": "Ingredients, cooking methods, fermentation and food culture.",
        "script_instructions": "Explain ingredients, technique and food science in a practical order. Keep safety, timing and cultural context accurate without inventing rigid rules.",
        "media_instructions": "Prefer the food, ingredient, preparation method, kitchen action or culinary context.", "historical": False,
    },
    "environment_climate": {
        "label": "Environment & Climate", "description": "Climate, ecosystems, energy, conservation and environmental change.",
        "script_instructions": "Explain environmental systems, evidence, impacts and solutions accurately. Separate observed facts from projections and represent uncertainty responsibly.",
        "media_instructions": "Prefer the relevant ecosystem, energy system, environmental process or human impact.", "historical": False,
    },
    "culture_society": {
        "label": "Culture & Society", "description": "Language, customs, communities, media and social behavior.",
        "script_instructions": "Explain cultural and social patterns with context and respectful language. Avoid stereotypes, sweeping claims and treating one community as universal.",
        "media_instructions": "Prefer authentic people, places, objects, practices and contextual community footage.", "historical": False,
    },
    "architecture_engineering": {
        "label": "Architecture & Engineering", "description": "Buildings, structures, construction and civil engineering.",
        "script_instructions": "Explain the structure, loads, materials, construction and design decisions clearly, connecting components to how the whole system performs.",
        "media_instructions": "Prefer the structure, construction process, material, component or engineering context.", "historical": False,
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
    return key if key in VIDEO_PROFILE_KEYS else "general_explainer"


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
- Biographies and other unsupported or ambiguous subjects use general_explainer.
- A historic building, monument or archaeological site normally uses historical_place.
- A war, revolution, dynasty, civilization, incident or historical era normally uses historical_event.
- Countries, cities and modern travel topics normally use geography_travel.
- Hypothetical or "what if" topics normally use what_if.
- Scientific and technology topics use tech_science.
- Animals and ecosystems use nature_wildlife.
- Space topics use space_astronomy.
- Companies, markets, finance and economics use business_economics.
- Process explanations use educational_how_it_works.
- Health and wellbeing topics use health_wellness.n- Sports rules, tactics and performance use sports.n- Vehicles and transport systems use automotive_transport.n- Cooking and food science use food_cooking.n- Climate, energy and conservation use environment_climate.n- Language, customs and social behavior use culture_society.n- Buildings and structural design use architecture_engineering.n- Use a specialized profile only when the title clearly fits it.n- Use general_explainer for ambiguous, niche, unknown or unsupported subjects.

Return exactly one profile key and nothing else.
"""
