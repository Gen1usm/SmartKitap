import csv

from django.contrib import messages
from django.contrib.auth.views import LoginView as AuthLoginView
from django.contrib.auth.views import LogoutView as AuthLogoutView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView

from .forms import BookForm, LoanForm, ReaderForm
from .models import Book, Loan, Reader


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        user = self.request.user
        return user.is_staff or user.is_superuser


class DashboardView(StaffRequiredMixin, TemplateView):
    template_name = 'library/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        active_loans = Loan.objects.filter(status=Loan.Status.ACTIVE)
        overdue_loans = active_loans.filter(due_date__lt=today)
        totals = Book.objects.aggregate(
            total_copies=Sum('total_copies'),
            available_copies=Sum('available_copies'),
        )
        context['total_copies'] = totals['total_copies'] or 0
        context['available_copies'] = totals['available_copies'] or 0
        context['issued_copies'] = active_loans.count()
        context['readers_count'] = Reader.objects.count()
        context['active_loans_count'] = active_loans.count()
        context['overdue_loans_count'] = overdue_loans.count()
        context['latest_loans'] = Loan.objects.select_related('book', 'reader').order_by('-created_at')[:5]
        return context


class ReportsView(StaffRequiredMixin, TemplateView):
    template_name = 'library/reports.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        context['current_loans'] = Loan.objects.select_related('book', 'reader').filter(
            status=Loan.Status.ACTIVE,
            due_date__gte=today,
        )
        context['overdue_loans'] = Loan.objects.select_related('book', 'reader').filter(
            status=Loan.Status.ACTIVE,
            due_date__lt=today,
        )
        context['loan_history'] = Loan.objects.select_related('book', 'reader').all()
        context['popular_books'] = Book.objects.annotate(loans_count=Count('loans')).filter(
            loans_count__gt=0,
        ).order_by('-loans_count', 'title')[:20]
        context['today'] = today
        return context


class BooksView(ListView):
    model = Book
    template_name = 'library/books.html'
    context_object_name = 'books'
    paginate_by = 9

    def get_queryset(self):
        queryset = Book.objects.all()
        query = self.request.GET.get('q', '').strip()
        genre = self.request.GET.get('genre', '').strip()
        availability = self.request.GET.get('availability', '').strip()
        view_mode = self.request.GET.get('view', '').strip()

        if query:
            query_variants = {
                query,
                query.lower(),
                query.upper(),
                query.capitalize(),
                query.title(),
            }
            search_filter = Q()
            for variant in query_variants:
                search_filter |= Q(title__icontains=variant) | Q(author__icontains=variant)
            queryset = queryset.filter(search_filter)

        if genre:
            queryset = queryset.filter(genre=genre)

        if availability == 'available':
            queryset = queryset.filter(available_copies__gt=0)
        elif availability == 'unavailable':
            queryset = queryset.filter(available_copies=0)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop('page', None)
        view_mode = self.request.GET.get('view', '').strip()
        context['total_books'] = Book.objects.count()
        context['genres'] = Book.objects.order_by('genre').values_list('genre', flat=True).distinct()
        context['query'] = self.request.GET.get('q', '').strip()
        context['selected_genre'] = self.request.GET.get('genre', '').strip()
        context['selected_availability'] = self.request.GET.get('availability', '').strip()
        context['view_mode'] = 'table' if view_mode == 'table' else 'grid'
        context['querystring'] = params.urlencode()
        view_params = self.request.GET.copy()
        view_params.pop('view', None)
        context['view_querystring'] = view_params.urlencode()
        context['filters_active'] = any([
            context['query'],
            context['selected_genre'],
            context['selected_availability'],
        ])
        return context


class BookCreateView(StaffRequiredMixin, CreateView):
    model = Book
    form_class = BookForm
    template_name = 'library/book_form.html'
    success_url = reverse_lazy('library:books')

    def form_valid(self, form):
        messages.success(self.request, _('Книга успешно добавлена в каталог.'))
        return super().form_valid(form)


class BookUpdateView(StaffRequiredMixin, UpdateView):
    model = Book
    form_class = BookForm
    template_name = 'library/book_form.html'
    success_url = reverse_lazy('library:books')

    def form_valid(self, form):
        messages.success(self.request, _('Информация о книге обновлена.'))
        return super().form_valid(form)


