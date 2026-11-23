# Модератор в Docker (по желанию; основной способ на сервере: systemd, deploy/install.sh)
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && useradd --system --uid 1001 guard
COPY guard ./guard
USER guard
CMD ["python", "-m", "guard"]
