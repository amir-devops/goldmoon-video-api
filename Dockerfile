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
COPY render_pipeline.py .
COPY sanity_client.py .
COPY assets/ ./assets/
COPY sounds/ ./sounds/

# Download audio + logo from GitHub (binaries excluded from HF git history)
ARG AUDIO_COMMIT=3861e69
ARG NEW_AUDIO_COMMIT=813a265d04a180ca505481d82b2ae6ef007af018
ARG LATEST_AUDIO_REF=main
ARG LOGO_COMMIT=13ee8a3
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
    && curl -fsSL -o sounds/alex-morgan-arab-trailer-545516.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${NEW_AUDIO_COMMIT}/sounds/alex-morgan-arab-trailer-545516.mp3" \
    && curl -fsSL -o sounds/elijah_k-cairo-500585.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${NEW_AUDIO_COMMIT}/sounds/elijah_k-cairo-500585.mp3" \
    && curl -fsSL -o sounds/gr0za-egyptian-egypt-desert-music-557539.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${NEW_AUDIO_COMMIT}/sounds/gr0za-egyptian-egypt-desert-music-557539.mp3" \
    && curl -fsSL -o sounds/grand_project-desert-travels-391123.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${NEW_AUDIO_COMMIT}/sounds/grand_project-desert-travels-391123.mp3" \
    && curl -fsSL -o sounds/onetent-ancient-181070.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${NEW_AUDIO_COMMIT}/sounds/onetent-ancient-181070.mp3" \
    && curl -fsSL -o sounds/the_mountain-ancient-empire-142301.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${NEW_AUDIO_COMMIT}/sounds/the_mountain-ancient-empire-142301.mp3" \
    && curl -fsSL -o sounds/jonasblakewood-motivation-music-557632.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${LATEST_AUDIO_REF}/sounds/jonasblakewood-motivation-music-557632.mp3" \
    && curl -fsSL -o sounds/kulakovka-fashion-house-275628.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${LATEST_AUDIO_REF}/sounds/kulakovka-fashion-house-275628.mp3" \
    && curl -fsSL -o sounds/the_mountain-summer-513165.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${LATEST_AUDIO_REF}/sounds/the_mountain-summer-513165.mp3" \
    && curl -fsSL -o sounds/the_mountain-tropical-tropical-music-508038.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${LATEST_AUDIO_REF}/sounds/the_mountain-tropical-tropical-music-508038.mp3" \
    && curl -fsSL -o sounds/white_records-short-background-music-for-video-vlog-summer-dance-tropical-house-158706.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${LATEST_AUDIO_REF}/sounds/white_records-short-background-music-for-video-vlog-summer-dance-tropical-house-158706.mp3" \
    && curl -fsSL -o assets/logo.png \
        "https://raw.githubusercontent.com/amir-devops/goldmoon-video-api/${LOGO_COMMIT}/assets/logo.png" \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
