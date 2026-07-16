import json
import sys
from pathlib import Path

from app.ai_pipeline import generate_video_package
from app.database import SessionLocal
from app.models import VideoJob

OUTPUT_ROOT = Path("/workspace/youtube-ai-generator/outputs/jobs")

def run_job(job_id: int) -> None:
    output_dir = OUTPUT_ROOT / str(job_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as db:
        job = db.get(VideoJob, job_id)
        if not job:
            return

        job.output_directory = str(output_dir)
        job.status = "generating_script"
        job.progress = 10
        job.error_message = None

        title = job.title
        duration_minutes = job.duration_minutes
        video_style = job.video_style
        db.commit()

    try:
        package = generate_video_package(
            title=title,
            duration_minutes=duration_minutes,
            video_style=video_style,
        )


        (output_dir / "script.txt").write_text(
            package["script"], encoding="utf-8"
        )
        (output_dir / "scenes.json").write_text(
            json.dumps(package["scenes"], indent=2), encoding="utf-8"
        )

        (output_dir / "package.json").write_text(
            json.dumps(package, indent=2), encoding="utf-8"
        )

        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if not job:
                return
            job.script = package["script"]
            job.scene_plan = json.dumps(package["scenes"])
            job.status = "completed"
            job.progress = 100
            job.worker_pid = None
            db.commit()

    except Exception as error:
        with SessionLocal() as db:
            job = db.get(VideoJob, job_id)
            if job:
                job.status = "failed"
                job.error_message = str(error)
                job.worker_pid = None
                db.commit()
        raise


if __name__ == "__main__":
    run_job(int(sys.argv[1]))
