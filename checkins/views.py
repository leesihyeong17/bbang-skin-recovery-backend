from config.utils import api_error, resolve_lang, t
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.utils.dateparse import parse_date
from datetime import date, timedelta
from calendar import monthrange

from PIL import Image, UnidentifiedImageError
from pillow_heif import register_heif_opener
register_heif_opener()  # HEIC/HEIF를 Pillow에서 열 수 있게 한다

from .models import Checkin, CheckinPhoto, CheckinSymptom
from care.services import care_items, state_of, completion, _fill
from protocols.models import ActivityRuleTemplate
from django.db import transaction
from .serializers import CheckinSerializer, CheckinSymptomSerializer

PHOTO_EXPIRE = 600  # S3 presigned URL 만료 시간. 초 단위. 10분이면 충분하다

def _own_checkin(request, checkin_id):
    """요청자가 소유한 체크인인지 확인. 아니면 404를 던진다."""
    surgery = request.user.surgery
    if surgery is None:
        return None, api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

    checkin = Checkin.objects.filter(id=checkin_id, surgery=surgery).first()
    if checkin is None:
        return None, api_error("NOT_FOUND", "체크인을 찾을 수 없습니다", 404)

    return checkin, None

def _return_box(surgery, today, lang):
    """귀국일 카드. 비행 규칙이 '귀국일에' 어떤 상태인지 담는다."""
    if surgery.return_date is None:
        return None

    return_day = surgery.return_day
    box = {
        "date": surgery.return_date,
        "day": return_day,
        "dn": (surgery.return_date - today).days,
        "flight_status": None,
        "flight_text": "",
    }

    rule = (
        ActivityRuleTemplate.objects.filter(
            procedure_type=surgery.procedure_type, key="flight"
        )
        .prefetch_related("phases")
        .first()
    )
    if rule:
        phase = state_of(rule, return_day)
        if phase:
            box["flight_status"] = phase["status"]
            box["flight_text"] = _fill(t(phase["text"], lang), surgery)

    return box


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
                "checkin_id": existing_checkin.id,
                "day": day,
                "date": date
                }, 
                status=status.HTTP_409_CONFLICT)

        checkin = serializer.save(surgery=surgery, date=date)
        return Response(
            {
                "checkin_id": checkin.id, "day": day, "date": date,
                "completed": False,
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
                "url": photo.image.storage.url(photo.image.name, expire=PHOTO_EXPIRE),
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

        lang = resolve_lang(request, default=request.user.lang or "ko")

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
                "completion_rate": completion(surgery, day),
            },
            status=status.HTTP_200_OK,
        )

class RecordPhotoList(APIView):
    def get(self, request):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        checkins = surgery.checkins.prefetch_related("photos").order_by("date")

        items = []
        for checkin in checkins:
            photos = {
                photo.angle: photo.image.storage.url(photo.image.name, expire=PHOTO_EXPIRE)
                for photo in checkin.photos.all()
            }
            if not photos:
                continue
            items.append({
                "checkin_id": checkin.id,
                "day": surgery.day_of(checkin.date),
                "date": checkin.date,
                "photos": photos,
            })

        return Response({"items": items}, status=status.HTTP_200_OK)


class RecordSymptomList(APIView):
    def get(self, request):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        try:
            days = int(request.query_params.get("days", 14))
        except (ValueError, TypeError):
            return api_error("VALIDATION_ERROR", "days는 숫자여야 합니다")
        if not 1 <= days <= surgery.program_days:
            return api_error("VALIDATION_ERROR", f"days는 1~{surgery.program_days} 사이여야 합니다")

        today = timezone.localdate()
        start = today - timezone.timedelta(days=days-1)

        rows = (
            CheckinSymptom.objects.filter(
                checkin__surgery=surgery, 
                checkin__date__gte=start,
                checkin__date__lte=today,
            )
            .select_related("term", "checkin")
            .order_by("term__order", "checkin__date")
        )
        lang = resolve_lang(request, default=request.user.lang or "ko")
        terms = {}
        for row in rows:
            entry = terms.setdefault(
                row.term_id, {
                    "key": row.term.key,
                    "name": t(row.term.name, lang),
                    "points": [],
                },
            )
            entry["points"].append({
                "day": surgery.day_of(row.checkin.date),
                "level": row.level,
            })

        return Response({"terms": list(terms.values())}, status=status.HTTP_200_OK)