class BookDeleteView(StaffRequiredMixin, DeleteView):
    model = Book
    template_name = 'library/book_confirm_delete.html'
    success_url = reverse_lazy('library:books')

    def form_valid(self, form):
        messages.success(self.request, _('Книга удалена из каталога.'))
        return super().form_valid(form)


class ReadersView(StaffRequiredMixin, ListView):
    model = Reader
    template_name = 'library/readers.html'
    context_object_name = 'readers'
    paginate_by = 10

    def get_queryset(self):
        queryset = Reader.objects.all()
        query = self.request.GET.get('q', '').strip()
        class_name = self.request.GET.get('class_name', '').strip()

        if query:
            query_variants = {
                query,
                query.lower(),
                query.upper(),
                query.capitalize(),
                query.title(),
            }
            search_filter = Q()
            for variant in query_variants:
                search_filter |= Q(full_name__icontains=variant)
            queryset = queryset.filter(search_filter)

        if class_name:
            queryset = queryset.filter(class_name=class_name)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop('page', None)
        context['total_readers'] = Reader.objects.count()
        context['classes'] = Reader.objects.order_by('class_name').values_list('class_name', flat=True).distinct()
        context['query'] = self.request.GET.get('q', '').strip()
        context['selected_class'] = self.request.GET.get('class_name', '').strip()
        context['querystring'] = params.urlencode()
        context['filters_active'] = any([context['query'], context['selected_class']])
        return context


class ReaderCreateView(StaffRequiredMixin, CreateView):
    model = Reader
    form_class = ReaderForm
    template_name = 'library/reader_form.html'
    success_url = reverse_lazy('library:readers')

    def form_valid(self, form):
        messages.success(self.request, _('Читатель успешно добавлен.'))
        return super().form_valid(form)


class ReaderUpdateView(StaffRequiredMixin, UpdateView):
    model = Reader
    form_class = ReaderForm
    template_name = 'library/reader_form.html'
    success_url = reverse_lazy('library:readers')

    def form_valid(self, form):
        messages.success(self.request, _('Информация о читателе обновлена.'))
        return super().form_valid(form)


class ReaderDeleteView(StaffRequiredMixin, DeleteView):
    model = Reader
    template_name = 'library/reader_confirm_delete.html'
    success_url = reverse_lazy('library:readers')

    def form_valid(self, form):
        messages.success(self.request, _('Читатель удалён.'))
        return super().form_valid(form)


class LoansView(StaffRequiredMixin, ListView):
    model = Loan
    template_name = 'library/loans.html'
    context_object_name = 'loans'
    paginate_by = 10

    def get_queryset(self):
        queryset = Loan.objects.select_related('book', 'reader').all()
        query = self.request.GET.get('q', '').strip()
        status = self.request.GET.get('status', '').strip()
        today = timezone.localdate()

        if query:
            query_variants = {
                query,
                query.lower(),
                query.upper(),
                query.capitalize(),
                query.title(),
            }
            search_filter = Q()
            for variant in query_variants:
                search_filter |= Q(book__title__icontains=variant) | Q(reader__full_name__icontains=variant)
            queryset = queryset.filter(search_filter)

        if status == 'active':
            queryset = queryset.filter(status=Loan.Status.ACTIVE, due_date__gte=today)
        elif status == 'returned':
            queryset = queryset.filter(status=Loan.Status.RETURNED)
        elif status == 'overdue':
            queryset = queryset.filter(status=Loan.Status.ACTIVE, due_date__lt=today)

        return queryset

    def get_context_data(self, **kwargs):
        form = kwargs.pop('form', None)
        context = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop('page', None)
        context['form'] = form or LoanForm()
        context['total_loans'] = Loan.objects.count()
        context['query'] = self.request.GET.get('q', '').strip()
        context['selected_status'] = self.request.GET.get('status', '').strip()
        context['querystring'] = params.urlencode()
        context['filters_active'] = any([context['query'], context['selected_status']])
        context['overdue_loans_count'] = Loan.objects.filter(
            status=Loan.Status.ACTIVE,
            due_date__lt=timezone.localdate(),
        ).count()
        context['available_books_count'] = Book.objects.filter(available_copies__gt=0).count()
        context['readers_count'] = Reader.objects.count()
        return context

    def post(self, request, *args, **kwargs):
        form = LoanForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                book = Book.objects.select_for_update().get(pk=form.cleaned_data['book'].pk)
                if book.available_copies <= 0:
                    form.add_error('book', _('У этой книги нет доступных экземпляров.'))
                else:
                    Loan.objects.create(
                        book=book,
                        reader=form.cleaned_data['reader'],
                        issue_date=form.cleaned_data['issue_date'],
                        due_date=form.cleaned_data['due_date'],
                        status=Loan.Status.ACTIVE,
                    )
                    book.available_copies -= 1
                    book.full_clean()
                    book.save(update_fields=['available_copies'])
                    messages.success(request, _('Книга успешно выдана читателю.'))
                    return redirect('library:loans')

        self.object_list = self.get_queryset()
        return self.render_to_response(self.get_context_data(form=form))


