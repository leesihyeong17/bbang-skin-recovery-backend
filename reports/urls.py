from django.urls import path

from .views import ReportListView

urlpatterns = [
    path("reports", ReportListView.as_view(), name="reports"),
]