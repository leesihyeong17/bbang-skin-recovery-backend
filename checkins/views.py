from config.utils import api_error, t
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone

from PIL import Image, UnidentifiedImageError
from pillow_heif import register_heif_opener
register_heif_opener()  # HEIC/HEIF를 Pillow에서 열 수 있게 한다

from .models import Checkin, CheckinPhoto, CheckinSymptom
from django.db import transaction
from .serializers import CheckinSerializer, CheckinSymptomSerializer

def _own_checkin(request, checkin_id):
    """요청자가 소유한 체크인인지 확인. 아니면 404를 던진다."""
    surgery = request.user.surgery
    if surgery is None:
        return None, api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

    checkin = Checkin.objects.filter(id=checkin_id, surgery=surgery).first()
    if checkin is None:
        return None, api_error("NOT_FOUND", "체크인을 찾을 수 없습니다", 404)

    return checkin, None

def _completion_rate(surgery, day):
    """완주율.

    TODO: A가 care/services.py에 completion()을 만들면 그걸로 교체할 것.
          accounts/views.py의 _stage_of()와 같은 성격의 임시 코드.
    """
    return None


class CheckinList(APIView):
    def post(self, request, format=None):
        surgery = request.user.surgery
        if not surgery:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        serializer = CheckinSerializer(data=request.data)

        if not serializer.is_valid():
                return api_error("VALIDATION_ERROR", "입력값을 확인해주세요")

        date = serializer.validated_data.get('date') or timezone.localdate()
        day = surgery.day_of(date)
        if day < 0 or day > surgery.program_days:
            return api_error("VALIDATION_ERROR", "체크인 날짜가 수술 범위를 벗어났습니다.")

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
                "photos": {}, "symptoms": {}, "completed": False,
                "symptom_terms": [term.key for term in surgery.procedure_type.symptom_terms.all()]
            }, 
                status=status.HTTP_201_CREATED
            )

ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "HEIF": "heic"}

def _image_ext(uploaded):
    """이미지인지 확인하고 실제 포맷의 확장자를 돌려준다. 아니면 None.

    verify()는 헤더만 읽고 픽셀을 디코딩하지 않는다. 재인코딩이 없으므로
    원본 바이트와 EXIF가 그대로 보존된다.
    """
    try:
        img = Image.open(uploaded)
        img.verify()
    except (UnidentifiedImageError, OSError):
        return None
    finally:
        uploaded.seek(0)           # verify()가 파일 포인터를 소진한다
    return ALLOWED_FORMATS.get(img.format)

class CheckinPhotoUpload(APIView):
    def post(self, request, checkin_id, format=None):
        checkin, err = _own_checkin(request, checkin_id)
        if err:
            return err
        surgery = checkin.surgery 

        angle = (request.data.get("angle") or "").strip()
        if angle not in dict(CheckinPhoto.Angle.choices):
            return api_error("VALIDATION_ERROR", "angle은 front · left · right 중 하나입니다")

        uploaded = request.data.get("image")
        if not uploaded:
            return api_error("VALIDATION_ERROR", "사진을 선택해 주세요")

        ext = _image_ext(uploaded)
        if ext is None:
            return api_error("VALIDATION_ERROR", "지원하지 않는 이미지 형식입니다")


        # 같은 각도 재촬영은 덮어쓴다(계약 689행). file_overwrite=False라
        # 새 키가 생기므로 기존 S3 객체를 명시적으로 지워야 고아 파일이 안 남는다.
        photo = checkin.photos.filter(angle=angle).first()
        if photo:
            photo.image.delete(save=False)
        else:
            photo = CheckinPhoto(checkin=checkin, angle=angle)

        photo.image.save(f"{checkin.id}_{angle}.{ext}", uploaded, save=False)
        photo.taken_at = timezone.now()
        photo.save()

        return Response(
            {
                "angle": angle,
                "url": photo.image.storage.url(photo.image.name, expire=600),
                "taken_at": photo.taken_at,
                "photo_count": checkin.photos.count(),
            },
            status=status.HTTP_201_CREATED,
        )

class CheckinSymptomUpdate(APIView):
    def put(self, request, checkin_id, format=None):
        checkin, err = _own_checkin(request, checkin_id)
        if err:
            return err
        surgery = checkin.surgery 

        terms = {term.key: term for term in surgery.procedure_type.symptom_terms.all()}
        serializer = CheckinSymptomSerializer(data=request.data, context={"terms": terms})
        if not serializer.is_valid():
            detail = serializer.errors.get("symptoms")
            message = (
                str(detail[0])
                if isinstance(detail, list) and detail
                else "증상 정도는 1~5 사이 숫자로 3항목 모두 입력해 주세요"
            )
            return api_error("VALIDATION_ERROR", message)

        levels = serializer.validated_data["symptoms"] #{term_key: level} 딕셔너리. term_key는 SymptomTerm.key

        with transaction.atomic():
            for key, level in levels.items():
                CheckinSymptom.objects.update_or_create(
                    checkin=checkin, term=terms[key], defaults={"level": level}
                )

        return Response(
            {
                "checkin_id": checkin.id,
                "day": checkin.surgery.day_of(checkin.date),
                "date": checkin.date,
                "symptoms": {s.term.key: s.level for s in checkin.symptoms.select_related("term").order_by("term__order")},
                "completed": checkin.completed_at is not None,
            },
            status=status.HTTP_200_OK,
        )

class CheckinComplete(APIView):
    def post(self, request, checkin_id, format=None):
        checkin, err = _own_checkin(request, checkin_id)
        if err:
            return err
        surgery = checkin.surgery

        lang = request.user.lang

        angles = set(checkin.photos.values_list("angle", flat=True))
        need = set(CheckinPhoto.Angle.values) - angles
        if need:
            return api_error("VALIDATION_ERROR", f"사진 {len(need)}컷이 더 필요해요",
                             "required_angles", [t(f"checkin.photo.{a}", lang) for a in need])
        
        terms = {term.key: term for term in surgery.procedure_type.symptom_terms.all()}
        submitted = set(checkin.symptoms.values_list("term__key", flat=True))
        need = [terms[k] for k in terms if k not in submitted]
        if need:
            names = " · ".join(t(term.name, lang) for term in need)
            return api_error("VALIDATION_ERROR", f"선택하지 않은 항목이 있어요 — {names}")

        checkin.completed_at = timezone.now()
        checkin.save(update_fields=["completed_at"])

        day = surgery.day_of(checkin.date)
        return Response(
            {
                "completed_at": checkin.completed_at,
                "day": day,
                "completion_rate": _completion_rate(surgery, day),
            },
            status=status.HTTP_200_OK,
        )