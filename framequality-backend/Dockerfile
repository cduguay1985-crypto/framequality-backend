FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY qc/ ./qc/
COPY app.py cli.py ./

ENV FQ_DATA_DIR=/data \
    FQ_WORKERS=2 \
    PORT=8000
EXPOSE 8000

# Job history, evidence frames and rendered reports live in /data and must
# survive a redeploy. Attach persistent storage mounted at /data:
#   Railway  — add a Volume on the service, mount path /data (VOLUME is not
#              supported in the Dockerfile and will fail the build)
#   Render   — add a Disk, mount path /data
#   Docker   — docker run -v fqdata:/data ...
# Without it the service still runs, but past scans disappear on each deploy.

# One uvicorn worker: scans run in an internal thread pool, and multiple
# uvicorn workers would each spawn their own pool and oversubscribe the CPU.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 120"]
