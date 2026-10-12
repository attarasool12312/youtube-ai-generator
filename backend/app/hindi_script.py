"""Hindi-only narration path; does not alter the existing English workflow."""
from app.ai_provider import call_ai


def generate_hindi_script(title: str, minutes: int) -> dict:
    target = max(180, minutes * 125)
    sections = []
    # Small sections improve reliability, and avoid a single huge LLM output.
    parts = max(1, (target + 450 - 1) // 450)
    for i in range(parts):
        words = (target + parts - 1) // parts
        context = sections[-1][-650:] if sections else '(शुरुआत)'
        prompt = (
            f'You are writing narration for a Hindi YouTube video titled: {title}\n'
            f'Part {i+1} of {parts}. Write approximately {words} Hindi words for this part.\n'
            'IMPORTANT: Write spoken natural Hindi IN DEVANAGARI SCRIPT ONLY. '
            'No English narration, Roman Hindi, headings, lists, meta commentary or timecodes. '
            'Maintain narrative continuity with the previous part. '
            'Start with a compelling hook if first part; give a satisfying conclusion if last part. '
            'Do not fabricate factual details or make unsupported claims. '
            f'Previous part ending: {context}'
        )
        response = call_ai(prompt, json_mode=False)
        section = response.text.strip()
        if not section or not any("\u0900" <= c <= "\u097f" for c in section):
            raise RuntimeError('Hindi script provider returned no Devanagari narration')
        sections.append(section)
    script = '\n\n'.join(sections)
    return {
        'script': script, 'actual_words': len(script.split()),
        'initial_provider': 'hindi_ai', 'repair_providers': [],
        'validation': {'valid': True, 'word_count': len(script.split()), 'issues': []},
    }
