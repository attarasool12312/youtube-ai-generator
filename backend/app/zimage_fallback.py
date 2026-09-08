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



def _zimage_title_anchor(video_title):
    value = " ".join(str(video_title or "").split()).strip()
    lowered = value.casefold()

    for prefix in ("how ", "why ", "what is ", "what are "):
        if lowered.startswith(prefix):
            value = value[len(prefix):].strip()
            lowered = value.casefold()
            break

    for suffix in (
        " support life",
        " supports life",
        " supporting life",
        " connect the world",
        " connects the world",
        " connecting the world",
        " explained",
        " explanation",
        " why it matters",
        " what it means",
    ):
        if lowered.endswith(suffix):
            value = value[:-len(suffix)].strip()
            lowered = value.casefold()
            break

    return value


def _zimage_graphic_risk(value):
    text = " ".join(str(value or "").lower().split())

    risky = (
        "digital globe",
        "globe showing",
        "world map",
        "interactive map",
        "map visualization",
        "global scale map",
        "glowing routes",
        "glowing lines tracing",
        "control room display",
        "display showing",
        "network lines spanning",
        "routes connecting continents",
        "global transoceanic",
        "title card",
        "subscribe button",
        "social media interface",
        "fake interface",
        "infographic",
        "poster layout",
        "presentation slide",
        "flow diagram",
        "flow chart",
        "diagram on paper",
        "receipt",
        "invoice",
        "billing statement",
        "account statement",
        "credit card statement",
        "loan statement",
        "financial ledger",
        "ledger sheet",
        "credit report document",
        "rewards brochure",
        "rewards catalog",
    )

    return any(term in text for term in risky)


