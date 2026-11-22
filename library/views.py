import csv
import zipfile
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.views import LoginView as AuthLoginView
from django.contrib.auth.views import LogoutView as AuthLogoutView
from django.contrib.auth import login
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DeleteView, FormView, ListView, TemplateView, UpdateView
from django.utils.decorators import method_decorator
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.cache import never_cache
from django.utils.safestring import mark_safe

from .forms import (BookFileForm, BookForm, LoanForm, ReaderForm, ReadingPositionForm,
                    RequestActionForm, StaffStudentCreateForm, StudentEditForm, StudentRegistrationForm)
from .models import Book, BookFile, BookRequest, Loan, Reader, ReadingProgress, StudentProfile
from .services import ACTIVE_REQUESTS, issue_book, request_book, return_loan, review_request
from .ebooks import epub_contents, safe_chapter
from .validators import pdf_page_count
from .metadata import search_metadata, work_description
from .pdf_responses import ranged_file_response
from .storage import EXISTING_BOOK_PREFIX
from .presentation import catalog_for, can_read_book, notifications_for


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        user = self.request.user
        return user.is_staff or user.is_superuser


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'library/dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not (request.user.is_staff or request.user.is_superuser):
            return redirect('library:student_dashboard')
        return super().dispatch(request, *args, **kwargs)

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
        context['pending_requests_count'] = BookRequest.objects.filter(status=BookRequest.Status.PENDING).count()
        context['reserved_copies'] = BookRequest.objects.filter(status=BookRequest.Status.APPROVED).count()
        context['recommendations'] = catalog_for(self.request.user)[:4]
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
        queryset = catalog_for(self.request.user)
        if self.request.user.is_authenticated and hasattr(self.request.user, 'student_profile'):
            queryset = queryset.annotate(has_active_request=Exists(BookRequest.objects.filter(
                student=self.request.user.student_profile, book_id=OuterRef('pk'), status__in=ACTIVE_REQUESTS)))
        if self.request.resolver_match.url_name == 'online_books':
            queryset = queryset.filter(digital_file__online_enabled=True)
        query = self.request.GET.get('q', '').strip()
        genre = self.request.GET.get('genre', '').strip()
        author = self.request.GET.get('author', '').strip()
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
                search_filter |= Q(title__icontains=variant) | Q(author__icontains=variant) | Q(genre__icontains=variant)
            queryset = queryset.filter(search_filter)

        if genre:
            queryset = queryset.filter(genre=genre)
        if author:
            queryset = queryset.filter(author=author)

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
        context['authors'] = Book.objects.order_by('author').values_list('author', flat=True).distinct()
        context['selected_author'] = self.request.GET.get('author', '').strip()
        context['selected_availability'] = self.request.GET.get('availability', '').strip()
        context['view_mode'] = 'table' if view_mode == 'table' else 'grid'
        context['querystring'] = params.urlencode()
        view_params = self.request.GET.copy()
        view_params.pop('view', None)
        context['view_querystring'] = view_params.urlencode()
        context['filters_active'] = any([
            context['query'],
            context['selected_genre'],
            context['selected_author'],
            context['selected_availability'],
        ])
        context['online_catalog'] = self.request.resolver_match.url_name == 'online_books'
        context['catalog_url'] = reverse('library:online_books' if context['online_catalog'] else 'library:books')
        return context


class BookDetailsView(TemplateView):
    template_name = 'library/book_detail.html'

    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'SmartKitap':
            return ['library/components/book_details.html']
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        book = get_object_or_404(catalog_for(self.request.user), pk=self.kwargs['pk'])
        student = getattr(self.request.user, 'student_profile', None) if self.request.user.is_authenticated else None
        book.has_active_request = bool(student and student.book_requests.filter(book=book, status__in=ACTIVE_REQUESTS).exists())
        context['book'] = book
        return context


class NotificationReadView(LoginRequiredMixin, View):
    def post(self, request):
        notices = notifications_for(request)
        seen = request.session.get('smartkitap_seen_notifications', [])
        request.session['smartkitap_seen_notifications'] = list(dict.fromkeys([*seen, *(n['key'] for n in notices)]))[-100:]
        if request.headers.get('X-Requested-With') == 'SmartKitap':
            return JsonResponse({'unread': 0})
        return redirect('library:dashboard')


