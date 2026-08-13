from django.urls import path

from .views import ClinicView, AppointmentListView, NotificationListView, ConsultMessageView

urlpatterns = [
    path("clinic", ClinicView.as_view(), name="clinic"),
    path("appointments", AppointmentListView.as_view(), name="appointments"),   
    path("notifications", NotificationListView.as_view(), name="notifications"),
    path("consult/messages", ConsultMessageView.as_view(), name="consult-messages"),
]