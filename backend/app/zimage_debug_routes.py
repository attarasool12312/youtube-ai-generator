from __future__ import annotations

import base64
import html
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.paths import PROJECT_ROOT
from app.config import settings
from app.project_info import get_project_info

router = APIRouter()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "backend" / "app" / "templates"))

OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "text2image"
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "zimage_batch.py"
ZIMAGE_PYTHON = Path.home() / "zimage-turbo" / "venv" / "bin" / "python"
HF_HOME = Path.home() / "zimage-turbo" / "hf"


def _page(request: Request, **context):
    defaults = {
        "app_name": settings.app_name,
        "active_page": "text_to_image",
        "project_info": get_project_info(),
        "prompt": "",
        "orientation": "landscape",
        "seed": "42",
        "image_b64": "",
        "saved_path": "",
        "load_seconds": "",
        "gen_seconds": "",
        "error": "",
        "raw_log": "",
    }
    defaults.update(context)
    return templates.TemplateResponse(
        request=request,
        name="text_to_image.html",
        context=defaults,
    )


@router.get("/tools/text-to-image", response_class=HTMLResponse)
def text_to_image_form(request: Request):
    return _page(request, )


@router.post("/tools/text-to-image", response_class=HTMLResponse)
def text_to_image_generate(
    request: Request,
    prompt: str = Form(...),
    orientation: str = Form("landscape"),
    seed: str = Form("42"),
):
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    run_id = uuid.uuid4().hex[:12]
    request_path = OUTPUT_ROOT / f"{run_id}-request.json"
    result_path = OUTPUT_ROOT / f"{run_id}-result.json"
    ready_path = OUTPUT_ROOT / f"{run_id}-ready"
    image_path = OUTPUT_ROOT / f"{run_id}.png"

    payload = {
        "orientation": orientation,
        "items": [
            {
                "scene_number": 1,
                "prompt": prompt,
                "seed": int(seed or 42),
                "output_path": str(image_path),
            }
        ],
    }
    request_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    env = os.environ.copy()
    env["HF_HOME"] = str(HF_HOME)
    env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
    env["TOKENIZERS_PARALLELISM"] = "false"
    cudnn_lib = str(Path.home() / "zimage-turbo" / "venv" / "lib" / "python3.12" / "site-packages" / "nvidia" / "cudnn" / "lib")
    env["LD_LIBRARY_PATH"] = cudnn_lib + ((":" + env["LD_LIBRARY_PATH"]) if env.get("LD_LIBRARY_PATH") else "")

    command = [
        str(ZIMAGE_PYTHON),
        str(SCRIPT_PATH),
        "--request", str(request_path),
        "--result", str(result_path),
        "--ready", str(ready_path),
    ]

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except Exception as e:
        return _page(request, 
            prompt=prompt,
            orientation=orientation,
            seed=seed,
            error=str(e),
        )

    raw_log = (completed.stdout or "") + "\n" + (completed.stderr or "")
    total_seconds = round(time.perf_counter() - started, 2)

    load_seconds = ""
    gen_seconds = ""
    error = ""

    for line in (completed.stdout or "").splitlines():
        if line.startswith("ZIMAGE_LOAD_SECONDS|"):
            load_seconds = line.split("|", 1)[1].strip()
        elif line.startswith("ZIMAGE_PROGRESS|"):
            pass

    if not result_path.is_file():
        error = f"No result JSON created. Exit code: {completed.returncode}"
        return _page(request, 
            prompt=prompt,
            orientation=orientation,
            seed=seed,
            error=error,
            raw_log=raw_log,
            load_seconds=load_seconds,
        )

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as e:
        return _page(request, 
            prompt=prompt,
            orientation=orientation,
            seed=seed,
            error=f"Could not parse result JSON: {e}",
            raw_log=raw_log,
            load_seconds=load_seconds,
        )

    item = (result.get("items") or [{}])[0]
    if not item.get("ok"):
        error = item.get("error") or f"Generation failed. Exit code: {completed.returncode}"
        return _page(request, 
            prompt=prompt,
            orientation=orientation,
            seed=seed,
            error=error,
            raw_log=raw_log,
            load_seconds=load_seconds,
        )

    gen_seconds = str(item.get("seconds") or total_seconds)

    if not image_path.is_file():
        return _page(request, 
            prompt=prompt,
            orientation=orientation,
            seed=seed,
            error="Image path missing after success response.",
            raw_log=raw_log,
            load_seconds=load_seconds,
            gen_seconds=gen_seconds,
        )

    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")

    return _page(request, 
        prompt=prompt,
        orientation=orientation,
        seed=seed,
        image_b64=image_b64,
        saved_path=str(image_path),
        load_seconds=load_seconds,
        gen_seconds=gen_seconds,
        raw_log=raw_log,
    )
