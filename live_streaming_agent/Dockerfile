FROM python:3.11-slim

WORKDIR /app

# foundation/ is copied in alongside the agent so the editable install works
COPY foundation /foundation
COPY live_streaming_agent /app

RUN pip install --no-cache-dir -e /foundation && \
    pip install --no-cache-dir -r requirements.txt

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["python", "-m", "src.live_streaming_agent.main"]