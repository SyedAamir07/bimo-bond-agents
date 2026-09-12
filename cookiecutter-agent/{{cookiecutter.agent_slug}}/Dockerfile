FROM python:3.11-slim

WORKDIR /app

# Repo root is the build context (see docker-compose.yml).
COPY foundation /foundation
COPY agents/{{cookiecutter.agent_slug}} /app

RUN pip install --no-cache-dir -e /foundation && \
    pip install --no-cache-dir -r requirements.txt

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["python", "-m", "src.{{cookiecutter.agent_slug}}.main"]
