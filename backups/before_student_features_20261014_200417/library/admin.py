from django.contrib import admin

from .models import Book, Loan, Reader


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
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
    readonly_fields = ('created_at',)
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
    readonly_fields = ('created_at',)
    autocomplete_fields = ('book', 'reader')

    @admin.display(boolean=True, description='Просрочена')
    def is_overdue(self, obj):
        return obj.is_overdue
