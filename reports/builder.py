"""리포트 문장 생성 — 규칙 기반.

AI 없이 완성된 문장을 만든다. AI는 나중에 이 결과를 다듬는 역할만 맡는다.
호출이 실패해도 리포트가 비지 않는 게 목적이다(계약 796행).

[절대 규칙]
kind는 ok · part · miss · info 4종 고정. warning · abnormal · risk를 추가하면
그 순간 판정이 되고 의료행위가 된다(계약 792행).

part가 증상에 붙을 때도 "더 높은 위치로 기록"이라는 관찰 서술이지 "악화"가 아니다.

[증상 변화 표현]
"호전 / 악화"는 쓰지 않는다. 좋아졌다·나빠졌다는 평가이고, 평가는 진료의 몫이다.
대신 "낮게 / 높게 기록"과 변화율(%)만 낸다 — 같은 사실을 전달하면서 판정이 아니다.
polish.py의 BANNED 목록이 이 어휘를 자동으로 되돌린다.

[level 주간 평균은 담는다 — 2026-08-17 결정]
"지난주 3.4 → 이번주 2.1" 형태로 리포트 샘플에 들어간다. 환자가 입력한 눈금의
주간 평균이라는 사실 보고이지 중증도 판정이 아니다. 출처를 함께 표기하고,
등급 라벨("경증/중등증")은 만들지 않는다.

낱개 level(그날 4점)은 여전히 담지 않는다. 평균만 낸다.
"""
from collections import defaultdict

from django.utils import timezone

from care.services import state_of, timeline
from checkins.models import CheckinSymptom
from config.utils import t
from protocols.models import (ActivityRulePhase, ActivityRuleTemplate,
                              CareTaskTemplate)


def _line(kind, text, **extra):
    """리포트 한 줄. kind와 text는 계약 고정이고 extra는 렌더러용 부속 데이터다.

    PDF는 "지난주 3.4 → 이번주 2.1" 같은 표를 그려야 해서 문장만으로는 부족하다.
    문장을 파싱하게 두면 문구를 바꿀 때마다 깨지므로 숫자를 따로 실어 보낸다.
    """
    return {"kind": kind, "text": text, **extra}


# ──────────────────────────────────────────────────────────────
# 증상 흐름
# ──────────────────────────────────────────────────────────────
SYMPTOM_PEAK = {
    "ko": "{name} — D+{peak}에 가장 높게 기록된 뒤 D+{settle}부터 낮은 위치로 유지",
    "ja": "{name} — D+{peak}に最も高く記録され、D+{settle}から低い位置で推移",
}
SYMPTOM_UP = {
    "ko": "{name} — D+{day}부터 더 높은 위치로 기록",
    "ja": "{name} — D+{day}から高い位置で記録",
}
SYMPTOM_FLAT = {
    "ko": "{name} — D+{a}~D+{b} 같은 위치로 유지",
    "ja": "{name} — D+{a}~D+{b} 同じ位置で推移",
}
SYMPTOM_ONE = {
    "ko": "{name} — D+{day} 1회 기록",
    "ja": "{name} — D+{day} 1回記録",
}
SYMPTOM_NONE = {
    "ko": "{name} — D+{a}~D+{b} 기록 없음",
    "ja": "{name} — D+{a}~D+{b} 記録なし",
}

# 이전 기간 대비 변화. "호전 / 악화"를 쓰지 않는 이유는 모듈 docstring 참조.
SYMPTOM_DOWN = {
    "ko": "{name} — 이전 7일 대비 {pct}% 낮게 기록 ({peak})",
    "ja": "{name} — 前7日比 {pct}% 低く記録 ({peak})",
}
SYMPTOM_RISE = {
    "ko": "{name} — 이전 7일 대비 {pct}% 높게 기록 ({peak})",
    "ja": "{name} — 前7日比 {pct}% 高く記録 ({peak})",
}
SYMPTOM_KEEP = {
    "ko": "{name} — 이전 7일과 비슷한 위치로 유지 ({peak})",
    "ja": "{name} — 前7日と同程度で推移 ({peak})",
}
SYMPTOM_FIRST = {
    "ko": "{name} — D+{a}~D+{b} 기록 ({peak})",
    "ja": "{name} — D+{a}~D+{b} 記録 ({peak})",
}
PEAK_NOTE = {"ko": "D+{day}에 가장 높음", "ja": "D+{day}が最も高い"}

