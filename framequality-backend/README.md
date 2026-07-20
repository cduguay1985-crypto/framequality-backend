# FrameQuality Pro — QC backend

The engine behind the Bubble front end. A filmmaker uploads a feature, picks a
delivery target, and gets back a scored QC report with timecoded defects,
extracted evidence frames, and a specific fix for each problem.

Bubble handles accounts, uploads and page layout. This service does the analysis
and renders the report. They talk over a small REST API.

---

## What it checks

Every check produces a **pass / review / fail** result, a measured value, the
required value, and a remediation note.

| Area | Checks |
|---|---|
| **Video** (40%) | codec & wrapper, raster size, frame rate, bit depth, scan type / interlacing, legal levels, freeze frames, unexpected black, colour tags, stuck pixels |
| **Audio** (30%) | integrated loudness, true peak, loudness range, channel layout & order, bit depth / sample rate, dead or silent channels, A/V stream-length drift |
| **Subtitles** (12%) | track presence, event duration & gaps, line length & count, reading speed |
| **Structure** (18%) | container integrity (full decode), embedded timecode, head/tail black, runtime vs paperwork, metadata completeness |

### Delivery profiles

Passed as `profile` on submit. Each carries its own thresholds.

| Key | Target |
|---|---|
| `netflix_imf` | Netflix / IMF streaming — 10-bit, progressive, −27 LKFS, −2 dBTP |
| `ebu_r128` | European broadcast — −23 LUFS, −1 dBTP, interlaced permitted |
| `atsc_a85` | US broadcast / CALM Act — −24 LKFS, −2 dBTP |
| `dcp_theatrical` | Theatrical DCP — JPEG2000, 12-bit, 5.1/7.1, not loudness-normalised |
| `generic` | Festival screener / internal review — permissive baseline |

`GET /v1/profiles` returns this list for your dropdown, so adding a profile in
`qc/profiles.py` makes it appear in Bubble without a front-end change.

### Scoring

Each check carries a weight inside its category. A pass earns full weight, a
review earns 55%, a fail earns zero. Category score is the weighted percentage;
the overall score blends categories by the weights in the table above, skipping
any category that had nothing to evaluate.

- **93+ A** delivery ready · **85+ B** minor notes · **75+ C** needs work ·
  **65+ D** significant work · **below 65 F** not deliverable
- Verdict is **FAIL** if any check fails, **PASS WITH NOTES** if only reviews
  remain, **PASS** if clean. A film can score 88 and still read FAIL — one
  blocking defect is a reject no matter how good everything else is.

---

## Running it

```bash
pip install -r requirements.txt      # needs ffmpeg + ffprobe on PATH
export FQ_API_KEYS=$(openssl rand -hex 24)
export FQ_PUBLIC_BASE=https://qc.yourdomain.com
uvicorn app:app --host 0.0.0.0 --port 8000
```

Or `docker build -t framequality . && docker run -p 8000:8000 -v fqdata:/data framequality`.

Command line, no server:

```bash
python cli.py FILM.mov --profile netflix_imf --subs FILM.srt --out ./qc_out
```

Exits non-zero on FAIL, so it drops into a delivery pipeline as a gate.

### Environment

| Var | Default | Meaning |
|---|---|---|
| `FQ_API_KEYS` | *(empty = no auth)* | comma-separated keys accepted in `X-API-Key` |
| `FQ_DATA_DIR` | `./data` | job database, evidence frames, rendered reports |
| `FQ_PUBLIC_BASE` | — | public origin, used to build absolute report/frame URLs |
| `FQ_WORKERS` | `2` | concurrent scans; roughly 2–4 CPU cores each |
| `FQ_MAX_UPLOAD_GB` | `80` | rejects larger sources |
| `FQ_ALLOWED_ORIGINS` | `*` | set to your Bubble app domain in production |
| `FQ_KEEP_SOURCE` | `0` | `1` keeps the master on disk after the scan |

---

## API

All endpoints except `/health` and evidence frames want `X-API-Key`.

### `POST /v1/scans` — submit

Multipart form:

