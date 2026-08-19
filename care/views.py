"""
care/views.py — A 담당 엔드포인트

    GET  /api/v1/home
    PUT  /api/v1/task-logs
    GET  /api/v1/care-items/<kind>/<key>
    GET  /api/v1/schedule
    GET  /api/v1/schedule/day
    GET  /api/v1/onboarding/status
    GET  /api/v1/onboarding/surgery
    GET  /api/v1/onboarding/questions
    POST /api/v1/onboarding/complete
    POST /api/v1/admin/clinic-data/import
    GET  /api/v1/prescriptions/ocr
    POST /api/v1/prescriptions/confirm

계산은 전부 services.py가 합니다. 이 파일은 "요청을 받아 → 서비스 호출 → JSON으로 포장"만
합니다. 여기서 CareTaskTemplate.objects.filter()를 직접 쓰지 마세요.
"""
from django.conf import settings

from config.utils import api_error as err

from datetime import date, datetime

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAdminUser
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from checkins.models import Checkin
from protocols.models import (ActivityRuleTemplate, CareTaskTemplate,
                              VariableQuestion)

from .import_serializers import (ClinicDataImportSerializer,
                                 PrescriptionConfirmSerializer,
                                 PrescriptionDraftImportSerializer)
from .import_service import import_clinic_data
from .models import (Appointment, Prescription, PrescriptionItem, Surgery,
                     VariableAnswer)
from .services import (DEFAULT_LANG, care_items, completion, next_unlock, pick,
                       stage_of, timeline, validate_task_key, _fill,
                       _phases_with_from, _medication_task)


# ──────────────────────────────────────────────────────────────
# 공통 헬퍼
# ──────────────────────────────────────────────────────────────

def get_surgery(request):
    """
    로그인한 환자의 시술을 가져옵니다. 없으면 None.
    여러 시술이 있으면 수술일이 가장 최근인 시술을 선택합니다.
    """
    return (request.user.surgeries
            .select_related("clinic", "surgeon", "procedure_type")
            .order_by("-surgery_date")
            .first())


def resolve_day(request, surgery):
    """
    ?date=2026-08-05 또는 ?day=2 를 D+N으로 정규화합니다.
    둘 다 없으면 오늘.
    """
    if (raw := request.query_params.get("day")) is not None:
        return int(raw)
    if raw := request.query_params.get("date"):
        return surgery.day_of(datetime.strptime(raw, "%Y-%m-%d").date())
    return surgery.day_of(timezone.localdate())


def file_url(field):
    """FileField/ImageField → URL. 파일이 없거나 스토리지 미설정이면 None."""
    try:
        return field.url if field else None
    except Exception:
        return None

def resolve_lang(request):
    """개발 중 언어 대조용. 운영에서는 항상 환자의 확정 언어를 씁니다."""
    if settings.DEBUG and (q := request.query_params.get("lang")):
        return q
    return request.user.lang or DEFAULT_LANG


def validation_error(detail):
    """Return serializer/service validation errors in the common envelope."""
    return err("VALIDATION_ERROR", str(detail), status.HTTP_400_BAD_REQUEST)


# ──────────────────────────────────────────────────────────────
# 관리자 대용량 데이터 입력
# ──────────────────────────────────────────────────────────────
class ClinicDataImportView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        serializer = ClinicDataImportSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        try:
            result = import_clinic_data(serializer.validated_data)
        except serializers.ValidationError as exc:
            return validation_error(exc.detail)
        return Response(result, status=status.HTTP_200_OK if result["dry_run"] else status.HTTP_201_CREATED)


