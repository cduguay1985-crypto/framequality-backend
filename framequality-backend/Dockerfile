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
VOLUME ["/data"]
EXPOSE 8000

# One uvicorn worker: scans run in an internal thread pool, and multiple
# uvicorn workers would each spawn their own pool and oversubscribe the CPU.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 120"]
