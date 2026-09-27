# KAN-1710 (ADR-0015): a feasibility Dockerfile for a Fly.io-hosted
# cuttlefish serve -- not deployed anywhere by this spike, built and run
# locally only, to prove the shape actually works before committing real
# infrastructure to it.
#
# Three stages: build the dashboard (frontend/dist), build kopicode from
# source (the same way CI already does, .github/workflows/ci.yml -- no
# published kopicode binary exists to just `apt install`), then assemble the
# runtime image from those two plus cuttlefish's own Python source. Multi-
# stage keeps the final image free of Node/Go toolchains it never needs again.

FROM node:22-slim AS frontend-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM golang:1.26-bookworm AS kopicode-build
RUN git clone --depth 1 https://github.com/leejianrong/kopicode.git /src/kopicode
WORKDIR /src/kopicode
RUN go build -o /usr/local/bin/kopicode ./cmd/kopicode

FROM python:3.12-slim AS runtime
# uv's own documented Docker pattern: copy the published binary rather than
# installing via pip/curl (https://docs.astral.sh/uv/guides/integration/docker/).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
# README.md too: pyproject.toml's own `readme = "README.md"` makes hatchling
# (the build backend) refuse `uv sync` outright without it (found live).
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

COPY --from=kopicode-build /usr/local/bin/kopicode /usr/local/bin/kopicode
COPY --from=frontend-build /src/frontend/dist ./frontend/dist

# ADR-0011: a Fly.io machine's own internal address is never loopback, so a
# real password is mandatory -- `fly secrets set CUTTLEFISH_SERVE_PASSWORD=...`,
# never baked into the image. ADR-0012: --dashboard-dir serves the dashboard
# built above from the same origin as the JSON API, so Fly's own edge is the
# only proxy in front of this -- no separate static-hosting story needed.
EXPOSE 8420
# --no-sync: found live -- a plain `uv run` re-resolves against the *full*
# dependency group (including `dev`, which `--no-dev` deliberately excluded at
# build time) and re-downloads it from the network on every single container
# start/wake, adding real cold-start latency and an unwanted runtime network
# dependency. The venv built above is already exactly what should run.
CMD ["/usr/local/bin/uv", "run", "--project", "/app", "--no-sync", "cuttlefish", "serve", \
     "--host", "0.0.0.0", "--dashboard-dir", "/app/frontend/dist"]
