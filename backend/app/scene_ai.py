import json

from app.ai_provider import (
    call_deepseek_metadata_flash,
    call_gemini,
    call_gemini_35_flash_lite,
    call_gemini_31_flash_lite,
)


def _visual_family(scene):
    text = " ".join([
        str(scene.get("visual_subject") or ""),
        str(scene.get("search_query") or ""),
        str(scene.get("zimage_prompt") or ""),
    ]).casefold()

    groups = {
        "document": (
            "receipt", "invoice", "billing statement",
            "account statement", "credit card statement",
            "loan statement", "ledger", "credit report",
            "brochure", "catalog",
        ),
        "payment_object": (
            "credit card", "debit card", "payment terminal",
            "point of sale", "cash register",
        ),
        "cash": (
            "currency stack", "paper currency", "dollar",
            "coins", "cash drawer", "money stack",
        ),
        "bank_exterior": (
            "bank building", "bank exterior", "bank facade",
            "bank vault",
        ),
        "editorial_graphic": (
            "diagram", "flow chart", "infographic",
            "chart on screen", "screen showing",
            "digital map", "world map", "globe",
            "title card", "interface",
        ),
    }

    for family, terms in groups.items():
        if any(term in text for term in terms):
            return family

    return ""


def _diversity_issues(data):
    scenes = list(data.get("scenes") or [])
    issues = []

    limits = {
        "document": 1,
        "payment_object": 2,
        "cash": 1,
        "bank_exterior": 1,
        "editorial_graphic": 0,
    }

    for index, scene in enumerate(scenes):
        start = max(0, index - 7)
        window = scenes[start:index + 1]

        family = _visual_family(scene)
        if family:
            count = sum(
                1 for item in window
                if _visual_family(item) == family
            )
            if count > limits[family]:
                issues.append(
                    f"scene {scene.get('scene_number')}: "
                    f"repeated visual family {family}"
                )

        subject = " ".join(
            str(scene.get("visual_subject") or "")
            .casefold().split()
        )

        if subject:
            previous = [
                " ".join(
                    str(item.get("visual_subject") or "")
                    .casefold().split()
                )
                for item in window[:-1]
            ]
            if subject in previous:
                issues.append(
                    f"scene {scene.get('scene_number')}: "
                    f"duplicate visual subject {subject}"
                )

    return issues


def decode_json(text):
    text = text.strip()

    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]

    positions = [
        value for value in (text.find("{"), text.find("["))
        if value >= 0
    ]

    if not positions:
        raise ValueError("No JSON found in AI response.")

    value, _ = json.JSONDecoder().raw_decode(
        text[min(positions):]
    )
    return value


