FROM python:3.12-slim

WORKDIR /app

COPY SD/backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY SD/backend /app/SD/backend
COPY SD/frontend /app/SD/frontend

EXPOSE 8000

CMD ["python", "SD/backend/app.py"]
