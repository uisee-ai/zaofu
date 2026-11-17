# Build from the repository root: docker build -f deploy/cloud/Dockerfile .
FROM node:24.14.1-bookworm-slim@sha256:b506e7321f176aae77317f99d67a24b272c1f09f1d10f1761f2773447d8da26c AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git tmux procps ripgrep tini \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.9.10 \
    && groupadd --gid 1000 zaofu \
    && useradd --uid 1000 --gid 1000 --home-dir /workspace/home --shell /bin/bash zaofu
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=web /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
WORKDIR /opt/zaofu
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY skills/ ./skills/
COPY --from=web /build/web/dist ./web/dist/
RUN uv sync --frozen --no-dev --extra web --extra controller --extra stream-json \
    && uv cache clean
ENV PATH="/opt/zaofu/.venv/bin:/workspace/home/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    HOME="/workspace/home" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /workspace
USER 1000:1000
EXPOSE 8443
ENTRYPOINT ["/usr/bin/tini", "--", "zf"]
CMD ["controller", "serve", "--workspace-config", "/etc/zaofu/zf.yaml", "--host", "0.0.0.0", "--port", "8443", "--ssl-keyfile", "/etc/zaofu/tls/tls.key", "--ssl-certfile", "/etc/zaofu/tls/tls.crt"]