| Field | Notes |
|---|---|
| `file` | the master — **or** `source_url` instead |
| `source_url` | URL the service fetches (use this for Bubble file URLs) |
| `profile` | one of the keys above, default `netflix_imf` |
| `title` | shown on the report |
| `subtitles` | optional sidecar `.srt` / `.vtt` |
| `expected_runtime` | optional, seconds, from the delivery paperwork |
| `callback_url` | optional — POSTed on completion |

Returns `{"job_id": "...", "status": "queued", "poll": "/v1/scans/{id}"}`.

### `GET /v1/scans/{id}` — poll

```json
{"id":"a1b2","status":"scanning","progress":55,
 "stage":"Scanning for black and frozen frames"}
```

`status` moves `queued → downloading → scanning → complete` (or `error`).
When complete it also carries `score` and `links`.

### Reports

- `GET /v1/scans/{id}/report` — full JSON
- `GET /v1/scans/{id}/report.pdf` — printable report
- `GET /v1/scans/{id}/report.html` — styled HTML for an iframe
- `GET /v1/scans/{id}/media/frames/*.jpg` — evidence frames
- `GET /v1/scans` — the caller's scan history
- `DELETE /v1/scans/{id}` — purge

Evidence frames are deliberately unauthenticated: `<img>` tags and Bubble image
elements can't send a custom header. Job IDs are random 128-bit values, so the
URLs are unguessable capability links. If you need them locked down, put them
behind signed URLs or proxy them through a Bubble backend workflow.

### Report shape

```jsonc
{
  "title": "My Feature",
  "score": {
    "overall": 61.0, "grade": "F", "verdict": "FAIL",
    "verdict_detail": "10 blocking issue(s) must be corrected before delivery.",
    "categories": {"video": 64.6, "audio": 67.9, "subtitles": 28.6, "structure": 63.2},
    "counts": {"fail": 10, "warn": 2, "pass": 13, "info": 0}
  },
  "source": { /* codec, raster, fps, bit depth, audio streams, timecode … */ },
  "findings": [{
    "check": "loudness_integrated", "label": "Integrated loudness",
    "category": "audio", "status": "fail", "weight": 12,
    "message": "Integrated loudness -15.8 LUFS is +11.2 dB off target…",
    "measured": "-15.8 LUFS", "expected": "-27 ±2 LKFS",
    "fix": "Apply a single static gain trim to the full mix…",
    "occurrences": [{"seconds": 8.4, "timecode": "00:00:08:09",
                     "note": "luma 236 (legal 64–940)",
                     "frame": "frames/002_levels_8s.jpg",
                     "frame_url": "https://…/media/frames/002_levels_8s.jpg"}]
  }],
  "action_list": [ /* fails then warnings, priority-ordered, ready to repeat over */ ]
}
```

`action_list` is the one to bind to in Bubble — it's already sorted worst-first
and stripped of everything that passed.

---

## Wiring it into Bubble

### 1. API Connector

Install the **API Connector** plugin, add an API named `FrameQuality`, shared
header `X-API-Key` = your key. Mark it **Private** so the key stays server-side.

Four calls:

| Name | Method | URL | Type |
|---|---|---|---|
| `submit scan` | POST | `https://qc.…/v1/scans` | Form-data. Params: `file` (file, send as file), `profile`, `title`, `source_url`, `callback_url` |
| `get scan` | GET | `https://qc.…/v1/scans/[job_id]` | job_id as a URL parameter |
| `get report` | GET | `https://qc.…/v1/scans/[job_id]/report` | Use "Data" so you can bind to `action_list` |
| `list profiles` | GET | `https://qc.…/v1/profiles` | Data — feeds the dropdown |

Initialize each against a real completed scan so Bubble learns the field types.
Set `action_list` and `findings` to **list of texts/objects**, not text.

### 2. Data types

**Film** — `name`, `file` (file), `subtitle_file`, `profile` (text),
`current_scan` (Scan), `owner` (User)

**Scan** — `job_id` (text), `film` (Film), `status`, `progress` (number),
`stage`, `overall_score` (number), `grade`, `verdict`, `video_score`,
`audio_score`, `subtitle_score`, `structure_score`, `fail_count`, `warn_count`,
`report_pdf_url`, `report_html_url`, `raw_report` (text — stash the JSON)

### 3. Upload → submit

