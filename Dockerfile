FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OCR_BASE_URL=http://124.222.179.175:7777 \
    CAPTCHA_API_HOST=0.0.0.0 \
    CAPTCHA_API_PORT=8080 \
    CAPTCHA_MAX_RETRIES=8

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY api_server.py main.py ./
COPY captcha_api ./captcha_api
COPY templates ./templates
COPY tencent_captcha ./tencent_captcha
COPY scripts ./scripts
COPY tdc ./tdc

# TDC bridge: download js deps + npm install jsdom
RUN python scripts/setup_tdc.py

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["python", "api_server.py", "--host", "0.0.0.0", "--port", "8080", "-v"]
