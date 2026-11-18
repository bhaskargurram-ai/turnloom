# An isolated place to run Turnloom unattended: the agent sees only the project you mount at /workspace.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/turnloom
COPY pyproject.toml README.md LICENSE ./
COPY turnloom ./turnloom
RUN pip install --no-cache-dir ".[mcp,skills]" \
    && useradd --create-home --uid 1000 agent \
    && mkdir /workspace && chown agent:agent /workspace

USER agent
WORKDIR /workspace
# The image has no credential store; pass TURNLOOM_ENDPOINT, TURNLOOM_MODEL and TURNLOOM_API_KEY.
ENV PYTHONUNBUFFERED=1 GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/workspace
ENTRYPOINT ["turnloom"]
CMD ["--help"]
