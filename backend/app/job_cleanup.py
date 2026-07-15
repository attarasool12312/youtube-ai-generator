import os
import shutil
import signal
import time
from pathlib import Path

OUTPUT_ROOT = Path(
    "/workspace/youtube-ai-generator/outputs/jobs"
).resolve()


def cleanup_job_files(worker_pid=None, output_directory=None):
    if worker_pid and worker_pid > 1:
        try:
            os.kill(worker_pid, signal.SIGTERM)
            time.sleep(1)

            try:
                os.kill(worker_pid, 0)
                os.kill(worker_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass
        except PermissionError:
            pass

    if not output_directory:
        return

    target = Path(output_directory).resolve()

    if target == OUTPUT_ROOT or OUTPUT_ROOT not in target.parents:
        raise ValueError("Unsafe output directory.")

    if target.exists():
        shutil.rmtree(target)
