from django.shortcuts import render
from config.utils import api_error
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import Http404
from django.utils import timezone
import io

from PIL import Image, ImageOps, UnidentifiedImageError
from django.core.files.base import ContentFile

from .models import Checkin, CheckinPhoto
from .serializers import CheckinSerializer

class CheckinList(APIView):
    def post(self, request, format=None):
        surgery = request.user.surgery
        if not surgery:
            return Response({"error": "VALIDATION_ERROR", "message": "등록된 수술이 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        serializer = CheckinSerializer(data=request.data)

        if not serializer.is_valid():
            return Response({"error": "VALIDATION_ERROR", "message": "입력값을 확인해주세요"}, status=status.HTTP_400_BAD_REQUEST)

        date = serializer.validated_data.get('date') or timezone.localdate()
        day = surgery.day_of(date)
        if day < 0 or day > surgery.program_days:
            return Response({"error": "VALIDATION_ERROR", "message": "체크인 날짜가 수술 범위를 벗어났습니다."}, status=status.HTTP_400_BAD_REQUEST)

        existing_checkin = Checkin.objects.filter(surgery=surgery, date=date).first()
        if existing_checkin:
            return Response({
                "error":{
                    "code": "CHECKIN_ALREADY_EXISTS",
                    "message": "오늘은 이미 체크인 완료"

                },
                "id": existing_checkin.id,
                "day": day,
                "date": date
                }, 
                status=status.HTTP_409_CONFLICT)

        checkin = serializer.save(surgery=surgery, date=date)
        return Response(
            {
                "checkin_id": checkin.id, "day": day, "date": date,
                "photos": [], "symptoms": [], "completed": False,
                "required_angles": ["front", "left", "right"],
                "symptom_terms": [term.key for term in surgery.procedure_type.symptom_terms.all()]
            }, 
                status=status.HTTP_201_CREATED
            )


class CheckinPhotoUpload(APIView):
    def post(self, request, checkin_id, format=None):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        # 남의 체크인 id를 넣어도 여기서 걸린다. 403이 아니라 404 —
        # "그 id는 존재하지만 네 것이 아니다"를 알려주지 않는다.
        checkin = Checkin.objects.filter(id=checkin_id, surgery=surgery).first()
        if checkin is None:
            return api_error("NOT_FOUND", "체크인을 찾을 수 없습니다", 404)

        angle = (request.data.get("angle") or "").strip()
        if angle not in dict(CheckinPhoto.Angle.choices):
            return api_error("VALIDATION_ERROR", "angle은 front · left · right 중 하나입니다")

        uploaded = request.data.get("image")
        if not uploaded:
            return api_error("VALIDATION_ERROR", "사진을 선택해 주세요")
        


        # 같은 각도 재촬영은 덮어쓴다(계약 689행). file_overwrite=False라
        # 새 키가 생기므로 기존 S3 객체를 명시적으로 지워야 고아 파일이 안 남는다.
        photo = checkin.photos.filter(angle=angle).first()
        if photo:
            photo.image.delete(save=False)
        else:
            photo = CheckinPhoto(checkin=checkin, angle=angle)

        photo.image.save(f"{checkin.id}_{angle}.jpg", uploaded, save=False)
        photo.save()

        return Response(
            {
                "angle": angle,
                "url": photo.image.storage.url(photo.image.name),
                "taken_at": photo.taken_at,
                "photo_count": checkin.photos.count(),
            },
            status=status.HTTP_201_CREATED,
        )