class BookCreateView(StaffRequiredMixin, CreateView):
    model = Book
    form_class = BookForm
    template_name = 'library/book_form.html'
    success_url = reverse_lazy('library:books')

    def form_valid(self, form):
        try:
            self.object = form.save()
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
            return self.form_invalid(form)
        messages.success(self.request, _('Книга успешно добавлена в каталог.'))
        return redirect(self.success_url)


class BookUpdateView(StaffRequiredMixin, UpdateView):
    model = Book
    form_class = BookForm
    template_name = 'library/book_form.html'
    success_url = reverse_lazy('library:books')

    def form_valid(self, form):
        try:
            self.object = form.save()
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
            return self.form_invalid(form)
        messages.success(self.request, _('Информация о книге обновлена.'))
        return redirect(self.success_url)


class BookDeleteView(StaffRequiredMixin, DeleteView):
    model = Book
    template_name = 'library/book_confirm_delete.html'
    success_url = reverse_lazy('library:books')

    def form_valid(self, form):
        try:
            self.object.delete()
        except ProtectedError:
            messages.error(self.request, _('Запись связана с историей выдач или запросов и не может быть удалена.'))
        else:
            messages.success(self.request, _('Книга удалена из каталога.'))
        return redirect(self.success_url)


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
        try:
            self.object.delete()
        except ProtectedError:
            messages.error(self.request, _('Запись связана с историей выдач или запросов и не может быть удалена.'))
        else:
            messages.success(self.request, _('Читатель удалён.'))
        return redirect(self.success_url)


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
            try:
                issue_book(form.cleaned_data['book'].pk, form.cleaned_data['reader'],
                           form.cleaned_data['issue_date'], form.cleaned_data['due_date'])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, _('Книга успешно выдана читателю.'))
                return redirect('library:loans')

        self.object_list = self.get_queryset()
        return self.render_to_response(self.get_context_data(form=form))


class LoanReturnView(StaffRequiredMixin, View):
    def post(self, request, pk):
        get_object_or_404(Loan, pk=pk)
        try:
            return_loan(pk)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        else:
            messages.success(request, _('Книга успешно возвращена.'))
        return redirect('library:loans')


def csv_safe_cell(value):
    """Prefix risky text for spreadsheet import; csv.writer handles CSV syntax."""
    if isinstance(value, str) and value:
        if any(ord(char) < 32 or ord(char) == 127 for char in value) or value.lstrip().startswith(('=', '+', '-', '@')):
            return "'" + value
    return value


class SafeCSVWriter:
    def __init__(self, response):
        self.writer = csv.writer(response)

    def writerow(self, values):
        self.writer.writerow([csv_safe_cell(value) for value in values])


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
        response.charset = 'utf-8'  # Write the BOM once, not once per CSV row.
        filename = f'smartkitap_{report_type}.csv'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response.write('\ufeff')
        writer = SafeCSVWriter(response)
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

    def get_default_redirect_url(self):
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return reverse('library:dashboard')
        return reverse('library:student_dashboard')

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        for field in form.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')
        return form


class LogoutView(AuthLogoutView):
    pass


class StudentRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return (not (self.request.user.is_staff or self.request.user.is_superuser) and
                hasattr(self.request.user, 'student_profile'))

    @property
    def student(self):
        return self.request.user.student_profile


