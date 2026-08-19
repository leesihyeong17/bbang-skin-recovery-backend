"""care/urls_v2.py — v2 실험 라우팅. 확정되면 v1으로 승격하거나 여기서 계속 관리."""
from django.urls import path

from . import views

urlpatterns = [
    path("prescriptions/ocr", views.PrescriptionOCRUploadView.as_view()),
]