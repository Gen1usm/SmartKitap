"""All circulation changes share transactions and conditional stock updates.

SQLite ignores SELECT FOR UPDATE. A no-op book UPDATE acquires its write lock
before we read balances or request states; F expressions protect the counters.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Book, BookRequest, Loan, StudentProfile

ACTIVE_REQUESTS = [BookRequest.Status.PENDING, BookRequest.Status.APPROVED, BookRequest.Status.ISSUED]


def lock_book(book_id):
    Book.objects.filter(pk=book_id).update(available_copies=F('available_copies'))
    return Book.objects.get(pk=book_id)


def take_copy(book_id):
    changed = Book.objects.filter(pk=book_id, available_copies__gt=0).update(available_copies=F('available_copies') - 1)
    if not changed:
        raise ValidationError(_('У этой книги нет доступных экземпляров.'))


def release_copy(book_id):
    Book.objects.filter(pk=book_id, available_copies__lt=F('total_copies')).update(available_copies=F('available_copies') + 1)


def held_copies(book_id):
    return (Loan.objects.filter(book_id=book_id, status=Loan.Status.ACTIVE).count() +
            BookRequest.objects.filter(book_id=book_id, status=BookRequest.Status.APPROVED).count())


@transaction.atomic
def request_book(student, book_id):
    book = lock_book(book_id)
    if BookRequest.objects.filter(student=student, book=book, status__in=ACTIVE_REQUESTS).exists():
        raise ValidationError(_('У вас уже есть активный запрос на эту книгу.'))
    if Loan.objects.filter(reader=student.reader, book=book, status=Loan.Status.ACTIVE).exists():
        raise ValidationError(_('Эта книга уже выдана вам.'))
    if not book.is_available:
        raise ValidationError(_('У этой книги нет доступных экземпляров.'))
    return BookRequest.objects.create(student=student, book=book)


def review_request(request_id, action, note=''):
    initial = BookRequest.objects.get(pk=request_id)
    with transaction.atomic():
        lock_book(initial.book_id)
        book_request = BookRequest.objects.get(pk=request_id)
        if action == 'approve' and book_request.status == BookRequest.Status.PENDING:
            take_copy(book_request.book_id)
            book_request.status = BookRequest.Status.APPROVED
        elif action == 'reject' and book_request.status in [BookRequest.Status.PENDING, BookRequest.Status.APPROVED]:
            if book_request.status == BookRequest.Status.APPROVED:
                release_copy(book_request.book_id)
            book_request.status = BookRequest.Status.REJECTED
        else:
            raise ValidationError(_('Это действие недоступно для текущего статуса запроса.'))
        book_request.note = note
        book_request.save(update_fields=['status', 'note', 'updated_at'])
        return book_request


@transaction.atomic
def issue_book(book_id, reader, issue_date, due_date, request_id=None):
    lock_book(book_id)
    student = StudentProfile.objects.filter(reader=reader).first()
    book_request = None
    if student:
        book_request = BookRequest.objects.filter(student=student, book_id=book_id,
                                                  status__in=ACTIVE_REQUESTS).first()
        if Loan.objects.filter(book_id=book_id, reader=reader, status=Loan.Status.ACTIVE).exists():
            raise ValidationError(_('Эта книга уже выдана вам.'))
    if request_id and (not book_request or book_request.pk != request_id or
                       book_request.status != BookRequest.Status.APPROVED):
        raise ValidationError(_('Это действие недоступно для текущего статуса запроса.'))
    if not book_request or book_request.status != BookRequest.Status.APPROVED:
        take_copy(book_id)
    loan = Loan(book_id=book_id, reader=reader, issue_date=issue_date, due_date=due_date)
    loan.full_clean()
    loan.save()
    if book_request:
        book_request.status = BookRequest.Status.ISSUED
        book_request.loan = loan
        book_request.save(update_fields=['status', 'loan', 'updated_at'])
    return loan


def return_loan(loan_id):
    initial = Loan.objects.get(pk=loan_id)
    with transaction.atomic():
        lock_book(initial.book_id)
        loan = Loan.objects.get(pk=loan_id)
        if not loan.is_active:
            raise ValidationError(_('Эта выдача уже была возвращена.'))
        loan.status = Loan.Status.RETURNED
        loan.return_date = timezone.localdate()
        loan.full_clean()
        loan.save(update_fields=['status', 'return_date'])
        release_copy(loan.book_id)
        BookRequest.objects.filter(loan=loan).update(status=BookRequest.Status.RETURNED, updated_at=timezone.now())
        return loan