# ──────────────────────────────────────────────────────────────
# 처방전 추출 초안 조회·확정
# ──────────────────────────────────────────────────────────────
class PrescriptionOCRView(APIView):
    """관리자 import가 등록한 이 환자의 미확정 OCR 초안을 반환합니다."""

    def get(self, request):
        s = get_surgery(request)
        if s is None:
            return err("ONBOARDING_REQUIRED", "등록된 시술이 없습니다", status.HTTP_403_FORBIDDEN)

        prescription = Prescription.objects.filter(surgery=s).first()
        if prescription is None or not (prescription.ocr_raw or {}).get("draft"):
            return err(
                "OCR_DRAFT_NOT_FOUND",
                "등록된 처방전 추출 결과가 없습니다",
                status.HTTP_404_NOT_FOUND,
            )
        if prescription.confirmed_at:
            return err(
                "PRESCRIPTION_ALREADY_CONFIRMED",
                "이미 확정한 처방전입니다",
                status.HTTP_409_CONFLICT,
            )

        raw = prescription.ocr_raw
        draft = raw["draft"]
        items = draft.get("items", [])
        regular_count = sum(not item.get("is_prn", False) for item in items)
        return Response({
            "ocr_id": raw["ocr_id"],
            "ocr_status": prescription.ocr_status,
            **draft,
            "regular_count": regular_count,
            "prn_count": len(items) - regular_count,
            "not_extracted": ["요양기관기호", "질병분류기호", "면허번호", "교부번호"],
        })


class PrescriptionConfirmView(APIView):
    """환자가 화면에서 확인한 서버 보관 초안을 그대로 확정합니다."""

    @transaction.atomic
    def post(self, request):
        s = get_surgery(request)
        if s is None:
            return err("ONBOARDING_REQUIRED", "등록된 시술이 없습니다", status.HTTP_403_FORBIDDEN)

        serializer = PrescriptionConfirmSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error(serializer.errors)
        data = serializer.validated_data

        try:
            prescription = Prescription.objects.select_for_update().get(surgery=s)
        except Prescription.DoesNotExist:
            return err(
                "OCR_DRAFT_NOT_FOUND",
                "등록된 처방전 추출 결과가 없습니다",
                status.HTTP_404_NOT_FOUND,
            )

        raw = prescription.ocr_raw or {}
        if prescription.confirmed_at:
            return err(
                "PRESCRIPTION_ALREADY_CONFIRMED",
                "이미 확정한 처방전입니다",
                status.HTTP_409_CONFLICT,
            )
        if not raw.get("ocr_id") or raw["ocr_id"] != data["ocr_id"]:
            return err(
                "OCR_DRAFT_EXPIRED",
                "처방전 추출 결과가 갱신되었습니다. 다시 확인해 주세요",
                status.HTTP_409_CONFLICT,
            )

        draft_serializer = PrescriptionDraftImportSerializer(data=raw.get("draft"))
        if not draft_serializer.is_valid():
            return err(
                "OCR_DRAFT_INVALID",
                "저장된 처방전 추출 결과가 올바르지 않습니다",
                status.HTTP_409_CONFLICT,
            )
        draft = draft_serializer.validated_data

        prescription.issued_date = draft.get("issued_date")
        prescription.timing = draft["timing"]
        prescription.per_day = draft["per_day"]
        prescription.total_days = draft["total_days"]
        prescription.ocr_status = Prescription.OcrStatus.DONE
        prescription.confirmed_at = timezone.now()

        # 실제 OCR에서 임시 원본을 쓰게 되더라도 확정 이후에는 보관하지 않습니다.
        if prescription.source_image:
            prescription.source_image.delete(save=False)
            prescription.source_image = ""
        prescription.save()

        prescription.items.all().delete()
        PrescriptionItem.objects.bulk_create([
            PrescriptionItem(prescription=prescription, **item)
            for item in draft["items"]
        ])

        regular_count = sum(not item["is_prn"] for item in draft["items"])
        return Response({
            "id": prescription.id,
            "confirmed_at": prescription.confirmed_at,
            "regular_count": regular_count,
            "prn_count": len(draft["items"]) - regular_count,
        }, status=status.HTTP_201_CREATED)

