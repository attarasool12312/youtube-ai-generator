import numpy as np
import soundfile as sf
from kokoro import KPipeline

pipe = KPipeline(lang_code="a")
parts = []

for _, _, audio in pipe(
    "Hello. Atta Video AI is now creating natural narration.",
    voice="am_michael",
    speed=1.0,
):
    parts.append(audio.cpu().numpy() if hasattr(audio, "cpu") else audio)

sf.write("outputs/kokoro-test.wav", np.concatenate(parts), 24000)
print("Kokoro audio generated successfully.")
