import json
import hashlib
import re
import unicodedata
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from library.models import Book, BookFile
from library.services import lock_book
from library.storage import EXISTING_BOOK_PREFIX, existing_book_path
from library.validators import pdf_page_count


def normalized_title(value):
    value = unicodedata.normalize('NFC', value).casefold()
    return re.sub(r'[\W_]+', ' ', value, flags=re.UNICODE).strip()


def identical_pdf(digital, source):
    try:
        with digital.file.open('rb') as uploaded:
            uploaded_digest = hashlib.file_digest(uploaded, 'sha256').hexdigest()
        with source.open('rb') as original:
            return uploaded_digest == hashlib.file_digest(original, 'sha256').hexdigest()
    except (OSError, ValueError):
        return False


class Command(BaseCommand):
    help = 'Связывает книги с существующими PDF в media/books без копирования и изменения файлов.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Проверить соответствия без изменения базы.')
        parser.add_argument('--mapping', type=Path, help='JSON: название книги → относительное имя PDF в media/books.')

    def handle(self, *args, **options):
        mapping_path = options['mapping'] or Path(__file__).resolve().parents[2] / 'data' / 'pdf_book_mapping.json'
        try:
            mapping = json.loads(mapping_path.read_text(encoding='utf-8-sig'))
            if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
                raise ValueError('Expected a title-to-filename object')
        except (OSError, ValueError) as exc:
            raise CommandError(f'Не удалось прочитать таблицу соответствий: {exc}') from exc
        folder = Path(settings.MEDIA_ROOT) / 'books'
        if not folder.is_dir():
            raise CommandError(f'Каталог не найден: {folder}')
        files = sorted(path.relative_to(folder).as_posix() for path in folder.rglob('*')
                       if path.is_file() and path.suffix.lower() == '.pdf')
        known_files = set(files)
        titles = {}
        for filename in files:
            titles.setdefault(normalized_title(Path(filename).stem), []).append(filename)
        explicit = {normalized_title(title): filename for title, filename in mapping.items()}
        books = list(Book.objects.all())
        title_counts = {}
        for book in books:
            key = normalized_title(book.title)
            title_counts[key] = title_counts.get(key, 0) + 1
        planned = []
        missing = []
        errors = []
        already_linked = []
        validation_cache = {}
        for book in books:
            key = normalized_title(book.title)
            filename = explicit.get(key)
            if not filename:
                candidates = titles.get(key, [])
                if len(candidates) > 1:
                    errors.append(f'{book.title}: неоднозначное имя PDF; укажите путь в JSON.')
                    continue
                filename = candidates[0] if candidates else None
            if not filename or filename not in known_files:
                missing.append(book.title)
                continue
            if title_counts[key] > 1:
                errors.append(f'{book.title}: несколько записей каталога с одинаковым названием; связь пропущена.')
                continue
            reference = EXISTING_BOOK_PREFIX + filename
            try:
                path = existing_book_path(reference)
                if len(reference) > BookFile._meta.get_field('file').max_length:
                    raise ValueError('Имя файла превышает длину поля BookFile.file.')
                if reference not in validation_cache:
                    with path.open('rb') as stream:
                        validation_cache[reference] = pdf_page_count(stream)
                digital = BookFile.objects.filter(book=book).first()
                if any(item[1] == reference for item in planned) or BookFile.objects.filter(file=reference).exclude(book=book).exists():
                    raise ValueError('Этот PDF уже связан с другой книгой.')
                if digital:
                    if digital.file.name == reference:
                        already_linked.append(book.title)
                    elif identical_pdf(digital, path):
                        planned.append((book, reference, digital.file.name))
                        self.stdout.write(f'{book.title} → {reference} (идентичный PDF; исходная копия сохраняется)')
                    else:
                        errors.append(f'{book.title}: электронная версия уже назначена; существующая связь сохранена.')
                    continue
                planned.append((book, reference, None))
                self.stdout.write(f'{book.title} → {reference} ({validation_cache[reference]} стр.)')
            except Exception as exc:
                errors.append(f'{book.title}: {exc}')

        linked = 0
        if not options['dry_run']:
            # Validate every candidate before changing any record. Concurrent
            # requests/loans use the same SQLite write lock as this operation.
            with transaction.atomic():
                for book, reference, previous_name in planned:
                    lock_book(book.pk)
                    if previous_name is not None:
                        changed = BookFile.objects.filter(book=book, file=previous_name).update(file=reference)
                        if changed:
                            linked += 1
                        else:
                            errors.append(f'{book.title}: электронная версия изменена другим процессом; связь сохранена.')
                        continue
                    if BookFile.objects.filter(book=book).exists():
                        errors.append(f'{book.title}: связь создана другим процессом; запись пропущена.')
                        continue
                    digital = BookFile(book=book, file=reference, online_enabled=True, rights_confirmed=True)
                    digital.full_clean(exclude=['file'])
                    digital.save()
                    linked += 1
        prefix = 'Будет связано' if options['dry_run'] else 'Связано'
        self.stdout.write(self.style.SUCCESS(
            f'PDF найдено: {len(files)}; {prefix}: {len(planned) if options["dry_run"] else linked}; '
            f'уже связано: {len(already_linked)}; без PDF: {len(missing)}; пропущено с замечаниями: {len(errors)}.'
        ))
        for title in missing:
            self.stdout.write(f'Без PDF: {title}')
        for message in errors:
            self.stderr.write(self.style.WARNING(message))
