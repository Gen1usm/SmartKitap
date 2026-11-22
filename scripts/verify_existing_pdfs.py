"""Verify existing linked books on the live server without creating any PDFs.

Optional dependency: playwright. The password is prompted or supplied through
SMARTKITAP_TEST_PASSWORD, and is never stored in a file or included in reports.
"""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from time import monotonic

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartkitap.settings')

import django
django.setup()

from library.models import Book


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--username', default='abai.korkem')
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    password = os.environ.get('SMARTKITAP_TEST_PASSWORD') or getpass.getpass('Student password: ')
    mapping = json.loads((ROOT / 'library/data/pdf_book_mapping.json').read_text(encoding='utf-8'))
    books = list(Book.objects.filter(title__in=mapping).values('id', 'title', 'author'))
    output = ROOT / 'artifacts/pdf-linking'
    output.mkdir(parents=True, exist_ok=True)
    report = {'books': [], 'page_errors': [], 'byte_range_requests': [], 'fullscreen': False, 'mobile': False}
    base = args.base_url.rstrip('/')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chromium', headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='kk-KZ')
        page = context.new_page()
        page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
        page.on('response', lambda response: report['byte_range_requests'].append(
            {'url': response.url, 'range': response.headers.get('content-range')})
            if response.status == 206 and '/content/' in response.url else None)
        page.goto(base + '/login/')
        page.locator('[name=username]').fill(args.username)
        page.locator('[name=password]').fill(password)
        page.locator('main button[type=submit]').click()
        page.wait_for_url('**/account/')
        page.goto(base + '/books/')
        expect(page.locator('main a[href$="/read/"]')).to_have_count(len(books))
        page.screenshot(path=str(output / 'catalog-linked.png'), full_page=True)

        for book in books:
            filename = mapping[book['title']]
            source = ROOT / 'media/books' / filename
            started = monotonic()
            response = page.goto(base + f'/books/{book["id"]}/read/')
            assert response.status == 200
            expect(page.locator('h2')).to_have_text(book['title'])
            expect(page.locator('main')).to_contain_text(book['author'])
            expect(page.locator('main')).to_contain_text('Каталогқа оралу')
            saved_position = page.locator('[name=position]').input_value()
            expect(page.locator('iframe')).to_have_attribute('src', f'/books/{book["id"]}/content/#page={saved_position}&view=FitH')

            # Authenticate each byte request through the browser's cookie jar.
            for suffix, expected in [('bytes=0-4', b'%PDF-')]:
                payload = context.request.get(base + f'/books/{book["id"]}/content/', headers={'Range': suffix})
                assert payload.status == 206 and payload.body() == expected
                assert payload.headers['content-range'] == f'bytes 0-4/{source.stat().st_size}'
            with source.open('rb') as file:
                file.seek(-32, 2)
                expected_tail = file.read()
            payload = context.request.get(base + '/media/books/' + filename, headers={'Range': 'bytes=-32'})
            assert payload.status == 206 and payload.body() == expected_tail

            # Wait for Chromium's PDF viewer, whose rendering follows iframe load.
            page.wait_for_timeout(2000)
            viewer = next((frame for frame in page.frames if frame.url.startswith('chrome-extension://')), None)
            assert viewer is not None, f'No browser PDF viewer for {book["title"]}'
            expect(viewer.locator('pdf-viewer')).to_be_visible(timeout=20000)
            viewer.wait_for_function("() => document.querySelector('pdf-viewer')?.loadState_ === 'success'", timeout=60000)
            page_count = viewer.evaluate("document.querySelector('pdf-viewer').shadowRoot.querySelector('viewer-toolbar').docLength")
            assert page_count > 0
            if book == books[0]:
                page_selector = viewer.locator('#pageSelector')
                page_selector.fill('2')
                page_selector.press('Enter')
                expect(page_selector).to_have_value('2')
                report['page_navigation'] = True
                zoom = viewer.locator('input[aria-label="Масштаб"]')
                zoom.fill('125%')
                zoom.press('Enter')
                expect(zoom).to_have_value('125%')
                report['zoom'] = True
            report['books'].append({'title': book['title'], 'file': 'media/books/' + filename,
                                    'http_status': response.status, 'native_viewer': True,
                                    'native_page_count': page_count,
                                    'elapsed_seconds': round(monotonic() - started, 2)})
            print(f'Verified: {book["title"]} -> {filename}', flush=True)
            if filename in ['abay_zholy.pdf', 'botagoz.pdf', 'ulpan.pdf']:
                page.screenshot(path=str(output / ('reader-' + source.stem + '.png')), full_page=True)

        page.goto(base + f'/books/{books[0]["id"]}/read/')
        page.wait_for_timeout(1500)
        viewer = next(frame for frame in page.frames if frame.url.startswith('chrome-extension://'))
        viewer.wait_for_function("() => document.querySelector('pdf-viewer')?.loadState_ === 'success'", timeout=20000)
        if page.locator('#pdf-fullscreen').is_visible():
            page.locator('#pdf-fullscreen').click()
            page.wait_for_function('Boolean(document.fullscreenElement)')
            page.screenshot(path=str(output / 'reader-fullscreen.png'))
            page.locator('#pdf-fullscreen').click()
            page.wait_for_function('!document.fullscreenElement')
            report['fullscreen'] = True
        page.set_viewport_size({'width': 390, 'height': 844})
        # Reload at mobile width to verify the initial fit-to-width PDF layout.
        page.reload()
        page.wait_for_timeout(1500)
        viewer = next(frame for frame in page.frames if frame.url.startswith('chrome-extension://'))
        viewer.wait_for_function("() => document.querySelector('pdf-viewer')?.loadState_ === 'success'", timeout=20000)
        expect(page.locator('iframe')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path=str(output / 'reader-mobile.png'), full_page=True)
        report['mobile'] = True
        page.locator('button[name=language][value=ru]').click()
        expect(page.locator('main')).to_contain_text('Вернуться в каталог')
        report['russian'] = True

        # Every path variant must protect the same original PDF from anonymous access.
        guest = browser.new_context()
        for path in ['/media/books/abay_zholy.pdf', '/media//books/abay_zholy.pdf',
                     '/media/other/../books/abay_zholy.pdf', '/media/./books/abay_zholy.pdf']:
            response = guest.request.get(base + path, headers={'Range': 'bytes=0-4'}, max_redirects=0)
            assert response.status not in [200, 206], (path, response.status)
        report['anonymous_media_access_blocked'] = True
        assert not report['page_errors'], report['page_errors']
        page.locator('form[action="/logout/"] button').click()
        guest.close()
        browser.close()
    (output / 'browser-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'books_verified': len(report['books']), 'fullscreen': report['fullscreen'],
                      'mobile': report['mobile'], 'page_errors': report['page_errors']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
