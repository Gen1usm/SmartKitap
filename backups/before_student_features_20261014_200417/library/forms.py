from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Book, Loan, Reader


class BookForm(forms.ModelForm):
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
            if self.instance.pk:
                previous = Book.objects.get(pk=self.instance.pk)
                self.instance.available_copies = min(previous.available_copies, total_copies)
            else:
                self.instance.available_copies = total_copies

        return cleaned_data

    def save(self, commit=True):
        book = super().save(commit=False)
        if book.pk:
            previous = Book.objects.get(pk=book.pk)
            book.available_copies = min(previous.available_copies, book.total_copies)
        else:
            book.available_copies = book.total_copies

        if commit:
            book.full_clean()
            book.save()
            self.save_m2m()
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
        self.fields['book'].queryset = Book.objects.filter(available_copies__gt=0).order_by('title', 'author')
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

        if book and book.available_copies <= 0:
            self.add_error('book', _('У этой книги нет доступных экземпляров.'))

        return cleaned_data
