# 1) Build the React frontend
FROM node:22-slim AS web
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 2) Python app; the Playwright image already includes Chromium for the CSSE 313 scraper
FROM mcr.microsoft.com/playwright/python:v1.56.0-jammy
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=web /app/frontend/dist frontend/dist
WORKDIR /app/backend
ENV PYTHONUNBUFFERED=1
CMD gunicorn wsgi:app --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 4 --timeout 180
