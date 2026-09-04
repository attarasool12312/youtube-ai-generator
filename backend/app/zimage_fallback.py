import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def _zimage_home() -> Path:
    return Path(os.getenv("ZIMAGE_HOME", "~/zimage-turbo")).expanduser()


def _zimage_python() -> Path:
    configured = os.getenv("ZIMAGE_PYTHON", "").strip()
    if configured:
        return Path(configured).expanduser()
    return _zimage_home() / "venv" / "bin" / "python"


def _batch_script() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "zimage_batch.py"




def _zimage_env():
    env = os.environ.copy()
    env["HF_HOME"] = str(_zimage_home() / "hf")
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    cudnn_lib = str(Path.home() / "zimage-turbo" / "venv" / "lib" / "python3.12" / "site-packages" / "nvidia" / "cudnn" / "lib")
    env["LD_LIBRARY_PATH"] = cudnn_lib + ((":" + env["LD_LIBRARY_PATH"]) if env.get("LD_LIBRARY_PATH") else "")
    return env


def start_zimage_warmup(output_dir, timeout_seconds=1800):
    """Load Z-Image early and wait for the real batch request.

    The subprocess owns the isolated Z-Image environment.  It is safe to
    start only after other GPU-heavy preparation (notably captions) has
    released VRAM.
    """
    python_path = _zimage_python()
    script_path = _batch_script()
    if not python_path.is_file() or not script_path.is_file():
        return None

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    request_path = output_dir / "zimage-request.json"
    result_path = output_dir / "zimage-result.json"
    ready_path = output_dir / "zimage-ready"
    for path in (request_path, result_path, ready_path):
        path.unlink(missing_ok=True)

    _release_ollama_gpu()
    command = [
        str(python_path),
        str(script_path),
        "--request",
        str(request_path),
        "--result",
        str(result_path),
        "--wait-for-request",
        str(max(60, int(timeout_seconds))),
        "--ready",
        str(ready_path),
    ]
    logger.info("ZIMAGE warmup start")
    try:
        process = subprocess.Popen(
            command,
            stdout=None,
            stderr=None,
            env=_zimage_env(),
        )
    except Exception as error:
        logger.warning("ZIMAGE warmup unavailable: %s", error)
        return None
    return {
        "process": process,
        "request_path": request_path,
        "result_path": result_path,
        "ready_path": ready_path,
    }


def cancel_zimage_warmup(handle):
    if not handle:
        return
    process = handle.get("process")
    if process is None or process.poll() is not None:
        return
    logger.info("ZIMAGE warmup cancel")
    try:
        process.terminate()
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    except Exception as error:
        logger.info("ZIMAGE warmup cleanup skipped: %s", error)


def _warmup_alive(handle):
    if not handle:
        return False
    process = handle.get("process")
    return process is not None and process.poll() is None



