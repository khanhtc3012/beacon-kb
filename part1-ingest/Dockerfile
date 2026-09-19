FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# tiktoken downloads its vocabulary on first use; fetch it now so a run does not depend on that download
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY main.py .
COPY app ./app

RUN useradd --create-home --uid 10001 appuser
USER appuser

CMD ["python", "main.py"]