# 이 폭 안이면 "비슷"으로 본다. 하루치 눈금 변동을 변화로 읽지 않기 위함.
FLAT_BAND_PCT = 10


def _levels_by_term(surgery, day_from, day_to):
    """{SymptomTerm: [(day, level), ...]} — 날짜 오름차순."""
    rows = (
        CheckinSymptom.objects.filter(
            checkin__surgery=surgery,
            checkin__date__gte=surgery.date_of(day_from),
            checkin__date__lte=surgery.date_of(day_to),
        )
        .select_related("term", "checkin")
        .order_by("term__order", "checkin__date")
    )
    by_term = defaultdict(list)
    for row in rows:
        by_term[row.term].append((surgery.day_of(row.checkin.date), row.level))
    return by_term


def symptom_flow(surgery, day_from, day_to, lang):
    """증상별 변화. 이전 같은 길이의 구간과 비교한다.

    level 원값은 문장에 넣지 않는다. 방향과 변화율만 낸다(규칙 2).
    """
    span = day_to - day_from + 1
    current = _levels_by_term(surgery, day_from, day_to)
    previous = _levels_by_term(surgery, max(0, day_from - span), day_from - 1)

    # 기록이 없는 항목도 목록에 남긴다 — 붓기·통증·멍은 계속 노출한다
    terms = sorted(
        surgery.procedure_type.symptom_terms.all(), key=lambda x: x.order
    )

    out = []
    for term in terms:
        name = t(term.name, lang)
        points = current.get(term, [])
        # 렌더러가 표를 그릴 때 쓰는 공통 필드. 문장을 파싱하지 않게 한다.
        base = {"key": term.key, "name": name}

        if not points:
            out.append(_line("miss", t(SYMPTOM_NONE, lang).format(
                name=name, a=day_from, b=day_to),
                curr_avg=None, prev_avg=None, delta_pct=None, **base))
            continue

        cur_avg = round(sum(lv for _, lv in points) / len(points), 1)
        peak_day = max(points, key=lambda p: (p[1], -p[0]))[0]
        peak = t(PEAK_NOTE, lang).format(day=peak_day)

        if len(points) == 1:
            out.append(_line("info", t(SYMPTOM_ONE, lang).format(
                name=name, day=points[0][0]),
                curr_avg=cur_avg, prev_avg=None, delta_pct=None,
                peak_day=peak_day, **base))
            continue

        prev_points = previous.get(term, [])
        if not prev_points:
            # 비교할 이전 구간이 없다. 첫 리포트이거나 그때 기록이 없었다.
            out.append(_line("info", t(SYMPTOM_FIRST, lang).format(
                name=name, a=day_from, b=day_to, peak=peak),
                curr_avg=cur_avg, prev_avg=None, delta_pct=None,
                peak_day=peak_day, **base))
            continue

        prev_avg = round(sum(lv for _, lv in prev_points) / len(prev_points), 1)
        pct = round((cur_avg - prev_avg) / prev_avg * 100) if prev_avg else 0
        extra = dict(curr_avg=cur_avg, prev_avg=prev_avg, delta_pct=pct,
                     peak_day=peak_day, **base)

        if pct <= -FLAT_BAND_PCT:
            out.append(_line("info", t(SYMPTOM_DOWN, lang).format(
                name=name, pct=abs(pct), peak=peak), **extra))
        elif pct >= FLAT_BAND_PCT:
            # 관찰 서술이지 악화 판정이 아니다
            out.append(_line("part", t(SYMPTOM_RISE, lang).format(
                name=name, pct=pct, peak=peak), **extra))
        else:
            out.append(_line("info", t(SYMPTOM_KEEP, lang).format(
                name=name, peak=peak), **extra))

    return out


