"""Optional browser QA on an isolated database; never modifies the school data.

Run: python -m pip install playwright
     python -m playwright install chromium
     python scripts/browser_smoke.py
"""
import json
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartkitap.settings')

from django.conf import settings

temporary = tempfile.TemporaryDirectory(prefix='smartkitap-browser-qa-')
settings.DATABASES['default']['NAME'] = Path(temporary.name) / 'qa.sqlite3'
settings.PRIVATE_MEDIA_ROOT = Path(temporary.name) / 'private'
settings.MEDIA_ROOT = Path(temporary.name) / 'public'
settings.ALLOWED_HOSTS = ['127.0.0.1']
settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

import django
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.db import close_old_connections, connections
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from library.models import Book, BookFile, BookRequest, Loan, ReadingProgress, Reader, StudentProfile
from library.services import request_book, review_request
from library.test_students import epub_upload, pdf_upload


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def db_call(callback):
    # Playwright's synchronous API still runs an event loop on its own thread.
    def invoke():
        try:
            return callback()
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(invoke).result()


def run():
    call_command('migrate', verbosity=0)
    get_user_model().objects.create_superuser('qa_librarian', password='Library-QA!123', email='qa@example.test')
    book = Book.objects.create(title='Оқу әлемі', author='SmartKitap', genre='Әңгіме', publication_year=2026,
                                description='Браузердегі оқуды тексеруге арналған кітап.', total_copies=1, available_copies=1)
    epub = Book.objects.create(title='EPUB оқу', author='SmartKitap', genre='Әңгіме', publication_year=2026,
                                total_copies=0, available_copies=0)
    digital = BookFile(book=epub, file=epub_upload(), rights_confirmed=True)
    digital.full_clean()
    digital.save()
    output = ROOT / 'artifacts' / 'browser-qa'
    output.mkdir(parents=True, exist_ok=True)
    server = make_server('127.0.0.1', 0, StaticFilesHandler(get_wsgi_application()), handler_class=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    page_errors = []
    checks = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chromium', headless=True)
            student_context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='kk-KZ')
            page = student_context.new_page()
            page.on('pageerror', lambda error: page_errors.append(str(error)))
            page.goto(base + '/register/')
            for field, value in {'full_name': 'Тест Оқушы', 'class_name': '7А', 'username': 'qa_student',
                                  'contact': 'qa_student@example.test', 'password1': 'RiverOcean!9284', 'password2': 'RiverOcean!9284'}.items():
                page.locator(f'[name="{field}"]').fill(value)
            page.locator('main button[type=submit], main .form-actions button').click()
            try:
                page.wait_for_url('**/account/', timeout=10000)
            except Exception:
                page.screenshot(path=str(output / 'registration-error.png'), full_page=True)
                print(page.locator('main').inner_text())
                raise
            expect(page.locator('#welcome-heading')).to_contain_text('Тест Оқушы')
            checks.append('Student registration with real CSRF and Kazakh dashboard')
            page.goto(base + '/books/')
            expect(page.locator('main')).not_to_contain_text('Кітап қосу')
            page.locator(f'form[action="/books/{book.pk}/request/"] button').click()
            page.wait_for_url('**/my-requests/')
            expect(page.locator('main')).to_contain_text('Күтуде')
            checks.append('Student submits request and sees pending status')

            staff_context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='kk-KZ')
            staff = staff_context.new_page()
            staff.on('pageerror', lambda error: page_errors.append(str(error)))
            staff.goto(base + '/login/')
            staff.locator('[name=username]').fill('qa_librarian')
            staff.locator('[name=password]').fill('Library-QA!123')
            staff.locator('main button[type=submit]').click()
            staff.wait_for_url(base + '/')
            staff.goto(base + '/requests/')
            staff.locator('button[value=approve]').click()
            expect(staff.locator('main')).to_contain_text('Қабылданды')
            staff.screenshot(path=str(output / 'requests-desktop.png'), full_page=True)
            staff.locator('button[value=issue]').click()
            expect(staff.locator('main')).to_contain_text('Берілді')
            db_call(book.refresh_from_db)
            assert book.available_copies == 0 and db_call(Loan.objects.count) == 1
            page.reload()
            expect(page.locator('main')).to_contain_text('Берілді')
            checks.append('Staff approves and issues; student sees update; stock is consistent')
            staff.goto(base + '/loans/')
            staff.locator('form[action$="/return/"] button').click()
            db_call(book.refresh_from_db)
            assert book.available_copies == 1 and db_call(lambda: BookRequest.objects.get().status) == 'returned'
            checks.append('Return restores stock and request history')

            staff.goto(base + f'/books/{book.pk}/file/')
            file = pdf_upload()
            staff.locator('input[type=file]').set_input_files({'name': 'qa.pdf', 'mimeType': 'application/pdf', 'buffer': file.read()})
            staff.locator('[name=rights_confirmed]').check()
            staff.locator('main .form-actions button').click()
            staff.wait_for_url('**/books/')
            page.goto(base + f'/books/{book.pk}/read/')
            expect(page.locator('iframe')).to_be_visible()
            page.locator('[name=position]').fill('2')
            page.locator('main form button').click()
            expect(page.locator('iframe')).to_have_attribute('src', f'/books/{book.pk}/content/#page=2&view=FitH')
            assert db_call(lambda: ReadingProgress.objects.get().position) == 2
            # The browser PDF plugin paints after the outer page's load event.
            page.wait_for_timeout(2000)
            page.screenshot(path=str(output / 'pdf-reader.png'), full_page=True)
            checks.append('PDF upload, embedded Chrome viewer, saved page and reload')

            page.goto(base + f'/books/{epub.pk}/read/')
            expect(page.locator('.epub-reader')).to_contain_text('First chapter')
            page.locator('main a', has_text='Алға').click()
            expect(page.locator('.epub-reader')).to_contain_text('Second chapter')
            page.locator('main form button').click()
            expect(page.locator('.epub-reader')).to_contain_text('Second chapter')
            checks.append('EPUB chapter navigation and saved chapter')
            page.goto(base + '/books/')
            page.screenshot(path=str(output / 'catalog-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(page.locator('.mobile-nav')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path=str(output / 'catalog-mobile.png'), full_page=True)
            page.locator('button[name=language][value=ru]').click()
            expect(page.locator('.page-heading h2')).to_contain_text('Каталог книг')
            checks.append('Mobile layout and Russian language switch')

            staff.goto(base + '/students/')
            staff.locator('main a[href^="/students/"][href$="/edit/"]').click()
            staff.locator('[name=contact]').fill('updated@example.test')
            staff.locator('main .form-actions button').click()
            expect(staff.locator('main')).to_contain_text('updated@example.test')
            checks.append('Staff manages student profile')
            assert not page_errors, page_errors
            browser.close()

        # Exercise simultaneous approvals against a real SQLite file.
        race_book = Book.objects.create(title='One copy', author='QA', genre='Test', publication_year=2026,
                                        total_copies=1, available_copies=1)
        first = StudentProfile.objects.get(user__username='qa_student')
        user = get_user_model().objects.create_user('qa_second', password='Test!234')
        second = StudentProfile.objects.create(user=user, reader=Reader.objects.create(full_name='Second Student', class_name='7A'))
        items = [request_book(first, race_book.pk), request_book(second, race_book.pk)]
        barrier = threading.Barrier(2)

        def approve(request_id):
            close_old_connections()
            barrier.wait(timeout=10)
            try:
                review_request(request_id, 'approve')
                return 'approved'
            except django.core.exceptions.ValidationError:
                return 'unavailable'
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(approve, [item.pk for item in items]))
        assert sorted(outcomes) == ['approved', 'unavailable'], outcomes
        race_book.refresh_from_db()
        assert race_book.available_copies == 0
        checks.append('Concurrent SQLite approvals reserve the last copy exactly once')
        (output / 'results.json').write_text(json.dumps({'passed': checks, 'page_errors': page_errors}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'passed': len(checks), 'screenshots': str(output), 'page_errors': page_errors}, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()
        connections.close_all()
        temporary.cleanup()


if __name__ == '__main__':
    run()
