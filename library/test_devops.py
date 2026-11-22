import csv
from datetime import timedelta
from io import BytesIO, StringIO

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Book, Loan, Reader
from .pdf_responses import ranged_file_response
from .views import csv_safe_cell


class RangeTests(SimpleTestCase):
    def test_ranges(self):
        for header, status, body, content_range in [
            (None, 200, b'abcdef', None), ('bytes=1-3', 206, b'bcd', 'bytes 1-3/6'),
            ('bytes=-2', 206, b'ef', 'bytes 4-5/6'), ('bytes=6-', 416, b'', 'bytes */6'),
            ('bytes=-0', 416, b'', 'bytes */6'), ('bytes=', 200, b'abcdef', None),
            ('bytes=1-2,4-5', 200, b'abcdef', None), ('bytes=' + '9' * 5000 + '-', 200, b'abcdef', None),
            ('bytes=abc-', 200, b'abcdef', None),
        ]:
            with self.subTest(header=header):
                source = BytesIO(b'abcdef')
                response = ranged_file_response(source, 'book.pdf', 'application/pdf', header)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.get('Content-Range'), content_range)
                actual = b''.join(response.streaming_content) if response.streaming else response.content
                self.assertEqual(actual, body)
                if status != 416:
                    self.assertEqual(int(response['Content-Length']), len(body))
                response.close()
                self.assertTrue(source.closed)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CSVTests(TestCase):
    def test_all_reports_quote_and_protect_text(self):
        user = get_user_model().objects.create_user('staff-csv', is_staff=True)
        self.client.force_login(user)
        title = '=1+1,"\u0446\u0438\u0442\u0430\u0442\u0430"\n\u0442\u0435\u043a\u0441\u0442'
        book = Book.objects.create(title=title, author='+author', genre='test', publication_year=2020, total_copies=2, available_copies=1)
        reader = Reader.objects.create(full_name='Читатель', class_name='=1+1')
        today = timezone.localdate()
        Loan.objects.create(book=book, reader=reader, issue_date=today - timedelta(days=3), due_date=today + timedelta(days=1))
        Loan.objects.create(book=book, reader=reader, issue_date=today - timedelta(days=4), due_date=today - timedelta(days=1))
        for report in ('current', 'overdue', 'history', 'popular'):
            with self.subTest(report=report):
                response = self.client.get(reverse('library:report_csv', args=[report]))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
                rows = list(csv.reader(StringIO(response.content.decode('utf-8-sig'), newline='')))
                self.assertEqual(rows[1][0], "'" + title)
                self.assertEqual(rows[1][1], "'+author")
                if report != 'popular':
                    self.assertEqual(rows[1][2:4], ['Читатель', "'=1+1"])
                else:
                    self.assertEqual(rows[1][2], '2')
        reader.refresh_from_db()
        self.assertEqual(reader.class_name, '=1+1')

    def test_prefix_policy(self):
        for value in ['=x', '+x', '-x', '@x', '  =x', '\tplain', '\rplain', '\nplain', 'plain\ntext', '\x00=x']:
            self.assertEqual(csv_safe_cell(value), "'" + value)
        for value in ['Привет, "мир"', '2026-01-01', 42, '']:
            self.assertEqual(csv_safe_cell(value), value)
