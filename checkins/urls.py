from django.contrib import admin
from django.urls import path, include
from checkins.views import CheckinList, CheckinPhotoUpload, CheckinSymptomUpdate, CheckinComplete, RecordPhotoList

urlpatterns = [
    path("checkins", CheckinList.as_view(), name="checkin-list"),
    path("checkins/<int:checkin_id>/photos", CheckinPhotoUpload.as_view(), name="checkin-photos"),
    path("checkins/<int:checkin_id>/symptoms", CheckinSymptomUpdate.as_view(), name="checkin-symptoms"),
    path("checkins/<int:checkin_id>/complete", CheckinComplete.as_view(), name="checkin-complete"),
    path("records/photos", RecordPhotoList.as_view(), name="record-photos"),
]