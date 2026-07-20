import os
from pathlib import Path

PROJECT_ROOT = Path(
    os.getenv(
        "YOUTUBE_AI_PROJECT_ROOT",
        str(Path(__file__).resolve().parents[2]),
    )
).resolve()

MODEL_ROOT = Path(
    os.getenv(
        "YOUTUBE_AI_MODEL_ROOT",
        str(PROJECT_ROOT.parent / "models"),
    )
).resolve()

OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "jobs"
DATA_ROOT = PROJECT_ROOT / "backend" / "data"
ENV_FILE = PROJECT_ROOT / ".env"
FLUX_MODEL_PATH = MODEL_ROOT / "flux1-schnell"
HF_HOME = MODEL_ROOT / "huggingface"
