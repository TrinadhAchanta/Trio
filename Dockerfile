# Single-container build for small hosts (e.g. Render free tier): the Next.js web app serves the
# public port and proxies /api/* to the FastAPI backend on 127.0.0.1:8000 inside the same container.
# For larger deployments use backend/Dockerfile and frontend/Dockerfile as separate services.

FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV NEXT_TELEMETRY_DISABLED=1 NEXT_OUTPUT=standalone TRIO_API_ORIGIN=http://127.0.0.1:8000
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 \
    TRIO_API_ORIGIN=http://127.0.0.1:8000 STORAGE_BACKEND=db
ARG INSTALL_FONTS=1
# DejaVu fonts give PDFs wide Unicode coverage; without them PDFs fall back to Latin-1 fonts.
RUN if [ "$INSTALL_FONTS" = 1 ]; then apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*; fi
COPY --from=web /usr/local/bin/node /usr/local/bin/node
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=web /web/.next/standalone /app/web
COPY --from=web /web/.next/static /app/web/.next/static
COPY --from=web /web/public /app/web/public
COPY scripts/start-container.sh /app/start.sh
RUN useradd --create-home trio && mkdir -p /app/backend/data/uploads && chown -R trio /app/backend/data && chmod +x /app/start.sh
USER trio
EXPOSE 10000
CMD ["/app/start.sh"]
