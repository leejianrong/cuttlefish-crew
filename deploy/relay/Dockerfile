# The hosted relay (ADR-0043, CUT-135): the relay service and the dashboard build it serves from its own
# origin. Build from the repository root:  docker build -f deploy/relay/Dockerfile -t cuttlefish-relay .
#
# Two stages: build the dashboard, then a slim Python image with the relay. No kopicode, Go or Node in
# the final image: the relay holds no project data and runs no agent.

FROM node:22-slim AS dashboard
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Not root, and nothing in the image is writable but the data volume.
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin relay \
    && mkdir /data && chown relay:relay /data

WORKDIR /app
# README.md too: pyproject.toml's `readme` makes hatchling refuse `uv sync` without it.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev
COPY --from=dashboard /src/frontend/dist ./dashboard

# The deploy bundle (CUT-153): how this release is run. `relay-deploy` takes these two files out of the
# image it is deploying, so the stack's configuration reaches the Droplet only with a release.
COPY deploy/relay/compose.yaml deploy/relay/Caddyfile ./deploy-bundle/

USER relay
ENV PATH="/app/.venv/bin:$PATH" \
    CUTTLEFISH_RELAY_DB=/data/relay.db
VOLUME /data
EXPOSE 8450

# The owner's sign-in secret is CUTTLEFISH_RELAY_OWNER_SECRET, given at run time, never baked in. The
# relay trusts X-Forwarded-For because the only thing that can reach its port is the proxy in front: in
# the compose stack it is on an internal network with Caddy and is never published to the host.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8450/healthz', timeout=2).status == 200 else 1)"
CMD ["cuttlefish", "relay", "--host", "0.0.0.0", "--port", "8450", "--pages", "/app/dashboard", "--trust-forwarded"]
