FROM node:22-alpine AS editor-build

WORKDIR /build
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY app/static/ ./app/static/
RUN npm run build:editor

FROM ghcr.io/home-assistant/base:3.24

ARG BUILD_VERSION=dev
ARG BUILD_ARCH=amd64

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}" \
    org.opencontainers.image.source="https://github.com/ed0910439/ha-addon"

ENV \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /opt/noticeboard

COPY requirements.txt ./

RUN apk add --no-cache \
        python3 \
        py3-pip \
        py3-virtualenv \
        libjpeg-turbo \
        zlib \
        libffi \
        openssl \
    && apk add --no-cache --virtual .build-deps \
        gcc \
        musl-dev \
        python3-dev \
        libffi-dev \
        openssl-dev \
        cargo \
        jpeg-dev \
        zlib-dev \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

COPY app ./app
COPY --from=editor-build /build/app/static/editor.bundle.js ./app/static/editor.bundle.js
COPY notification_gateway ./notification_gateway
COPY ha_addon/rootfs /

RUN chmod a+x \
    /etc/cont-init.d/noticeboard-config \
    /etc/services.d/noticeboard/run \
    /etc/services.d/notification-gateway/run
