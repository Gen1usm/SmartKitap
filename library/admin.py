from django.contrib import admin
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .forms import BookFileForm
from .models import Book, BookFile, BookRequest, Loan, Reader, ReadingProgress, StudentProfile
from .services import held_copies, lock_book


class BookInventoryAdminForm(forms.ModelForm):
    class Meta:
        model = Book
        exclude = ['available_copies', 'created_at']

    def clean(self):
        data = super().clean()
        total = data.get('total_copies')
        if total is not None:
            held = held_copies(self.instance.pk) if self.instance.pk else 0
            if total < held:
                self.add_error('total_copies', _('Количество экземпляров меньше числа выданных и зарезервированных книг.'))
            self.instance.available_copies = max(0, total - held)
        return data

    def save(self, commit=True):
        book = super().save(commit=False)
        if book.pk:
            lock_book(book.pk)
        held = held_copies(book.pk) if book.pk else 0
        if book.total_copies < held:
            raise ValidationError(_('Количество экземпляров меньше числа выданных и зарезервированных книг.'))
        book.available_copies = book.total_copies - held
        if commit:
            book.save()
            self.save_m2m()
        return book


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    form = BookInventoryAdminForm
    list_display = (
        'title',
        'author',
        'genre',
        'publication_year',
        'total_copies',
        'available_copies',
        'created_at',
    )
    list_filter = ('genre', 'publication_year', 'created_at')
    search_fields = ('title', 'author')
    readonly_fields = ('created_at', 'available_copies')
    ordering = ('title', 'author')


@admin.register(Reader)
class ReaderAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'class_name', 'contact', 'created_at')
    list_filter = ('class_name', 'created_at')
    search_fields = ('full_name', 'class_name', 'contact')
    readonly_fields = ('created_at',)
    ordering = ('full_name',)


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ('book', 'reader', 'issue_date', 'due_date', 'return_date', 'status', 'is_overdue')
    list_filter = ('status', 'issue_date', 'due_date', 'return_date')
    search_fields = ('book__title', 'book__author', 'reader__full_name', 'reader__class_name')
    # Circulation is performed by the app services, including linked requests.
    readonly_fields = ('book', 'reader', 'issue_date', 'due_date', 'return_date', 'status', 'created_at')
    autocomplete_fields = ('book', 'reader')

    @admin.display(boolean=True, description='Просрочена')
    def is_overdue(self, obj):
        return obj.is_overdue

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class HistoryAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StudentProfile)
class StudentProfileAdmin(HistoryAdmin):
    list_display = ('reader', 'user')
    search_fields = ('reader__full_name', 'user__username')


@admin.register(BookRequest)
class BookRequestAdmin(HistoryAdmin):
    list_display = ('book', 'student', 'status', 'created_at')
    list_filter = ('status',)


@admin.register(BookFile)
class BookFileAdmin(admin.ModelAdmin):
    form = BookFileForm
    list_display = ('book', 'online_enabled', 'rights_confirmed', 'uploaded_at')
    # Upload/replace files in the app; admin is for access settings only.
    fields = ('book', 'online_enabled', 'allowed_students', 'rights_confirmed', 'source_url')
    readonly_fields = ('book',)

    def has_add_permission(self, request):
        return False


@admin.register(ReadingProgress)
class ReadingProgressAdmin(HistoryAdmin):
    list_display = ('student', 'book', 'position', 'updated_at')
