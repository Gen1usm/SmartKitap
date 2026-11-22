"""Serve authenticated PDF bytes, including a single HTTP Range for large books."""
import re

from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header


def ranged_file_response(file, filename, content_type, range_header=None):
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    # Ignore malformed, excessive, unsupported and multi-range headers.
    header = range_header or ''
    match = re.fullmatch(r'bytes=([0-9]{0,20})-([0-9]{0,20})', header) if len(header) <= 48 else None
    if match and not any(match.groups()):
        match = None
    if not match:
        response = FileResponse(file, content_type=content_type, filename=filename)
        response.block_size = 64 * 1024
    else:
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(int(last) if last else size - 1, size - 1)
        else:
            suffix = int(last or 0)
            start = max(0, size - suffix)
            end = size - 1
        if not size or start > end or start >= size or (not first and not last):
            file.close()
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
        else:
            file.seek(start)

            def chunks():
                remaining = end - start + 1
                try:
                    while remaining:
                        block = file.read(min(64 * 1024, remaining))
                        if not block:
                            break
                        remaining -= len(block)
                        yield block
                finally:
                    file.close()

            response = StreamingHttpResponse(chunks(), status=206, content_type=content_type)
            # Also close the file if a browser cancels before consuming the iterator.
            response._resource_closers.append(file.close)
            response['Content-Length'] = end - start + 1
            response['Content-Range'] = f'bytes {start}-{end}/{size}'
            response['Content-Disposition'] = content_disposition_header(False, filename)
    response['Accept-Ranges'] = 'bytes'
    return response