# ──────────────────────────────────────────────────────────────
# 루틴 이행
# ──────────────────────────────────────────────────────────────
ADHERENCE_OK = {
    "ko": "{name} — D+{a}~D+{b} 전일 완료",
    "ja": "{name} — D+{a}~D+{b} 全日完了",
}
ADHERENCE_PART = {
    "ko": "{name} — D+{a}~D+{b} 중 {done}일 완료 ({total}일 중)",
    "ja": "{name} — D+{a}~D+{b} のうち{done}日完了（全{total}日）",
}
ADHERENCE_MISS = {
    "ko": "{name} — D+{a}~D+{b} 이행 기록 없음",
    "ja": "{name} — D+{a}~D+{b} 実施記録なし",
}
# 매일 기록은 있는데 하루 목표 횟수를 한 번도 채우지 못한 경우.
# 이걸 "이행 기록 없음"으로 쓰면 병원에 사실과 다르게 전달된다.
ADHERENCE_PARTIAL_ONLY = {
    "ko": "{name} — D+{a}~D+{b} 중 {logged}일 기록, 하루 {per}회 목표는 미달",
    "ja": "{name} — D+{a}~D+{b} のうち{logged}日記録、1日{per}回の目標には未達",
}


def _adherence_targets(surgery, lang):
    """이행을 따질 대상. (key, 이름, day_from, day_to, 하루 횟수) 목록.

    템플릿 + 복약입니다. 복약은 마스터에 없고 처방에서 합성되는데,
    completion()이 완주율에 포함하므로 리포트에도 같이 실려야 숫자와 문장이 맞습니다.
    services._medication_task()와 같은 조건을 씁니다.
    """
    targets = [
        (tpl.key, t(tpl.name, lang), tpl.day_from, tpl.day_to, tpl.times_per_day)
        for tpl in CareTaskTemplate.objects.filter(procedure_type=surgery.procedure_type)
    ]

    rx = getattr(surgery, "prescription", None)
    if rx and rx.confirmed_at:
        regular = rx.items.filter(is_prn=False).count()   # 돈복은 세지 않는다
        if regular:
            name = t({"ko": f"처방약 {regular}종", "ja": f"処方薬 {regular}種"}, lang)
            targets.append(("medication", name, 0, (rx.total_days or 1) - 1, rx.per_day or 1))

    return targets


def care_adherence(surgery, day_from, day_to, lang):
    """완주율과 같은 원천(TaskLog)을 쓴다. 화면과 리포트가 어긋나지 않도록."""
    from checkins.models import TaskLog

    targets = _adherence_targets(surgery, lang)
    logs = {
        (r["task_key"], r["date"]): r["done_count"]
        for r in TaskLog.objects.filter(
            surgery=surgery,
            date__gte=surgery.date_of(day_from),
            date__lte=surgery.date_of(day_to),
        ).values("task_key", "date", "done_count")
    }

    out = []
    for key, name, task_from, task_to, per_day in targets:
        a = max(day_from, task_from)
        b = min(day_to, task_to)
        if a > b:
            continue                        # 이 구간에 해당 없는 루틴

        total = b - a + 1
        counts = [logs.get((key, surgery.date_of(d)), 0) for d in range(a, b + 1)]
        done = sum(1 for c in counts if c >= per_day)     # 목표를 채운 날
        logged = sum(1 for c in counts if c > 0)          # 조금이라도 한 날

        if done == total:
            out.append(_line("ok", t(ADHERENCE_OK, lang).format(name=name, a=a, b=b)))
        elif logged == 0:
            out.append(_line("miss", t(ADHERENCE_MISS, lang).format(name=name, a=a, b=b)))
        elif done == 0:
            # 기록은 있으나 목표 횟수를 채운 날이 없다. miss로 쓰면 사실과 다르다.
            out.append(_line("part", t(ADHERENCE_PARTIAL_ONLY, lang).format(
                name=name, a=a, b=b, logged=logged, per=per_day)))
        else:
            out.append(_line("part", t(ADHERENCE_PART, lang).format(
                name=name, a=a, b=b, done=done, total=total)))

    return out


