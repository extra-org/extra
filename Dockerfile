FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN useradd -r -s /bin/false agent
RUN mkdir -p /workspace && chown -R agent:agent /app /workspace
USER agent

WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve", "--config", "/workspace/agents.yml"]
