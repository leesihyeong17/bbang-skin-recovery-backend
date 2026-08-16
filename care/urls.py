"""care/urls.py — A 담당 라우팅"""
from django.urls import path

from . import views

urlpatterns = [
    path("admin/clinic-data/import", views.ClinicDataImportView.as_view()),

    path("home", views.HomeView.as_view()),
    path("task-logs", views.TaskLogView.as_view()),
    path("care-items/<str:kind>/<str:key>", views.CareItemDetailView.as_view()),

    path("schedule", views.ScheduleView.as_view()),
    path("schedule/day", views.ScheduleDayView.as_view()),

    path("onboarding/status", views.OnboardingStatusView.as_view()),
    path("onboarding/surgery", views.OnboardingSurgeryView.as_view()),
    path("onboarding/questions", views.OnboardingQuestionsView.as_view()),
    path("onboarding/complete", views.OnboardingCompleteView.as_view()),

    path("prescriptions/ocr", views.PrescriptionOCRView.as_view()),
    path("prescriptions/confirm", views.PrescriptionConfirmView.as_view()),
]