# ──────────────────────────────────────────────────────────────
# GET /home — 명세서 3.5
# ──────────────────────────────────────────────────────────────
class HomeView(APIView):
    """홈 화면 전체를 이 하나로 채웁니다."""

    def get(self, request):
        s = get_surgery(request)
        if s is None:
            return err("ONBOARDING_REQUIRED", "등록된 시술이 없습니다", status.HTTP_403_FORBIDDEN)

        lang = resolve_lang(request) or DEFAULT_LANG
        day = resolve_day(request, s)
        ci = care_items(s, day, lang)

        rule_total = ActivityRuleTemplate.objects.filter(
            procedure_type=s.procedure_type).count()

        checkin = Checkin.objects.filter(surgery=s, date=s.date_of(day)).first()
        nu = next_unlock(s, day, lang)

        return Response({
            "day": day,
            "date": s.date_of(day),
            "stage": stage_of(s, day, lang),
            "progress": round(min(max(day, 0), s.program_days) / s.program_days, 3),

            "summary": {
                # 회복 점수·이상 판정은 만들지 않습니다 (명세서 1.2)
                "unlocked_count": ci["rules"]["ok"]["count"],
                "unlocked_total": rule_total,
                "completion_rate": completion(s, day),
                "next_unlock": ({"day": nu["day"], "date": nu["date"], "label": nu["label"]}
                                if nu else None),
                "return_dn": (s.return_day - day) if s.return_date else None,
                "return_date": s.return_date,
            },

            "checkin": {
                "completed": bool(checkin and checkin.completed_at),
                "checkin_id": checkin.id if checkin else None,
            },

            "tasks": ci["tasks"],
            "rules": ci["rules"],
        })


# ──────────────────────────────────────────────────────────────
# PUT /task-logs — 명세서 3.5 회차 칩
# ──────────────────────────────────────────────────────────────
class TaskLogView(APIView):
    """
    done_count를 절대값으로 받습니다(증감 아님).
    같은 요청을 두 번 보내도 결과가 같아야 오프라인·중복 탭에서 안전합니다.
    """

    def put(self, request):
        s = get_surgery(request)
        if s is None:
            return err("ONBOARDING_REQUIRED", "등록된 시술이 없습니다", status.HTTP_403_FORBIDDEN)

        task_key = request.data.get("task_key")
        raw_date = request.data.get("date")
        done_count = request.data.get("done_count")

        if not task_key or raw_date is None or done_count is None:
            return err("VALIDATION_ERROR", "task_key, date, done_count가 모두 필요합니다")

        try:
            the_date = datetime.strptime(str(raw_date), "%Y-%m-%d").date()
            done_count = int(done_count)
        except (ValueError, TypeError):
            return err("VALIDATION_ERROR", "date는 YYYY-MM-DD, done_count는 정수여야 합니다")

        day = s.day_of(the_date)

        # 오타난 키가 조용히 저장되면 나중에 완주율이 틀어집니다.
        # 'medication'은 처방에서 합성되는 키라 여기서 함께 허용됩니다.
        if not validate_task_key(s, day, task_key):
            return err("UNKNOWN_TASK_KEY", f"D+{day}에 '{task_key}' 루틴이 없습니다")

        # 상한을 넘겨 보내도 그날 최대 회차로 자릅니다
        item = next(t for t in care_items(s, day)["tasks"] if t["key"] == task_key)
        done_count = max(0, min(done_count, item["times_per_day"]))

        from checkins.models import TaskLog
        TaskLog.objects.update_or_create(
            surgery=s, task_key=task_key, date=the_date,
            defaults={"done_count": done_count},
        )

        return Response({
            "task_key": task_key,
            "date": the_date,
            "done_count": done_count,
            "times_per_day": item["times_per_day"],
            "completion_rate": completion(s, day),
        })


