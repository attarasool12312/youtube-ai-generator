import time

from sqlalchemy import select

from app.database import SessionLocal
from app.models import VideoJob


def get_next_job(db):
    return db.scalar(
        select(VideoJob)
        .where(VideoJob.status == "waiting")
        .order_by(VideoJob.queue_position)
        .limit(1)
    )


def process_job(db, job):
    job.status = "processing"
    job.progress = 10
    db.commit()

    for progress in (25, 50, 75, 100):
        time.sleep(2)
        job.progress = progress
        db.commit()

    job.status = "completed"
    db.commit()
    print(f"Completed: {job.title}")


def run_worker():
    while True:
        with SessionLocal() as db:
            job = get_next_job(db)
            if not job:
                print("No waiting jobs.")
                break
            process_job(db, job)


if __name__ == "__main__":
    run_worker()
