# syntax=docker/dockerfile:1.7
# Community Edition SPA: nginx serves static bundle and proxies API routes.
# Build context: selfhost/ (repo root for this edition).

ARG NODE_VERSION=20
ARG VERSION=1.0.0
ARG VCS_REF=unknown
ARG BUILD_DATE=

FROM node:${NODE_VERSION}-alpine AS builder
WORKDIR /app

COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY web/ .

ENV VITE_API_BASE=/ \
    NODE_ENV=production
RUN npm run build

FROM nginx:1.27-alpine AS runtime

ARG VERSION=1.0.0
ARG VCS_REF=unknown
ARG BUILD_DATE=

LABEL org.opencontainers.image.title="Exlogare Community Edition Web" \
      org.opencontainers.image.description="nginx + SPA dashboard for Exlogare Community Edition" \
      org.opencontainers.image.url="https://exlogare.net" \
      org.opencontainers.image.source="https://github.com/exlogare/exlogare" \
      org.opencontainers.image.documentation="https://exlogare.net/docs" \
      org.opencontainers.image.vendor="Exlogare" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      io.exlogare.edition="community" \
      io.exlogare.component="web"

COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