# ──────────────────────────────────────────────────────────────
# GET /care-items/<kind>/<key> — 근거 시트 (명세서 3.5)
# ──────────────────────────────────────────────────────────────
class CareItemDetailView(APIView):
    """
    "왜 그런가요 → 언제부터 달라지나요 → 병원 안내문 원문" 순서 그대로 내려줍니다.

    source_text는 병원 원문이라 번역하지 않고 한국어 그대로 보냅니다.
    원문을 고치지 않는다는 원칙(명세서 1.2)이 여기서 지켜집니다.
    """

    def get(self, request, kind, key):
        s = get_surgery(request)
        if s is None:
            return err("ONBOARDING_REQUIRED", "등록된 시술이 없습니다", status.HTTP_403_FORBIDDEN)

        lang = resolve_lang(request) or DEFAULT_LANG
        day = resolve_day(request, s)

        if kind == "rule":
            r = get_object_or_404(
                ActivityRuleTemplate.objects.prefetch_related("phases"),
                procedure_type=s.procedure_type, key=key)
            phases = _phases_with_from(r)
            cur = next((p for p in phases if day <= p["day_to"]), phases[-1] if phases else None)
            return Response({
                "kind": "rule", "key": r.key, "icon": r.icon,
                "name": pick(r.name, lang),
                "current": {"status": cur["status"],
                            "text": _fill(pick(cur["text"], lang), s)} if cur else None,
                "why": _fill(pick(r.why, lang), s),
                "phases": [{
                    "day_from": p["day_from"], "day_to": p["day_to"],
                    "status": p["status"],
                    "text": _fill(pick(p["text"], lang), s),
                    "current": p is cur,
                } for p in phases],
                "source_text": pick(r.source_text, DEFAULT_LANG),
                "source_ref": r.source_ref,
            })

        if kind == "task":
            # 처방에서 합성된 복약은 템플릿에 없으므로 따로 처리합니다
            if key == "medication":
                med = _medication_task(s, day, lang)
                if med is None:
                    return err("VALIDATION_ERROR", "등록된 처방이 없습니다", status.HTTP_404_NOT_FOUND)
                return Response({
                    "kind": "task", "key": "medication", "icon": med["icon"],
                    "name": pick(med["name"], lang),
                    "subtitle": pick(med["subtitle"], lang),
                    "why": pick(med["why"], lang),
                    "day_from": med["day_from"], "day_to": med["day_to"],
                    "times_per_day": med["times_per_day"],
                    "source_text": pick(med["source_text"], DEFAULT_LANG),
                    "source_ref": med["source_ref"],
                })

            t = get_object_or_404(CareTaskTemplate,
                                  procedure_type=s.procedure_type, key=key)
            return Response({
                "kind": "task", "key": t.key, "icon": t.icon,
                "name": pick(t.name, lang),
                "why": _fill(pick(t.why, lang), s),
                "day_from": t.day_from, "day_to": t.day_to,
                "times_per_day": t.times_per_day,
                "interval_days": t.interval_days,
                "source_text": pick(t.source_text, DEFAULT_LANG),
                "source_ref": t.source_ref,
            })

        return err("VALIDATION_ERROR", "kind는 task 또는 rule이어야 합니다")


# ──────────────────────────────────────────────────────────────
# GET /schedule — 명세서 3.6
# ──────────────────────────────────────────────────────────────
class ScheduleView(APIView):
    """
    월 캘린더 마커 + 앞으로의 변화.
    마커는 내원·귀국 두 종류만 넣습니다(명세서: 그 외는 가독성 때문에 제외).
    """

    def get(self, request):
        s = get_surgery(request)
        if s is None:
            return err("ONBOARDING_REQUIRED", "등록된 시술이 없습니다", status.HTTP_403_FORBIDDEN)

        lang = resolve_lang(request) or DEFAULT_LANG
        events = timeline(s, lang)

        markers = [{
            "date": e["date"], "day": e["day"], "type": e["type"], "label": e["label"],
        } for e in events if e["type"] in ("visit", "remote", "return")]

        first, last = s.surgery_date, s.date_of(s.program_days)
        return Response({
            "range": {"min": first.strftime("%Y-%m"), "max": last.strftime("%Y-%m")},
            "markers": markers,
            "upcoming": [{
                "day": e["day"], "date": e["date"], "type": e["type"],
                "badge": e["badge"], "label": e["label"],
            } for e in events],
        })


