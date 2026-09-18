FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# A long-running container runs the full pipeline, so it needs both files.
COPY requirements.txt requirements-pipeline.txt ./
RUN pip install --no-cache-dir -r requirements-pipeline.txt

COPY . .

RUN mkdir -p drafts/blogs drafts/linkedin drafts/newsletters data

COPY start.sh .
RUN chmod +x start.sh

EXPOSE 8000

CMD ["./start.sh"]
