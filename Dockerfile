FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY auto_deploy_too/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY auto_deploy_too /app/auto_deploy_too

WORKDIR /app/auto_deploy_too

EXPOSE 8000

CMD ["adk", "web", "--host", "0.0.0.0", "--port", "8000", "./deploy_agent"]
