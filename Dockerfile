FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=0 \
    APP_ROLE=ui

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnss3 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxcursor1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxi6 \
        libxrandr2 \
        libxrender1 \
        libxshmfence1 \
        libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY prompts ./prompts
COPY docs ./docs
COPY streamlit_app.py ./

RUN python -m pip install --upgrade pip \
    && pip install . \
    && python -m playwright install chromium

EXPOSE 7860

CMD ["sh", "-c", "if [ \"$APP_ROLE\" = \"worker\" ]; then uvicorn humanonn.worker_service:app --host 0.0.0.0 --port ${PORT:-7860}; else streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port ${PORT:-7860}; fi"]