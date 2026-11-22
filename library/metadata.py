"""Optional Open Library metadata; no full-text downloads or arbitrary URLs."""
import json
import hashlib
import re
from io import BytesIO
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.core.cache import cache
from django.core.files.base import ContentFile
from PIL import Image


def fetch(url, limit):
    request = Request(url, headers={'User-Agent': 'SmartKitap/1.0 (school-library metadata lookup)'})
    with urlopen(request, timeout=8) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('API response too large')
    return data


def search_metadata(query):
    cache_key = 'openlibrary-search:' + hashlib.sha256(query.encode('utf-8')).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    params = urlencode({'q': query, 'limit': 6, 'fields': 'key,title,author_name,first_publish_year,subject,cover_i'})
    payload = json.loads(fetch('https://openlibrary.org/search.json?' + params, 2 * 1024 * 1024))
    results = []
    for item in payload.get('docs', [])[:6]:
        work_id = str(item.get('key', '')).removeprefix('/works/')
        if not re.fullmatch(r'OL\d+W', work_id):
            continue
        cover_id = item.get('cover_i')
        if not isinstance(cover_id, int) or cover_id < 1:
            cover_id = None
        results.append({'id': work_id, 'title': str(item.get('title', ''))[:200],
                        'author': ', '.join(str(name) for name in item.get('author_name', []))[:160],
                        'publication_year': item.get('first_publish_year'),
                        'genre': ', '.join(str(name) for name in item.get('subject', [])[:2])[:100],
                        'cover_id': cover_id,
                        'cover_url': f'https://covers.openlibrary.org/b/id/{cover_id}-M.jpg' if cover_id else ''})
    cache.set(cache_key, results, 3600)
    return results


def work_description(work_id):
    if not re.fullmatch(r'OL\d+W', work_id):
        raise ValueError('Invalid Open Library work ID')
    key = 'openlibrary-work:' + work_id
    cached = cache.get(key)
    if cached is not None:
        return cached
    payload = json.loads(fetch(f'https://openlibrary.org/works/{work_id}.json', 2 * 1024 * 1024))
    value = payload.get('description', '')
    if isinstance(value, dict):
        value = value.get('value', '')
    description = str(value)[:10000]
    cache.set(key, description, 3600)
    return description


def download_cover(cover_id):
    if not isinstance(cover_id, int) or not 1 <= cover_id <= 10**12:
        raise ValueError('Invalid cover ID')
    data = fetch(f'https://covers.openlibrary.org/b/id/{cover_id}-L.jpg?default=false', 2 * 1024 * 1024)
    with Image.open(BytesIO(data)) as image:
        if image.format != 'JPEG':
            raise ValueError('Invalid cover format')
        image.verify()
    return ContentFile(data, name=f'openlibrary_{cover_id}.jpg')