def build_zimage_prompt(scene, video_title="", video_profile=""):
    subject = " ".join(str(scene.get("visual_subject") or scene.get("search_query") or "").split()).strip()
    narration = " ".join(str(scene.get("narration") or "").split()).strip()
    negatives = scene.get("negative_keywords") or []
    if isinstance(negatives, str):
        negatives = [negatives]
    negatives = [str(value).strip() for value in negatives if str(value).strip()]
    lower_subject = subject.lower()
    lower_narration = narration.lower()

    def has(*terms):
        text = lower_subject + " " + lower_narration
        return any(term in text for term in terms)

    shot_style = (
        "Use a clean, high-end documentary composition with a clear focal subject, realistic depth, natural materials, and believable environmental detail."
    )
    subject_treatment = (
        "Show the scene literally and accurately, with the main subject visually obvious at first glance."
    )

    if has("satellite", "orbit", "earth from space", "communication satellite"):
        shot_style = (
            "Create a cinematic orbital documentary shot, photographed from space with a wide-angle lens feel, dramatic scale, crisp detail, and realistic lighting."
        )
        subject_treatment = (
            "Show a real communication satellite in orbit around Earth with the planet visible below, emphasizing space, altitude, and global scale."
        )
    elif has("map", "network", "global traffic", "globe", "routes"):
        shot_style = (
            "Create a sleek cinematic visualization frame with a realistic globe or world-scale network view, elegant lighting, dark atmospheric background, and subtle glowing route lines."
        )
        subject_treatment = (
            "Depict worldwide data movement and global connectivity visually, but without any written labels, legends, UI panels, or infographic text."
        )
    elif has("ocean floor", "seabed", "fiber optic cable", "submarine cable"):
        shot_style = (
            "Create a dramatic underwater documentary frame with clear depth, suspended particles, soft shafts of light, rich blue tones, and realistic seabed textures."
        )
        subject_treatment = (
            "Show the cable resting on the ocean floor as the main subject. Do not show submarines, naval vehicles, divers, or unrelated underwater machinery unless explicitly required."
        )
    elif has("cross section", "cutaway", "schematic", "anatomy of cable"):
        shot_style = (
            "Create a premium photoreal cutaway product-style frame with studio-quality lighting, clean composition, extremely sharp materials, and a realistic macro engineering look."
        )
        subject_treatment = (
            "Show the cable cross-section or internal structure clearly as a realistic physical cutaway, not as a textbook diagram, poster, or labeled infographic."
        )
    elif has("deep sea", "abyss", "underwater abyss"):
        shot_style = (
            "Create a moody cinematic deep-ocean frame with vast scale, darkness, volumetric water haze, and subtle light falloff into the abyss."
        )
        subject_treatment = (
            "Emphasize isolation, depth, and the extreme deep-sea environment in a realistic documentary style."
        )
    elif has("landing station", "server rack", "data center", "telecom room"):
        shot_style = (
            "Create a realistic interior documentary frame with crisp detail, controlled cinematic lighting, and a professional commercial-camera look."
        )
        subject_treatment = (
            "Show the infrastructure clearly and realistically, focusing on equipment, scale, and operational detail."
        )

    parts = [
        "Create one photorealistic cinematic documentary image.",
        "This must look like a real frame from a professionally edited documentary film.",
        "It is NOT a poster, thumbnail, presentation slide, collage, or infographic.",
        f"Primary visible subject: {subject or 'the literal subject described in the narration'}.",
        f"Scene meaning and context: {narration[:700]}.",
        subject_treatment,
        shot_style,
        "Use realistic lighting, grounded physical detail, correct scale, believable textures, and strong composition.",
        "Avoid repetitive generic imagery. Choose a visually distinctive interpretation of this specific scene.",
        "No text or symbols. ABSOLUTELY NO TEXT OR SYMBOLS: no words, letters, numbers, captions, titles, labels, logos, watermarks, UI, map labels, signage, or readable markings anywhere in the image.",
        "If the scene contains a monitor, document, device display, control panel, museum label, book page, scientific interface, chart, or computer screen, it must show only abstract non-readable shapes, blurred content, or be out of focus.",
        "Any object surface that would normally contain text should appear blank, obscured, or unreadable.",
        "Do not invent unrelated subjects, and do not add submarines unless the scene explicitly requires a submarine vehicle.",
        "Do not show cameras, camcorders, lenses, tripods, camera crews, studio rigs, microphones, filming setups, or behind-the-scenes production equipment unless the scene explicitly requires them.",
    ]

    if negatives:
        parts.append("Avoid these wrong meanings or unwanted subjects: " + ", ".join(negatives[:8]) + ".")

    if video_profile:
        parts.append(f"Visual tone: {video_profile} documentary style.")

    return " ".join(parts)


