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
    action = " ".join(str(scene.get("action") or "").split()).strip()
    environment = " ".join(str(scene.get("environment") or "").split()).strip()
    negatives = scene.get("negative_keywords") or []
    if isinstance(negatives, str):
        negatives = [negatives]
    negatives = [str(value).strip() for value in negatives if str(value).strip()]

    lower = subject.lower()
    visual_type = "photorealistic real-world scene"
    if any(word in lower for word in ("cross-section", "cross section", "cutaway", "schematic")):
        visual_type = "high-detail realistic scientific cutaway visualization with no labels"
    elif "map" in lower:
        visual_type = "clean realistic scientific map visualization with no labels, legends, interface, borders, or writing"
    elif "animation" in lower:
        visual_type = "realistic scientific visualization, not a cartoon or presentation slide"

    parts = [
        f"Create a {visual_type} showing exactly: {subject}.",
        "The requested subject must be the clear main focus and accurately match its real environment, scale, materials, geography, and physical context.",
        "Use natural realistic lighting, believable detail, clean composition, realistic colors, and high visual fidelity.",
        "Do not add unrelated objects or concepts that are not required by the subject.",
        "No text. No readable words, letters, numbers, labels, logos, captions, signs, watermarks, interface elements, article layouts, poster layouts, or presentation layouts.",
    ]
    if action:
        parts.insert(1, f"Visible action: {action}.")
    if environment:
        parts.insert(2 if action else 1, f"Environment: {environment}.")
    if negatives:
        parts.append("Exclude: " + ", ".join(negatives[:8]) + ".")
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