def build_zimage_prompt(scene, video_title="", video_profile=""):
    subject = " ".join(
        str(
            scene.get("visual_subject")
            or scene.get("search_query")
            or ""
        ).split()
    ).strip()

    action = " ".join(
        str(scene.get("action") or "").split()
    ).strip()

    environment = " ".join(
        str(scene.get("environment") or "").split()
    ).strip()

    shot_type = " ".join(
        str(scene.get("shot_type") or "").split()
    ).strip()

    camera_angle = " ".join(
        str(scene.get("camera_angle") or "").split()
    ).strip()

    focal_detail = " ".join(
        str(scene.get("focal_detail") or "").split()
    ).strip()

    visual_distinction = " ".join(
        str(scene.get("visual_distinction") or "").split()
    ).strip()

    planned_prompt = " ".join(
        str(scene.get("zimage_prompt") or "").split()
    ).strip()

    negatives = scene.get("negative_keywords") or []
    if isinstance(negatives, str):
        negatives = [negatives]

    negatives = [
        str(value).strip()
        for value in negatives
        if str(value).strip()
    ]

    # Z-Image has no true negative-prompt channel. Never mention
    # unwanted editorial objects anywhere in the positive prompt.
    # Even "no X" can cause X to appear.
    if _zimage_graphic_risk(subject):
        subject = ""

    for field_name in (
        "action",
        "environment",
        "shot_type",
        "camera_angle",
        "focal_detail",
        "visual_distinction",
        "planned_prompt",
    ):
        value = locals()[field_name]
        if _zimage_graphic_risk(value):
            if field_name == "action":
                action = ""
            elif field_name == "environment":
                environment = ""
            elif field_name == "shot_type":
                shot_type = ""
            elif field_name == "camera_angle":
                camera_angle = ""
            elif field_name == "focal_detail":
                focal_detail = ""
            elif field_name == "visual_distinction":
                visual_distinction = ""
            elif field_name == "planned_prompt":
                planned_prompt = ""

    if not subject and environment:
        subject = environment

    narration = " ".join(
        str(scene.get("narration") or "").lower().split()
    )

    is_cta = (
        "subscribe" in narration
        or (
            "comment" in narration
            and "like" in narration
        )
    )

    if is_cta:
        # Closing visuals must stay physical. Never send a video title,
        # CTA wording, poster-style concept or title-like composition
        # into Z-Image.
        planned_prompt = ""
        action = ""
        visual_distinction = ""

        if environment:
            subject = environment
        elif focal_detail:
            subject = focal_detail
        else:
            subject = _zimage_title_anchor(video_title)

    if not subject:
        subject = _zimage_title_anchor(video_title)

    lower = subject.lower()

    visual_type = "photorealistic real-world documentary scene"

    if any(
        word in lower
        for word in (
            "cross-section",
            "cross section",
            "cutaway",
            "schematic",
        )
    ):
        visual_type = (
            "high-detail realistic scientific cutaway visualization "
            "with no labels"
        )

    parts = [
        f"Create a {visual_type} showing exactly: {subject}.",
    ]

    if planned_prompt:
        parts.append(
            "Scene-specific visual plan: "
            + planned_prompt
            + "."
        )

    if action:
        parts.append(f"Visible action: {action}.")

    if environment:
        parts.append(f"Environment: {environment}.")

    if shot_type:
        parts.append(f"Shot composition: {shot_type}.")

    if camera_angle:
        parts.append(f"Camera viewpoint: {camera_angle}.")

    if focal_detail:
        parts.append(
            f"Primary focal detail: {focal_detail}."
        )

    if visual_distinction:
        parts.append(
            "Visual distinction from nearby scenes: "
            + visual_distinction
            + "."
        )

    parts.extend([
        (
            "The requested subject must be the clear main focus and "
            "accurately match its real environment, scale, materials, "
            "geography, and physical context."
        ),
        (
            "Use realistic documentary photography, natural believable "
            "lighting, physically plausible materials, clean composition, "
            "realistic colors, and high visual fidelity."
        ),
        (
            "Do not add unrelated objects, symbolic concepts, generic "
            "filler imagery, or invented events."
        ),
        (
            "Keep the frame as one coherent real-world physical scene "
            "captured by a documentary camera, with no composited panels, "
            "editorial overlays, or artificial visual inserts."
        ),
        (
            "Use clean unbranded physical objects and surfaces without "
            "readable writing or visible branding."
        ),
    ])

    # Do not append textual negative keywords. Z-Image Turbo can
    # visually react to forbidden nouns even when phrased as exclusions.
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
    def valid_media(value, min_bytes=1):
        if not value:
            return False
        try:
            path = Path(str(value))
            return path.is_file() and path.stat().st_size >= min_bytes
        except OSError:
            return False

    targets = [
        scene for scene in scenes
        if scene.get("needs_ai_fallback")
    ]
    if not targets:
        return scenes

    python_path = _zimage_python()
    script_path = _batch_script()

    if not python_path.is_file() or not script_path.is_file():
        unresolved = [
            int(scene["scene_number"])
            for scene in targets
            if not valid_media(scene.get("media_file"))
        ]

        if unresolved:
            raise RuntimeError(
                "ZIMAGE_UNAVAILABLE_NO_MEDIA scenes="
                + ",".join(map(str, unresolved))
            )

        for scene in targets:
            scene["needs_ai_fallback"] = False
            scene["stock_decision"] = (
                "ai_fallback_unavailable_stock_retained"
            )

        return scenes

    output_dir = Path(output_dir)
    ai_dir = output_dir / "media" / "ai"
    ai_dir.mkdir(parents=True, exist_ok=True)

    for scene in targets:
        scene["ai_prompt"] = build_zimage_prompt(
            scene,
            video_title,
            video_profile,
        )

    generated_by_scene = {}
    failed_by_scene = {}
    pending = list(targets)
    last_reported_progress = 0

    for attempt in range(1, 4):
        if not pending:
            break

        using_warmup = (
            attempt == 1
            and _warmup_alive(warmup)
        )

        if using_warmup:
            request_path = Path(warmup["request_path"])
            result_path = Path(warmup["result_path"])
        else:
            suffix = "" if attempt == 1 else f"-retry-{attempt}"
            request_path = (
                output_dir / f"zimage-request{suffix}.json"
            )
            result_path = (
                output_dir / f"zimage-result{suffix}.json"
            )

        request_items = []

        for scene in pending:
            number = int(scene["scene_number"])
            output_path = (
                ai_dir / f"scene-{number:03d}.png"
            )

            request_items.append({
                "scene_number": number,
                "prompt": scene["ai_prompt"],
                "output_path": str(output_path.resolve()),
                "seed": (
                    100000
                    + number
                    + ((attempt - 1) * 10000)
                ),
            })

        request_path.write_text(
            json.dumps(
                {
                    "orientation": orientation,
                    "items": request_items,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result_path.unlink(missing_ok=True)

        logger.info(
            "ZIMAGE batch start scenes=%d warm=%s attempt=%d",
            len(pending),
            using_warmup,
            attempt,
        )

        if using_warmup:
            process = warmup["process"]
            last_progress = 0

            while process.poll() is None:
                if result_path.is_file():
                    try:
                        payload = json.loads(
                            result_path.read_text(
                                encoding="utf-8"
                            )
                        )
                        completed = len(
                            payload.get("items") or []
                        )

                        if completed > last_progress:
                            last_progress = completed

                            if progress_callback:
                                progress_callback(
                                    completed,
                                    len(targets),
                                )
                                last_reported_progress = max(
                                    last_reported_progress,
                                    completed,
                                )
                    except Exception:
                        pass

                time.sleep(0.5)

            return_code = process.wait()

        else:
            _release_ollama_gpu()

            process = subprocess.Popen(
                [
                    str(python_path),
                    str(script_path),
                    "--request",
                    str(request_path),
                    "--result",
                    str(result_path),
                ],
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

                if (
                    text.startswith("ZIMAGE_PROGRESS|")
                    and progress_callback
                ):
                    parts = text.split("|")

                    if len(parts) >= 4:
                        try:
                            completed = int(parts[1])
                            progress_callback(
                                completed,
                                len(targets),
                            )
                            last_reported_progress = max(
                                last_reported_progress,
                                completed,
                            )
                        except Exception:
                            pass

            return_code = process.wait()

        if return_code != 0:
            logger.warning(
                "ZIMAGE batch exit=%s attempt=%d",
                return_code,
                attempt,
            )

        try:
            payload = (
                json.loads(
                    result_path.read_text(
                        encoding="utf-8"
                    )
                )
                if result_path.is_file()
                else {}
            )
        except Exception:
            payload = {}

        result_items = {
            int(item.get("scene_number") or 0): item
            for item in payload.get("items", [])
            if isinstance(item, dict)
        }

        retry = []

        for scene in pending:
            number = int(scene["scene_number"])
            item = result_items.get(number) or {}

            output_path = Path(
                item.get("output_path")
                or ai_dir / f"scene-{number:03d}.png"
            )

            # The actual file is authoritative.
            # This recovers interrupted/missing result JSON entries.
            if valid_media(output_path, min_bytes=1024):
                old_media = str(
                    scene.get("media_file") or ""
                )

                scene.update({
                    "stock_fallback_file": (
                        old_media or None
                    ),
                    "media_file": str(output_path),
                    "media_provider": "z-image-turbo",
                    "provider": "z-image-turbo",
                    "media_type": "image",
                    "source": (
                        "ai://z-image-turbo/"
                        f"scene-{number:03d}"
                    ),
                    "needs_ai_fallback": False,
                    "stock_decision": (
                        "ai_generated"
                        if attempt == 1
                        else "ai_generated_retry"
                    ),
                    "ai_generation_seconds": (
                        item.get("seconds")
                    ),
                })

                generated_by_scene[number] = scene
                failed_by_scene.pop(number, None)
                continue

            error = str(
                item.get("error")
                or f"z-image batch exit {return_code}"
            )[:500]

            # Never throw away an existing valid stock asset.
            if valid_media(scene.get("media_file")):
                scene["needs_ai_fallback"] = False
                scene["stock_decision"] = (
                    "ai_fallback_failed_stock_retained"
                )
                scene["ai_fallback_error"] = error
                failed_by_scene[number] = error
                continue

            scene["ai_fallback_error"] = error

            if attempt < 3:
                retry.append(scene)
            else:
                failed_by_scene[number] = error
                retry.append(scene)

        pending = retry

    _rewrite_manifest(
        output_dir,
        scenes,
        generated_by_scene,
        failed_by_scene,
    )

    unresolved = [
        int(scene["scene_number"])
        for scene in scenes
        if not valid_media(scene.get("media_file"))
    ]

    if unresolved:
        raise RuntimeError(
            "ZIMAGE_UNRESOLVED_MEDIA scenes="
            + ",".join(map(str, unresolved))
        )

    if (
        progress_callback
        and last_reported_progress < len(targets)
    ):
        progress_callback(
            len(targets),
            len(targets),
        )

    logger.info(
        "ZIMAGE complete generated=%d retained=%d total=%d",
        len(generated_by_scene),
        len(failed_by_scene),
        len(targets),
    )

    return scenes
