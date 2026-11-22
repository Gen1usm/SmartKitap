"""Read-only presentation data shared by the Django UI components."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Book, BookFile, BookRequest, Loan


def catalog_for(user):
    return Book.objects.select_related('digital_file').prefetch_related('digital_file__allowed_students')


def can_read_book(book, user):
    if not user.is_authenticated:
        return False
    try:
        digital = book.digital_file
    except BookFile.DoesNotExist:
        return False
    if not digital.file_exists:
        return False
    if user.is_staff or user.is_superuser:
        return True
    student = getattr(user, 'student_profile', None)
    if not student or not digital.online_enabled or not digital.rights_confirmed:
        return False
    allowed = list(digital.allowed_students.all())
    return not allowed or any(item.pk == student.pk for item in allowed)


def notifications_for(request):
    """Only real events belonging to this user; acknowledgements live in the session."""
    user = request.user
    if not user.is_authenticated:
        return []
    notices = []
    today = timezone.localdate()
    if user.is_staff or user.is_superuser:
        for item in BookRequest.objects.filter(status='pending').select_related('book')[:8]:
            notices.append({'key': f'request:{item.pk}:pending', 'title': _('Новый запрос книги'),
                            'text': item.book.title, 'url': reverse('library:requests') + '?status=pending',
                            'icon': 'clipboard-list'})
    else:
        student = getattr(user, 'student_profile', None)
        if not student:
            return []
        loans = student.reader.loans.filter(status='active', due_date__lte=today + timedelta(days=3))
        for loan in loans.select_related('book')[:8]:
            notices.append({'key': f'loan:{loan.pk}:{loan.due_date}:{loan.is_overdue}',
                            'title': _('Срок возврата истёк') if loan.is_overdue else _('Скоро вернуть'),
                            'text': f'{loan.book.title} · {loan.due_date:%d.%m.%Y}',
                            'url': reverse('library:student_dashboard') + '#my-books', 'icon': 'book-open'})
        for item in student.book_requests.exclude(status='pending').select_related('book')[:8]:
            notices.append({'key': f'request:{item.pk}:{item.updated_at.isoformat()}',
                            'title': str(item.get_status_display()), 'text': item.book.title,
                            'url': reverse('library:my_requests'), 'icon': 'clipboard-list'})
    seen = set(request.session.get('smartkitap_seen_notifications', []))
    for notice in notices:
        notice['unread'] = notice['key'] not in seen
    return notices


def interface_context(request):
    notices = notifications_for(request)
    student = getattr(request.user, 'student_profile', None) if request.user.is_authenticated else None
    name = ''
    if request.user.is_authenticated:
        name = student.reader.full_name if student else (request.user.get_full_name() or request.user.username)
    return {'ui_notifications': notices, 'ui_unread_count': sum(n['unread'] for n in notices),
            'ui_user_name': name, 'ui_is_student': bool(student and not (request.user.is_staff or request.user.is_superuser))}
