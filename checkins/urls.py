from django.contrib import admin
from django.urls import path, include
from checkins.views import *

urlpatterns = [
    path("checkins", CheckinList.as_view(), name="checkin-list"),
    path("checkins/<int:checkin_id>/photos", CheckinPhotoUpload.as_view(), name="checkin-photos"),
]