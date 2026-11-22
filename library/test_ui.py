"""Contract checks for real UI data, access control and progressive enhancement."""
import tempfile
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Book, BookFile, BookRequest, Loan, Reader, ReadingProgress, StudentProfile
from .test_students import pdf_upload


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class UIComponentTests(TestCase):
    def setUp(self):
        self.private = tempfile.TemporaryDirectory()
        self.addCleanup(self.private.cleanup)
        storage = override_settings(PRIVATE_MEDIA_ROOT=Path(self.private.name))
        storage.enable()
        self.addCleanup(storage.disable)
        self.user = get_user_model().objects.create_user('ui_student', password='test')
        self.reader = Reader.objects.create(full_name='Оқушы', class_name='7А')
        self.student = StudentProfile.objects.create(user=self.user, reader=self.reader)
        self.book = Book.objects.create(title='Кітап', author='Автор', genre='Әңгіме',
                                       publication_year=2026, total_copies=3, available_copies=2)
        self.client.force_login(self.user)

    def digital(self):
        return BookFile.objects.create(book=self.book, file=pdf_upload(10), rights_confirmed=True)

    def test_guest_pages_and_details_have_kazakh_by_default(self):
        self.client.logout()
        for url in ['/login/', '/register/', '/books/', reverse('library:book_details', args=[self.book.pk])]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '<html lang="kk">')

    def test_dashboard_counts_are_owned_and_real(self):
        Loan.objects.create(book=self.book, reader=self.reader, due_date=timezone.localdate() + timedelta(days=8))
        response = self.client.get('/account/')
        self.assertEqual(response.context['my_books_count'], 1)
        self.assertEqual(response.context['catalog_available_count'], 1)
        self.assertEqual(response.context['returned_count'], 0)
        self.assertEqual(response.context['my_requests_count'], 0)

    def test_continue_reading_uses_actual_page_count(self):
        self.digital()
        ReadingProgress.objects.create(student=self.student, book=self.book, position=4)
        response = self.client.get('/account/')
        self.assertEqual(response.context['continue_reading']['percent'], 40)
        self.assertEqual(response.context['continue_reading']['total'], 10)

    def test_corrupt_or_missing_file_does_not_break_dashboard(self):
        digital = self.digital()
        ReadingProgress.objects.create(student=self.student, book=self.book, position=4)
        Path(digital.file.path).write_bytes(b'broken')
        response = self.client.get('/account/')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['continue_reading'])
        Path(digital.file.path).unlink()
        self.assertIsNone(self.client.get('/account/').context['continue_reading'])

    def test_restricted_file_has_no_read_action_or_progress_card(self):
        digital = self.digital()
        other_user = get_user_model().objects.create_user('other')
        other = StudentProfile.objects.create(user=other_user, reader=Reader.objects.create(full_name='Басқа', class_name='8А'))
        digital.allowed_students.add(other)
        ReadingProgress.objects.create(student=self.student, book=self.book)
        response = self.client.get('/account/')
        self.assertIsNone(response.context['continue_reading'])
        details = self.client.get(reverse('library:book_details', args=[self.book.pk]))
        self.assertNotContains(details, f'href="/books/{self.book.pk}/read/"')

    def test_due_soon_boundaries_and_returned_status(self):
        today = timezone.localdate()
        for days, tone in [(-1, 'overdue'), (0, 'due-soon'), (3, 'due-soon'), (4, 'active')]:
            loan = Loan(book=self.book, reader=self.reader, due_date=today + timedelta(days=days))
            self.assertEqual(loan.status_tone, tone)
        loan.status = 'returned'
        self.assertEqual(loan.status_tone, 'returned')

    def test_search_combines_author_genre_and_availability(self):
        Book.objects.create(title='Басқа', author='Өзге', genre='Роман', publication_year=2026, total_copies=1, available_copies=1)
        response = self.client.get('/books/', {'q': 'Әңгіме', 'author': 'Автор', 'genre': 'Әңгіме', 'availability': 'available'})
        self.assertEqual(list(response.context['books']), [self.book])
        self.assertEqual(self.client.get('/books/', {'author': 'Өзге', 'q': 'Кітап'}).context['paginator'].count, 0)

    def test_pagination_keeps_all_filters(self):
        for index in range(10):
            Book.objects.create(title=f'Кітап {index}', author='Автор', genre='Әңгіме', publication_year=2026,
                                total_copies=1, available_copies=1)
        response = self.client.get('/books/', {'author': 'Автор', 'genre': 'Әңгіме', 'availability': 'available', 'view': 'table'})
        import html
        import re
        next_url = html.unescape(re.findall(r'class="page-link" href="([^"]+)"', response.content.decode())[-1])
        from urllib.parse import parse_qs, urlsplit
        self.assertEqual(parse_qs(urlsplit(next_url).query)['author'], ['Автор'])
        self.assertEqual(parse_qs(urlsplit(next_url).query)['view'], ['table'])
        self.assertEqual(self.client.get('/books/' + next_url).context['paginator'].count, 11)

    def test_details_fragment_and_full_page_have_same_data_and_permissions(self):
        path = reverse('library:book_details', args=[self.book.pk])
        fragment = self.client.get(path, HTTP_X_REQUESTED_WITH='SmartKitap')
        self.assertContains(fragment, self.book.title)
        self.assertNotContains(fragment, '<html')
        self.assertContains(fragment, f'action="/books/{self.book.pk}/request/"')
        self.assertNotContains(fragment, f'/books/{self.book.pk}/edit/')
        self.assertContains(self.client.get(path), '<html')
        self.assertEqual(self.client.get('/books/999999/').status_code, 404)

    def test_duplicate_request_button_is_disabled_in_details(self):
        BookRequest.objects.create(book=self.book, student=self.student)
        response = self.client.get(reverse('library:book_details', args=[self.book.pk]))
        self.assertContains(response, 'disabled')
        self.assertContains(response, 'Сұраныс жіберілген')

    def test_notification_read_requires_login_post_and_csrf(self):
        self.assertEqual(self.client.get('/notifications/read/').status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post('/notifications/read/').status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post('/notifications/read/').status_code, 302)

    def test_notifications_are_private_and_acknowledged_per_session(self):
        loan = Loan.objects.create(book=self.book, reader=self.reader,
                                   issue_date=timezone.localdate() - timedelta(days=10), due_date=timezone.localdate())
        response = self.client.get('/account/')
        self.assertEqual(response.context['ui_unread_count'], 1)
        self.assertEqual(self.client.post('/notifications/read/', HTTP_X_REQUESTED_WITH='SmartKitap').json()['unread'], 0)
        self.assertEqual(self.client.get('/account/').context['ui_unread_count'], 0)
        loan.due_date = timezone.localdate() - timedelta(days=1)
        loan.save()
        self.assertEqual(self.client.get('/account/').context['ui_unread_count'], 1)
        other = get_user_model().objects.create_user('other')
        StudentProfile.objects.create(user=other, reader=Reader.objects.create(full_name='Басқа', class_name='9А'))
        self.client.force_login(other)
        self.assertEqual(self.client.get('/account/').context['ui_unread_count'], 0)
