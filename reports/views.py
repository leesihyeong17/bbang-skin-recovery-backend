"""리포트 — 계약 9.6"""
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from datetime import datetime, time

from care.models import Appointment
from care.services import completion
from config.utils import api_error, t

from .builder import build_body, build_repatriation_body
from .models import RecoveryReport
from .polish import PROMPT_VERSION, polish_body

DISCLAIMER = {
    "ko": "본 문서는 환자 자가 보고 기반이며 진단서가 아닙니다.",
    "ja": "本書は患者の自己申告に基づくものであり、診断書ではありません。",
}

# 제출용은 매번 새로 만드는 문서가 아니라 최근 7일이 계속 갱신되는 위클리 리포트다.
WEEKLY_DAYS = 7

TITLE = {
    RecoveryReport.Kind.SUBMISSION: {"ko": "위클리 리포트", "ja": "ウィークリーレポート"},
    RecoveryReport.Kind.REPATRIATION: {"ko": "귀국용 요약", "ja": "帰国用サマリー"},
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
        "title": t(TITLE.get(report.kind, {}), report.lang),
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

        today_day = surgery.day_of(timezone.localdate())

        # 위클리 리포트는 구간을 프론트가 정하지 않는다. 안 보내면 최근 7일.
        # 귀국용은 전체 기간이 기본이다 — 현지 의료진이 처음 보는 문서라서.
        if kind == RecoveryReport.Kind.SUBMISSION:
            default_to, default_from = today_day, max(0, today_day - WEEKLY_DAYS + 1)
        else:
            default_to, default_from = today_day, 0

        try:
            day_from = int(request.data.get("day_from", default_from))
            day_to = int(request.data.get("day_to", default_to))
        except (TypeError, ValueError):
            return api_error("VALIDATION_ERROR", "day_from과 day_to는 숫자여야 합니다")

        if not 0 <= day_from <= day_to <= surgery.program_days:
            return api_error(
                "VALIDATION_ERROR",
                f"구간은 D+0 ~ D+{surgery.program_days} 안이어야 합니다",
            )

        # 미래를 담으면 completion()의 분모에 오지 않은 날이 들어가 완주율이 실제보다
        # 낮게 나간다. 병원에 내는 문서라 사실이 아닌 줄이 붙으면 안 된다.
        if day_to > today_day:
            return api_error("VALIDATION_ERROR", "아직 오지 않은 날짜는 담을 수 없습니다")

        lang = request.data.get("lang") or request.user.lang
        if lang not in ("ko", "ja", "en"):
            return api_error("VALIDATION_ERROR", "지원하지 않는 언어입니다")

        # 제출용 뷰는 화면 진입마다 호출된다. 같은 날 같은 조건이면 이미 만든 걸 준다.
        # 안 그러면 화면을 들락거릴 때마다 행이 쌓이고, 상담 첨부에서 어느 걸 고를지
        # 모호해진다. 전달한 리포트를 그 시점 내용으로 고정하는 성질은 그대로 유지된다.
        today = timezone.localdate()
        day_start = timezone.make_aware(datetime.combine(today, time.min))
        existing = (
            surgery.reports.filter(
                kind=kind,
                lang=lang,
                day_from=day_from,
                day_to=day_to,
                generated_at__gte=day_start,
            )
            .first()                      # Meta.ordering = ["-generated_at"] → 최신
        )
        if existing:
            return Response(_payload(existing), status=status.HTTP_200_OK)

        # 독자가 다르면 문서도 다르다. 위클리는 증상 변화, 귀국용은 시술 개요와
        # 남은 금기가 본문이다(API-DESIGN 843행).
        if kind == RecoveryReport.Kind.REPATRIATION:
            body = build_repatriation_body(surgery, day_from, day_to, lang)
        else:
            body = build_body(surgery, day_from, day_to, lang)

        body, ai_model = polish_body(body, lang)

        report = RecoveryReport.objects.create(
            surgery=surgery,
            kind=kind,
            lang=lang,
            day_from=day_from,
            day_to=day_to,
            body=body,
            meta=_meta(surgery, day_from, day_to, lang),
            ai_model=ai_model,
            ai_prompt_version=PROMPT_VERSION if ai_model else "",
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

class ReportDetailView(APIView):
    def get(self, request, report_id):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        # 남의 리포트 id를 넣어도 404 — 존재 여부를 알려주지 않는다
        report = RecoveryReport.objects.filter(id=report_id, surgery=surgery).first()
        if report is None:
            return api_error("NOT_FOUND", "리포트를 찾을 수 없습니다", 404)

        return Response(_payload(report))