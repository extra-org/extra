FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install the project into an agent-owned venv. Runtime user dependencies
# (mounted via /workspace/requirements.txt) are installed into the same venv
# by entrypoint.sh, so they resolve for the non-root `agent` user.
RUN python -m venv --system-site-packages /venv \
    && /venv/bin/pip install --no-cache-dir . \
    && useradd -r -s /bin/false agent \
    && mkdir -p /workspace /home/agent \
    && chown -R agent:agent /app /workspace /venv /home/agent

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PATH="/venv/bin:$PATH" HOME="/home/agent"
USER agent

WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve", "--config", "/workspace/agents.yml"]