Two options, and the second is the one you want for features:

**Direct upload through Bubble** — FileUploader → on click, API Connector
`submit scan` with `file = Input's value`. Simple, but Bubble proxies the whole
master, which is slow and hits plan limits on a 60 GB ProRes file.

**Pass a URL instead (recommended)** — let Bubble store the file (or upload
straight to S3), then send `source_url = Film's file:URL`. The QC service pulls
it directly. Prefix Bubble's protocol-relative URLs with `https:` — they come
back as `//s3.amazonaws.com/…` and the fetch will fail without it.

Then: Create a new **Scan** with `job_id = Result of step 1's job_id`,
`status = "queued"`.

### 4. Progress

Add a **"Do every 5 seconds"** workflow on the scan page, only while
`Current Scan's status is not "complete"`:

1. API Connector → `get scan` with `job_id = Current Scan's job_id`
2. Make changes to Current Scan: `status`, `progress`, `stage` from the result
3. Only when `status = "complete"` → `get report`, then write the scores across
   and set `report_pdf_url` to `…/v1/scans/[job_id]/report.pdf`

Bind a progress bar to `Current Scan's progress` and a text element to `stage`.
A feature takes roughly 0.5–2× runtime to scan depending on codec and cores, so
the stage text matters — a two-hour ProRes master is not a spinner-and-wait.

**Better than polling:** set `callback_url` on submit to a Bubble **backend
workflow** exposed as a public API endpoint. The service POSTs
`{job_id, status, score, links}` the moment the scan finishes, and you update the
Scan record from there. Keep the 5-second poll as a fallback for missed webhooks.

### 5. Displaying the report

Easiest: an **HTML element** containing
`<iframe src="https://qc.…/v1/scans/[job_id]/report.html" style="width:100%;height:1400px;border:0"></iframe>`.
Whole report, evidence frames included, nothing to build.

Native Bubble version: `get report` returns `action_list`, so drop a
**RepeatingGroup** of type `action_list` over it and show `issue`, `detail`,
`fix`, and `at` (the timecodes). Add an Image element bound to
`findings:first item's occurrences:first item's frame_url` for the evidence
frames. Score rings can be Shape elements with width bound to the category score.

**Print / download** — a button with "Open an external website" pointed at
`…/report.pdf` gives the user the printable version. Don't try to render the
PDF inside Bubble; the service already renders it properly with the frames
embedded.

---

## Production notes

- **CPU is the bottleneck.** A full-decode scan of a two-hour master is a real
  workload. Budget 2–4 cores per concurrent scan and size `FQ_WORKERS` to
  `cores / 3`. Put this on its own box, not alongside anything latency-sensitive.
- **Disk.** Sources are deleted after each scan by default; evidence frames and
  reports stay. Budget a few MB per scan plus peak source size × `FQ_WORKERS`.
- **Timeouts.** If you front this with nginx or a load balancer, raise the
  proxy read timeout — uploads of large masters take a while. The scan itself is
  async, so no request waits on it.
- **Scaling out.** The thread pool is in-process. Past a handful of concurrent
  scans, move to Redis + RQ/Celery workers; the `process()` function in `app.py`
  is already shaped like a task and will lift out cleanly.
- **What this doesn't do.** No full IMF/DCP package validation (needs the whole
  package, not a flat file), no caption text QC, no A/V sync measurement against
  a reference (it only flags stream-length drift), and no perceptual artefact
  detection — banding, macroblocking and compression artefacts still need eyes.
  Sell this as automated first-pass QC that catches the mechanical rejects, not
  as a replacement for a QC operator.

---

## Layout

```
qc/profiles.py   delivery specs, check weights, remediation copy
qc/probe.py      ffmpeg/ffprobe wrappers — the only place that shells out
qc/engine.py     checks, scoring, orchestration
qc/report.py     PDF (ReportLab) and HTML rendering
qc/store.py      SQLite job state
app.py           FastAPI service
cli.py           command-line runner
```

Adding a check: write the measurement in `probe.py`, register weight and fix
text in `CHECKS` in `profiles.py`, call `ctx.add(...)` from a check function in
`engine.py`. Scoring, both report formats and the API pick it up automatically.
