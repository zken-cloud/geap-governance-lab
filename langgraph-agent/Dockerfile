FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Cloud Run injects $PORT. One worker keeps the demo's logs readable.
ENV PORT=8080
EXPOSE 8080
CMD exec uvicorn main:app --app-dir app --host 0.0.0.0 --port ${PORT} --workers 1
