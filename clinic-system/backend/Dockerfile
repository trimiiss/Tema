FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Shell form on purpose: `${PORT}` has to be expanded at container start.
# Render (and most hosts) assign a port through that variable and route to it;
# a hardcoded 8000 means the platform health-checks a port nothing is
# listening on and the deploy is marked failed. The default keeps
# `docker compose up` and a bare `docker run` working unchanged.
#
# No `--reload` here: it is a development flag that watches the filesystem and
# holds a second process open. Local dev gets it from the `command:` override
# in docker-compose.yml, which is also where the source is bind-mounted for it
# to watch.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
