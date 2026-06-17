FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY assets/ ./assets/
COPY sounds/ ./sounds/

# Download audio + logo from GitHub (binaries excluded from HF git history)
ARG AUDIO_COMMIT=3861e69
ARG LOGO_COMMIT=cc8c051
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && mkdir -p sounds assets \
    && curl -fsSL -o sounds/bombinsound-vlog-youtube-499475.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${AUDIO_COMMIT}/sounds/bombinsound-vlog-youtube-499475.mp3" \
    && curl -fsSL -o sounds/samuelfjohanns-cinematic-duduk-192901.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${AUDIO_COMMIT}/sounds/samuelfjohanns-cinematic-duduk-192901.mp3" \
    && curl -fsSL -o sounds/samuelfjohanns-egypt-expedition-a-mysterious-discovery-119128.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${AUDIO_COMMIT}/sounds/samuelfjohanns-egypt-expedition-a-mysterious-discovery-119128.mp3" \
    && curl -fsSL -o sounds/tunetank-vlog-beat-background-349853.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${AUDIO_COMMIT}/sounds/tunetank-vlog-beat-background-349853.mp3" \
    && curl -fsSL -o assets/logo.png \
        "https://raw.githubusercontent.com/amir-devops/goldmoon-video-api/${LOGO_COMMIT}/assets/logo.png" \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
