from dataclasses import dataclass
from pathlib import Path


VOICE_PREVIEW_URL_ROOT = "/static/voice-previews"
VOICE_PREVIEW_EXTENSION = ".mp3"
VOICE_PREVIEW_DIRECTORY = Path(__file__).resolve().parent / "static" / "voice-previews"


@dataclass(frozen=True)
class NarratorVoice:
    voice_id: str
    label: str
    english_variant: str
    gender: str
    approved: bool = True
    recommended_speed: float = 1.0
    quality_status: str = "verified"

    @property
    def pipeline_language(self) -> str:
        return "b" if self.english_variant == "British English" else "a"

    @property
    def display_label(self) -> str:
        return f"{self.label} - {self.gender} ({self.english_variant})"


# Official Kokoro-82M v1.0 English voices. Only explicit approved entries are exposed.
_VOICE_ROWS = (
    ("am_michael", "Michael", "American English", "Male"),
    ("am_fenrir", "Fenrir", "American English", "Male"),
    ("am_puck", "Puck", "American English", "Male"),
    ("af_heart", "Heart", "American English", "Female"),
    ("af_bella", "Bella", "American English", "Female"),
    ("af_nicole", "Nicole", "American English", "Female"),
    ("af_alloy", "Alloy", "American English", "Female"),
    ("af_aoede", "Aoede", "American English", "Female"),
    ("af_jessica", "Jessica", "American English", "Female"),
    ("af_kore", "Kore", "American English", "Female"),
    ("af_nova", "Nova", "American English", "Female"),
    ("af_river", "River", "American English", "Female"),
    ("af_sarah", "Sarah", "American English", "Female"),
    ("af_sky", "Sky", "American English", "Female"),
    ("am_adam", "Adam", "American English", "Male"),
    ("am_echo", "Echo", "American English", "Male"),
    ("am_eric", "Eric", "American English", "Male"),
    ("am_liam", "Liam", "American English", "Male"),
    ("am_onyx", "Onyx", "American English", "Male"),
    ("am_santa", "Santa", "American English", "Male"),
    ("bf_alice", "Alice", "British English", "Female"),
    ("bf_emma", "Emma", "British English", "Female"),
    ("bf_isabella", "Isabella", "British English", "Female"),
    ("bf_lily", "Lily", "British English", "Female"),
    ("bm_daniel", "Daniel", "British English", "Male"),
    ("bm_fable", "Fable", "British English", "Male"),
    ("bm_george", "George", "British English", "Male"),
    ("bm_lewis", "Lewis", "British English", "Male"),
)
ENGLISH_NARRATOR_VOICES = tuple(NarratorVoice(*row) for row in _VOICE_ROWS)
APPROVED_NARRATOR_VOICES = tuple(v for v in ENGLISH_NARRATOR_VOICES if v.approved)
VOICE_BY_ID = {v.voice_id: v for v in APPROVED_NARRATOR_VOICES}
DEFAULT_NARRATOR_VOICE = "am_michael"
DEFAULT_VOICE_POOL_CSV = ",".join(VOICE_BY_ID)
ADDITIONAL_VOICE_IDS = ("am_fenrir", "af_heart", "af_nova", "bm_george", "bf_emma")
ADDITIONAL_VOICES = tuple(VOICE_BY_ID[voice_id] for voice_id in ADDITIONAL_VOICE_IDS)
MIN_VOICE_SPEED = 0.75
MAX_VOICE_SPEED = 1.25


def get_narrator_voice(voice_id: str) -> NarratorVoice:
    try:
        return VOICE_BY_ID[voice_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported narrator voice: {voice_id!r}") from exc


def validate_voice_speed(speed: float) -> float:
    try:
        value = float(speed)
    except (TypeError, ValueError) as exc:
        raise ValueError("Voice speed must be numeric.") from exc
    if not MIN_VOICE_SPEED <= value <= MAX_VOICE_SPEED:
        raise ValueError(f"Voice speed must be between {MIN_VOICE_SPEED} and {MAX_VOICE_SPEED}.")
    return value


def validated_voice_pool(voice_ids) -> list[str]:
    return list(dict.fromkeys(get_narrator_voice(value).voice_id for value in voice_ids))


def voice_preview_filename(voice_id: str) -> str:
    return f"{get_narrator_voice(voice_id).voice_id}{VOICE_PREVIEW_EXTENSION}"


def voice_preview_path(voice_id: str) -> Path:
    return VOICE_PREVIEW_DIRECTORY / voice_preview_filename(voice_id)


def voice_preview_url(voice_id: str) -> str:
    return f"{VOICE_PREVIEW_URL_ROOT}/{voice_preview_filename(voice_id)}"


def voice_template_context() -> dict:
    return {
        "narrator_voices": APPROVED_NARRATOR_VOICES,
        "additional_voices": ADDITIONAL_VOICES,
        "default_narrator_voice": DEFAULT_NARRATOR_VOICE,
        "voice_preview_url_root": VOICE_PREVIEW_URL_ROOT,
    }
