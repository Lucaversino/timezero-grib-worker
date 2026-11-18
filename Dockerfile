FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=10000
WORKDIR /app
RUN useradd --create-home --uid 10001 worker
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY grib_generator.py server.py ./
USER worker
EXPOSE 10000
CMD ["python", "server.py"]
