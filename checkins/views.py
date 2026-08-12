from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import Http404
from django.utils import timezone

from accounts.views import _latest_surgery
# Create your views here.

from .models import Checkin
from .serializers import CheckinSerializer

class CheckinList(APIView):
    def post(self, request, format=None):
        surgery = _latest_surgery(request.user)
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
        