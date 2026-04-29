from fastapi import FastAPI
import subprocess
import time
import sys
import os

app = FastAPI()


@app.get("/")
def home():
    return {"status": "ok", "message": "FB crawl API is running"}


@app.get("/run-all")
def run_all():
    start = time.time()

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        [sys.executable, "main.py"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )

    return {
        "status": "success" if result.returncode == 0 else "error",
        "duration": f"{time.time() - start:.2f}s",
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }
