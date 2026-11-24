# Stage 1: Builder
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt \
    && python -m pip check

COPY . .

RUN SECRET_KEY=build-only-placeholder \
    python manage.py compilemessages

# Remove pip from virtual environment
RUN python -m pip uninstall -y pip


# Stage 2: Runtime
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Remove system pip and bundled ensurepip files
RUN python -m pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.13/ensurepip

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

EXPOSE 8000

CMD ["gunicorn", "smartkitap.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
