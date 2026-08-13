"""리포트 — 계약 9.6"""
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from care.models import Appointment
from care.services import completion
from config.utils import api_error, t

from .builder import build_body
from .models import RecoveryReport

DISCLAIMER = {
    "ko": "본 문서는 환자 자가 보고 기반이며 진단서가 아닙니다.",
    "ja": "本書は患者の自己申告に基づくものであり、診断書ではありません。",
}


def _meta(surgery, day_from, day_to, lang):
    checkins = surgery.checkins.filter(
        date__gte=surgery.date_of(day_from), date__lte=surgery.date_of(day_to)
    )
    stats = checkins.aggregate(n=Count("id", distinct=True), photos=Count("photos", distinct=True))

    now = timezone.now()
    # 계약 794행: Appointment에서 파생한다
    confirmed = (
        surgery.appointments.filter(scheduled_at__lte=now)
        .exclude(status=Appointment.Status.MISSED)
        .order_by("-scheduled_at")
        .first()
    )
    upcoming = (
        surgery.appointments.filter(
            scheduled_at__gt=now, status=Appointment.Status.SCHEDULED
        )
        .order_by("scheduled_at")
        .first()
    )

    today_day = surgery.day_of(timezone.localdate())
    return {
        "checkin_count": stats["n"],
        "photo_count": stats["photos"],
        # 지난 구간이면 마지막 날까지 센다
        "completion_rate": completion(
            surgery, day_to, include_today=day_to < today_day
        ),
        "procedure": t(surgery.procedure_type.name, lang),
        "clinic": t(surgery.clinic.name, lang),
        "confirmed_at": confirmed and timezone.localtime(confirmed.scheduled_at).date().isoformat(),
        "next_confirm_at": upcoming and timezone.localtime(upcoming.scheduled_at).date().isoformat(),
    }


def _payload(report):
    return {
        "id": report.id,
        "kind": report.kind,
        "lang": report.lang,
        "day_from": report.day_from,
        "day_to": report.day_to,
        "meta": report.meta,
        "body": report.body,
        "disclaimer": t(DISCLAIMER, report.lang),
        "generated_at": report.generated_at,
    }


class ReportListView(APIView):
    def post(self, request):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        kind = request.data.get("kind")
        if kind not in dict(RecoveryReport.Kind.choices):
            return api_error("VALIDATION_ERROR", "kind는 submission 또는 repatriation입니다")

        try:
            day_from = int(request.data.get("day_from", 0))
            day_to = int(request.data.get("day_to"))
        except (TypeError, ValueError):
            return api_error("VALIDATION_ERROR", "day_from과 day_to는 숫자여야 합니다")

        if not 0 <= day_from <= day_to <= surgery.program_days:
            return api_error(
                "VALIDATION_ERROR",
                f"구간은 D+0 ~ D+{surgery.program_days} 안이어야 합니다",
            )

        lang = request.data.get("lang") or request.user.lang
        if lang not in ("ko", "ja", "en"):
            return api_error("VALIDATION_ERROR", "지원하지 않는 언어입니다")

        report = RecoveryReport.objects.create(
            surgery=surgery,
            kind=kind,
            lang=lang,
            day_from=day_from,
            day_to=day_to,
            body=build_body(surgery, day_from, day_to, lang),
            meta=_meta(surgery, day_from, day_to, lang),
        )
        return Response(_payload(report), status=status.HTTP_201_CREATED)

    def get(self, request):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        reports = surgery.reports.all()          # Meta.ordering = ["-generated_at"]

        kind = request.query_params.get("kind")
        if kind:
            if kind not in dict(RecoveryReport.Kind.choices):
                return api_error(
                    "VALIDATION_ERROR", "kind는 submission 또는 repatriation입니다"
                )
            reports = reports.filter(kind=kind)

        return Response({"items": [_summary(r) for r in reports]})

def _summary(report):
    """목록용. body는 안 담는다 — 상세에서만 받는다."""
    return {
        "id": report.id,
        "kind": report.kind,
        "lang": report.lang,
        "day_from": report.day_from,
        "day_to": report.day_to,
        "generated_at": report.generated_at,
    }