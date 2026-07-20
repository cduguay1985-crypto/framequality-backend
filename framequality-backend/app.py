"""
FrameQuality Pro — HTTP API.

Designed to be driven by a Bubble front end:

    POST /v1/scans                 submit a film (URL or multipart upload)
    GET  /v1/scans/{id}            poll status / progress
    GET  /v1/scans/{id}/report     full JSON report
    GET  /v1/scans/{id}/report.pdf printable PDF
    GET  /v1/scans/{id}/report.html embeddable HTML view
    GET  /v1/scans                 list scans for the calling user
    GET  /v1/profiles              delivery profiles for the dropdown
    GET  /health

Auth: send `X-API-Key: <key>` on every call. Keys come from the FQ_API_KEYS env
var (comma-separated). Bubble stores the key in a server-side API Connector
header so it never reaches the browser.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from fastapi import (
    BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from qc import __version__, run_scan
from qc.profiles import list_profiles, PROFILES
from qc.report import build_html, build_pdf
from qc.store import Store

DATA_DIR = os.environ.get("FQ_DATA_DIR", "./data")
MAX_WORKERS = int(os.environ.get("FQ_WORKERS", "2"))
MAX_UPLOAD_GB = float(os.environ.get("FQ_MAX_UPLOAD_GB", "80"))
API_KEYS = {
    k.strip() for k in os.environ.get("FQ_API_KEYS", "").split(",") if k.strip()
}
ALLOWED_ORIGINS = [
    o.strip() for o in
    os.environ.get("FQ_ALLOWED_ORIGINS", "*").split(",") if o.strip()
]

os.makedirs(DATA_DIR, exist_ok=True)
store = Store(os.path.join(DATA_DIR, "jobs.sqlite"))
pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)

app = FastAPI(title="FrameQuality Pro QC API", version=__version__)
app.add_middleware(
    CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------


def require_key(x_api_key: str | None = Header(default=None)) -> str:
    if not API_KEYS:                      # dev mode: no keys configured
        return "dev"
    if x_api_key not in API_KEYS:
        raise HTTPException(401, "Invalid or missing X-API-Key.")
    return x_api_key


def job_dir(job_id: str) -> str:
    return os.path.join(DATA_DIR, "scans", job_id)


# --------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------


def _download(url: str, dest: str, job_id: str) -> str:
    store.update(job_id, status="downloading", stage="Fetching source file",
                 progress=2)
    req = urllib.request.Request(url, headers={"User-Agent": "FrameQualityPro/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        length = int(resp.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_GB * 1_000_000_000:
            raise ValueError(
                f"Source is {length / 1e9:.1f} GB; limit is {MAX_UPLOAD_GB:.0f} GB."
            )
        with open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh, length=8 * 1024 * 1024)
    return dest


def _notify(callback_url: str | None, payload: dict) -> None:
    """POST the result to a Bubble backend workflow endpoint."""
    if not callback_url:
        return
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            callback_url, data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=30).read()
    except Exception:                      # a failed webhook must not fail the job
        traceback.print_exc()


def process(job_id: str, media_path: str | None, source_url: str | None,
            profile: str, subtitle_path: str | None,
            expected_runtime: float | None, title: str | None,
            callback_url: str | None, public_base: str) -> None:
    work = job_dir(job_id)
    os.makedirs(work, exist_ok=True)
    try:
        if media_path is None:
            media_path = _download(
                source_url, os.path.join(work, "source.media"), job_id
            )

        def on_progress(pct: int, stage: str) -> None:
            store.update(job_id, progress=pct, stage=stage, status="scanning")

        store.update(job_id, status="scanning", stage="Starting scan", progress=3)
        report = run_scan(
            media_path, profile, work,
            subtitle_path=subtitle_path,
            expected_runtime=expected_runtime,
            title=title,
            progress=on_progress,
        )

        base = f"{public_base}/v1/scans/{job_id}/media/"
        pdf_path = os.path.join(work, "qc_report.pdf")
        build_pdf(report, pdf_path, work)
        with open(os.path.join(work, "report.html"), "w", encoding="utf-8") as fh:
            fh.write(build_html(report, media_base=base))
        with open(os.path.join(work, "report.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)

        # absolute URLs so Bubble can bind straight to them
        report["links"] = {
            "json": f"{public_base}/v1/scans/{job_id}/report",
            "pdf": f"{public_base}/v1/scans/{job_id}/report.pdf",
            "html": f"{public_base}/v1/scans/{job_id}/report.html",
        }
        for f in report["findings"]:
            for o in f.get("occurrences", []):
                if o.get("frame"):
                    o["frame_url"] = base + o["frame"]

        store.save_report(job_id, report)
        _notify(callback_url, {
            "job_id": job_id, "status": "complete",
            "score": report["score"], "links": report["links"],
        })
    except Exception as exc:
        traceback.print_exc()
        store.update(job_id, status="error", stage="failed", error=str(exc)[:1000])
        _notify(callback_url, {"job_id": job_id, "status": "error",
                               "error": str(exc)[:1000]})
    finally:
        # keep evidence frames + reports, drop the source master
        if media_path and os.path.exists(media_path) and \
                os.environ.get("FQ_KEEP_SOURCE") != "1":
            try:
                os.remove(media_path)
            except OSError:
                pass


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"ok": True, "version": __version__,
            "profiles": list(PROFILES), "workers": MAX_WORKERS}


@app.get("/v1/profiles")
def profiles(_key: str = Depends(require_key)) -> dict:
    return {"profiles": list_profiles()}


@app.post("/v1/scans")
async def submit(
    background: BackgroundTasks,
    profile: str = Form("netflix_imf"),
    title: str | None = Form(None),
    source_url: str | None = Form(None),
    callback_url: str | None = Form(None),
    expected_runtime: float | None = Form(None),
    public_base: str = Form(os.environ.get("FQ_PUBLIC_BASE", "")),
    file: UploadFile | None = File(None),
    subtitles: UploadFile | None = File(None),
    key: str = Depends(require_key),
) -> dict:
    if profile not in PROFILES:
        raise HTTPException(400, f"Unknown profile '{profile}'.")
    if not source_url and file is None:
        raise HTTPException(400, "Provide either source_url or an uploaded file.")

    job_id = store.create(
        title=title, profile=profile, source_url=source_url,
        filename=(file.filename if file else os.path.basename(source_url or "")),
        callback_url=callback_url, owner=key,
    )
    work = job_dir(job_id)
    os.makedirs(work, exist_ok=True)

    media_path = None
    if file is not None:
        media_path = os.path.join(work, os.path.basename(file.filename or "source"))
        with open(media_path, "wb") as fh:
            while chunk := await file.read(8 * 1024 * 1024):
                fh.write(chunk)

    sub_path = None
    if subtitles is not None:
        sub_path = os.path.join(work, os.path.basename(subtitles.filename or "subs.srt"))
        with open(sub_path, "wb") as fh:
            fh.write(await subtitles.read())

    pool.submit(process, job_id, media_path, source_url, profile, sub_path,
                expected_runtime, title, callback_url, public_base.rstrip("/"))

    return {"job_id": job_id, "status": "queued",
            "poll": f"/v1/scans/{job_id}"}


@app.get("/v1/scans")
def list_scans(key: str = Depends(require_key), limit: int = 50) -> dict:
    return {"scans": store.list(owner=key, limit=limit)}


@app.get("/v1/scans/{job_id}")
def get_scan(job_id: str, key: str = Depends(require_key)) -> dict:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "No such scan.")
    out = {k: job[k] for k in
           ("id", "status", "progress", "stage", "title", "profile",
            "filename", "error", "created_at", "updated_at")}
    if job.get("report"):
        out["score"] = job["report"]["score"]
        out["links"] = job["report"].get("links")
    return out


@app.get("/v1/scans/{job_id}/report")
def get_report(job_id: str, key: str = Depends(require_key)) -> JSONResponse:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "No such scan.")
    if not job.get("report"):
        raise HTTPException(409, f"Scan is {job['status']}, not complete.")
    return JSONResponse(job["report"])


@app.get("/v1/scans/{job_id}/report.pdf")
def get_pdf(job_id: str, key: str = Depends(require_key)) -> FileResponse:
    path = os.path.join(job_dir(job_id), "qc_report.pdf")
    if not os.path.exists(path):
        raise HTTPException(404, "Report not ready.")
    job = store.get(job_id) or {}
    name = (job.get("title") or "qc-report").replace("/", "-")
    return FileResponse(path, media_type="application/pdf",
                        filename=f"{name} — QC report.pdf")


@app.get("/v1/scans/{job_id}/report.html", response_class=HTMLResponse)
def get_html(job_id: str, key: str = Depends(require_key)) -> HTMLResponse:
    path = os.path.join(job_dir(job_id), "report.html")
    if not os.path.exists(path):
        raise HTTPException(404, "Report not ready.")
    with open(path, encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@app.get("/v1/scans/{job_id}/media/{sub:path}")
def get_media(job_id: str, sub: str) -> FileResponse:
    """Evidence frames. Unauthenticated on purpose — Bubble image elements and
    <img> tags in the HTML report cannot send a custom header. IDs are random
    128-bit values, so these are unguessable capability URLs."""
    if ".." in sub:
        raise HTTPException(400, "Bad path.")
    path = os.path.join(job_dir(job_id), sub)
    if not os.path.exists(path):
        raise HTTPException(404, "Not found.")
    return FileResponse(path)


@app.delete("/v1/scans/{job_id}")
def delete_scan(job_id: str, key: str = Depends(require_key)) -> dict:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "No such scan.")
    shutil.rmtree(job_dir(job_id), ignore_errors=True)
    store.update(job_id, status="deleted", report_json=None)
    return {"deleted": job_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
