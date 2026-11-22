from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from pypdf import PdfReader

from .ebooks import epub_contents


def pdf_page_count(file):
    file.seek(0)
    if not file.read(8).startswith(b'%PDF-'):
        raise ValueError('Not a PDF')
    file.seek(0)
    pdf = PdfReader(file)
    if pdf.is_encrypted:
        raise ValueError('Encrypted PDF is unsupported')
    root = pdf.trailer['/Root']
    names = root.get('/Names', {})
    if hasattr(names, 'get_object'):
        names = names.get_object()
    if root.get('/OpenAction') or root.get('/AA') or names.get('/JavaScript') or names.get('/EmbeddedFiles'):
        raise ValueError('Active PDF content is unsupported')
    pages = len(pdf.pages)
    if not 1 <= pages <= 10000 or any(page.get('/AA') for page in pdf.pages):
        raise ValueError('Invalid or active PDF')
    for page in pdf.pages:
        for reference in page.get('/Annots', []):
            annotation = reference.get_object()
            action = annotation.get('/A')
            if action:
                action = action.get_object()
                if action.get('/S') not in {'/GoTo', '/URI'}:
                    raise ValueError('Active PDF annotation')
            if annotation.get('/AA') or annotation.get('/Subtype') == '/FileAttachment':
                raise ValueError('Active PDF attachment')
    return pages


def validate_book_file(file):
    suffix = Path(file.name).suffix.lower()
    if suffix not in {'.pdf', '.epub'}:
        raise ValidationError(_('Загрузите книгу в формате PDF или EPUB.'))
    try:
        # The upload limit applies to new uploads. Existing, already validated
        # media PDFs may be larger; they are read without copying or rewriting.
        from .storage import EXISTING_BOOK_PREFIX
        from django.db.models.fields.files import FieldFile
        existing_media = isinstance(file, FieldFile) and file.name.startswith(EXISTING_BOOK_PREFIX)
        if not existing_media and file.size > settings.BOOK_FILE_MAX_SIZE:
            raise ValidationError(_('Размер файла книги не должен превышать 25 МБ.'))
        file.seek(0)
        if suffix == '.pdf':
            pdf_page_count(file)
        else:
            epub_contents(file)
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(_('Файл повреждён, защищён паролем или содержит неподдерживаемое содержимое.')) from exc
    finally:
        try:
            file.seek(0)
        except OSError:
            pass
