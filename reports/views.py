"""리포트 — 계약 9.6"""
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from datetime import datetime, time

from care.services import completion
from config.utils import api_error, t

from care.services import DEFAULT_LANG

from .builder import build_body, build_repatriation_body, routine_rates
from .models import RecoveryReport
from .polish import PROMPT_VERSION, polish_body

# 샘플 PDF 하단 3줄. 문서 성격 · 원본 보관 · 판정하지 않음을 각각 밝힌다.
DISCLAIMERS = [
    {
        "ko": "본 자료는 환자가 입력한 체크인 기록과 자가관리 이행 내역을 자동으로 정리한 참고 자료입니다.",
        "ja": "本資料は患者が入力したチェックイン記録と自己管理の実施内容を自動で整理した参考資料です。",
    },
    {
        "ko": "환자가 입력한 자료는 원본 그대로 보관되며, 의료기관에 함께 제공됩니다.",
        "ja": "患者が入力した資料は原本のまま保管され、医療機関にも提供されます。",
    },
    {
        "ko": "본 자료는 의료진의 진단이나 의학적 소견을 대신하지 않습니다. 증상의 중증도나 정상·이상 여부를 자동으로 판단하지 않습니다.",
        "ja": "本資料は医療者の診断や医学的所見に代わるものではありません。症状の重症度や正常・異常の判定は行いません。",
    },
]

# 제출용은 매번 새로 만드는 문서가 아니라 최근 7일이 계속 갱신되는 위클리 리포트다.
WEEKLY_DAYS = 7

TITLE = {
    RecoveryReport.Kind.SUBMISSION: {"ko": "위클리 리포트", "ja": "ウィークリーレポート"},
    RecoveryReport.Kind.REPATRIATION: {"ko": "귀국용 요약", "ja": "帰国用サマリー"},
}


def _window_stats(surgery, day_from, day_to, today_day):
    """한 구간의 체크인·사진·완주율. 이번 주와 지난주에 같은 함수를 쓴다."""
    if day_from > day_to:
        return None

    stats = surgery.checkins.filter(
        date__gte=surgery.date_of(day_from), date__lte=surgery.date_of(day_to)
    ).aggregate(
        n=Count("id", distinct=True), photos=Count("photos", distinct=True)
    )
    # 구간 이행률. 샘플 PDF의 "78% 주간 평균" 카드가 이 값이다.
    # routines와 같은 함수를 써서 목록 합계와 카드 숫자가 어긋나지 않는다.
    routines = routine_rates(surgery, day_from, day_to, DEFAULT_LANG)
    done = sum(r["done"] for r in routines)
    required = sum(r["required"] for r in routines)

    return {
        "day_from": day_from,
        "day_to": day_to,
        "checkin_count": stats["n"],
        "checkin_total": day_to - day_from + 1,     # "6 / 7일" 표기용
        "photo_count": stats["photos"],
        # 이 구간만. 리포트 카드용
        "window_completion_rate": round(done / required, 3) if required else None,
        # D+0부터 누적. /home의 완주율과 같은 값이라 화면 간 일관성을 위해 유지한다
        "completion_rate": completion(
            surgery, day_to, include_today=day_to < today_day
        ),
    }


def _meta(surgery, day_from, day_to, lang):
    today_day = surgery.day_of(timezone.localdate())
    current = _window_stats(surgery, day_from, day_to, today_day)

    # 같은 길이의 직전 구간. 샘플 PDF의 "지난주 대비" 카드가 여기서 나온다.
    span = day_to - day_from + 1
    prev_from, prev_to = day_from - span, day_from - 1
    previous = _window_stats(surgery, max(0, prev_from), prev_to, today_day) \
        if prev_to >= 0 else None

    def delta(key):
        """이번 주 - 지난주. 비교할 구간이 없으면 None."""
        if previous is None or current[key] is None or previous[key] is None:
            return None
        return round(current[key] - previous[key], 3)

    # confirmed_at / next_confirm_at은 뺐다(2026-08-17 결정).
    # "병원이 이 기록을 확인했다"를 Appointment 내원 이력으로 근사하려 했는데,
    # 병원 스태프 화면이 없어 missed를 찍을 주체가 없다. 안 간 내원도 확인으로
    # 잡히므로 사실이 아닌 이력이 병원 제출 문서에 남는다.
    # 예약 일정 자체는 GET /appointments에 그대로 있다.

    return {
        "checkin_count": current["checkin_count"],
        "checkin_total": current["checkin_total"],
        "photo_count": current["photo_count"],
        "window_completion_rate": current["window_completion_rate"],
        "completion_rate": current["completion_rate"],
        # 지난주 값과 증감. 없으면(첫 주) null — 프론트는 비교 칸을 숨기면 된다.
        "previous": previous,
        "delta": {
            "checkin_count": delta("checkin_count"),
            "photo_count": delta("photo_count"),
            "window_completion_rate": delta("window_completion_rate"),
            "completion_rate": delta("completion_rate"),
        },
        "procedure": t(surgery.procedure_type.name, lang),
        "clinic": t(surgery.clinic.name, lang),
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
        # 문구는 저장하지 않는다. 코드 상수라 고치면 과거 리포트에도 반영된다.
        "disclaimers": [t(d, report.lang) for d in DISCLAIMERS],
        # 어느 모델·프롬프트로 만든 문서인지. 비어 있으면 AI를 쓰지 않았다는 뜻이다
        # (API-DESIGN 778행 "재현과 감사").
        "ai_model": report.ai_model,
        "ai_prompt_version": report.ai_prompt_version,
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

        # meta를 먼저 만든다. weekly_summary가 이행률 이동을 문장에 넣으려면
        # 같은 숫자를 봐야 한다 — 따로 계산하면 본문과 카드가 어긋난다.
        meta = _meta(surgery, day_from, day_to, lang)

        # 독자가 다르면 문서도 다르다. 위클리는 증상 변화, 귀국용은 시술 개요와
        # 남은 금기가 본문이다(API-DESIGN 843행).
        if kind == RecoveryReport.Kind.REPATRIATION:
            body = build_repatriation_body(surgery, day_from, day_to, lang)
        else:
            body = build_body(surgery, day_from, day_to, lang, meta)

        body, ai_model = polish_body(body, lang)

        report = RecoveryReport.objects.create(
            surgery=surgery,
            kind=kind,
            lang=lang,
            day_from=day_from,
            day_to=day_to,
            body=body,
            meta=meta,
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