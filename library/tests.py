import shutil
import tempfile
from datetime import timedelta
from io import BytesIO
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .forms import BookForm
from .models import Book, Loan, Reader


TEST_MEDIA_ROOT = tempfile.mkdtemp()


def tiny_png(name='cover.png'):
    image = Image.new('RGB', (1, 1), color='white')
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    buffer.seek(0)
    return SimpleUploadedFile(
        name,
        buffer.read(),
        content_type='image/png',
    )


class LibraryPageTests(TestCase):
    def setUp(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'ru'
        self.user = get_user_model().objects.create_user('librarian', password='pass12345', is_staff=True)

    def test_main_pages_are_available(self):
        self.client.force_login(self.user)
        page_names = [
            'dashboard',
            'books',
            'readers',
            'loans',
        ]

        for page_name in page_names:
            with self.subTest(page=page_name):
                response = self.client.get(reverse(f'library:{page_name}'))
                self.assertEqual(response.status_code, 200)

    def test_login_page_is_available_without_login(self):
        self.client.logout()

        response = self.client.get(reverse('library:login'))

        self.assertEqual(response.status_code, 200)

    def test_authenticated_user_is_redirected_from_login(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('library:login'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('library:dashboard'))

    def test_public_catalog_is_available_without_login(self):
        self.client.logout()

        response = self.client.get(reverse('library:books'))

        self.assertEqual(response.status_code, 200)

    def test_management_pages_redirect_without_login(self):
        self.client.logout()

        for page_name in ['dashboard', 'readers', 'loans', 'reports', 'book_add']:
            with self.subTest(page=page_name):
                response = self.client.get(reverse(f'library:{page_name}'))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('library:login'), response['Location'])


class BookCatalogTests(TestCase):
    def setUp(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'ru'
        self.user = get_user_model().objects.create_user('librarian', password='pass12345', is_staff=True)
        self.client.force_login(self.user)

    def create_book(self, **kwargs):
        data = {
            'title': 'Абай жолы',
            'author': 'Мұхтар Әуезов',
            'publication_year': 1942,
            'genre': 'Роман',
            'description': 'Тестовая запись каталога.',
            'total_copies': 5,
            'available_copies': 5,
        }
        data.update(kwargs)
        return Book.objects.create(**data)

    def test_book_creation(self):
        book = self.create_book()

        self.assertEqual(str(book), 'Абай жолы — Мұхтар Әуезов')
        self.assertTrue(book.is_available)

    def test_book_is_saved_in_sqlite_database(self):
        self.create_book(title='Менің атым Қожа', author='Бердібек Соқпақбаев')

        self.assertTrue(Book.objects.filter(title='Менің атым Қожа').exists())

    def test_search_by_title(self):
        self.create_book(title='Абай жолы')
        self.create_book(title='Көшпенділер', author='Ілияс Есенберлин')

        response = self.client.get(reverse('library:books'), {'q': 'жолы'})

        self.assertContains(response, 'Абай жолы')
        self.assertNotContains(response, 'Көшпенділер')

    def test_search_by_author(self):
        self.create_book(title='Көшпенділер', author='Ілияс Есенберлин')
        self.create_book(title='Ботагөз', author='Сәбит Мұқанов')

        response = self.client.get(reverse('library:books'), {'q': 'Есенберлин'})

        self.assertContains(response, 'Көшпенділер')
        self.assertNotContains(response, 'Ботагөз')

    def test_search_is_case_insensitive(self):
        self.create_book(title='Ай мен Айша', author='Шерхан Мұртаза')

        response = self.client.get(reverse('library:books'), {'q': 'айша'})

        self.assertContains(response, 'Ай мен Айша')

    def test_filter_by_genre_and_availability(self):
        self.create_book(title='Доступная книга', genre='Повесть', available_copies=2, total_copies=2)
        self.create_book(title='Недоступная книга', genre='Повесть', available_copies=0, total_copies=2)
        self.create_book(title='Другой жанр', genre='Роман', available_copies=3, total_copies=3)

        response = self.client.get(reverse('library:books'), {
            'genre': 'Повесть',
            'availability': 'available',
        })

        self.assertContains(response, 'Доступная книга')
        self.assertNotContains(response, 'Недоступная книга')
        self.assertNotContains(response, 'Другой жанр')

    def test_add_book_with_form(self):
        response = self.client.post(reverse('library:book_add'), {
            'title': 'Ботагөз',
            'author': 'Сәбит Мұқанов',
            'publication_year': 1938,
            'genre': 'Роман',
            'description': 'Демонстрационное описание.',
            'total_copies': 6,
        })

        self.assertRedirects(response, reverse('library:books'))
        book = Book.objects.get(title='Ботагөз')
        self.assertEqual(book.available_copies, 6)

    def test_invalid_book_data_is_rejected(self):
        form = BookForm(data={
            'title': 'Ошибка',
            'author': 'Автор',
            'publication_year': 900,
            'genre': 'Жанр',
            'total_copies': -1,
        })

        self.assertFalse(form.is_valid())
        self.assertIn('publication_year', form.errors)
        self.assertIn('total_copies', form.errors)

    def test_edit_book(self):
        book = self.create_book(title='Старое название', total_copies=4, available_copies=4)

        response = self.client.post(reverse('library:book_edit', args=[book.pk]), {
            'title': 'Новое название',
            'author': book.author,
            'publication_year': book.publication_year,
            'genre': book.genre,
            'description': book.description,
            'total_copies': 8,
        })

        self.assertRedirects(response, reverse('library:books'))
        book.refresh_from_db()
        self.assertEqual(book.title, 'Новое название')
        self.assertEqual(book.total_copies, 8)

    def test_delete_book(self):
        book = self.create_book()

        response = self.client.post(reverse('library:book_delete', args=[book.pk]))

        self.assertRedirects(response, reverse('library:books'))
        self.assertFalse(Book.objects.filter(pk=book.pk).exists())

    def test_available_copies_are_calculated_correctly(self):
        response = self.client.post(reverse('library:book_add'), {
            'title': 'Кітап',
            'author': 'Автор',
            'publication_year': 2020,
            'genre': 'Оқу құралы',
            'description': '',
            'total_copies': 3,
        })

        self.assertRedirects(response, reverse('library:books'))
        self.assertEqual(Book.objects.get(title='Кітап').available_copies, 3)

    def test_available_copies_do_not_exceed_total_after_edit(self):
        book = self.create_book(total_copies=5, available_copies=4)

        self.client.post(reverse('library:book_edit', args=[book.pk]), {
            'title': book.title,
            'author': book.author,
            'publication_year': book.publication_year,
            'genre': book.genre,
            'description': book.description,
            'total_copies': 2,
        })

        book.refresh_from_db()
        self.assertEqual(book.available_copies, 2)

    def test_empty_catalog_display(self):
        response = self.client.get(reverse('library:books'))

        self.assertContains(response, 'В каталоге пока нет книг')

    @override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
    def test_add_book_with_cover(self):
        response = self.client.post(reverse('library:book_add'), {
            'title': 'Мұқабалы кітап',
            'author': 'Автор',
            'publication_year': 2022,
            'genre': 'Оқу құралы',
            'description': '',
            'total_copies': 2,
            'cover': tiny_png(),
        })

        self.assertRedirects(response, reverse('library:books'))
        book = Book.objects.get(title='Мұқабалы кітап')
        self.assertTrue(book.cover.name.startswith('book_covers/'))

    def test_book_without_cover_displays_placeholder(self):
        self.create_book(title='Мұқабасыз кітап')

        response = self.client.get(reverse('library:books'))

        self.assertContains(response, 'Нет обложки')

    @override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
    def test_replace_cover_when_editing_book(self):
        book = self.create_book(title='Обложка')

        self.client.post(reverse('library:book_edit', args=[book.pk]), {
            'title': book.title,
            'author': book.author,
            'publication_year': book.publication_year,
            'genre': book.genre,
            'description': book.description,
            'total_copies': book.total_copies,
            'cover': tiny_png('new-cover.png'),
        })

        book.refresh_from_db()
        self.assertIn('new-cover', book.cover.name)

    def test_catalog_still_works_after_reader_changes(self):
        self.create_book(title='Каталог работает')

        response = self.client.get(reverse('library:books'), {'q': 'Каталог'})

        self.assertContains(response, 'Каталог работает')


class ReaderCatalogTests(TestCase):
    def setUp(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'ru'
        self.user = get_user_model().objects.create_user('librarian', password='pass12345', is_staff=True)
        self.client.force_login(self.user)

    def create_reader(self, **kwargs):
        data = {
            'full_name': 'Айжан Нурланова',
            'class_name': '7А',
            'contact': '',
        }
        data.update(kwargs)
        return Reader.objects.create(**data)

    def test_reader_creation(self):
        reader = self.create_reader()

        self.assertEqual(str(reader), 'Айжан Нурланова — 7А')

    def test_search_reader_by_full_name(self):
        self.create_reader(full_name='Айжан Нурланова')
        self.create_reader(full_name='Ерлан Сагатов')

        response = self.client.get(reverse('library:readers'), {'q': 'Айжан'})

        self.assertContains(response, 'Айжан Нурланова')
        self.assertNotContains(response, 'Ерлан Сагатов')

    def test_filter_readers_by_class(self):
        self.create_reader(full_name='Айжан Нурланова', class_name='7А')
        self.create_reader(full_name='Ерлан Сагатов', class_name='8Б')

        response = self.client.get(reverse('library:readers'), {'class_name': '7А'})

        self.assertContains(response, 'Айжан Нурланова')
        self.assertNotContains(response, 'Ерлан Сагатов')

    def test_add_reader_with_form(self):
        response = self.client.post(reverse('library:reader_add'), {
            'full_name': 'Мадина Ермекова',
            'class_name': '6В',
            'contact': '',
        })

        self.assertRedirects(response, reverse('library:readers'))
        self.assertTrue(Reader.objects.filter(full_name='Мадина Ермекова').exists())

    def test_edit_and_delete_reader(self):
        reader = self.create_reader()

        edit_response = self.client.post(reverse('library:reader_edit', args=[reader.pk]), {
            'full_name': 'Айжан Ахметова',
            'class_name': '7Б',
            'contact': 'parent@example.com',
        })
        self.assertRedirects(edit_response, reverse('library:readers'))
        reader.refresh_from_db()
        self.assertEqual(reader.full_name, 'Айжан Ахметова')
        self.assertEqual(reader.class_name, '7Б')

        delete_response = self.client.post(reverse('library:reader_delete', args=[reader.pk]))
        self.assertRedirects(delete_response, reverse('library:readers'))
        self.assertFalse(Reader.objects.filter(pk=reader.pk).exists())

    def test_reader_form_errors_are_displayed(self):
        response = self.client.post(reverse('library:reader_add'), {
            'full_name': 'Айжан',
            'class_name': '',
            'contact': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Укажите фамилию и имя читателя.')


class LanguageSwitchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('librarian', password='pass12345', is_staff=True)
        self.client.force_login(self.user)

    def test_default_language_is_kazakh(self):
        response = self.client.get(reverse('library:dashboard'))

        self.assertContains(response, 'Басты бет')

    def test_switch_from_kazakh_to_russian_and_back(self):
        response = self.client.post('/i18n/setlang/', {
            'language': 'ru',
            'next': reverse('library:books') + '?q=Абай',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('library:books') + '?q=' + quote('Абай'))
        self.assertEqual(self.client.cookies[settings.LANGUAGE_COOKIE_NAME].value, 'ru')

        response = self.client.get(reverse('library:books'))
        self.assertContains(response, 'Каталог книг')

        self.client.post('/i18n/setlang/', {
            'language': 'kk',
            'next': reverse('library:books'),
        })
        response = self.client.get(reverse('library:books'))
        self.assertContains(response, 'Кітаптар каталогы')

    def test_language_is_kept_during_navigation(self):
        self.client.post('/i18n/setlang/', {
            'language': 'ru',
            'next': reverse('library:books'),
        })

        response = self.client.get(reverse('library:readers'))

        self.assertContains(response, 'Читатели')


class LoanWorkflowTests(TestCase):
    def setUp(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'ru'
        self.user = get_user_model().objects.create_user('librarian', password='pass12345', is_staff=True)
        self.client.force_login(self.user)
        self.book = Book.objects.create(
            title='Абай жолы',
            author='Мұхтар Әуезов',
            publication_year=1942,
            genre='Роман',
            total_copies=2,
            available_copies=2,
        )
        self.reader = Reader.objects.create(
            full_name='Айжан Нурланова',
            class_name='7А',
            contact='',
        )

    def issue_payload(self, **kwargs):
        today = timezone.localdate()
        data = {
            'book': self.book.pk,
            'reader': self.reader.pk,
            'issue_date': today.isoformat(),
            'due_date': (today + timedelta(days=14)).isoformat(),
        }
        data.update(kwargs)
        return data

    def create_loan(self, **kwargs):
        data = {
            'book': self.book,
            'reader': self.reader,
            'issue_date': timezone.localdate(),
            'due_date': timezone.localdate() + timedelta(days=14),
            'status': Loan.Status.ACTIVE,
        }
        data.update(kwargs)
        return Loan.objects.create(**data)

    def test_successful_loan_for_available_book(self):
        response = self.client.post(reverse('library:loans'), self.issue_payload())

        self.assertRedirects(response, reverse('library:loans'))
        self.assertEqual(Loan.objects.count(), 1)
        self.assertEqual(Loan.objects.first().book, self.book)

    def test_available_copies_decrease_after_loan(self):
        self.client.post(reverse('library:loans'), self.issue_payload())

        self.book.refresh_from_db()
        self.assertEqual(self.book.available_copies, 1)

    def test_loan_is_forbidden_when_no_available_copies(self):
        self.book.available_copies = 0
        self.book.save(update_fields=['available_copies'])

        response = self.client.post(reverse('library:loans'), self.issue_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Loan.objects.count(), 0)
        self.book.refresh_from_db()
        self.assertEqual(self.book.available_copies, 0)

    def test_successful_book_return(self):
        loan = self.create_loan()
        self.book.available_copies = 1
        self.book.save(update_fields=['available_copies'])

        response = self.client.post(reverse('library:loan_return', args=[loan.pk]))

        self.assertRedirects(response, reverse('library:loans'))
        loan.refresh_from_db()
        self.assertEqual(loan.status, Loan.Status.RETURNED)
        self.assertEqual(loan.return_date, timezone.localdate())

    def test_available_copies_are_restored_after_return(self):
        loan = self.create_loan()
        self.book.available_copies = 1
        self.book.save(update_fields=['available_copies'])

        self.client.post(reverse('library:loan_return', args=[loan.pk]))

        self.book.refresh_from_db()
        self.assertEqual(self.book.available_copies, 2)

    def test_repeated_return_is_forbidden(self):
        loan = self.create_loan(status=Loan.Status.RETURNED, return_date=timezone.localdate())

        response = self.client.post(reverse('library:loan_return', args=[loan.pk]))

        self.assertRedirects(response, reverse('library:loans'))
        self.book.refresh_from_db()
        self.assertEqual(self.book.available_copies, 2)

    def test_overdue_loan_is_detected(self):
        loan = self.create_loan(
            issue_date=timezone.localdate() - timedelta(days=20),
            due_date=timezone.localdate() - timedelta(days=1),
        )

        self.assertTrue(loan.is_overdue)

    def test_returned_book_is_not_overdue(self):
        loan = self.create_loan(
            issue_date=timezone.localdate() - timedelta(days=20),
            due_date=timezone.localdate() - timedelta(days=1),
            return_date=timezone.localdate(),
            status=Loan.Status.RETURNED,
        )

        self.assertFalse(loan.is_overdue)

    def test_search_loans_by_reader_and_book(self):
        self.create_loan()
        other_book = Book.objects.create(
            title='Көшпенділер',
            author='Ілияс Есенберлин',
            publication_year=1969,
            genre='Тарихи роман',
            total_copies=1,
            available_copies=1,
        )
        other_reader = Reader.objects.create(full_name='Ерлан Сагатов', class_name='8Б')
        Loan.objects.create(
            book=other_book,
            reader=other_reader,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=7),
        )

        response = self.client.get(reverse('library:loans'), {'q': 'Айжан'})
        content = response.content.decode()
        self.assertIn('<strong>Абай жолы</strong>', content)
        self.assertNotIn('<strong>Көшпенділер</strong>', content)

        response = self.client.get(reverse('library:loans'), {'q': 'Көшпенділер'})
        content = response.content.decode()
        self.assertIn('Ерлан Сагатов', content)
        self.assertNotIn('<td>Айжан Нурланова</td>', content)

    def test_loan_filters_work(self):
        active = self.create_loan()
        returned_book = Book.objects.create(
            title='Ботагөз',
            author='Сәбит Мұқанов',
            publication_year=1938,
            genre='Роман',
            total_copies=1,
            available_copies=1,
        )
        returned = Loan.objects.create(
            book=returned_book,
            reader=self.reader,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=7),
            return_date=timezone.localdate(),
            status=Loan.Status.RETURNED,
        )
        overdue_book = Book.objects.create(
            title='Ай мен Айша',
            author='Шерхан Мұртаза',
            publication_year=1997,
            genre='Роман',
            total_copies=1,
            available_copies=1,
        )
        overdue = Loan.objects.create(
            book=overdue_book,
            reader=self.reader,
            issue_date=timezone.localdate() - timedelta(days=10),
            due_date=timezone.localdate() - timedelta(days=1),
        )

        response = self.client.get(reverse('library:loans'), {'status': 'active'})
        content = response.content.decode()
        self.assertIn(f'<strong>{active.book.title}</strong>', content)
        self.assertNotIn(f'<strong>{returned.book.title}</strong>', content)
        self.assertNotIn(f'<strong>{overdue.book.title}</strong>', content)

        response = self.client.get(reverse('library:loans'), {'status': 'returned'})
        content = response.content.decode()
        self.assertIn(f'<strong>{returned.book.title}</strong>', content)
        self.assertNotIn(f'<strong>{active.book.title}</strong>', content)

        response = self.client.get(reverse('library:loans'), {'status': 'overdue'})
        content = response.content.decode()
        self.assertIn(f'<strong>{overdue.book.title}</strong>', content)
        self.assertNotIn(f'<strong>{active.book.title}</strong>', content)

    def test_history_is_kept_after_return(self):
        loan = self.create_loan()

        self.client.post(reverse('library:loan_return', args=[loan.pk]))

        self.assertTrue(Loan.objects.filter(pk=loan.pk, status=Loan.Status.RETURNED).exists())

    def test_data_integrity_is_kept_when_operation_has_error(self):
        response = self.client.post(reverse('library:loans'), self.issue_payload(
            due_date=(timezone.localdate() - timedelta(days=1)).isoformat()
        ))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Loan.objects.count(), 0)
        self.book.refresh_from_db()
        self.assertEqual(self.book.available_copies, 2)

    def test_loan_interface_is_available_in_both_languages(self):
        response = self.client.get(reverse('library:loans'))
        self.assertContains(response, 'Выдача и возврат')

        self.client.post('/i18n/setlang/', {
            'language': 'kk',
            'next': reverse('library:loans'),
        })
        response = self.client.get(reverse('library:loans'))
        self.assertContains(response, 'Беру және қайтару')


class AuthAndReportsTests(TestCase):
    def setUp(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'ru'
        self.user = get_user_model().objects.create_user('librarian', password='pass12345', is_staff=True)
        self.book = Book.objects.create(
            title='Абай жолы',
            author='Мұхтар Әуезов',
            publication_year=1942,
            genre='Роман',
            total_copies=3,
            available_copies=2,
        )
        self.reader = Reader.objects.create(full_name='Айжан Нурланова', class_name='7А')
        self.loan = Loan.objects.create(
            book=self.book,
            reader=self.reader,
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=7),
        )

    def test_librarian_can_login_and_logout(self):
        response = self.client.post(reverse('library:login'), {
            'username': 'librarian',
            'password': 'pass12345',
        })
        self.assertRedirects(response, reverse('library:dashboard'))

        response = self.client.post(reverse('library:logout'))
        self.assertRedirects(response, reverse('library:login'))

    def test_wrong_login_shows_error(self):
        response = self.client.post(reverse('library:login'), {
            'username': 'librarian',
            'password': 'wrong',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Неверное имя пользователя или пароль.')

    def test_dashboard_statistics_are_real(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('library:dashboard'))

        self.assertContains(response, '3')
        self.assertContains(response, '2')
        self.assertContains(response, '1')

    def test_reports_show_real_data(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('library:reports'))

        self.assertContains(response, 'Абай жолы')
        self.assertContains(response, 'Айжан Нурланова')
        self.assertContains(response, 'Текущие выдачи')

    def test_report_csv_is_generated_with_utf8_bom(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('library:report_csv', args=['current']))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8-sig')
        self.assertTrue(response.content.startswith('\ufeff'.encode('utf-8')))
        self.assertIn('Абай жолы'.encode('utf-8'), response.content)

    def test_reports_are_protected(self):
        response = self.client.get(reverse('library:reports'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('library:login'), response['Location'])


def tearDownModule():
    shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)
