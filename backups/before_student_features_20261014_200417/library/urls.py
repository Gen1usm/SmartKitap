from django.urls import path

from .views import (
    BookCreateView,
    BookDeleteView,
    BooksView,
    BookUpdateView,
    DashboardView,
    LoanReturnView,
    LoansView,
    LoginView,
    LogoutView,
    ReaderCreateView,
    ReaderDeleteView,
    ReadersView,
    ReaderUpdateView,
    ReportCSVView,
    ReportsView,
)

app_name = 'library'

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('books/', BooksView.as_view(), name='books'),
    path('books/add/', BookCreateView.as_view(), name='book_add'),
    path('books/<int:pk>/edit/', BookUpdateView.as_view(), name='book_edit'),
    path('books/<int:pk>/delete/', BookDeleteView.as_view(), name='book_delete'),
    path('readers/', ReadersView.as_view(), name='readers'),
    path('readers/add/', ReaderCreateView.as_view(), name='reader_add'),
    path('readers/<int:pk>/edit/', ReaderUpdateView.as_view(), name='reader_edit'),
    path('readers/<int:pk>/delete/', ReaderDeleteView.as_view(), name='reader_delete'),
    path('loans/', LoansView.as_view(), name='loans'),
    path('loans/<int:pk>/return/', LoanReturnView.as_view(), name='loan_return'),
    path('reports/', ReportsView.as_view(), name='reports'),
    path('reports/<str:report_type>/csv/', ReportCSVView.as_view(), name='report_csv'),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
]
