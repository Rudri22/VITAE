FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY SD/backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY SD/backend /app/SD/backend
COPY SD/frontend /app/SD/frontend

RUN useradd --create-home --uid 10001 vitae \
    && chown -R vitae:vitae /app

USER vitae

EXPOSE 8000

CMD ["python", "-m", "SD.backend.app"]
