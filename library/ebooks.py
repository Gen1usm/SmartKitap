"""Bounded EPUB parsing and a text-first reader without executable book content."""
import posixpath
import zipfile
from html import escape
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from defusedxml import ElementTree

MAX_EXPANDED_SIZE = 100 * 1024 * 1024
MAX_DOCUMENT_SIZE = 5 * 1024 * 1024


def archive_path(base, href):
    url = urlsplit(href)
    if url.scheme or url.netloc or url.path.startswith('/') or '\\' in url.path:
        raise ValueError('External or unsafe EPUB path')
    path = posixpath.normpath(posixpath.join(base, unquote(url.path)))
    if path == '..' or path.startswith('../') or path.startswith('/') or '\\' in path:
        raise ValueError('Unsafe EPUB path')
    return path


def epub_contents(file):
    with zipfile.ZipFile(file) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_SIZE:
            raise ValueError('EPUB is too large after unpacking')
        if any(entry.flag_bits & 1 for entry in entries):
            raise ValueError('Encrypted EPUB is unsupported')
        if len({entry.filename for entry in entries}) != len(entries):
            raise ValueError('Duplicate EPUB entries')
        for entry in entries:
            archive_path('', entry.filename)
        if archive.read('mimetype').strip() != b'application/epub+zip':
            raise ValueError('Invalid EPUB mimetype')

        def read_xml(path):
            if archive.getinfo(path).file_size > MAX_DOCUMENT_SIZE:
                raise ValueError('EPUB document too large')
            return ElementTree.fromstring(archive.read(path))

        container = read_xml('META-INF/container.xml')
        rootfile = container.find('.//{*}rootfile')
        if rootfile is None:
            raise ValueError('Missing EPUB package')
        package_path = archive_path('', rootfile.attrib['full-path'])
        package = read_xml(package_path)
        base = posixpath.dirname(package_path)
        manifest = {}
        for item in package.findall('.//{*}manifest/{*}item'):
            manifest[item.attrib['id']] = (archive_path(base, item.attrib['href']), item.attrib.get('media-type', ''))
        chapters = []
        for item in package.findall('.//{*}spine/{*}itemref'):
            if item.attrib.get('linear') == 'no':
                continue
            path, media_type = manifest[item.attrib['idref']]
            if media_type not in ('application/xhtml+xml', 'text/html'):
                continue
            if archive.getinfo(path).file_size > MAX_DOCUMENT_SIZE:
                raise ValueError('EPUB chapter too large')
            content = archive.read(path).decode('utf-8-sig')
            if media_type == 'application/xhtml+xml':
                ElementTree.fromstring(content)
            chapters.append((path, content))
        if not chapters:
            raise ValueError('No readable EPUB chapters')
        return chapters, manifest


class SafeChapterParser(HTMLParser):
    allowed = {'p', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b',
               'em', 'i', 'u', 's', 'br', 'hr', 'ul', 'ol', 'li', 'blockquote', 'pre',
               'code', 'sub', 'sup', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'section'}
    blocked = {'head', 'script', 'style', 'iframe', 'object', 'embed', 'svg', 'math', 'form'}

    def __init__(self, chapter_path, manifest, image_url):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.block_stack = []
        self.base = posixpath.dirname(chapter_path)
        self.images = {path for path, mime in manifest.values()
                       if mime in {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}}
        self.image_url = image_url

    def handle_starttag(self, tag, attrs):
        if tag in self.blocked:
            self.block_stack.append(tag)
            return
        if self.block_stack:
            return
        if tag in self.allowed:
            self.output.append(f'<{tag}>')
        elif tag == 'img':
            attributes = dict(attrs)
            try:
                path = archive_path(self.base, attributes.get('src', ''))
            except ValueError:
                return
            if path in self.images:
                self.output.append(f'<img src="{escape(self.image_url(path), quote=True)}" '
                                   f'alt="{escape(attributes.get("alt", ""), quote=True)}">')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {'br', 'hr', 'img'}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.block_stack:
            if tag == self.block_stack[-1]:
                self.block_stack.pop()
        elif tag in self.allowed and tag not in {'br', 'hr'}:
            self.output.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.block_stack:
            self.output.append(escape(data))


def safe_chapter(chapter_path, content, manifest, image_url):
    parser = SafeChapterParser(chapter_path, manifest, image_url)
    parser.feed(content)
    parser.close()
    return ''.join(parser.output)