class StudentRegisterView(FormView):
    template_name = 'library/student_form.html'
    form_class = StudentRegistrationForm
    success_url = reverse_lazy('library:student_dashboard')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('library:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
        messages.success(self.request, _('Регистрация завершена. Добро пожаловать!'))
        return super().form_valid(form)


@method_decorator(never_cache, name='dispatch')
class StudentDashboardView(StudentRequiredMixin, TemplateView):
    template_name = 'library/student_dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['student'] = self.student
        loans = self.student.reader.loans.select_related('book', 'book__digital_file').prefetch_related('book__digital_file__allowed_students')
        context['current_loans'] = loans.filter(status=Loan.Status.ACTIVE)
        context['previous_loans'] = loans.filter(status=Loan.Status.RETURNED)
        context['my_books_count'] = context['current_loans'].count()
        context['returned_count'] = context['previous_loans'].count()
        context['catalog_available_count'] = Book.objects.filter(available_copies__gt=0).count()
        context['my_requests_count'] = self.student.book_requests.filter(status__in=['pending', 'approved']).count()
        context['book_requests'] = self.student.book_requests.select_related('book', 'loan')[:10]
        context['reading_progress'] = self.student.reading_progress.select_related('book', 'book__digital_file').prefetch_related('book__digital_file__allowed_students')[:6]
        context['continue_reading'] = None
        for progress in context['reading_progress']:
            if not can_read_book(progress.book, self.request.user):
                continue
            try:
                digital = progress.book.digital_file
                with digital.file.open('rb') as file:
                    count = pdf_page_count(file) if digital.format == 'pdf' else len(epub_contents(file)[0])
                position = min(progress.position, count)
                context['continue_reading'] = {'book': progress.book, 'position': position, 'total': count,
                                               'percent': round(position / count * 100), 'format': digital.format}
                break
            except Exception:
                continue
        preferences = self.student.reader.loans.values_list('book__genre', flat=True)
        context['recommendations'] = catalog_for(self.request.user).annotate(
            preferred_genre=Exists(Book.objects.filter(pk=OuterRef('pk'), genre__in=preferences))
        ).exclude(pk__in=context['current_loans'].values_list('book_id', flat=True)).order_by('-preferred_genre', 'title')[:4]
        return context


class StudentProfileEditView(StudentRequiredMixin, FormView):
    template_name = 'library/student_form.html'
    form_class = ReaderForm
    success_url = reverse_lazy('library:student_dashboard')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.student.reader
        return kwargs

    def get_context_data(self, **kwargs):
        return super().get_context_data(editing=True, **kwargs)

    def form_valid(self, form):
        form.save()
        messages.success(self.request, _('Профиль обновлён.'))
        return super().form_valid(form)


class StudentsView(StaffRequiredMixin, ListView):
    model = StudentProfile
    template_name = 'library/students.html'
    context_object_name = 'students'
    paginate_by = 20

    def get_queryset(self):
        queryset = StudentProfile.objects.select_related('reader', 'user')
        query = self.request.GET.get('q', '').strip()
        if query:
            search = Q()
            for variant in {query, query.lower(), query.upper(), query.capitalize(), query.title()}:
                search |= (Q(reader__full_name__icontains=variant) | Q(user__username__icontains=variant) |
                           Q(reader__class_name__icontains=variant))
            queryset = queryset.filter(search)
        return queryset


class StudentCreateView(StaffRequiredMixin, FormView):
    form_class = StaffStudentCreateForm
    template_name = 'library/student_form.html'
    success_url = reverse_lazy('library:students')

    def form_valid(self, form):
        form.save()
        messages.success(self.request, _('Ученик добавлен.'))
        return super().form_valid(form)


class StudentUpdateView(StaffRequiredMixin, FormView):
    form_class = StudentEditForm
    template_name = 'library/student_form.html'
    success_url = reverse_lazy('library:students')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['student'] = get_object_or_404(StudentProfile.objects.select_related('reader', 'user'), pk=self.kwargs['pk'])
        return kwargs

    def get_context_data(self, **kwargs):
        return super().get_context_data(editing=True, **kwargs)

    def form_valid(self, form):
        form.save()
        messages.success(self.request, _('Профиль обновлён.'))
        return super().form_valid(form)


class BookRequestCreateView(StudentRequiredMixin, View):
    def post(self, request, pk):
        get_object_or_404(Book, pk=pk)
        try:
            request_book(self.student, pk)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        else:
            messages.success(request, _('Запрос отправлен библиотекарю.'))
        return redirect('library:my_requests')


@method_decorator(never_cache, name='dispatch')
class MyRequestsView(StudentRequiredMixin, ListView):
    template_name = 'library/requests.html'
    context_object_name = 'book_requests'
    paginate_by = 20

    def get_queryset(self):
        # No student ID from the URL or form can affect ownership.
        return self.student.book_requests.select_related('book', 'loan')


class RequestsView(StaffRequiredMixin, ListView):
    template_name = 'library/requests.html'
    context_object_name = 'book_requests'
    paginate_by = 20

    def get_queryset(self):
        queryset = BookRequest.objects.select_related('book', 'loan', 'student__reader', 'student__user')
        status = self.request.GET.get('status', '')
        if status in BookRequest.Status.values:
            queryset = queryset.filter(status=status)
        return queryset

    def get_context_data(self, **kwargs):
        return super().get_context_data(managing=True, statuses=BookRequest.Status.choices,
            selected_status=self.request.GET.get('status', ''),
            default_due_date=(timezone.localdate() + timedelta(days=14)).isoformat(), **kwargs)


class RequestActionView(StaffRequiredMixin, View):
    def post(self, request, pk):
        book_request = get_object_or_404(BookRequest.objects.select_related('student__reader'), pk=pk)
        form = RequestActionForm(request.POST)
        if not form.is_valid():
            messages.error(request, ' '.join(str(error) for errors in form.errors.values() for error in errors))
        else:
            try:
                if form.cleaned_data['action'] == 'issue':
                    issue_book(book_request.book_id, book_request.student.reader, timezone.localdate(),
                               form.cleaned_data['due_date'], request_id=pk)
                else:
                    review_request(pk, form.cleaned_data['action'], form.cleaned_data['note'])
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))
            else:
                messages.success(request, _('Статус запроса обновлён.'))
        return redirect('library:requests')