class ScheduleDayView(APIView):
    """캘린더에서 날짜를 눌렀을 때 뜨는 시트. /home에서 요약부를 뺀 형태입니다."""

    def get(self, request):
        s = get_surgery(request)
        if s is None:
            return err("ONBOARDING_REQUIRED", "등록된 시술이 없습니다", status.HTTP_403_FORBIDDEN)

        lang = resolve_lang(request) or DEFAULT_LANG
        day = resolve_day(request, s)
        ci = care_items(s, day, lang)
        return Response({
            "day": day, "date": s.date_of(day),
            "stage": stage_of(s, day, lang),
            "tasks": ci["tasks"], "rules": ci["rules"],
        })


# ──────────────────────────────────────────────────────────────
# 온보딩 — 명세서 3.2 ~ 3.4
# ──────────────────────────────────────────────────────────────
class OnboardingStatusView(APIView):
    """STEP 1 자료 수신. 처방이 없으면 다음 단계로 못 넘어갑니다."""

    def get(self, request):
        s = get_surgery(request)
        if s is None:
            return err("ONBOARDING_REQUIRED", "등록된 시술이 없습니다", status.HTTP_403_FORBIDDEN)

        rx = getattr(s, "prescription", None)
        registered = bool(rx and rx.confirmed_at)
        appt_days = [s.day_of(a.scheduled_at.date())
                     for a in s.appointments.filter(kind=Appointment.Kind.VISIT)]

        return Response({
            "documents": [
                {"key": "surgery_record", "label": "수술기록 (PDF)",
                 "received": bool(s.surgery_record), "url": file_url(s.surgery_record)},
                {"key": "instructions", "label": "수술 후 주의사항 안내문",
                 "received": bool(s.procedure_type.source_document),
                 "url": file_url(s.procedure_type.source_document)},
                {"key": "appointments",
                 "label": "예약 일정 (" + ", ".join(f"D+{d}" for d in sorted(appt_days)) + ")",
                 "received": bool(appt_days), "url": None},
            ],
            "prescription": {
                "registered": registered,
                "summary": (f"{rx.items.filter(is_prn=False).count()}종 · "
                            f"하루 {rx.per_day}회 · {rx.total_days}일") if registered else None,
            },
            "can_proceed": registered,
        })


class OnboardingSurgeryView(APIView):
    """STEP 2 시술 확인. 읽기 전용 5행."""

    def get(self, request):
        s = get_surgery(request)
        if s is None:
            return err("ONBOARDING_REQUIRED", "등록된 시술이 없습니다", status.HTTP_403_FORBIDDEN)

        lang = resolve_lang(request) or DEFAULT_LANG
        p = request.user
        name = pick(p.name, lang)
        return Response({
            "rows": [
                {"key": "procedure_type", "value": pick(s.procedure_type.name, lang)},
                {"key": "detail", "value": pick(s.detail, lang)},
                {"key": "surgery_date", "value": s.surgery_date},
                {"key": "clinic",
                 "value": f"{pick(s.clinic.name, lang)} · {pick(s.surgeon.name, lang)}"},
                {"key": "patient",
                 "value": f"{name} ({p.name_romaji})" if p.name_romaji else name},
            ],
            "editable": False,
            "notice": "정보가 다르면 병원 국제진료팀으로 문의해 주세요",
        })