class LoanReturnView(StaffRequiredMixin, View):
    def post(self, request, pk):
        with transaction.atomic():
            loan = get_object_or_404(
                Loan.objects.select_for_update().select_related('book'),
                pk=pk,
            )

            if loan.status == Loan.Status.RETURNED:
                messages.error(request, _('Эта выдача уже была возвращена.'))
                return redirect('library:loans')

            book = Book.objects.select_for_update().get(pk=loan.book_id)
            loan.return_date = timezone.localdate()
            loan.status = Loan.Status.RETURNED
            loan.full_clean()
            loan.save(update_fields=['return_date', 'status'])

            book.available_copies = min(book.available_copies + 1, book.total_copies)
            book.full_clean()
            book.save(update_fields=['available_copies'])

        messages.success(request, _('Книга успешно возвращена.'))
        return redirect('library:loans')


class ReportCSVView(StaffRequiredMixin, View):
    report_titles = {
        'current': _('Текущие выдачи'),
        'overdue': _('Просроченные книги'),
        'history': _('История операций'),
        'popular': _('Популярные книги'),
    }

    def get(self, request, report_type):
        if report_type not in self.report_titles:
            return redirect('library:reports')

        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        filename = f'smartkitap_{report_type}.csv'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response.write('\ufeff')
        writer = csv.writer(response)
        today = timezone.localdate()

        if report_type == 'current':
            writer.writerow(['Книга', 'Автор', 'Читатель', 'Класс', 'Дата выдачи', 'Срок возврата'])
            loans = Loan.objects.select_related('book', 'reader').filter(
                status=Loan.Status.ACTIVE,
                due_date__gte=today,
            )
            for loan in loans:
                writer.writerow([
                    loan.book.title,
                    loan.book.author,
                    loan.reader.full_name,
                    loan.reader.class_name,
                    loan.issue_date.isoformat(),
                    loan.due_date.isoformat(),
                ])
        elif report_type == 'overdue':
            writer.writerow(['Книга', 'Автор', 'Читатель', 'Класс', 'Срок возврата', 'Дней просрочки'])
            loans = Loan.objects.select_related('book', 'reader').filter(
                status=Loan.Status.ACTIVE,
                due_date__lt=today,
            )
            for loan in loans:
                writer.writerow([
                    loan.book.title,
                    loan.book.author,
                    loan.reader.full_name,
                    loan.reader.class_name,
                    loan.due_date.isoformat(),
                    (today - loan.due_date).days,
                ])
        elif report_type == 'history':
            writer.writerow(['Книга', 'Автор', 'Читатель', 'Класс', 'Дата выдачи', 'Срок возврата', 'Дата возврата', 'Статус'])
            for loan in Loan.objects.select_related('book', 'reader').all():
                writer.writerow([
                    loan.book.title,
                    loan.book.author,
                    loan.reader.full_name,
                    loan.reader.class_name,
                    loan.issue_date.isoformat(),
                    loan.due_date.isoformat(),
                    loan.return_date.isoformat() if loan.return_date else '',
                    str(loan.display_status),
                ])
        elif report_type == 'popular':
            writer.writerow(['Книга', 'Автор', 'Количество выдач'])
            books = Book.objects.annotate(loans_count=Count('loans')).filter(
                loans_count__gt=0,
            ).order_by('-loans_count', 'title')
            for book in books:
                writer.writerow([book.title, book.author, book.loans_count])

        return response


class LoginView(AuthLoginView):
    template_name = 'library/login.html'
    redirect_authenticated_user = True

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        for field in form.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
        return form


class LogoutView(AuthLogoutView):
    pass