# ──────────────────────────────────────────────────────────────
# 일정
# ──────────────────────────────────────────────────────────────
def events(surgery, day_from, day_to, lang):
    """timeline()에서 구간에 걸치는 것만. 전부 info — 일정은 판정이 아니다."""
    return [
        _line("info", f"D+{e['day']} — {e['label']}")
        for e in timeline(surgery, lang)
        if day_from <= e["day"] <= day_to
    ]


def routine_rates(surgery, day_from, day_to, lang):
    """루틴별 이행률. 문장이 아니라 숫자로 낸다 — 샘플 PDF의 이행 목록용.

    분모는 '일수'가 아니라 '회차'다. completion()과 같은 기준이다.

    오늘은 세지 않는다. 아직 진행 중인 하루를 미이행으로 잡으면 이행률이
    사실과 달라진다 — completion(include_today=False)와 같은 원칙이다.
    """
    from checkins.models import TaskLog

    # 오늘은 아직 끝나지 않았다. 어제까지만 센다.
    day_to = min(day_to, surgery.day_of(timezone.localdate()) - 1)
    if day_from > day_to:
        return []

    logs = {
        (r["task_key"], r["date"]): r["done_count"]
        for r in TaskLog.objects.filter(
            surgery=surgery,
            date__gte=surgery.date_of(day_from),
            date__lte=surgery.date_of(day_to),
        ).values("task_key", "date", "done_count")
    }

    out = []
    for key, name, task_from, task_to, per_day in _adherence_targets(surgery, lang):
        a = max(day_from, task_from)
        b = min(day_to, task_to)
        if a > b:
            continue                       # 이 구간에 없는 루틴은 목록에서 뺀다

        required = per_day * (b - a + 1)
        done = sum(
            min(logs.get((key, surgery.date_of(d)), 0), per_day)
            for d in range(a, b + 1)
        )
        out.append({
            "key": key,
            "name": name,
            "rate": round(done / required, 3) if required else 0.0,
            "done": done,
            "required": required,
            "day_from": task_from,
            "day_to": task_to,
            # 루틴 기간이 이 구간 안에서 끝났다. 화면에서 "완료"로 표시할 수 있다.
            "ended": task_to <= day_to,
        })

    out.sort(key=lambda x: (-x["rate"], x["key"]))
    return out


def checkin_days(surgery, day_from, day_to):
    """구간의 날짜별 체크인 유무. 샘플 PDF의 D+7 ✓ / D+11 · 스트립용.

    오늘도 포함하되 is_today로 구분한다. 아직 안 끝난 하루를 빈 점으로 그리면
    '빠뜨린 날'처럼 읽히므로, 프론트가 다르게 표시할 수 있어야 한다.
    """
    done = dict(
        surgery.checkins.filter(
            date__gte=surgery.date_of(day_from),
            date__lte=surgery.date_of(day_to),
        ).values_list("date", "completed_at")
    )
    today = timezone.localdate()

    out = []
    for day in range(day_from, day_to + 1):
        the_date = surgery.date_of(day)
        out.append({
            "day": day,
            # body는 JSONField다. date 객체를 그대로 넣으면 저장에서 TypeError.
            "date": the_date.isoformat(),
            "has_checkin": the_date in done,
            "completed": done.get(the_date) is not None,
            "is_today": the_date == today,
        })
    return out


ROUTINE_CONTINUE = {
    "ko": "{name} 계속 — D+{day_to}까지 유지",
    "ja": "{name} 継続 — D+{day_to}まで",
}


