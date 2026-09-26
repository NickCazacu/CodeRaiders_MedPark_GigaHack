FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-service.txt .
RUN pip install --no-cache-dir -r requirements-service.txt \
    && pip install --no-cache-dir --no-deps "silero-vad>=5.1"
COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.preprocess_service:app", "--host", "0.0.0.0", "--port", "8000"]