class OnboardingQuestionsView(APIView):
    """STEP 3 개인 변수. 질문 2개 + 귀국일 입력 범위."""

    def get(self, request):
        s = get_surgery(request)
        if s is None:
            return err("ONBOARDING_REQUIRED", "등록된 시술이 없습니다", status.HTTP_403_FORBIDDEN)

        lang = resolve_lang(request) or DEFAULT_LANG
        qs = VariableQuestion.objects.filter(procedure_type=s.procedure_type)
        return Response({
            "questions": [{
                "key": q.key,
                "question": pick(q.question, lang),
                "hint": pick(q.hint, lang),
                "answer_type": q.answer_type,
            } for q in qs],
            "return_date": {
                "min": s.date_of(1),
                "max": s.date_of(s.program_days),
                "default": s.return_date,
            },
        })


class OnboardingCompleteView(APIView):
    """
    답변 저장 + 귀국일 확정 + 상태 전환을 한 트랜잭션으로 처리합니다.
    중간에 실패하면 전부 롤백해야 합니다 — 답변만 저장되고 상태가 안 바뀌면
    다시 들어왔을 때 화면이 꼬입니다.
    """

    @transaction.atomic
    def post(self, request):
        s = get_surgery(request)
        if s is None:
            return err("ONBOARDING_REQUIRED", "등록된 시술이 없습니다", status.HTTP_403_FORBIDDEN)

        answers = request.data.get("answers") or {}
        raw_return = request.data.get("return_date")
        if not raw_return:
            return err("VALIDATION_ERROR", "return_date가 필요합니다")

        try:
            return_date = datetime.strptime(str(raw_return), "%Y-%m-%d").date()
        except ValueError:
            return err("VALIDATION_ERROR", "return_date는 YYYY-MM-DD 형식이어야 합니다")

        rd = s.day_of(return_date)
        if not (1 <= rd <= s.program_days):
            return err("RETURN_DATE_OUT_OF_RANGE",
                       f"귀국일은 D+1 ~ D+{s.program_days} 사이여야 합니다 (입력값 D+{rd})")

        qs = {q.key: q for q in VariableQuestion.objects.filter(procedure_type=s.procedure_type)}
        for key, value in answers.items():
            if key not in qs:
                return err("VALIDATION_ERROR", f"알 수 없는 질문 키: {key}")
            VariableAnswer.objects.update_or_create(
                surgery=s, question=qs[key], defaults={"value": value})

        # 답변에 따라 예약이 추가되는 경우 (콧볼 실밥 제거 D+10 등)
        from django.utils import timezone
        for key, value in answers.items():
            if value is not True:
                continue
            for eff in (qs[key].effects or {}).get("on_true", []):
                if eff.get("type") != "add_appointment":
                    continue
                when = timezone.make_aware(
                    datetime.combine(s.date_of(eff["day"]), datetime.min.time().replace(hour=14)))
                Appointment.objects.get_or_create(
                    surgery=s, scheduled_at=when,
                    defaults={"title": eff.get("title", {}),
                              "kind": eff.get("kind", Appointment.Kind.VISIT)})

        s.return_date = return_date
        s.onboarded_at = timezone.now()
        s.care_status = Surgery.CareStatus.ACTIVE
        s.save(update_fields=["return_date", "onboarded_at", "care_status"])

        lang = resolve_lang(request) or DEFAULT_LANG
        day = s.day_of(timezone.localdate())
        ci = care_items(s, day, lang)
        events = timeline(s, lang)
        rx = getattr(s, "prescription", None)

        return Response({
            "care_status": s.care_status,
            "summary": {
                "routine_count": CareTaskTemplate.objects.filter(
                    procedure_type=s.procedure_type).count(),
                "rule_count": ActivityRuleTemplate.objects.filter(
                    procedure_type=s.procedure_type).count(),
                "unlock_event_count": sum(1 for e in events if e["type"] == "unlock"),
                "visit_count": s.appointments.count(),
                "medication_period": (f"D+0 ~ D+{(rx.total_days or 1) - 1}"
                                      if rx and rx.confirmed_at else None),
                "return_day": s.return_day,
                "return_date": s.return_date,
            },
        })