def _release_ollama_gpu():
    model = str(getattr(settings, "ollama_model", "") or "").strip()
    if not model or not shutil.which("ollama"):
        return
    try:
        subprocess.run(
            ["ollama", "stop", model],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except Exception as error:
        logger.info("ZIMAGE Ollama unload skipped: %s", error)


def _rewrite_manifest(output_dir, scenes, generated_by_scene, failed_by_scene):
    manifest_path = Path(output_dir) / "media-manifest.jsonl"
    if manifest_path.is_file():
        rewritten = []
        for raw_line in manifest_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                entry = json.loads(raw_line)
            except Exception:
                rewritten.append(raw_line)
                continue
            number = int(entry.get("scene_number") or 0)
            if number in generated_by_scene:
                scene = generated_by_scene[number]
                entry.update({
                    "provider": "z-image-turbo",
                    "source": f"ai://z-image-turbo/scene-{number:03d}",
                    "media_type": "image",
                    "needs_ai_fallback": False,
                    "stock_decision": "ai_generated",
                    "ai_prompt": scene.get("ai_prompt"),
                    "ai_generation_seconds": scene.get("ai_generation_seconds"),
                    "stock_fallback_file": scene.get("stock_fallback_file"),
                })
            elif number in failed_by_scene:
                entry["stock_decision"] = "ai_fallback_failed_stock_retained"
                entry["ai_fallback_error"] = failed_by_scene[number]
            rewritten.append(json.dumps(entry, ensure_ascii=False))
        manifest_path.write_text("\n".join(rewritten) + ("\n" if rewritten else ""), encoding="utf-8")

    attribution_path = Path(output_dir) / "attributions.json"
    if attribution_path.is_file() and generated_by_scene:
        try:
            values = json.loads(attribution_path.read_text(encoding="utf-8"))
            values = [
                item for item in values
                if int(item.get("scene_number") or 0) not in generated_by_scene
            ]
            attribution_path.write_text(json.dumps(values, indent=2), encoding="utf-8")
        except Exception as error:
            logger.warning("ZIMAGE attribution cleanup skipped: %s", error)


def apply_zimage_fallbacks(
    scenes,
    output_dir,
    orientation="landscape",
    video_title="",
    video_profile="",
    progress_callback=None,
    warmup=None,
):
    targets = [scene for scene in scenes if scene.get("needs_ai_fallback")]
    if not targets:
        return scenes

    python_path = _zimage_python()
    script_path = _batch_script()
    if not python_path.is_file() or not script_path.is_file():
        logger.warning(
            "ZIMAGE unavailable python=%s script=%s; retaining weak stock fallback.",
            python_path,
            script_path,
        )
        for scene in targets:
            scene["stock_decision"] = "ai_fallback_unavailable_stock_retained"
        return scenes

    output_dir = Path(output_dir)
    ai_dir = output_dir / "media" / "ai"
    ai_dir.mkdir(parents=True, exist_ok=True)
    request_path = output_dir / "zimage-request.json"
    result_path = output_dir / "zimage-result.json"
    using_warmup = _warmup_alive(warmup)
    if using_warmup:
        request_path = Path(warmup["request_path"])
        result_path = Path(warmup["result_path"])

    request_items = []
    for scene in targets:
        number = int(scene["scene_number"])
        prompt = build_zimage_prompt(scene, video_title, video_profile)
        request_items.append({
            "scene_number": number,
            "prompt": prompt,
            "output_path": str((ai_dir / f"scene-{number:03d}.png").resolve()),
            "seed": 100000 + number,
        })
        scene["ai_prompt"] = prompt

    request_path.write_text(
        json.dumps({"orientation": orientation, "items": request_items}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    result_path.unlink(missing_ok=True)

    logger.info("ZIMAGE batch start scenes=%d warm=%s", len(targets), using_warmup)
    if using_warmup:
        process = warmup["process"]
        last_progress = 0
        while process.poll() is None:
            if result_path.is_file():
                try:
                    payload = json.loads(result_path.read_text(encoding="utf-8"))
                    completed = len(payload.get("items") or [])
                    if completed > last_progress:
                        last_progress = completed
                        if progress_callback:
                            progress_callback(completed, len(targets))
                except Exception:
                    pass
            time.sleep(0.5)
        return_code = process.wait()
        if result_path.is_file() and progress_callback and last_progress < len(targets):
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                completed = len(payload.get("items") or [])
                if completed > last_progress:
                    progress_callback(completed, len(targets))
            except Exception:
                pass
    else:
        _release_ollama_gpu()
        command = [
            str(python_path),
            str(script_path),
            "--request",
            str(request_path),
            "--result",
            str(result_path),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=_zimage_env(),
        )

        assert process.stdout is not None
        for line in process.stdout:
            text = line.rstrip()
            print(text, flush=True)
            if text.startswith("ZIMAGE_PROGRESS|") and progress_callback:
                parts = text.split("|")
                if len(parts) >= 4:
                    try:
                        progress_callback(int(parts[1]), int(parts[2]))
                    except Exception:
                        pass
        return_code = process.wait()
    if return_code != 0:
        logger.warning("ZIMAGE batch exited with code %s; retaining stock for failed scenes.", return_code)

    try:
        result_payload = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
    except Exception:
        result_payload = {}

    result_items = {
        int(item.get("scene_number") or 0): item
        for item in result_payload.get("items", [])
        if isinstance(item, dict)
    }

    generated_by_scene = {}
    failed_by_scene = {}

    for scene in targets:
        number = int(scene["scene_number"])
        item = result_items.get(number) or {}
        output_path = Path(item.get("output_path") or ai_dir / f"scene-{number:03d}.png")
        if item.get("ok") and output_path.is_file() and output_path.stat().st_size > 1024:
            old_media = str(scene.get("media_file") or "")
            scene.update({
                "stock_fallback_file": old_media or None,
                "media_file": str(output_path),
                "media_provider": "z-image-turbo",
                "provider": "z-image-turbo",
                "media_type": "image",
                "source": f"ai://z-image-turbo/scene-{number:03d}",
                "needs_ai_fallback": False,
                "stock_decision": "ai_generated",
                "ai_generation_seconds": item.get("seconds"),
            })
            generated_by_scene[number] = scene
        else:
            error = str(item.get("error") or f"z-image batch exit {return_code}")[:500]
            scene["stock_decision"] = "ai_fallback_failed_stock_retained"
            scene["ai_fallback_error"] = error
            failed_by_scene[number] = error

    _rewrite_manifest(output_dir, scenes, generated_by_scene, failed_by_scene)
    logger.info(
        "ZIMAGE batch done generated=%d retained_stock=%d",
        len(generated_by_scene),
        len(failed_by_scene),
    )
    return scenes
