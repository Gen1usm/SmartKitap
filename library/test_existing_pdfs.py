"""Read-only integration tests against the PDFs already supplied in media/books.

No PDF fixtures are generated, copied, modified or removed by these tests.
"""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .management.commands.link_book_pdfs import normalized_title
from .models import Book, BookFile, Reader, StudentProfile
from .storage import EXISTING_BOOK_PREFIX, PrivateBookStorage, existing_book_path

@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ExistingPDFTests(TestCase):
    @classmethod
    def setUpClass(cls):
        global SOURCE
        temporary = TemporaryDirectory(prefix='smartkitap-pdf-')
        cls.addClassCleanup(temporary.cleanup)
        media = Path(temporary.name)
        SOURCE = media / 'books' / 'abay_zholy.pdf'
        SOURCE.parent.mkdir(parents=True)
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.write(str(SOURCE))
        cls.settings_override = override_settings(MEDIA_ROOT=media)
        cls.settings_override.enable()
        cls.addClassCleanup(cls.settings_override.disable)
        super().setUpClass()

    def setUp(self):
        user = get_user_model().objects.create_user('pdf-student', password='test-only')
        self.student = StudentProfile.objects.create(user=user,
            reader=Reader.objects.create(full_name='Тест Оқушы', class_name='7'))
        self.book = self.create_book('Абай жолы')
        self.client.force_login(user)

    def create_book(self, title):
        return Book.objects.create(title=title, author='Автор', genre='Роман', publication_year=2020,
                                    total_copies=2, available_copies=2)

    def link(self, book=None, filename='abay_zholy.pdf'):
        return BookFile.objects.create(book=book or self.book, file=EXISTING_BOOK_PREFIX + filename,
                                       online_enabled=True, rights_confirmed=True)

    def command(self, **kwargs):
        stdout, stderr = StringIO(), StringIO()
        call_command('link_book_pdfs', stdout=stdout, stderr=stderr, **kwargs)
        return stdout.getvalue(), stderr.getvalue()

    def content_url(self):
        return reverse('library:book_content', args=[self.book.pk])

    def test_dry_run_validates_without_creating_links(self):
        stdout, _ = self.command(dry_run=True)
        self.assertIn('Будет связано: 1', stdout)
        self.assertEqual(BookFile.objects.count(), 0)

    def test_command_links_explicit_filename_and_is_idempotent(self):
        original = Book.objects.values().get(pk=self.book.pk)
        stdout, _ = self.command()
        self.assertIn('Связано: 1', stdout)
        self.assertEqual(BookFile.objects.get().file.name, 'media/books/abay_zholy.pdf')
        stdout, _ = self.command()
        self.assertIn('уже связано: 1', stdout)
        self.assertEqual(BookFile.objects.count(), 1)
        self.assertEqual(Book.objects.values().get(pk=self.book.pk), original)

    def test_unmatched_title_is_reported_without_random_file_or_new_book(self):
        self.create_book('Неизвестная книга без PDF')
        stdout, _ = self.command()
        self.assertIn('Без PDF: Неизвестная книга без PDF', stdout)
        self.assertEqual(Book.objects.count(), 2)
        self.assertEqual(BookFile.objects.count(), 1)

    def test_existing_digital_link_is_not_overwritten(self):
        original = BookFile.objects.create(book=self.book, file='books/already-uploaded.pdf', rights_confirmed=True)
        _, stderr = self.command()
        original.refresh_from_db()
        self.assertEqual(original.file.name, 'books/already-uploaded.pdf')
        self.assertIn('существующая связь сохранена', stderr)

    def test_identical_existing_copy_relinks_without_deleting_files_or_access_settings(self):
        digital = BookFile.objects.create(book=self.book, file='books/uploaded-copy.pdf',
                                          online_enabled=False, rights_confirmed=True)
        digital.allowed_students.add(self.student)
        with patch.object(digital.file.storage, 'open', side_effect=lambda *a, **k: SOURCE.open('rb')), \
             patch.object(digital.file.storage, 'delete') as deletion:
            self.command()
            deletion.assert_not_called()
        digital.refresh_from_db()
        self.assertEqual(digital.file.name, 'media/books/abay_zholy.pdf')
        self.assertFalse(digital.online_enabled)
        self.assertEqual(list(digital.allowed_students.all()), [self.student])

    def test_ambiguous_catalog_titles_are_not_linked(self):
        self.create_book('Абай жолы')
        _, stderr = self.command()
        self.assertEqual(BookFile.objects.count(), 0)
        self.assertIn('несколько записей', stderr)

    def test_unicode_case_spaces_and_hyphens_normalize_without_transliteration(self):
        self.assertEqual(normalized_title('  Абай—ЖОЛЫ  '), normalized_title('Абай жолы'))
        self.assertEqual(normalized_title('Менің_атым-Қожа'), normalized_title('Менің атым Қожа'))
        self.assertNotEqual(normalized_title('abai_zholy'), normalized_title('Абай жолы'))

    def test_existing_file_path_resolves_to_original_without_copy(self):
        digital = self.link()
        self.assertEqual(Path(digital.file.path), SOURCE.resolve())
        self.assertTrue(self.book.has_online_version)

    def test_reader_contains_correct_book_author_and_back_link(self):
        self.link()
        response = self.client.get(reverse('library:book_read', args=[self.book.pk]))
        self.addCleanup(response.close)
        self.assertContains(response, self.book.title)
        self.assertContains(response, self.book.author)
        self.assertContains(response, 'Каталогқа оралу')
        self.assertContains(response, '<iframe')
        self.assertContains(response, 'pdf-fullscreen')

    def test_byte_range_returns_only_requested_bytes_inline(self):
        self.link()
        response = self.client.get(self.content_url(), HTTP_RANGE='bytes=0-4')
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-')
        self.assertEqual(response['Content-Range'], f'bytes 0-4/{SOURCE.stat().st_size}')
        self.assertEqual(response['Content-Length'], '5')
        self.assertEqual(response['Accept-Ranges'], 'bytes')
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response['Content-Disposition'].startswith('inline'))
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')

    def test_suffix_range_matches_original_file(self):
        self.link()
        with SOURCE.open('rb') as file:
            file.seek(-16, 2)
            expected = file.read()
        response = self.client.get(self.content_url(), HTTP_RANGE='bytes=-16')
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join(response.streaming_content), expected)

    def test_unsatisfiable_range_is_416(self):
        self.link()
        response = self.client.get(self.content_url(), HTTP_RANGE=f'bytes={SOURCE.stat().st_size}-')
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response['Content-Range'], f'bytes */{SOURCE.stat().st_size}')

    def test_direct_media_url_uses_same_authorization_and_correct_original(self):
        self.link()
        response = self.client.get('/media/books/abay_zholy.pdf', HTTP_RANGE='bytes=0-4')
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-')
        self.client.logout()
        for path in [self.content_url(), '/media/books/abay_zholy.pdf',
                     '/media//books/abay_zholy.pdf', '/media/other/../books/abay_zholy.pdf']:
            self.assertNotIn(self.client.get(path, HTTP_RANGE='bytes=0-4').status_code, [200, 206])

    def test_restricted_and_disabled_books_cannot_be_opened_by_media_url(self):
        digital = self.link()
        other = StudentProfile.objects.create(user=get_user_model().objects.create_user('other'),
            reader=Reader.objects.create(full_name='Другой Ученик', class_name='8'))
        digital.allowed_students.add(other)
        for path in [self.content_url(), '/media/books/abay_zholy.pdf']:
            self.assertEqual(self.client.get(path, HTTP_RANGE='bytes=0-4').status_code, 403)
        digital.allowed_students.clear()
        digital.online_enabled = False
        digital.save()
        self.assertEqual(self.client.get('/media/books/abay_zholy.pdf', HTTP_RANGE='bytes=0-4').status_code, 403)

    def test_missing_pdf_shows_message_in_both_catalog_layouts(self):
        self.link(filename='file-not-present.pdf')
        for mode in ['grid', 'table']:
            response = self.client.get(reverse('library:books'), {'view': mode})
            self.addCleanup(response.close)
            self.assertContains(response, 'Онлайн нұсқасы жоқ')
            self.assertNotContains(response, reverse('library:book_read', args=[self.book.pk]))
        self.assertEqual(self.client.get(self.content_url()).status_code, 404)

    def test_path_traversal_is_rejected_without_opening_server_files(self):
        with self.assertRaises(SuspiciousFileOperation):
            existing_book_path('media/books/../../README.pdf')
        self.link(filename='../../smartkitap/settings.py')
        self.assertFalse(self.book.has_online_version)
        self.assertEqual(self.client.get(self.content_url()).status_code, 404)
        self.assertEqual(self.client.get('/media/books/../../smartkitap/settings.py').status_code, 404)

    def test_original_files_cannot_be_deleted_or_overwritten_by_storage(self):
        storage = PrivateBookStorage()
        with patch.object(FileSystemStorage, 'delete') as deletion:
            storage.delete('media/books/abay_zholy.pdf')
            deletion.assert_not_called()
        with patch.object(FileSystemStorage, '_save') as write:
            with self.assertRaises(SuspiciousFileOperation):
                storage._save('media/books/abay_zholy.pdf', object())
            write.assert_not_called()
        with patch.object(FileSystemStorage, '_open') as opening:
            with self.assertRaises(SuspiciousFileOperation):
                storage.open('media/books/abay_zholy.pdf', mode='wb')
            opening.assert_not_called()

    @override_settings(BOOK_FILE_MAX_SIZE=1)
    def test_existing_pdf_is_not_subject_to_new_upload_size_limit(self):
        digital = self.link()
        digital.full_clean()
        self.assertTrue(digital.file_exists)
