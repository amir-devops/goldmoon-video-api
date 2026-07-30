FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-liberation \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY render_pipeline.py .
COPY sanity_client.py .
COPY tts.py .
COPY assets/ ./assets/
COPY sounds/ ./sounds/

# Download audio + logo from GitHub (binaries excluded from HF git history)
ARG AUDIO_COMMIT=3861e69
ARG NEW_AUDIO_COMMIT=813a265d04a180ca505481d82b2ae6ef007af018
ARG LATEST_AUDIO_REF=08cda36
ARG LOGO_COMMIT=13ee8a3
ARG SUBSCRIBE_ICON_COMMIT=c8674f2
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
    && (curl -fsSL -o sounds/jonasblakewood-motivation-music-557632.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${LATEST_AUDIO_REF}/sounds/jonasblakewood-motivation-music-557632.mp3" \
        || rm -f sounds/jonasblakewood-motivation-music-557632.mp3) \
    && (curl -fsSL -o sounds/kulakovka-fashion-house-275628.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${LATEST_AUDIO_REF}/sounds/kulakovka-fashion-house-275628.mp3" \
        || rm -f sounds/kulakovka-fashion-house-275628.mp3) \
    && (curl -fsSL -o sounds/the_mountain-summer-513165.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${LATEST_AUDIO_REF}/sounds/the_mountain-summer-513165.mp3" \
        || rm -f sounds/the_mountain-summer-513165.mp3) \
    && (curl -fsSL -o sounds/the_mountain-tropical-tropical-music-508038.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${LATEST_AUDIO_REF}/sounds/the_mountain-tropical-tropical-music-508038.mp3" \
        || rm -f sounds/the_mountain-tropical-tropical-music-508038.mp3) \
    && (curl -fsSL -o sounds/white_records-short-background-music-for-video-vlog-summer-dance-tropical-house-158706.mp3 \
        "https://media.githubusercontent.com/media/amir-devops/goldmoon-video-api/${LATEST_AUDIO_REF}/sounds/white_records-short-background-music-for-video-vlog-summer-dance-tropical-house-158706.mp3" \
        || rm -f sounds/white_records-short-background-music-for-video-vlog-summer-dance-tropical-house-158706.mp3) \
    && curl -fsSL -o assets/logo.png \
        "https://raw.githubusercontent.com/amir-devops/goldmoon-video-api/${LOGO_COMMIT}/assets/logo.png" \
    && curl -fsSL -o assets/subscribe_icon.png \
        "https://raw.githubusercontent.com/amir-devops/goldmoon-video-api/${SUBSCRIBE_ICON_COMMIT}/assets/subscribe_icon.png" \
    # Bundle the brand fonts the presets expect (Oswald / Montserrat / Playfair).
    # Without these, every caption falls back to DejaVu Sans and all the preset
    # typography is lost. Each fetch is non-fatal (|| true): a static bold is
    # tried first, then the variable font, and if both fail the renderer still
    # falls back to the installed DejaVu/Liberation fonts, so the build and
    # rendering never break over a font download.
    && (curl -fsSL -o assets/Oswald-Bold.ttf \
        "https://github.com/google/fonts/raw/main/ofl/oswald/static/Oswald-Bold.ttf" \
        || curl -fsSL -o assets/Oswald-Bold.ttf \
        "https://github.com/google/fonts/raw/main/ofl/oswald/Oswald%5Bwght%5D.ttf" \
        || rm -f assets/Oswald-Bold.ttf) \
    && (curl -fsSL -o assets/Montserrat-Bold.ttf \
        "https://github.com/google/fonts/raw/main/ofl/montserrat/static/Montserrat-Bold.ttf" \
        || curl -fsSL -o assets/Montserrat-Bold.ttf \
        "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf" \
        || rm -f assets/Montserrat-Bold.ttf) \
    && (curl -fsSL -o PlayfairDisplay-Regular.ttf \
        "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/static/PlayfairDisplay-Regular.ttf" \
        || curl -fsSL -o PlayfairDisplay-Regular.ttf \
        "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf" \
        || rm -f PlayfairDisplay-Regular.ttf) \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
