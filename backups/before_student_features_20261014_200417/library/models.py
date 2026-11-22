from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


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
        return _('Активная')

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