class BookFileUploadView(StaffRequiredMixin, FormView):
    template_name = 'library/book_file_form.html'
    form_class = BookFileForm
    success_url = reverse_lazy('library:books')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        self.book = get_object_or_404(Book, pk=self.kwargs['pk'])
        kwargs['instance'] = BookFile.objects.filter(book=self.book).first() or BookFile(book=self.book)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['book'] = self.book
        return context

    def form_valid(self, form):
        form.save()
        messages.success(self.request, _('Электронная книга сохранена.'))
        return super().form_valid(form)


class BookMetadataView(StaffRequiredMixin, View):
    def get(self, request):
        query = request.GET.get('q', '').strip()
        work_id = request.GET.get('work', '')
        if not work_id and not 2 <= len(query) <= 200:
            return JsonResponse({'error': str(_('Введите от 2 до 200 символов для поиска.'))}, status=400)
        try:
            if work_id:
                return JsonResponse({'description': work_description(work_id)})
            return JsonResponse({'results': search_metadata(query)})
        except Exception:
            return JsonResponse({'error': str(_('Open Library недоступна. Заполните данные книги вручную.'))}, status=503)


class EbookAccessMixin(LoginRequiredMixin):
    def get_digital(self):
        digital = get_object_or_404(BookFile.objects.select_related('book'), book_id=self.kwargs['pk'])
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return digital
        student = getattr(user, 'student_profile', None)
        if not student or not digital.online_enabled or not digital.rights_confirmed:
            raise PermissionDenied(_('Онлайн-чтение этой книги недоступно.'))
        allowed = digital.allowed_students.all()
        if allowed.exists() and not allowed.filter(pk=student.pk).exists():
            raise PermissionDenied(_('Библиотекарь ограничил доступ к этой книге.'))
        return digital


