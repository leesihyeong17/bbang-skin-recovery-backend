"""리포트 문장 생성 — 규칙 기반.

AI 없이 완성된 문장을 만든다. AI는 나중에 이 결과를 다듬는 역할만 맡는다.
호출이 실패해도 리포트가 비지 않는 게 목적이다(계약 796행).

[절대 규칙]
kind는 ok · part · miss · info 4종 고정. warning · abnormal · risk를 추가하면
그 순간 판정이 되고 의료행위가 된다(계약 792행).

part가 증상에 붙을 때도 "더 높은 위치로 기록"이라는 관찰 서술이지 "악화"가 아니다.
"""
from collections import defaultdict

from care.services import completion, timeline
from checkins.models import CheckinSymptom
from config.utils import t
from protocols.models import CareTaskTemplate


def _line(kind, text):
    return {"kind": kind, "text": text}


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


def symptom_flow(surgery, day_from, day_to, lang):
    """증상 그래프를 문장으로. level 값 자체는 절대 문장에 넣지 않는다."""
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

    out = []
    for term, points in by_term.items():
        name = t(term.name, lang)

        if len(points) == 1:
            out.append(_line("info", t(SYMPTOM_ONE, lang).format(
                name=name, day=points[0][0])))
            continue

        peak_day, peak_level = max(points, key=lambda p: (p[1], -p[0]))
        last_day, last_level = points[-1]

        if last_level < peak_level:
            # 피크 이후 처음으로 낮아진 날
            settle = next(d for d, lv in points if d > peak_day and lv < peak_level)
            out.append(_line("info", t(SYMPTOM_PEAK, lang).format(
                name=name, peak=peak_day, settle=settle)))
        elif last_level > points[0][1]:
            # 관찰 서술이지 악화 판정이 아니다
            rise = next(d for d, lv in points if lv > points[0][1])
            out.append(_line("part", t(SYMPTOM_UP, lang).format(name=name, day=rise)))
        else:
            out.append(_line("info", t(SYMPTOM_FLAT, lang).format(
                name=name, a=points[0][0], b=last_day)))

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


def build_body(surgery, day_from, day_to, lang):
    return {
        "symptom_flow": symptom_flow(surgery, day_from, day_to, lang),
        "care_adherence": care_adherence(surgery, day_from, day_to, lang),
        "events": events(surgery, day_from, day_to, lang),
    }