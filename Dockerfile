FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN SECRET_KEY=build-only-placeholder python manage.py compilemessages

EXPOSE 8000

CMD ["gunicorn", "smartkitap.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