@method_decorator(never_cache, name='dispatch')
class BookReadView(EbookAccessMixin, TemplateView):
    template_name = 'library/book_read.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        digital = self.get_digital()
        context['digital'] = digital
        context['book'] = digital.book
        try:
            with digital.file.open('rb') as file:
                if digital.format == 'pdf':
                    count = pdf_page_count(file)
                    chapters = None
                elif digital.format == 'epub':
                    chapters, manifest = epub_contents(file)
                    count = len(chapters)
                else:
                    raise ValueError('Unsupported book format')
            student = getattr(self.request.user, 'student_profile', None)
            saved = ReadingProgress.objects.filter(student=student, book=digital.book).first() if student else None
            position = min(saved.position if saved else 1, count)
            if digital.format == 'epub' and self.request.GET.get('chapter'):
                position = int(self.request.GET['chapter'])
            if not 1 <= position <= count:
                raise ValueError('Invalid chapter')
            context.update(position=position, position_count=count, can_save_progress=bool(student))
            if chapters:
                path, content = chapters[position - 1]
                image_url = lambda resource: reverse('library:book_resource', args=[digital.book_id]) + '?' + urlencode({'path': resource})
                context['chapter_html'] = mark_safe(safe_chapter(path, content, manifest, image_url))
        except Exception:
            context['reading_error'] = _('Не удалось открыть файл книги. Обратитесь к библиотекарю.')
        return context

    def render_to_response(self, context, **response_kwargs):
        if context.get('reading_error'):
            response_kwargs['status'] = 404
        return super().render_to_response(context, **response_kwargs)


@method_decorator(xframe_options_sameorigin, name='dispatch')
@method_decorator(never_cache, name='dispatch')
class BookContentView(EbookAccessMixin, View):
    def get(self, request, pk):
        digital = self.get_digital()
        try:
            file = digital.file.open('rb')
            if digital.format == 'pdf':
                pdf_page_count(file)
                content_type = 'application/pdf'
            elif digital.format == 'epub':
                epub_contents(file)
                content_type = 'application/epub+zip'
            else:
                raise ValueError('Unsupported format')
            file.seek(0)
        except Exception:
            if 'file' in locals():
                file.close()
            return HttpResponse(str(_('Не удалось открыть файл книги. Обратитесь к библиотекарю.')), status=404)
        if digital.format == 'pdf':
            response = ranged_file_response(file, filename=f'book-{pk}.pdf', content_type=content_type,
                                            range_header=request.headers.get('Range'))
        else:
            response = FileResponse(file, content_type=content_type, as_attachment=True,
                                    filename=f'book-{pk}.{digital.format}')
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'self'"
        return response


class ExistingMediaBookView(BookContentView):
    def get(self, request, file_path):
        # The original filename is a database lookup, never an arbitrary path to open.
        digital = get_object_or_404(BookFile, file=EXISTING_BOOK_PREFIX + file_path)
        self.kwargs['pk'] = digital.book_id
        return super().get(request, digital.book_id)


@method_decorator(never_cache, name='dispatch')
class BookResourceView(EbookAccessMixin, View):
    def get(self, request, pk):
        digital = self.get_digital()
        if digital.format != 'epub':
            return HttpResponse(status=404)
        try:
            with digital.file.open('rb') as file:
                _, manifest = epub_contents(file)
                resource = request.GET.get('path', '')
                images = {path: mime for path, mime in manifest.values()
                          if mime in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}}
                if resource not in images:
                    return HttpResponse(status=404)
                file.seek(0)
                with zipfile.ZipFile(file) as archive:
                    if archive.getinfo(resource).file_size > 5 * 1024 * 1024:
                        return HttpResponse(status=404)
                    data = archive.read(resource)
        except Exception:
            return HttpResponse(status=404)
        response = HttpResponse(data, content_type=images[resource])
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Security-Policy'] = "default-src 'none'"
        return response


class ReadingProgressView(EbookAccessMixin, View):
    def post(self, request, pk):
        digital = self.get_digital()
        student = getattr(request.user, 'student_profile', None)
        if not student:
            raise PermissionDenied
        form = ReadingPositionForm(request.POST)
        try:
            with digital.file.open('rb') as file:
                count = pdf_page_count(file) if digital.format == 'pdf' else len(epub_contents(file)[0])
        except Exception:
            messages.error(request, _('Не удалось открыть файл книги. Обратитесь к библиотекарю.'))
            return redirect('library:book_read', pk=pk)
        if not form.is_valid() or form.cleaned_data['position'] > count:
            messages.error(request, _('Укажите существующую страницу или главу.'))
        else:
            ReadingProgress.objects.update_or_create(student=student, book=digital.book,
                defaults={'position': form.cleaned_data['position']})
            messages.success(request, _('Прогресс чтения сохранён.'))
        return redirect('library:book_read', pk=pk)
