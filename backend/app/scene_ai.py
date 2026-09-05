import json

from app.ai_provider import (
    call_gemini_35_flash_lite,
    call_gemini_31_flash_lite,
    call_deepseek_metadata_flash,
)


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

Return only JSON: {"scenes":[{"scene_number":1,"visual_subject":"concrete visible subject","action":"visible physical action","environment":"real visible setting","search_query":"specific stock-footage search","required_terms":["distinctive subject proof"],"search_variants":["same subject alternate search"],"stock_keywords":["keyword"],"negative_keywords":["wrong meaning only"],"media_type":"video","shot_type":"wide establishing","camera_angle":"low angle","focal_detail":"specific visible detail","visual_distinction":"how this shot differs from neighboring scenes","zimage_prompt":"photorealistic documentary still with no text"}]}. Include every scene number. No Markdown.
"""

    providers = [
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
            "deepseek-v4-flash",
            call_deepseek_metadata_flash,
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
                return data
            except Exception as error:
                errors.append(
                    f"{name} #{attempt + 1}: {error}"
                )

    raise RuntimeError("Scene AI failed: " + " | ".join(errors))
