import posixpath
from pathlib import Path

from django.conf import settings
from django.http import Http404
from django.views.static import serve

from .views import ExistingMediaBookView


def guarded_media(request, path, document_root=None, show_indexes=False):
    """Keep development covers public, protecting every spelling of media/books."""
    normalized = posixpath.normpath(path.replace('\\', '/')).lstrip('/')
    if normalized == 'books' or normalized.startswith('books/'):
        return ExistingMediaBookView.as_view()(request, file_path=normalized.removeprefix('books/'))
    root = Path(settings.MEDIA_ROOT).resolve()
    resolved = (root / normalized).resolve()
    if not resolved.is_relative_to(root):
        raise Http404
    # A symlink from another media folder must not bypass book authorization.
    books_root = (root / 'books').resolve()
    if resolved.is_relative_to(books_root):
        return ExistingMediaBookView.as_view()(request, file_path=resolved.relative_to(books_root).as_posix())
    return serve(request, normalized, document_root=root, show_indexes=False)
