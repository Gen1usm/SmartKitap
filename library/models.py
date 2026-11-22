import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .storage import PrivateBookStorage
from .validators import validate_book_file


class Book(models.Model):
    title = models.CharField(_('Название'), max_length=200)
    author = models.CharField(_('Автор'), max_length=160)
    publication_year = models.PositiveIntegerField(
        _('Год издания'),
        validators=[
            MinValueValidator(1000, message=_('Год издания должен быть не раньше 1000.')),
            MaxValueValidator(2100, message=_('Год издания должен быть не позже 2100.')),
        ],
    )
    genre = models.CharField(_('Жанр'), max_length=100)
    description = models.TextField(_('Описание'), blank=True)
    total_copies = models.PositiveIntegerField(
        _('Всего экземпляров'),
        validators=[MinValueValidator(0, message=_('Количество экземпляров не может быть отрицательным.'))],
    )
    available_copies = models.PositiveIntegerField(
        _('Доступно экземпляров'),
        validators=[MinValueValidator(0, message=_('Количество доступных экземпляров не может быть отрицательным.'))],
    )
    cover = models.ImageField(
        _('Обложка'),
        upload_to='book_covers/',
        blank=True,
        null=True,
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])],
    )
    created_at = models.DateTimeField(_('Дата добавления'), auto_now_add=True)

    class Meta:
        ordering = ['title', 'author']
        verbose_name = _('Книга')
        verbose_name_plural = _('Книги')
        constraints = [
            models.CheckConstraint(
                condition=Q(total_copies__gte=0),
                name='book_total_copies_non_negative',
            ),
            models.CheckConstraint(
                condition=Q(available_copies__gte=0),
                name='book_available_copies_non_negative',
            ),
            models.CheckConstraint(
                condition=Q(available_copies__lte=F('total_copies')),
                name='book_available_not_more_than_total',
            ),
        ]

    def __str__(self):
        return f'{self.title} — {self.author}'

    @property
    def is_available(self):
        return self.available_copies > 0

    @property
    def has_online_version(self):
        try:
            return self.digital_file.online_enabled and self.digital_file.file_exists
        except BookFile.DoesNotExist:
            return False

    def clean(self):
        super().clean()
        if self.available_copies is None or self.total_copies is None:
            return
        if self.available_copies > self.total_copies:
            raise ValidationError({
                'available_copies': _('Доступных экземпляров не может быть больше общего количества.'),
            })


class Reader(models.Model):
    full_name = models.CharField(_('ФИО'), max_length=180)
    class_name = models.CharField(_('Класс'), max_length=40)
    contact = models.CharField(_('Контакт'), max_length=160, blank=True)
    created_at = models.DateTimeField(_('Дата регистрации'), auto_now_add=True)

    class Meta:
        ordering = ['full_name']
        verbose_name = _('Читатель')
        verbose_name_plural = _('Читатели')

    def __str__(self):
        return f'{self.full_name} — {self.class_name}'


class Loan(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', _('Активная выдача')
        RETURNED = 'returned', _('Книга возвращена')

    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name='loans', verbose_name=_('Книга'))
    reader = models.ForeignKey(Reader, on_delete=models.PROTECT, related_name='loans', verbose_name=_('Читатель'))
    issue_date = models.DateField(_('Дата выдачи'), default=timezone.localdate)
    due_date = models.DateField(_('Срок возврата'))
    return_date = models.DateField(_('Дата возврата'), blank=True, null=True)
    status = models.CharField(
        _('Статус'),
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    created_at = models.DateTimeField(_('Дата создания'), auto_now_add=True)

    class Meta:
        ordering = ['-issue_date', '-created_at']
        verbose_name = _('Выдача')
        verbose_name_plural = _('Выдачи')
        constraints = [
            models.CheckConstraint(
                condition=Q(due_date__gte=F('issue_date')),
                name='loan_due_date_not_before_issue_date',
            ),
        ]

    def __str__(self):
        return f'{self.book.title} — {self.reader.full_name}'

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    @property
    def is_overdue(self):
        return self.is_active and self.due_date < timezone.localdate()

    @property
    def display_status(self):
        if self.is_overdue:
            return _('Просрочена')
        if self.status == self.Status.RETURNED:
            return _('Возвращена')
        if self.is_due_soon:
            return _('Скоро вернуть')
        return _('Активная')

    @property
    def is_due_soon(self):
        return self.is_active and 0 <= (self.due_date - timezone.localdate()).days <= 3

    @property
    def status_tone(self):
        if self.is_overdue:
            return 'overdue'
        if self.status == self.Status.RETURNED:
            return 'returned'
        return 'due-soon' if self.is_due_soon else 'active'

    @property
    def overdue_days(self):
        if not self.is_overdue:
            return 0
        return (timezone.localdate() - self.due_date).days

    def clean(self):
        super().clean()
        if self.due_date and self.issue_date and self.due_date < self.issue_date:
            raise ValidationError({'due_date': _('Срок возврата не может быть раньше даты выдачи.')})
        if self.status == self.Status.RETURNED and not self.return_date:
            raise ValidationError({'return_date': _('Для возвращённой книги укажите дату возврата.')})
        if self.return_date and self.return_date < self.issue_date:
            raise ValidationError({'return_date': _('Дата возврата не может быть раньше даты выдачи.')})


class StudentProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                related_name='student_profile', verbose_name=_('Пользователь'))
    reader = models.OneToOneField(Reader, on_delete=models.PROTECT,
                                  related_name='student_profile', verbose_name=_('Читатель'))

    class Meta:
        ordering = ['reader__full_name']
        verbose_name = _('Ученик')
        verbose_name_plural = _('Ученики')

    def __str__(self):
        return str(self.reader)


class BookRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', _('Ожидает')
        APPROVED = 'approved', _('Принята')
        REJECTED = 'rejected', _('Отклонена')
        ISSUED = 'issued', _('Выдана')
        RETURNED = 'returned', _('Возвращена')

    student = models.ForeignKey(StudentProfile, on_delete=models.PROTECT, related_name='book_requests',
                                verbose_name=_('Ученик'))
    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name='book_requests', verbose_name=_('Книга'))
    loan = models.OneToOneField(Loan, on_delete=models.PROTECT, related_name='book_request',
                                blank=True, null=True, verbose_name=_('Выдача'))
    status = models.CharField(_('Статус'), max_length=12, choices=Status.choices, default=Status.PENDING)
    note = models.CharField(_('Комментарий библиотекаря'), max_length=500, blank=True)
    created_at = models.DateTimeField(_('Дата создания'), auto_now_add=True)
    updated_at = models.DateTimeField(_('Дата изменения'), auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('Запрос книги')
        verbose_name_plural = _('Запросы книг')
        constraints = [
            models.UniqueConstraint(fields=['student', 'book'],
                                    condition=Q(status__in=['pending', 'approved', 'issued']),
                                    name='one_active_request_per_student_book'),
            models.CheckConstraint(condition=(Q(status__in=['issued', 'returned'], loan__isnull=False) |
                                              Q(status__in=['pending', 'approved', 'rejected'], loan__isnull=True)),
                                   name='request_status_matches_loan'),
        ]

    def __str__(self):
        return f'{self.student} — {self.book.title}'


def book_file_path(instance, filename):
    return f'books/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'


class BookFile(models.Model):
    book = models.OneToOneField(Book, on_delete=models.CASCADE, related_name='digital_file', verbose_name=_('Книга'))
    file = models.FileField(_('Файл книги'), storage=PrivateBookStorage(), upload_to=book_file_path,
                            validators=[validate_book_file])
    online_enabled = models.BooleanField(_('Доступна для онлайн-чтения'), default=True)
    allowed_students = models.ManyToManyField(StudentProfile, blank=True, verbose_name=_('Разрешённые ученики'),
                                              help_text=_('Пустой список: доступ всем вошедшим ученикам.'))
    rights_confirmed = models.BooleanField(_('Право на использование подтверждено'), default=False)
    source_url = models.URLField(_('Источник или лицензия'), blank=True)
    uploaded_at = models.DateTimeField(_('Дата загрузки'), auto_now=True)

    class Meta:
        verbose_name = _('Электронная книга')
        verbose_name_plural = _('Электронные книги')

    @property
    def format(self):
        return Path(self.file.name).suffix.lower().lstrip('.')

    @property
    def file_exists(self):
        try:
            return bool(self.file.name and Path(self.file.path).is_file())
        except (OSError, ValueError, SuspiciousFileOperation, ValidationError):
            return False

    def clean(self):
        super().clean()
        if not self.rights_confirmed:
            raise ValidationError({'rights_confirmed': _('Подтвердите право на использование файла.')})

    def __str__(self):
        return self.book.title


class ReadingProgress(models.Model):
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='reading_progress')
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name='reading_progress')
    position = models.PositiveIntegerField(_('Страница или глава'), default=1, validators=[MinValueValidator(1)])
    updated_at = models.DateTimeField(_('Дата изменения'), auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [models.UniqueConstraint(fields=['student', 'book'], name='unique_reading_progress'),
                       models.CheckConstraint(condition=Q(position__gte=1), name='reading_position_positive')]
        verbose_name = _('Прогресс чтения')
        verbose_name_plural = _('Прогресс чтения')
