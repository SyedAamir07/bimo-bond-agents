FROM python:3.11-slim

WORKDIR /app

# foundation/ is copied in alongside the agent so the editable install works
COPY foundation /foundation
COPY {{cookiecutter.agent_slug}} /app

RUN pip install --no-cache-dir -e /foundation && \
    pip install --no-cache-dir -r requirements.txt

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["python", "-m", "src.{{cookiecutter.agent_slug}}.main"]
