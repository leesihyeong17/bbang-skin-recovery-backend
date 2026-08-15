from django.urls import path

from .views import ReportListView, ReportDetailView

urlpatterns = [
    path("reports", ReportListView.as_view(), name="reports"),
    path("reports/<int:report_id>", ReportDetailView.as_view(), name="report-detail"),
]