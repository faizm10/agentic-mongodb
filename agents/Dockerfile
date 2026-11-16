# OpsRelay investigation worker. Build from the repo root:
#   docker build -f agents/Dockerfile --build-arg OPSRELAY_RELEASE=$(git rev-parse --short HEAD) -t opsrelay-agent-worker .
# The same image is the verifier image (OPSRELAY_VERIFIER_IMAGE): it has pytest and the apps' test deps.
FROM docker:27.5.1-cli AS docker-cli

FROM python:3.12-slim
ARG OPSRELAY_RELEASE=dev
ENV OPSRELAY_RELEASE=${OPSRELAY_RELEASE} PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    OPSRELAY_ENV=production OPSRELAY_REPO_PATH=/repo OPSRELAY_HEARTBEAT_FILE=/tmp/opsrelay-heartbeat.json
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home opsrelay \
    && mkdir /repo && chown opsrelay /repo
# Only used when the verifier runs in Docker (auto with an image, or =docker), which points DOCKER_HOST at a separate runner host.
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /app
COPY agents/requirements.lock agents/requirements.lock
RUN pip install --no-cache-dir -r agents/requirements.lock
COPY agents/ agents/
USER opsrelay
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 CMD ["python", "-m", "agents.worker", "health"]
CMD ["python", "-m", "agents.worker", "run"]
