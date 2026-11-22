import tempfile
import zipfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation
from pypdf import PdfWriter
from PIL import Image

from .ebooks import safe_chapter
from .forms import BookForm
from .models import Book, BookFile, BookRequest, Loan, Reader, ReadingProgress, StudentProfile
from .services import issue_book, request_book, return_loan, review_request
from .validators import validate_book_file


def pdf_upload(pages=2, name='book.pdf'):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=400)
    buffer = BytesIO()
    writer.write(buffer)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='application/pdf')


def epub_upload():
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        archive.writestr('OPS/book.opf', '<package><manifest><item id="c1" href="one.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="two.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        archive.writestr('OPS/one.xhtml', '<html><head><meta/><title>Book</title></head><body><h1>First chapter</h1><p>Safe reading.</p><script>alert(1)</script><a href="javascript:alert(1)">Link</a></body></html>')
        archive.writestr('OPS/two.xhtml', '<html><body><h1>Second chapter</h1></body></html>')
    return SimpleUploadedFile('book.epub', buffer.getvalue(), content_type='application/epub+zip')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class StudentFeaturesTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_dir = tempfile.TemporaryDirectory()
        cls.media_dir = tempfile.TemporaryDirectory()
        cls.storage_settings = override_settings(PRIVATE_MEDIA_ROOT=Path(cls.private_dir.name), MEDIA_ROOT=Path(cls.media_dir.name))
        cls.storage_settings.enable()

    @classmethod
    def tearDownClass(cls):
        cls.storage_settings.disable()
        cls.private_dir.cleanup()
        cls.media_dir.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'ru'
        self.staff = get_user_model().objects.create_user('librarian', password='test-password', is_staff=True)
        self.student = self.make_student('student', 'Иван Первый')
        self.other = self.make_student('other', 'Пётр Второй')
        self.book = Book.objects.create(title='Test book', author='Author', genre='Story', publication_year=2020,
                                        total_copies=1, available_copies=1)

    def make_student(self, username, name):
        user = get_user_model().objects.create_user(username, password='test-password')
        reader = Reader.objects.create(full_name=name, class_name='7A', contact=username + '@example.test')
        return StudentProfile.objects.create(user=user, reader=reader)

    def url(self, name, *args):
        return reverse('library:' + name, args=args)

    def login_student(self):
        self.client.force_login(self.student.user)

    def upload(self, file=None, **extra):
        self.client.force_login(self.staff)
        data = {'file': file or pdf_upload(), 'online_enabled': 'on', 'rights_confirmed': 'on'}
        data.update(extra)
        return self.client.post(self.url('book_file', self.book.pk), data)

    def test_registration_login_and_reader_link(self):
        response = self.client.post(self.url('register'), {'username': 'new-student', 'full_name': 'Новый Ученик',
            'class_name': '8B', 'contact': '12345', 'password1': 'Qwerty!672-school', 'password2': 'Qwerty!672-school'})
        self.assertRedirects(response, self.url('student_dashboard'))
        student = StudentProfile.objects.get(user__username='new-student')
        self.assertEqual(student.reader.class_name, '8B')
        self.assertFalse(student.user.is_staff)
        self.assertTrue(student.user.check_password('Qwerty!672-school'))
        self.client.logout()
        self.assertRedirects(self.client.post(self.url('login'), {'username': 'new-student', 'password': 'Qwerty!672-school'}), self.url('student_dashboard'))

    def test_invalid_and_duplicate_registration_do_not_leave_readers(self):
        before = Reader.objects.count()
        response = self.client.post(self.url('register'), {'username': 'student', 'full_name': 'Single',
            'class_name': '7A', 'password1': 'x', 'password2': 'y'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Reader.objects.count(), before)

    def test_own_profile_and_history_only(self):
        request_book(self.student, self.book.pk)
        other_book = Book.objects.create(title='Private other book', author='Author', genre='Story', publication_year=2020,
                                         total_copies=1, available_copies=1)
        request_book(self.other, other_book.pk)
        self.login_student()
        for name in ['student_dashboard', 'my_requests']:
            response = self.client.get(self.url(name), {'student': self.other.pk})
            self.assertContains(response, self.book.title)
            # Public catalog titles can appear in recommendations. Request/history
            # ownership and private profile data must still remain isolated.
            self.assertEqual({item.student_id for item in response.context['book_requests']}, {self.student.pk})
            if name == 'student_dashboard':
                self.assertTrue(all(loan.reader_id == self.student.reader_id for loan in response.context['current_loans']))
            self.assertNotContains(response, self.other.reader.contact)

    def test_staff_management_is_forbidden_to_students(self):
        self.login_student()
        for name in ['students', 'student_add', 'requests', 'loans', 'readers', 'reports', 'book_add', 'book_metadata']:
            self.assertEqual(self.client.get(self.url(name)).status_code, 403, name)
        self.assertEqual(self.client.post(self.url('book_file', self.book.pk), {'file': pdf_upload()}).status_code, 403)
        self.assertEqual(self.client.get(self.url('student_edit', self.other.pk)).status_code, 403)

    def test_student_navigation_hides_staff_controls(self):
        self.login_student()
        for mode in ['grid', 'table']:
            response = self.client.get(self.url('books'), {'view': mode})
            self.assertContains(response, 'Запросить книгу')
            self.assertContains(response, 'Мои запросы')
            self.assertNotContains(response, 'Добавить книгу')
            self.assertNotContains(response, 'Редактировать')
            self.assertNotContains(response, 'Отчёты')

    def test_request_requires_login_post_and_csrf(self):
        url = self.url('book_request', self.book.pk)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.login_student()
        self.assertEqual(self.client.get(url).status_code, 405)
        strict_client = Client(enforce_csrf_checks=True)
        strict_client.force_login(self.student.user)
        self.assertEqual(strict_client.post(url).status_code, 403)
        self.assertEqual(BookRequest.objects.count(), 0)

    def test_request_created_duplicate_prevented_and_unavailable_message(self):
        self.login_student()
        response = self.client.post(self.url('book_request', self.book.pk))
        self.assertRedirects(response, self.url('my_requests'))
        self.client.post(self.url('book_request', self.book.pk))
        self.assertEqual(BookRequest.objects.count(), 1)
        self.book.available_copies = 0
        self.book.save()
        self.client.force_login(self.other.user)
        response = self.client.post(self.url('book_request', self.book.pk), follow=True)
        self.assertContains(response, 'нет доступных экземпляров')
        self.assertEqual(BookRequest.objects.count(), 1)

    def test_database_enforces_single_active_request(self):
        request_book(self.student, self.book.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BookRequest.objects.create(student=self.student, book=self.book)

    def test_approval_issue_and_return_update_all_balances(self):
        item = request_book(self.student, self.book.pk)
        self.client.force_login(self.staff)
        self.client.post(self.url('request_action', item.pk), {'action': 'approve'})
        item.refresh_from_db()
        self.book.refresh_from_db()
        self.assertEqual(item.status, 'approved')
        self.assertEqual(self.book.available_copies, 0)
        self.assertEqual(Loan.objects.count(), 0)
        self.client.post(self.url('request_action', item.pk), {'action': 'issue', 'due_date': (timezone.localdate() + timedelta(days=14)).isoformat()})
        item.refresh_from_db()
        self.book.refresh_from_db()
        self.assertEqual(item.status, 'issued')
        self.assertEqual(self.book.available_copies, 0)
        self.assertEqual(Loan.objects.filter(status='active').count(), 1)
        self.client.post(self.url('loan_return', item.loan_id))
        item.refresh_from_db()
        self.book.refresh_from_db()
        self.assertEqual(item.status, 'returned')
        self.assertEqual(self.book.available_copies, 1)
        self.login_student()
        self.assertContains(self.client.get(self.url('my_requests')), 'Возвращена')

    def test_repeated_approval_and_issue_are_idempotent(self):
        item = request_book(self.student, self.book.pk)
        review_request(item.pk, 'approve')
        with self.assertRaises(ValidationError):
            review_request(item.pk, 'approve')
        issue_book(self.book.pk, self.student.reader, timezone.localdate(), timezone.localdate(), item.pk)
        with self.assertRaises(ValidationError):
            issue_book(self.book.pk, self.student.reader, timezone.localdate(), timezone.localdate(), item.pk)
        self.assertEqual(Loan.objects.count(), 1)
        self.book.refresh_from_db()
        self.assertEqual(self.book.available_copies, 0)

    def test_two_students_cannot_reserve_the_last_copy(self):
        first = request_book(self.student, self.book.pk)
        second = request_book(self.other, self.book.pk)
        review_request(first.pk, 'approve')
        with self.assertRaises(ValidationError):
            review_request(second.pk, 'approve')
        second.refresh_from_db()
        self.assertEqual(second.status, 'pending')

    def test_rejection_releases_reservation_and_allows_new_request(self):
        item = request_book(self.student, self.book.pk)
        review_request(item.pk, 'approve')
        review_request(item.pk, 'reject', 'Try another book')
        self.book.refresh_from_db()
        self.assertEqual(self.book.available_copies, 1)
        new = request_book(self.student, self.book.pk)
        self.assertNotEqual(item.pk, new.pk)

    def test_student_cannot_review_any_request(self):
        item = request_book(self.other, self.book.pk)
        self.login_student()
        self.assertEqual(self.client.post(self.url('request_action', item.pk), {'action': 'approve'}).status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')

    def test_invalid_issue_date_rolls_back_reservation(self):
        item = request_book(self.student, self.book.pk)
        review_request(item.pk, 'approve')
        with self.assertRaises(ValidationError):
            issue_book(self.book.pk, self.student.reader, timezone.localdate(), timezone.localdate() - timedelta(days=1), item.pk)
        item.refresh_from_db()
        self.assertEqual(item.status, 'approved')
        self.assertEqual(Loan.objects.count(), 0)

    def test_existing_loan_form_consumes_reservation_once(self):
        item = request_book(self.student, self.book.pk)
        review_request(item.pk, 'approve')
        self.client.force_login(self.staff)
        response = self.client.post(self.url('loans'), {'book': self.book.pk, 'reader': self.student.reader_id,
            'issue_date': timezone.localdate().isoformat(), 'due_date': timezone.localdate().isoformat()})
        self.assertRedirects(response, self.url('loans'))
        item.refresh_from_db()
        self.assertEqual(item.status, 'issued')
        self.book.refresh_from_db()
        self.assertEqual(self.book.available_copies, 0)

    def test_manual_loan_is_visible_and_blocks_repeat_request(self):
        loan = issue_book(self.book.pk, self.student.reader, timezone.localdate(), timezone.localdate())
        with self.assertRaises(ValidationError):
            request_book(self.student, self.book.pk)
        self.login_student()
        self.assertContains(self.client.get(self.url('student_dashboard')), self.book.title)
        return_loan(loan.pk)
        self.assertContains(self.client.get(self.url('student_dashboard')), 'Возвращена')

    def test_inventory_edit_counts_active_loans_and_reservations(self):
        item = request_book(self.student, self.book.pk)
        review_request(item.pk, 'approve')
        data = {'title': self.book.title, 'author': 'Author', 'genre': 'Story', 'publication_year': 2020, 'total_copies': 0}
        form = BookForm(data=data, instance=self.book)
        self.assertFalse(form.is_valid())
        data['total_copies'] = 3
        form = BookForm(data=data, instance=Book.objects.get(pk=self.book.pk))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.book.refresh_from_db()
        self.assertEqual(self.book.available_copies, 2)

    def test_staff_can_link_existing_reader_without_losing_history(self):
        reader = Reader.objects.create(full_name='Старый Читатель', class_name='9A')
        loan = issue_book(self.book.pk, reader, timezone.localdate(), timezone.localdate())
        self.client.force_login(self.staff)
        response = self.client.post(self.url('student_add'), {'username': 'linked', 'full_name': 'Старый Читатель',
            'class_name': '9A', 'existing_reader': reader.pk, 'password1': 'Long-school!678', 'password2': 'Long-school!678'})
        self.assertRedirects(response, self.url('students'))
        student = StudentProfile.objects.get(user__username='linked')
        self.assertEqual(student.reader_id, loan.reader_id)

    def test_staff_can_disable_student_and_student_can_edit_only_own_profile(self):
        self.login_student()
        self.client.post(self.url('profile_edit'), {'full_name': 'Иван Новый', 'class_name': '8A', 'contact': 'new', 'is_staff': 'on', 'reader': self.other.reader_id})
        self.student.reader.refresh_from_db()
        self.other.reader.refresh_from_db()
        self.assertEqual(self.student.reader.contact, 'new')
        self.assertNotEqual(self.other.reader.contact, 'new')
        self.client.force_login(self.staff)
        self.client.post(self.url('student_edit', self.student.pk), {'full_name': 'Иван Новый', 'class_name': '8A', 'contact': 'new'})
        self.student.user.refresh_from_db()
        self.assertFalse(self.student.user.is_active)

    def test_upload_pdf_and_serve_inline_only_to_authenticated_users(self):
        self.assertRedirects(self.upload(), self.url('books'))
        digital = BookFile.objects.get(book=self.book)
        self.assertTrue(digital.file.name.startswith('books/'))
        self.assertTrue(Path(digital.file.path).resolve().is_relative_to(Path(self.private_dir.name).resolve()))
        with self.assertRaises(ValueError):
            _ = digital.file.url
        self.client.logout()
        self.assertEqual(self.client.get(self.url('book_content', self.book.pk)).status_code, 302)
        self.login_student()
        response = self.client.get(self.url('book_content', self.book.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response['Content-Disposition'].startswith('inline'))
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF-'))
        self.assertContains(self.client.get(self.url('book_read', self.book.pk)), '<iframe')
        self.assertEqual(BookRequest.objects.count(), 0)

    def test_private_files_are_not_available_from_public_media(self):
        self.upload()
        digital = BookFile.objects.get(book=self.book)
        self.client.logout()
        # The legacy /media/books/ route is authenticated, including unknown paths.
        self.assertEqual(self.client.get('/media/' + digital.file.name).status_code, 302)
        self.login_student()
        self.assertEqual(self.client.get('/media/' + digital.file.name).status_code, 404)

    def test_reading_progress_is_saved_and_owned(self):
        self.upload()
        self.login_student()
        self.client.post(self.url('reading_progress', self.book.pk), {'position': 2, 'student': self.other.pk})
        self.assertEqual(ReadingProgress.objects.get().student_id, self.student.pk)
        self.assertContains(self.client.get(self.url('book_read', self.book.pk)), '#page=2')
        self.client.post(self.url('reading_progress', self.book.pk), {'position': 99})
        self.assertEqual(ReadingProgress.objects.get().position, 2)

    def test_access_restrictions_apply_to_reader_file_and_progress(self):
        self.upload(allowed_students=[self.student.pk])
        self.client.force_login(self.other.user)
        for name in ['book_read', 'book_content', 'book_resource']:
            self.assertEqual(self.client.get(self.url(name, self.book.pk)).status_code, 403)
        self.assertEqual(self.client.post(self.url('reading_progress', self.book.pk), {'position': 1}).status_code, 403)
        self.assertEqual(ReadingProgress.objects.count(), 0)
        self.login_student()
        self.assertEqual(self.client.get(self.url('book_read', self.book.pk)).status_code, 200)

    def test_disabled_online_reading_blocks_file_even_without_physical_request(self):
        self.upload(online_enabled='')
        self.login_student()
        self.assertEqual(self.client.get(self.url('book_content', self.book.pk)).status_code, 403)
        self.assertNotContains(self.client.get(self.url('online_books')), self.book.title)

    def test_wrong_extension_fake_pdf_and_missing_rights_are_rejected(self):
        for file in [SimpleUploadedFile('virus.exe', b'MZpayload'), SimpleUploadedFile('fake.pdf', b'<script>bad</script>'), SimpleUploadedFile('bad.epub', b'not a zip')]:
            with self.subTest(file=file.name):
                self.assertEqual(self.upload(file).status_code, 200)
                self.assertEqual(BookFile.objects.count(), 0)
        self.assertEqual(self.upload(rights_confirmed='').status_code, 200)
        self.assertEqual(BookFile.objects.count(), 0)

    @override_settings(BOOK_FILE_MAX_SIZE=10)
    def test_oversized_file_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_book_file(pdf_upload())

    def test_encrypted_and_active_pdfs_are_rejected(self):
        for active in [True, False]:
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            if active:
                writer.add_js('app.alert("bad")')
            else:
                writer.encrypt('secret')
            data = BytesIO()
            writer.write(data)
            with self.assertRaises(ValidationError):
                validate_book_file(SimpleUploadedFile('book.pdf', data.getvalue()))

    def test_missing_and_broken_stored_files_show_understandable_error(self):
        self.upload()
        digital = BookFile.objects.get(book=self.book)
        Path(digital.file.path).unlink()
        self.login_student()
        self.assertContains(self.client.get(self.url('book_read', self.book.pk)), 'Не удалось открыть файл', status_code=404)
        self.assertEqual(self.client.get(self.url('book_content', self.book.pk)).status_code, 404)

    def test_missing_ebook_does_not_break_physical_book_editing(self):
        self.upload()
        digital = BookFile.objects.get(book=self.book)
        Path(digital.file.path).unlink()
        self.client.force_login(self.staff)
        response = self.client.post(self.url('book_edit', self.book.pk), {'title': 'Updated physical book',
            'author': 'Author', 'genre': 'Story', 'publication_year': 2020, 'total_copies': 2})
        self.assertRedirects(response, self.url('books'))
        self.book.refresh_from_db()
        self.assertEqual(self.book.available_copies, 2)

    def test_admin_keeps_circulation_readonly_and_can_manage_file_access(self):
        self.upload()
        self.staff.is_superuser = True
        self.staff.save(update_fields=['is_superuser'])
        loan = issue_book(self.book.pk, self.student.reader, timezone.localdate(), timezone.localdate())
        response = self.client.get(reverse('admin:library_loan_change', args=[loan.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="status"')
        self.assertEqual(self.client.get(reverse('admin:library_loan_add')).status_code, 403)
        response = self.client.get(reverse('admin:library_book_change', args=[self.book.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="available_copies"')
        digital = BookFile.objects.get(book=self.book)
        url = reverse('admin:library_bookfile_change', args=[digital.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {'rights_confirmed': 'on', 'allowed_students': [self.student.pk], '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        digital.refresh_from_db()
        self.assertFalse(digital.online_enabled)
        self.assertEqual(list(digital.allowed_students.values_list('pk', flat=True)), [self.student.pk])

    def test_book_upload_works_in_book_creation_form(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url('book_add'), {'title': 'Digital book', 'author': 'Author', 'genre': 'Story',
            'publication_year': 2020, 'total_copies': 0, 'ebook_file': pdf_upload(), 'rights_confirmed': 'on', 'online_enabled': 'on'})
        self.assertRedirects(response, self.url('books'))
        book = Book.objects.get(title='Digital book')
        self.assertTrue(book.digital_file.online_enabled)
        self.assertEqual(book.available_copies, 0)

    def test_file_replacement_resets_progress_and_existing_file_form_renders(self):
        self.upload()
        self.login_student()
        self.client.post(self.url('reading_progress', self.book.pk), {'position': 2})
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url('book_file', self.book.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url('book_edit', self.book.pk)).status_code, 200)
        self.upload(pdf_upload(pages=1))
        self.assertEqual(ReadingProgress.objects.count(), 0)

    def test_epub_opens_in_site_and_removes_active_content(self):
        self.assertRedirects(self.upload(epub_upload()), self.url('books'))
        self.login_student()
        response = self.client.get(self.url('book_read', self.book.pk))
        self.assertContains(response, 'First chapter')
        self.assertContains(response, 'Safe reading.')
        self.assertNotContains(response, 'alert(1)')
        self.assertNotContains(response, 'javascript:')
        response = self.client.get(self.url('book_read', self.book.pk), {'chapter': 2})
        self.assertContains(response, 'Second chapter')
        self.client.post(self.url('reading_progress', self.book.pk), {'position': 2})
        self.assertContains(self.client.get(self.url('book_read', self.book.pk)), 'Second chapter')

    def test_epub_sanitizer_removes_events_external_images_and_scripts(self):
        result = safe_chapter('OPS/one.xhtml', '<p onclick="bad()">Text<img src="https://evil.test/a"/><script>bad()</script></p>', {}, lambda path: path)
        self.assertIn('Text', result)
        for bad in ['onclick', 'evil.test', 'bad()', '<script']:
            self.assertNotIn(bad, result)

    def test_epub_zip_path_traversal_is_rejected(self):
        file = epub_upload()
        buffer = BytesIO(file.read())
        with zipfile.ZipFile(buffer, 'a') as archive:
            archive.writestr('../outside.txt', 'bad')
        with self.assertRaises(ValidationError):
            validate_book_file(SimpleUploadedFile('unsafe.epub', buffer.getvalue()))

    @patch('library.views.search_metadata', side_effect=TimeoutError)
    def test_metadata_failure_does_not_break_manual_creation(self, _mock):
        self.client.force_login(self.staff)
        response = self.client.get(self.url('book_metadata'), {'q': 'Book'})
        self.assertEqual(response.status_code, 503)
        self.assertIn('вручную', response.json()['error'])
        response = self.client.post(self.url('book_add'), {'title': 'Manual book', 'author': 'Author', 'genre': 'Story', 'publication_year': 2020, 'total_copies': 2})
        self.assertRedirects(response, self.url('books'))

    @patch('library.views.search_metadata', return_value=[{'title': 'API book', 'author': 'API author', 'id': 'OL1W'}])
    def test_metadata_search_returns_data_only_for_staff(self, _mock):
        self.client.force_login(self.staff)
        response = self.client.get(self.url('book_metadata'), {'q': 'API'})
        self.assertEqual(response.json()['results'][0]['title'], 'API book')
        self.assertEqual(Book.objects.count(), 1)
        self.assertEqual(BookFile.objects.count(), 0)

    @patch('library.metadata.fetch')
    def test_metadata_cover_is_verified_and_imported(self, fetch_mock):
        image = BytesIO()
        Image.new('RGB', (2, 2), 'white').save(image, 'JPEG')
        fetch_mock.return_value = image.getvalue()
        self.client.force_login(self.staff)
        response = self.client.post(self.url('book_edit', self.book.pk), {'title': self.book.title,
            'author': 'Author', 'genre': 'Story', 'publication_year': 2020, 'total_copies': 1, 'cover_import_id': 42})
        self.assertRedirects(response, self.url('books'))
        self.book.refresh_from_db()
        self.assertTrue(self.book.cover.name.endswith('.jpg'))
        fetch_mock.assert_called_once_with('https://covers.openlibrary.org/b/id/42-L.jpg?default=false', 2 * 1024 * 1024)

    @patch('library.metadata.fetch', side_effect=TimeoutError)
    def test_cover_import_timeout_allows_manual_save(self, fetch_mock):
        self.client.force_login(self.staff)
        data = {'title': 'Manual after timeout', 'author': 'Author', 'genre': 'Story', 'publication_year': 2020,
                'total_copies': 1, 'cover_import_id': 42}
        response = self.client.post(self.url('book_edit', self.book.pk), data)
        self.assertContains(response, 'отключите импорт')
        data['cover_import_id'] = ''
        self.assertRedirects(self.client.post(self.url('book_edit', self.book.pk), data), self.url('books'))
        fetch_mock.assert_called_once()

    def test_new_interface_and_statuses_in_kazakh_and_russian(self):
        request_book(self.student, self.book.pk)
        self.login_student()
        for language, expected in [('kk', 'Менің сұраныстарым'), ('ru', 'Мои запросы')]:
            self.client.post('/i18n/setlang/', {'language': language, 'next': self.url('my_requests')})
            self.assertContains(self.client.get(self.url('my_requests')), expected)
        with translation.override('kk'):
            self.assertEqual(str(BookRequest.Status.PENDING.label), 'Күтуде')
            self.assertEqual(str(BookRequest.Status.APPROVED.label), 'Қабылданды')

    def test_protected_history_delete_shows_message(self):
        request_book(self.student, self.book.pk)
        self.client.force_login(self.staff)
        response = self.client.post(self.url('book_delete', self.book.pk), follow=True)
        self.assertContains(response, 'не может быть удалена')
        self.assertTrue(Book.objects.filter(pk=self.book.pk).exists())
