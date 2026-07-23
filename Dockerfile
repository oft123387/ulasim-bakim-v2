# syntax=docker/dockerfile:1

# ============================================================
# 1. Aşama — Arayüzü (React/Vite) derle
# ============================================================
FROM node:20-slim AS frontend
WORKDIR /app/frontend

# Önce sadece bağımlılık dosyaları (katman önbelleği için)
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

# Kaynakları kopyala ve üretim derlemesini üret (frontend/dist)
COPY frontend/ ./
RUN npm run build


# ============================================================
# 2. Aşama — Backend (FastAPI) çalışma ortamı
# ============================================================
FROM python:3.12-slim AS backend
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app/backend

# Python bağımlılıkları
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Backend kaynakları
COPY backend/ ./

# Derlenmiş arayüz -> /app/frontend/dist (app/main.py buradan sunar)
COPY --from=frontend /app/frontend/dist /app/frontend/dist

# Başlatma betiği
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