def next_week(surgery, day_to, span, lang):
    """다음 구간에 예정된 것. 병원 프로토콜에서 파생한다.

    일정(해금·내원·귀국)과 이어지는 루틴 두 종류를 섞는다.
    아이콘은 담지 않는다 — 리포트는 의료기관 제시용 문서라 이모지를 쓰지 않는다.
    """
    start, end = day_to + 1, day_to + span

    out = []
    for event in timeline(surgery, lang):
        if not start <= event["day"] <= end:
            continue
        out.append(_line(
            "info", f"D+{event['day']} — {event['label']}",
            day=event["day"],
            date=event["date"].isoformat(),      # body는 JSONField다
            type=event["type"],
            badge=event["badge"],
        ))

    # 다음 구간에도 이어지는 루틴. 이번 구간 이행률을 함께 실어 보낸다.
    rates = {r["key"]: r for r in routine_rates(surgery, day_to - span + 1, day_to, lang)}
    for key, name, task_from, task_to, _per_day in _adherence_targets(surgery, lang):
        if task_to <= day_to or task_from > end:
            continue                       # 끝났거나 아직 시작 전
        rate = rates.get(key, {}).get("rate")
        out.append(_line(
            "info", t(ROUTINE_CONTINUE, lang).format(name=name, day_to=task_to),
            key=key, type="routine", day_to=task_to, rate=rate,
        ))

    return out


def build_body(surgery, day_from, day_to, lang):
    """위클리 리포트 본문.

    care_adherence(문장 목록)는 담지 않는다. 대신 routines에 루틴별 이행률을
    숫자로 낸다 — 샘플 PDF가 "상체 45° 수면 100% / 압박 테이핑 43%" 형태로
    그리기 때문이다(2026-08-17 샘플 확인).
    care_adherence() 함수는 남겨둔다 — 귀국용 요약에서 다시 쓸 수 있다.
    """
    return {
        "checkin_days": checkin_days(surgery, day_from, day_to),
        "symptom_flow": symptom_flow(surgery, day_from, day_to, lang),
        "routines": routine_rates(surgery, day_from, day_to, lang),
        "events": events(surgery, day_from, day_to, lang),
        "next_week": next_week(surgery, day_to, day_to - day_from + 1, lang),
    }


# ──────────────────────────────────────────────────────────────
# 귀국용 요약 — 현지 의료진이 처음 보는 문서
# ──────────────────────────────────────────────────────────────
# 제출용(위클리)과 독자가 다르다. 한국 병원은 맥락을 알지만 현지 의사는 모른다.
# "무슨 수술을 받았고, 지금 뭐가 금지이고, 다음 진료가 언제인지"가 본문이다.
OVERVIEW = {
    "procedure": {"ko": "시술", "ja": "施術", "en": "Procedure"},
    "detail": {"ko": "상세", "ja": "詳細", "en": "Detail"},
    "date": {"ko": "시술일", "ja": "施術日", "en": "Date"},
    "clinic": {"ko": "시술 기관", "ja": "施術機関", "en": "Clinic"},
    "surgeon": {"ko": "담당의", "ja": "担当医", "en": "Surgeon"},
    "implant": {"ko": "삽입물", "ja": "挿入物", "en": "Implant"},
    "course": {"ko": "경과", "ja": "経過", "en": "Course"},
    "restriction": {"ko": "주의", "ja": "注意", "en": "Restrictions"},
    "next_visit": {"ko": "다음 진료", "ja": "次回受診", "en": "Next visit"},
}
COURSE_TEXT = {
    "ko": "체크인 기록 기반 자동 요약 (D+{a}~D+{b}, {n}건)",
    "ja": "チェックイン記録に基づく自動要約（D+{a}〜D+{b}、{n}件）",
}
NOW_TEXT = {"ko": "{date} / 현재 D+{day}", "ja": "{date} / 現在 D+{day}"}
UNTIL_TEXT = {"ko": "D+{day}까지 {what}", "ja": "D+{day}まで{what}"}
NO_NEXT_VISIT = {"ko": "예정 없음", "ja": "予定なし"}

