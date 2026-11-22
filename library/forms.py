from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Book, BookFile, BookRequest, Loan, Reader, ReadingProgress, StudentProfile
from .services import held_copies, lock_book
from .validators import validate_book_file
from .metadata import download_cover


class BookForm(forms.ModelForm):
    cover_import_id = forms.IntegerField(required=False, min_value=1, max_value=10**12, widget=forms.HiddenInput)
    ebook_file = forms.FileField(label=_('Файл книги'), required=False, validators=[validate_book_file],
                                help_text=_('PDF или EPUB до 25 МБ. Загрузите только файл с разрешением на использование.'))
    online_enabled = forms.BooleanField(label=_('Доступна для онлайн-чтения'), required=False, initial=True)
    rights_confirmed = forms.BooleanField(label=_('Право на использование подтверждено'), required=False)
    source_url = forms.URLField(label=_('Источник или лицензия'), required=False)
    allowed_students = forms.ModelMultipleChoiceField(label=_('Разрешённые ученики'), required=False,
                                                       queryset=StudentProfile.objects.select_related('reader'),
                                                       help_text=_('Пустой список: доступ всем вошедшим ученикам.'))

    class Meta:
        model = Book
        fields = [
            'title',
            'author',
            'publication_year',
            'genre',
            'description',
            'total_copies',
            'cover',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
        }
        labels = {
            'title': _('Название'),
            'author': _('Автор'),
            'publication_year': _('Год издания'),
            'genre': _('Жанр'),
            'description': _('Описание'),
            'total_copies': _('Общее количество экземпляров'),
            'cover': _('Обложка'),
        }
        help_texts = {
            'cover': _('Необязательно. Поддерживаются JPG, PNG и WEBP до 2 МБ.'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
        self.fields['cover'].widget.attrs.update({'accept': 'image/jpeg,image/png,image/webp'})
        self.fields['ebook_file'].widget.attrs['accept'] = '.pdf,.epub'
        for name in ['online_enabled', 'rights_confirmed']:
            self.fields[name].widget.attrs['class'] = 'form-check-input'
        if self.instance.pk:
            digital = BookFile.objects.filter(book=self.instance).first()
            if digital:
                for name in ['online_enabled', 'rights_confirmed', 'source_url']:
                    self.fields[name].initial = getattr(digital, name)
                self.fields['allowed_students'].initial = digital.allowed_students.all()

    def clean_title(self):
        return self.cleaned_data['title'].strip()

    def clean_author(self):
        return self.cleaned_data['author'].strip()

    def clean_genre(self):
        return self.cleaned_data['genre'].strip()

    def clean_cover(self):
        cover = self.cleaned_data.get('cover')
        if not cover:
            return cover

        max_size = 2 * 1024 * 1024
        if cover.size > max_size:
            raise forms.ValidationError(_('Размер обложки не должен превышать 2 МБ.'))

        content_type = getattr(cover, 'content_type', '')
        allowed_types = {'image/jpeg', 'image/png', 'image/webp'}
        if content_type and content_type not in allowed_types:
            raise forms.ValidationError(_('Загрузите изображение в формате JPG, PNG или WEBP.'))

        return cover

    def clean(self):
        cleaned_data = super().clean()
        total_copies = cleaned_data.get('total_copies')

        if total_copies is not None:
            held = held_copies(self.instance.pk) if self.instance.pk else 0
            if total_copies < held:
                self.add_error('total_copies', _('Количество экземпляров меньше числа выданных и зарезервированных книг.'))
            self.instance.available_copies = max(0, total_copies - held)
        if cleaned_data.get('ebook_file') and not cleaned_data.get('rights_confirmed'):
            self.add_error('rights_confirmed', _('Подтвердите право на использование файла.'))
        if cleaned_data.get('cover_import_id') and not self.files.get('cover'):
            try:
                cleaned_data['cover'] = download_cover(cleaned_data['cover_import_id'])
            except Exception:
                self.add_error('cover', _('Не удалось загрузить обложку. Загрузите изображение вручную или отключите импорт.'))

        return cleaned_data

    @transaction.atomic
    def save(self, commit=True):
        book = super().save(commit=False)
        if book.pk:
            lock_book(book.pk)
            held = held_copies(book.pk)
            if book.total_copies < held:
                raise forms.ValidationError(_('Количество экземпляров меньше числа выданных и зарезервированных книг.'))
            book.available_copies = book.total_copies - held
        else:
            book.available_copies = book.total_copies

        if commit:
            book.full_clean()
            book.save()
            self.save_m2m()
            uploaded = self.cleaned_data.get('ebook_file')
            digital = BookFile.objects.filter(book=book).first()
            if uploaded or digital:
                digital = digital or BookFile(book=book)
                old_name = digital.file.name
                if uploaded:
                    digital.file = uploaded
                for name in ['online_enabled', 'source_url']:
                    setattr(digital, name, self.cleaned_data.get(name))
                digital.rights_confirmed = bool(uploaded or digital.rights_confirmed)
                digital.full_clean(exclude=['file'] if not uploaded else None)
                digital.save()
                digital.allowed_students.set(self.cleaned_data.get('allowed_students', []))
                if uploaded:
                    ReadingProgress.objects.filter(book=book).delete()
                    if old_name:
                        transaction.on_commit(lambda: digital.file.storage.delete(old_name))
        return book


class ReaderForm(forms.ModelForm):
    class Meta:
        model = Reader
        fields = ['full_name', 'class_name', 'contact']
        labels = {
            'full_name': _('ФИО'),
            'class_name': _('Класс'),
            'contact': _('Контакт'),
        }
        help_texts = {
            'contact': _('Необязательно. Укажите телефон или e-mail только при необходимости.'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')

    def clean_full_name(self):
        full_name = self.cleaned_data['full_name'].strip()
        if len(full_name.split()) < 2:
            raise forms.ValidationError(_('Укажите фамилию и имя читателя.'))
        return full_name

    def clean_class_name(self):
        return self.cleaned_data['class_name'].strip()

    def clean_contact(self):
        return self.cleaned_data.get('contact', '').strip()


class LoanForm(forms.ModelForm):
    class Meta:
        model = Loan
        fields = ['reader', 'book', 'issue_date', 'due_date']
        labels = {
            'reader': _('Читатель'),
            'book': _('Книга'),
            'issue_date': _('Дата выдачи'),
            'due_date': _('Срок возврата'),
        }
        widgets = {
            'issue_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'due_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['book'].queryset = Book.objects.filter(
            Q(available_copies__gt=0) | Q(book_requests__status=BookRequest.Status.APPROVED)
        ).distinct().order_by('title', 'author')
        self.fields['reader'].queryset = Reader.objects.order_by('full_name')
        self.fields['issue_date'].initial = self.fields['issue_date'].initial or timezone.localdate()
        self.fields['issue_date'].input_formats = ['%Y-%m-%d']
        self.fields['due_date'].input_formats = ['%Y-%m-%d']
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
        self.fields['book'].empty_label = _('Выберите книгу')
        self.fields['reader'].empty_label = _('Выберите читателя')

    def clean(self):
        cleaned_data = super().clean()
        issue_date = cleaned_data.get('issue_date')
        due_date = cleaned_data.get('due_date')
        book = cleaned_data.get('book')

        if issue_date and due_date and due_date < issue_date:
            self.add_error('due_date', _('Срок возврата не может быть раньше даты выдачи.'))

        reader = cleaned_data.get('reader')
        reserved = book and reader and BookRequest.objects.filter(
            book=book, student__reader=reader, status=BookRequest.Status.APPROVED).exists()
        if book and book.available_copies <= 0 and not reserved:
            self.add_error('book', _('У этой книги нет доступных экземпляров.'))

        return cleaned_data


class StudentRegistrationForm(UserCreationForm):
    full_name = forms.CharField(label=_('ФИО'), max_length=180)
    class_name = forms.CharField(label=_('Класс'), max_length=40)
    contact = forms.CharField(label=_('Контакт'), max_length=160, required=False)

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ['full_name', 'class_name', 'username', 'contact', 'password1', 'password2']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = _('Имя пользователя')
        self.fields['password1'].label = _('Пароль')
        self.fields['password2'].label = _('Повторите пароль')
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

    def clean_full_name(self):
        name = self.cleaned_data['full_name'].strip()
        if len(name.split()) < 2:
            raise forms.ValidationError(_('Укажите фамилию и имя читателя.'))
        return name

    @transaction.atomic
    def save(self, commit=True):
        if not commit:
            raise ValueError('Student registration requires an atomic save.')
        user = super().save()
        reader = self.cleaned_data.get('existing_reader')
        if reader is None:
            reader = Reader.objects.create(full_name=self.cleaned_data['full_name'],
                                           class_name=self.cleaned_data['class_name'],
                                           contact=self.cleaned_data.get('contact', ''))
        StudentProfile.objects.create(user=user, reader=reader)
        return user


class StaffStudentCreateForm(StudentRegistrationForm):
    existing_reader = forms.ModelChoiceField(label=_('Связать с существующим читателем'), required=False,
        queryset=Reader.objects.filter(student_profile__isnull=True),
        help_text=_('Выберите читателя, чтобы сохранить его прежние выдачи.'))


class StudentEditForm(ReaderForm):
    is_active = forms.BooleanField(label=_('Аккаунт активен'), required=False)

    def __init__(self, *args, student, **kwargs):
        self.student = student
        super().__init__(*args, instance=student.reader, **kwargs)
        self.fields['is_active'].initial = student.user.is_active
        self.fields['is_active'].widget.attrs['class'] = 'form-check-input'

    @transaction.atomic
    def save(self, commit=True):
        reader = super().save(commit=commit)
        if commit:
            self.student.user.is_active = self.cleaned_data['is_active']
            self.student.user.save(update_fields=['is_active'])
        return reader


class BookFileForm(forms.ModelForm):
    rights_confirmed = forms.BooleanField(label=_('Право на использование подтверждено'), required=True)

    class Meta:
        model = BookFile
        fields = ['file', 'online_enabled', 'allowed_students', 'rights_confirmed', 'source_url']
        widgets = {'file': forms.FileInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control'
        if 'file' in self.fields:
            self.fields['file'].widget.attrs['accept'] = '.pdf,.epub'
            self.fields['file'].help_text = _('PDF или EPUB до 25 МБ. Загрузите только файл с разрешением на использование.')
        self.fields['allowed_students'].queryset = StudentProfile.objects.select_related('reader')

    @transaction.atomic
    def save(self, commit=True):
        lock_book(self.instance.book_id)
        old = BookFile.objects.filter(pk=self.instance.pk).first()
        digital = super().save(commit=commit)
        if commit and 'file' in self.changed_data:
            ReadingProgress.objects.filter(book=digital.book).delete()
            if old and old.file.name != digital.file.name:
                transaction.on_commit(lambda: old.file.delete(save=False))
        return digital


class RequestActionForm(forms.Form):
    action = forms.ChoiceField(choices=[('approve', _('Принять')), ('reject', _('Отклонить')), ('issue', _('Выдать книгу'))])
    note = forms.CharField(label=_('Комментарий библиотекаря'), max_length=500, required=False)
    due_date = forms.DateField(label=_('Срок возврата'), required=False,
                               widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}))

    def clean(self):
        data = super().clean()
        if data.get('action') == 'issue':
            if not data.get('due_date') or data['due_date'] < timezone.localdate():
                self.add_error('due_date', _('Укажите срок возврата не раньше сегодняшней даты.'))
        return data


class ReadingPositionForm(forms.Form):
    position = forms.IntegerField(label=_('Страница или глава'), min_value=1)