def call_scene_json(prompt):
    strict_prompt = prompt + """
Scene metadata quality rules:
- visual_subject must be one concrete, physically depictable subject or scene, not an abstract idea.
- action must describe what the visible subject is physically doing.
- environment must name the actual visible setting around the subject.
- search_query must describe the same exact visible subject, action and setting using practical stock-footage language.
- Never output abstract visual subjects such as viewer engagement, global chemistry, ecosystem resilience, economic impact, scientific importance, social impact, concept, prompt, implication or call-to-action.
- For abstract narration, choose a concrete visual from the real mechanism, entity, location or physical process being discussed.
- For like/subscribe/comment narration, keep the visual anchored to the video's real subject; never request a screen, button, social-media interface, advertisement or fake UI.
- negative_keywords must contain only genuinely wrong meanings. Never put the requested subject, required entity, action or environment itself into negative_keywords.
- required_terms must prove the intended real subject.
- Do not include reasoning or explanations.

Additional visual-planning rules:
- shot_type must name a practical composition such as wide establishing, medium close-up, macro detail, overhead, tracking, aerial, or low-angle.
- camera_angle must specify a clearly visible viewpoint.
- focal_detail must identify the exact detail the viewer should notice.
- visual_distinction must state how this shot must differ from nearby scenes.
- zimage_prompt must describe one photorealistic documentary still matching this exact scene.
- Never request text, labels, captions, logos, UI, screens, title cards, posters, diagrams with writing, or fake interface elements in zimage_prompt.
- Never return "none", "n/a", "unknown", or placeholders for visual fields.
- Avoid repeating the same shot type, camera angle, environment and composition across adjacent scenes.
- Treat the full returned scene batch as one visual sequence, not independent prompts.
- Every scene must add a meaningfully different visual idea from nearby scenes.
- Within any 8 consecutive scenes, use at most one receipt/invoice/statement/ledger/paper-document visual.
- Within any 8 consecutive scenes, use at most two card/payment-terminal/cash-register visuals.
- Within any 8 consecutive scenes, use at most one cash/currency/coin visual.
- Do not use diagrams, charts, maps, globes, interfaces, title cards, article screenshots or editorial graphics as scene media. Remotion handles those later.
- For business/economics narration, prefer real human activity and infrastructure: customer behavior, merchant activity, checkout interaction, office operations, bank operations, data centers, logistics, factories, storefronts, call centers, ATMs, physical commerce and real workplaces.
- For abstract ideas, show the physical mechanism or people affected by it instead of paper documents or symbolic money.
- CTA/final scenes must reuse a strong REAL physical subject/location from the video's topic, never the video title as visual_subject.

Return only JSON: {"scenes":[{"scene_number":1,"visual_subject":"concrete visible subject","action":"visible physical action","environment":"real visible setting","search_query":"specific stock-footage search","required_terms":["distinctive subject proof"],"search_variants":["same subject alternate search"],"stock_keywords":["keyword"],"negative_keywords":["wrong meaning only"],"media_type":"video","shot_type":"wide establishing","camera_angle":"low angle","focal_detail":"specific visible detail","visual_distinction":"how this shot differs from neighboring scenes","zimage_prompt":"photorealistic documentary still with no text"}]}. Include every scene number. No Markdown.
"""

    providers = [
        (
            "together-deepseek-v4-flash",
            call_deepseek_metadata_flash,
            1,
        ),
        (
            "gemini-3.5-flash-lite",
            call_gemini_35_flash_lite,
            1,
        ),
        (
            "gemini-3.1-flash-lite",
            call_gemini_31_flash_lite,
            1,
        ),
        (
            "gemini-free-pool",
            call_gemini,
            1,
        ),
    ]

    errors = []

    for name, provider, attempts in providers:
        for attempt in range(attempts):
            try:
                result = provider(
                    strict_prompt,
                    json_mode=True,
                )
                data = decode_json(result.text)
                if isinstance(data, list):
                    data = {"scenes": data}
                if not isinstance(data, dict) or not data.get("scenes"):
                    raise ValueError("Empty scenes JSON.")

                issues = _diversity_issues(data)

                if issues and name.startswith("gemini"):
                    repair_prompt = strict_prompt + """
The candidate scene plan below still has visual repetition or editorial-media problems.

Repair ONLY the visual planning while preserving each scene's narration meaning and scene_number.

Hard repair requirements:
- Replace repeated receipts, statements, ledgers, cards, payment terminals, cash, bank exteriors and similar repeated object families with distinct real-world physical scenes.
- Prefer humans performing relevant actions, workplaces, infrastructure, machinery, environments and physical processes.
- Do not request diagrams, charts, maps, globes, UI, article screenshots, title cards or readable documents. Remotion will create editorial graphics later.
- Do not simply change camera angle on the same object. Change the actual visual concept where repetition exists.
- Keep visual_subject, action, environment, search_query and zimage_prompt semantically faithful to the narration.
- Return the complete corrected scenes JSON only.

Detected problems:
""" + "\n".join(issues) + """

Candidate JSON:
""" + json.dumps(data, ensure_ascii=False)

                    try:
                        repaired_result = provider(
                            repair_prompt,
                            json_mode=True,
                        )
                        repaired = decode_json(
                            repaired_result.text
                        )
                        if isinstance(repaired, list):
                            repaired = {"scenes": repaired}

                        if (
                            isinstance(repaired, dict)
                            and repaired.get("scenes")
                            and len(_diversity_issues(repaired))
                            < len(issues)
                        ):
                            data = repaired
                    except Exception:
                        pass

                return data
            except Exception as error:
                errors.append(
                    f"{name} #{attempt + 1}: {error}"
                )

    raise RuntimeError("Scene AI failed: " + " | ".join(errors))
