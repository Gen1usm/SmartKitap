"""UI regression checks and screenshots on an isolated, disposable database.

Run: python scripts/ui_smoke.py
The school database and catalog media are never changed.
"""
import json
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartkitap.settings')
from django.conf import settings

temporary = tempfile.TemporaryDirectory(prefix='smartkitap-ui-')
settings.DATABASES['default']['NAME'] = Path(temporary.name) / 'ui.sqlite3'
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
from django.db import connections
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright
from library.models import Book, BookFile, BookRequest, Loan, Reader, ReadingProgress, StudentProfile
from library.test_students import pdf_upload, epub_upload


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def db_call(callback):
    def invoke():
        try:
            return callback()
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(invoke).result()


def run():
    call_command('migrate', verbosity=0)
    user = get_user_model().objects.create_user('abai.korkem', password='UI-test!123')
    student = StudentProfile.objects.create(user=user, reader=Reader.objects.create(full_name='Абай Көркем', class_name='8А'))
    get_user_model().objects.create_user('ui_librarian', password='UI-test!123', is_staff=True)
    data = [
        ('Көшпенділер', 'Ілияс Есенберлин', 'Роман', 'koshpendiler.png'),
        ('Менің атым Қожа', 'Бердібек Соқпақбаев', 'Повесть', 'menin_atym_qoja.png'),
        ('Абай жолы', 'Мұхтар Әуезов', 'Роман', 'abay_zholy.png'),
        ('Ұлпан', 'Ғабит Мүсірепов', 'Роман', 'Ulpan.png'),
        ('Ботагөз', 'Сәбит Мұқанов', 'Роман', 'botagoz.png'),
        ('Ай мен Айша', 'Шерхан Мұртаза', 'Роман', 'ai_men_aisha.png'),
        ('Мұқабасыз кітап', 'Жаңа автор', 'Әңгіме', ''),
    ]
    from PIL import Image
    covers = settings.MEDIA_ROOT / 'book_covers'
    covers.mkdir(parents=True, exist_ok=True)
    for _, _, _, cover in data:
        if cover:
            Image.new('RGB', (160, 240), (65, 100, 125)).save(covers / cover)
    books = []
    for title, author, genre, cover in data:
        book = Book.objects.create(title=title, author=author, genre=genre, publication_year=2026,
                                   description='Қазақ әдебиетінің рухани әлеміне саяхат. Оқырманды ойға жетелейтін көркем шығарма.',
                                   cover='book_covers/' + cover if cover else '', total_copies=4, available_copies=3)
        books.append(book)
    for book in books[:3]:
        BookFile.objects.create(book=book, file=pdf_upload(100), rights_confirmed=True)
    BookFile.objects.create(book=books[3], file=epub_upload(), rights_confirmed=True)
    today = timezone.localdate()
    Loan.objects.create(book=books[1], reader=student.reader, issue_date=today - timedelta(days=3), due_date=today + timedelta(days=14))
    Loan.objects.create(book=books[0], reader=student.reader, issue_date=today - timedelta(days=7), due_date=today + timedelta(days=2))
    Loan.objects.create(book=books[4], reader=student.reader, issue_date=today - timedelta(days=20),
                        due_date=today - timedelta(days=6), return_date=today - timedelta(days=8), status='returned')
    ReadingProgress.objects.create(student=student, book=books[0], position=38)
    BookRequest.objects.create(student=student, book=books[5])
    output = ROOT / 'artifacts/ui-qa'
    output.mkdir(parents=True, exist_ok=True)
    server = make_server('127.0.0.1', 0, StaticFilesHandler(get_wsgi_application()), handler_class=QuietHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    checks, errors, failed_requests = [], [], []
    page = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1126, 'height': 641}, locale='kk-KZ', reduced_motion='reduce')
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('response', lambda response: failed_requests.append(f'{response.status} {response.url}') if response.status >= 400 else None)
            page.goto(base + '/login/')
            page.locator('[name=username]').fill('abai.korkem')
            page.locator('[name=password]').fill('UI-test!123')
            page.locator('main button[type=submit]').click()
            page.wait_for_url('**/account/')
            expect(page.locator('html')).to_have_attribute('lang', 'kk')
            expect(page.locator('#welcome-heading')).to_have_text('Абай Көркем!')
            assert page.locator('.stat-value strong').all_text_contents() == ['2', '7', '1', '1']
            expect(page.locator('progress')).to_have_attribute('value', '38')
            assert abs(page.locator('.sidebar').bounding_box()['width'] - 167.2) < 1
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output / 'dashboard-1126.png'), full_page=True)
            page.screenshot(path=str(output / 'reference-viewport-1126.png'))
            checks.append('Kazakh dashboard, ORM totals, actual PDF percentage and 167 px sidebar')

            menu = page.locator('.my-books-panel .action-menu').first
            menu.locator('summary').click()
            expect(menu).to_have_attribute('open', '')
            page.keyboard.press('Escape')
            expect(menu).not_to_have_attribute('open', '')
            expect(menu.locator('summary')).to_be_focused()
            menu.locator('summary').click()
            page.locator('#welcome-heading').click()
            expect(menu).not_to_have_attribute('open', '')
            checks.append('Action menu opens, closes outside/on Escape and restores focus')

            trigger = page.locator('.recommendations .book-title').first
            book_url = trigger.get_attribute('href')
            held = []
            page.route('**' + book_url, lambda route: held.append(route))
            trigger.click()
            expect(page.locator('.loading-skeleton')).to_be_visible()
            page.screenshot(path=str(output / 'dialog-loading.png'))
            held.pop().continue_()
            expect(page.locator('.book-details-dialog .book-details')).to_be_visible()
            page.unroute('**' + book_url)
            page.screenshot(path=str(output / 'dialog-loaded.png'))
            page.keyboard.press('Escape')
            expect(page.locator('#book-details-dialog')).not_to_be_visible()
            expect(trigger).to_be_focused()
            page.route('**' + book_url, lambda route: route.fulfill(status=503, body='unavailable'))
            trigger.click()
            expect(page.locator('.dialog-error')).to_be_visible()
            page.screenshot(path=str(output / 'dialog-error.png'))
            page.unroute('**' + book_url)
            page.locator('[data-dialog-retry]').click()
            expect(page.locator('.book-details-dialog .book-details')).to_be_visible()
            page.locator('[data-dialog-close]').click()
            checks.append('Book dialog, visible skeleton, error recovery, retry, Escape and focus return')

            page.locator('.notification-trigger').click()
            expect(page.locator('.notification-dot')).to_be_visible()
            page.locator('[data-notifications-read] button').click()
            expect(page.locator('.toast-success')).to_be_visible()
            expect(page.locator('.notification-dot')).to_have_count(0)
            page.locator('[data-toast-close]').click()
            page.reload()
            expect(page.locator('.notification-dot')).to_have_count(0)
            checks.append('Real due-date notification acknowledged with CSRF and survives reload; toast dismissed')

            search = page.locator('#global-search')
            search.fill('Абай')
            page.locator('.topbar-search [data-search-clear]').click()
            expect(search).to_have_value('')
            search.fill('Абай')
            search.press('Enter')
            page.wait_for_url('**/books/?q=*')
            expect(page.locator('.ui-book-grid .ui-book-card')).to_have_count(1)
            page.locator('[name=author]').select_option('Мұхтар Әуезов')
            page.locator('[name=genre]').select_option('Роман')
            page.locator('[name=availability]').select_option('available')
            page.locator('.catalog-filter button[type=submit]').click()
            expect(page.locator('.ui-book-grid .ui-book-card')).to_have_count(1)
            page.locator('.view-switcher a').last.click()
            expect(page.locator('.catalog-table tbody tr')).to_have_count(1)
            assert 'author=' in page.url and 'genre=' in page.url
            page.locator('#catalog-search').fill('ештеңетабылмайды')
            page.locator('.catalog-filter button[type=submit]').click()
            expect(page.locator('.ui-empty h3')).to_have_text('Кітаптар табылмады')
            page.screenshot(path=str(output / 'catalog-empty.png'), full_page=True)
            page.locator('.ui-empty .btn').click()
            expect(page.locator('.ui-book-grid .ui-book-card')).to_have_count(7)
            page.screenshot(path=str(output / 'catalog-desktop.png'), full_page=True)
            checks.append('Search clear, real title search, combined filters, table view, no results and reset')

            no_cover = page.locator('.ui-book-card', has_text='Мұқабасыз кітап')
            expect(no_cover.locator('.cover-fallback')).to_be_visible()
            no_cover.locator('.book-title').click()
            expect(page.locator('.book-details-dialog')).to_be_visible()
            expect(page.locator('.book-details-dialog a[href$="/read/"]')).to_have_count(0)
            page.locator('.book-details-dialog form[action$="/request/"] button').click()
            page.wait_for_url('**/my-requests/')
            assert db_call(lambda: BookRequest.objects.filter(student=student, book=books[6]).exists())
            expect(page.locator('.toast-success')).to_be_visible()
            page.locator('[data-toast-close]').click()
            checks.append('Cover fallback, rights-aware dialog actions and actual request created from dialog')

            page.goto(base + '/account/')
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(page.locator('[data-drawer-open]')).to_be_visible()
            page.locator('[data-drawer-open]').click()
            expect(page.locator('#app-sidebar')).to_have_attribute('aria-modal', 'true')
            page.keyboard.press('Shift+Tab')
            assert page.evaluate("document.activeElement.closest('#app-sidebar') !== null")
            page.screenshot(path=str(output / 'drawer-mobile.png'))
            page.keyboard.press('Escape')
            expect(page.locator('[data-drawer-open]')).to_be_focused()
            page.locator('[data-drawer-open]').click()
            page.locator('.drawer-backdrop').click(position={'x': 320, 'y': 400})
            expect(page.locator('body')).not_to_have_class('drawer-open')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output / 'dashboard-mobile.png'), full_page=True)
            checks.append('390 px drawer, focus trap, Escape, backdrop, focus restoration and no page overflow')

            page.goto(base + '/books/')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output / 'catalog-mobile.png'), full_page=True)
            page.locator('button[name=language][value=ru]').click()
            expect(page.locator('html')).to_have_attribute('lang', 'ru')
            expect(page.locator('.page-heading h2')).to_have_text('Каталог книг')
            page.locator('button[name=language][value=kk]').click()
            expect(page.locator('html')).to_have_attribute('lang', 'kk')
            checks.append('Mobile catalog and real Russian/Kazakh language switching')

            for width in [320, 768, 900, 1440]:
                page.set_viewport_size({'width': width, 'height': 900})
                page.goto(base + '/account/')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            page.screenshot(path=str(output / 'dashboard-1440.png'), full_page=True)
            checks.append('Responsive dashboard at 320, 768, 900, 1126 and 1440 pixels')

            staff_context = browser.new_context(viewport={'width': 1126, 'height': 800}, locale='kk-KZ')
            staff = staff_context.new_page()
            staff.on('pageerror', lambda error: errors.append(str(error)))
            staff.goto(base + '/login/')
            staff.locator('[name=username]').fill('ui_librarian')
            staff.locator('[name=password]').fill('UI-test!123')
            staff.locator('main button[type=submit]').click()
            staff.wait_for_url(base + '/')
            staff.screenshot(path=str(output / 'staff-dashboard.png'), full_page=True)
            for url in ['/books/', '/requests/', '/students/', '/readers/', '/loans/', '/reports/']:
                assert staff.goto(base + url).status == 200
                assert staff.evaluate('document.documentElement.scrollWidth <= innerWidth'), url
            staff.goto(base + '/books/')
            staff.locator('.ui-book-card .book-title').first.click()
            expect(staff.locator('.book-details-dialog a[href$="/edit/"]')).to_be_visible()
            expect(staff.locator('.book-details-dialog form[action$="/request/"]')).to_have_count(0)
            staff.locator('[data-dialog-close]').click()
            staff.locator('.logout-button').click()
            staff.wait_for_url('**/login/')
            checks.append('Staff dashboard, all management sections, proper dialog permissions and logout')

            no_js = browser.new_context(java_script_enabled=False, viewport={'width': 1126, 'height': 800}, locale='kk-KZ')
            plain = no_js.new_page()
            plain.goto(base + '/books/')
            plain.locator('.ui-book-card .book-title').first.click()
            expect(plain.locator('.details-page .book-details')).to_be_visible()
            checks.append('Book details remain accessible as an ordinary page without JavaScript')
            assert not errors, errors
            unexpected = [item for item in failed_requests if not item.startswith('503 ')]
            assert not unexpected, unexpected
            result = {'checks': checks, 'page_errors': errors, 'expected_injected_errors': [item for item in failed_requests if item.startswith('503 ')]}
            (output / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=True, indent=2))
            browser.close()
    except Exception:
        if page and not page.is_closed():
            try:
                page.screenshot(path=str(output / 'failure.png'), full_page=True)
            except Exception:
                pass
        raise
    finally:
        server.shutdown()
        connections.close_all()
        temporary.cleanup()


if __name__ == '__main__':
    run()