class RecordCalendar(APIView):
    """월별 점 + 귀국 박스. 명세서 3.8"""

    def get(self, request):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        today = timezone.localdate()
        try:
            year = int(request.query_params.get("year", today.year))
            month = int(request.query_params.get("month", today.month))
        except (TypeError, ValueError):
            return api_error("VALIDATION_ERROR", "year와 month는 숫자여야 합니다")
        if not 1 <= month <= 12:
            return api_error("VALIDATION_ERROR", "month는 1~12 사이여야 합니다")
        if not date.min.year <= year <= date.max.year:
            return api_error("VALIDATION_ERROR", "year가 올바르지 않습니다")

        first = date(year, month, 1)
        last = date(year, month, monthrange(year, month)[1])

        # 오늘 이후 날짜에는 has_checkin을 내리지 않는다(계약 727행).
        checked = set(
            surgery.checkins.filter(date__gte=first, date__lte=last)
            .values_list("date", flat=True)
        )

        days = []
        for i in range((last - first).days + 1):
            d = first + timedelta(days=i)
            if d > today:
                break
            days.append(
                {"date": d, "day": surgery.day_of(d), "has_checkin": d in checked}
            )

        lang = resolve_lang(request, default=request.user.lang or "ko")
        return Response({"return_box": _return_box(surgery, today, lang), "days": days})

class RecordDay(APIView):
    """날짜 상세 시트. level과 medical_term이 함께 나가는 유일한 곳(계약 754행)."""

    def get(self, request):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        raw = request.query_params.get("date")
        if not raw:
            return api_error("VALIDATION_ERROR", "date를 지정해 주세요")
        parsed = parse_date(raw)
        if parsed is None:
            return api_error("VALIDATION_ERROR", "date 형식이 올바르지 않습니다 (YYYY-MM-DD)")

        day = surgery.day_of(parsed)
        if not 0 <= day <= surgery.program_days:
            return api_error("VALIDATION_ERROR", "기록 기간을 벗어난 날짜입니다")
        # 미래를 열면 care_items()가 아직 오지 않은 루틴을 전부 미이행으로 내려준다.
        # 기록 시트라 "안 한 것"으로 보이면 안 된다.
        if parsed > timezone.localdate():
            return api_error("VALIDATION_ERROR", "아직 오지 않은 날짜입니다")

        lang = resolve_lang(request, default=request.user.lang or "ko")

        checkin = (
            surgery.checkins.filter(date=parsed)
            .prefetch_related("photos", "symptoms__term")
            .first()
        )

        photos, symptoms = {}, []
        if checkin:
            photos = {
                p.angle: p.image.storage.url(p.image.name, expire=PHOTO_EXPIRE)
                for p in checkin.photos.all()
            }
            symptoms = [
                {
                    "name": t(s.term.name, lang),
                    # 이름만 변환하고 등급은 변환하지 않는다(계약 760행)
                    "medical_term": t(s.term.medical_term, lang),
                    "level": s.level,
                }
                for s in sorted(checkin.symptoms.all(), key=lambda s: s.term.order)
            ]

        items = care_items(surgery, day, lang)
        tasks_done, tasks_missing = [], []
        for task in items["tasks"]:
            if task["done_count"] >= task["times_per_day"]:
                tasks_done.append(
                    {
                        "name": task["name"],
                        "done_count": task["done_count"],
                        "times_per_day": task["times_per_day"],
                    }
                )
            else:
                tasks_missing.append({
                    "name": task["name"],
                    "done_count": task["done_count"],
                    "times_per_day": task["times_per_day"],
                })

        return Response(
            {
                "day": day,
                "date": parsed,
                "photos": photos,
                "symptoms": symptoms,
                "tasks_done": tasks_done,
                "tasks_missing": tasks_missing,
            }
        )