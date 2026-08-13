from django.urls import path

from .views import ClinicView

urlpatterns = [
    path("clinic", ClinicView.as_view(), name="clinic"),
]