# 발표용. 국제 환자 요약(IPS) 표준 섹션에 우리 항목이 어디로 대응되는지 보여준다.
IPS_MAPPING = {
    "history_of_procedures": ["procedure", "date", "clinic", "surgeon", "implant"],
    "plan_of_care": ["restriction", "next_visit"],
    "patient_story": ["course"],
}


def _remaining_restrictions(surgery, day, lang):
    """오늘 이후에 풀리는 규칙만 모은다.

    하드코딩이 아니라 phase에서 수집하므로 프로토콜이 바뀌면 문장도 따라 바뀐다.
    이미 풀린 규칙은 현지 의사에게 알릴 이유가 없어 제외한다.
    """
    out = []
    rules = (
        ActivityRuleTemplate.objects
        .filter(procedure_type=surgery.procedure_type)
        .prefetch_related("phases")
    )
    for rule in rules:
        current = state_of(rule, day)
        if current is None or current["status"] == ActivityRulePhase.Status.OK:
            continue                       # 이미 제한이 없다
        out.append({
            "day_to": current["day_to"],
            "text": t(UNTIL_TEXT, lang).format(
                day=current["day_to"], what=t(rule.name, lang)
            ),
        })

    # 오래 남는 제한을 뒤로 — 읽는 사람이 가까운 것부터 본다
    out.sort(key=lambda x: x["day_to"])
    return [x["text"] for x in out]


def build_repatriation_body(surgery, day_from, day_to, lang):
    """귀국용 요약. 제출용과 구조 자체가 다르다.

    lines는 "라벨: 값" 한 줄씩이라 현지 의사가 훑어 읽기 쉽다.
    kind는 전부 info — 개요와 제한 안내이지 판정이 아니다.
    """
    today = surgery.day_of(timezone.localdate())
    checkin_count = surgery.checkins.filter(
        date__gte=surgery.date_of(day_from), date__lte=surgery.date_of(day_to)
    ).count()

    def row(key, value):
        return _line("info", f"{t(OVERVIEW[key], lang)}: {value}")

    lines = [
        row("procedure", t(surgery.procedure_type.name, lang)),
        row("date", t(NOW_TEXT, lang).format(date=surgery.surgery_date, day=today)),
        row("clinic", f"{t(surgery.clinic.name, lang)} {surgery.clinic.tel}"),
        row("surgeon", surgery.surgeon.name_romaji or t(surgery.surgeon.name, lang)),
    ]

    if detail := t(surgery.detail, lang):
        lines.insert(1, row("detail", detail))

    # 보형물 정보는 귀국 후 MRI·재수술 검토에 필수다(ERD 865행)
    if surgery.implant:
        lines.append(row("implant", " / ".join(
            str(v) for v in surgery.implant.values() if v)))

    lines.append(row("course", t(COURSE_TEXT, lang).format(
        a=day_from, b=day_to, n=checkin_count)))

    restrictions = _remaining_restrictions(surgery, today, lang)
    if restrictions:
        lines.append(row("restriction", " / ".join(restrictions)))

    upcoming = (
        surgery.appointments
        .filter(scheduled_at__gt=timezone.now())
        .order_by("scheduled_at")
        .first()
    )
    if upcoming:
        local = timezone.localtime(upcoming.scheduled_at)
        lines.append(row("next_visit", "D+{d} {title} ({date})".format(
            d=surgery.day_of(local.date()),
            title=t(upcoming.title, lang),
            date=local.date(),
        )))
    else:
        lines.append(row("next_visit", t(NO_NEXT_VISIT, lang)))

    return {
        "overview": lines,
        "symptom_flow": symptom_flow(surgery, day_from, day_to, lang),
        "ips_mapping": IPS_MAPPING,
    }