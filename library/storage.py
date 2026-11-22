from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from pathlib import Path

EXISTING_BOOK_PREFIX = 'media/books/'


def existing_book_path(name):
    """Resolve an existing PDF strictly inside MEDIA_ROOT/books, including symlinks."""
    if not name.startswith(EXISTING_BOOK_PREFIX):
        raise SuspiciousFileOperation('Not an existing media book reference.')
    relative = name[len(EXISTING_BOOK_PREFIX):]
    if not relative or '\\' in relative or Path(relative).is_absolute():
        raise SuspiciousFileOperation('Unsafe book path.')
    media_root = Path(settings.MEDIA_ROOT).resolve()
    books_root = media_root / 'books'
    path = (books_root / relative).resolve()
    if not path.is_relative_to(books_root) or not path.is_relative_to(media_root) or path.suffix.lower() != '.pdf':
        raise SuspiciousFileOperation('Book path must remain inside media/books.')
    return path


@deconstructible
class PrivateBookStorage(FileSystemStorage):
    def __init__(self):
        # Read settings at runtime so tests and deployments can relocate files.
        super().__init__()

    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return str(Path(self.base_location).resolve())

    def path(self, name):
        if name.startswith(EXISTING_BOOK_PREFIX):
            return str(existing_book_path(name))
        return super().path(name)

    def _save(self, name, content):
        if name.startswith(EXISTING_BOOK_PREFIX):
            raise SuspiciousFileOperation('Existing media PDFs are read-only.')
        return super()._save(name, content)

    def _open(self, name, mode='rb'):
        if name.startswith(EXISTING_BOOK_PREFIX) and mode not in {'r', 'rb'}:
            raise SuspiciousFileOperation('Existing media PDFs are read-only.')
        return super()._open(name, mode)

    def delete(self, name):
        # Replacing a book's electronic edition must never remove the user's PDF.
        if name.startswith(EXISTING_BOOK_PREFIX):
            return
        return super().delete(name)

    def url(self, name):
        raise ValueError('Private books are available only through authorized views.')
