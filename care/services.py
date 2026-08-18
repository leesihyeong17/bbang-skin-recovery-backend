"""
care/services.py — D+N 계산 계층

이 프로젝트의 심장입니다. DB에는 "규칙 원본"만 있고, "오늘 화면"은 여기서 만듭니다.

    CareTaskTemplate(냉찜질 D+0~D+3 하루4회) + Surgery(수술일) + 오늘날짜
        → care_items() → {"tasks": [냉찜질 4회 중 2회 완료, ...], "rules": {...}}

[반드시 지킬 것]
- 엔드포인트에서 CareTaskTemplate.objects.filter()를 직접 쓰지 마세요.
  홈 / 캘린더 / 기록상세 / 리포트가 전부 같은 계산을 쓰는데, view마다 따로 짜면
  "홈에서는 가능인데 캘린더에서는 주의"인 버그가 납니다. 여기서 day만 바꿔 부르세요.
- 판정을 만들지 마세요(기능명세서 1.2). 이 파일 어디에도
  severity / risk / is_normal / grade / recovery_score를 만들지 않습니다.

[공개 함수]
    pick(value, lang)                    다국어 JSON → 문자열
    stage_of(surgery, day)               회복 단계 이름
    state_of(rule, day)                  규칙 하나의 그날 상태
    care_items(surgery, day)             그날의 루틴 + 규칙 (핵심)
    completion(surgery, day)             루틴 완주율
    timeline(surgery)                    앞으로의 변화
    validate_task_key(surgery, day, key) PUT /task-logs 검증
"""
from config.utils import t as pick

from datetime import date

from checkins.models import TaskLog
from protocols.models import ActivityRuleTemplate, CareTaskTemplate

DEFAULT_LANG = "ko"

# 상태 등급. 숫자가 클수록 자유로움 → 전이 방향 판정에 씀 (해금인가 주의 시작인가)
STATUS_RANK = {"no": 0, "care": 1, "ok": 2}

TIMELINE_BADGES = {
    "unlock": {"ko": "해금", "ja": "解禁", "en": "Unlocked"},
    "care_start": {"ko": "주의", "ja": "注意", "en": "Caution"},
    "visit": {"ko": "내원", "ja": "来院", "en": "Visit"},
    "remote": {"ko": "원격", "ja": "遠隔", "en": "Remote"},
    "return": {"ko": "귀국", "ja": "帰国", "en": "Return"},
    "complete": {"ko": "완주", "ja": "完了", "en": "Complete"},
}


# ──────────────────────────────────────────────────────────────
# 다국어
# ──────────────────────────────────────────────────────────────
def pick(value, lang=DEFAULT_LANG):
    """
    {"ko": "냉찜질", "ja": "冷やす"} → "냉찜질"

    응답에는 절대 dict를 그대로 담지 않습니다. 프론트가 언어 분기를 하지 않도록
    서버가 골라서 문자열 하나만 내려주기로 했습니다(API 명세 9.0).
    해당 언어가 없으면 ko로, ko도 없으면 아무 값이나 폴백합니다.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return str(value)
    return value.get(lang) or value.get(DEFAULT_LANG) or next(iter(value.values()), "")


def timeline_badge(event_type, lang=DEFAULT_LANG):
    """일정 이벤트 종류를 요청 언어에 맞는 짧은 배지 문구로 바꿉니다."""
    return pick(TIMELINE_BADGES[event_type], lang)


def _fill(text, surgery):
    """텍스트 안의 {RET} 플레이스홀더를 실제 귀국일로 바꿉니다."""
    if not text or "{RET}" not in text:
        return text
    ret = f"D+{surgery.return_day}" if surgery.return_date else "귀국일"
    return text.replace("{RET}", ret)


# ──────────────────────────────────────────────────────────────
# 회복 단계
# ──────────────────────────────────────────────────────────────
def stage_of(surgery, day, lang=DEFAULT_LANG):
    """
    stage_map = [{"to": 3, "name": {...}}, {"to": 7, ...}, ...]
    day가 속하는 첫 구간의 이름을 반환합니다.
    """
    for stage in surgery.procedure_type.stage_map or []:
        if day <= stage["to"]:
            return pick(stage["name"], lang)
    return ""


# ──────────────────────────────────────────────────────────────
# 규칙 구간 판정
# ──────────────────────────────────────────────────────────────
def _phases_with_from(rule):
    """
    ActivityRulePhase에는 day_to만 있고 day_from이 없습니다(설계 D5).
    앞 구간의 day_to + 1이 시작일이므로 여기서 채워 넣습니다.

        [~14 no] [~28 care] [~120 ok]
          ↓
        [0~14 no] [15~28 care] [29~120 ok]
    """
    out, prev_to = [], -1
    for p in rule.phases.all():          # Meta.ordering = ["order"]
        out.append({
            "day_from": prev_to + 1,
            "day_to": p.day_to,
            "status": p.status,
            "text": p.text,
        })
        prev_to = p.day_to
    return out


def state_of(rule, day):
    """
    규칙 하나가 그날 가능/주의/금지 중 무엇인지.
    프로그램 기간을 넘어선 day는 마지막 구간을 그대로 적용합니다.
    """
    phases = _phases_with_from(rule)
    if not phases:
        return None
    for ph in phases:
        if day <= ph["day_to"]:
            return ph
    return phases[-1]


# ──────────────────────────────────────────────────────────────
# 개인 변수 오버레이
# ──────────────────────────────────────────────────────────────
# VariableQuestion.effects에는 key/기간만 있고 표시 문구가 없습니다.
# effects 해석기를 일반화하는 건 v1 범위 밖이라(초안 D6 축약안) 여기 하드코딩합니다.
# 질문이 늘어나면 이 dict에 항목을 추가하세요.
OVERLAY_TASKS = {
    "packing_removal": {
        "icon": "",
        "name": {"ko": "패킹 제거 확인", "ja": "パッキング除去の確認"},
        "why": {
            "ko": "코 안에 넣은 솜은 병원에서 빼는 것이 원칙이에요. 날짜가 지났는데 그대로면 병원에 문의하세요.",
            "ja": "鼻の中の綿は病院で抜くのが原則です。日が過ぎても入ったままなら病院にお問い合わせください。",
        },
        "source_text": {"ko": "패킹 제거는 담당 의료진의 안내에 따릅니다."},
        "source_ref": "온보딩 개인 변수 응답",
    },
}


def _overlay_tasks(surgery, day):
    """VariableAnswer가 true인 질문의 effects.on_true 중 add_task를 적용합니다."""
    items = []
    answers = surgery.variable_answers.select_related("question")
    for ans in answers:
        if ans.value is not True:
            continue
        for eff in (ans.question.effects or {}).get("on_true", []):
            if eff.get("type") != "add_task":
                continue
            key = eff["key"]
            if not (eff.get("day_from", 0) <= day <= eff.get("day_to", 0)):
                continue
            meta = OVERLAY_TASKS.get(key, {})
            items.append({
                "key": key,
                "icon": meta.get("icon", ""),
                "name": meta.get("name", key),
                "why": meta.get("why", ""),
                "source_text": meta.get("source_text", ""),
                "source_ref": meta.get("source_ref", ""),
                "day_from": eff.get("day_from", 0),
                "day_to": eff.get("day_to", 0),
                "times_per_day": eff.get("times_per_day", 1),
                "interval_days": None,
                "weight": 1.0,
                "source": "overlay",
            })
    return items


# ──────────────────────────────────────────────────────────────
# 복약 루틴 (처방에서 합성) — 기능명세서 3.5 / 검토 R2
# ──────────────────────────────────────────────────────────────
def _medication_task(surgery, day, lang=DEFAULT_LANG):
    """
    복약은 마스터 템플릿에 없습니다. 처방이 환자마다 다르기 때문입니다.
    확인(confirmed_at)이 끝난 처방이 있을 때만 런타임에 만들어 붙입니다.

    is_prn(돈복) 약은 개수에서 제외합니다 — 안 먹은 날이 전부 미이행으로
    잡히면 완주율과 제출용 기록이 사실과 달라집니다(기능명세서 3.3).
    """
    rx = getattr(surgery, "prescription", None)
    if rx is None or rx.confirmed_at is None:
        return None

    day_to = (rx.total_days or 1) - 1
    if not (0 <= day <= day_to):
        return None

    regular = rx.items.filter(is_prn=False).count()
    if regular == 0:
        return None

    per_day = rx.per_day or 1
    timing = rx.timing or ""
    return {
        "key": "medication",
        "icon": "",
        "name": {"ko": f"처방약 {regular}종", "ja": f"処方薬 {regular}種"},
        "subtitle": {
            "ko": f"하루 {per_day}회 · 매 {timing} · D+0~D+{day_to}".replace("· 매  ·", "·"),
            "ja": f"1日{per_day}回 · D+0〜D+{day_to}",
        },
        "why": {
            "ko": "항생제는 증상이 나아져도 처방 기간을 끝까지 채워야 해요. 중간에 끊으면 감염이 다시 올라옵니다.",
            "ja": "抗生剤は症状が良くなっても処方期間を最後まで飲み切ってください。途中でやめると感染が再発します。",
        },
        "source_text": {"ko": "상세 성분·용량은 처방전(환자 보관용 사본)을 참고하십시오."},
        "source_ref": "수술 후 주의사항 · 11. 복약 안내",
        "day_from": 0,
        "day_to": day_to,
        "times_per_day": per_day,
        "interval_days": None,
        "weight": 1.0,
        "source": "prescription",
    }


# ──────────────────────────────────────────────────────────────
# 핵심 — 그날의 루틴 + 규칙
# ──────────────────────────────────────────────────────────────
def care_items(surgery, day, lang=DEFAULT_LANG):
    """
    그날 화면에 필요한 전부를 한 번에 만듭니다.

    반환:
    {
      "tasks": [ {key, icon, name, times_per_day, done_count, ...}, ... ],
      "rules": {
        "ok":   {"count": n, "items": [...]},
        "care": {"count": n, "items": [...]},
        "no":   {"count": n, "items": [...]},
      }
    }
    """
    ptype = surgery.procedure_type

    # ── 루틴 ────────────────────────────────────────────────
    raw = []
    for t in CareTaskTemplate.objects.filter(
        procedure_type=ptype, day_from__lte=day, day_to__gte=day
    ):
        raw.append({
            "key": t.key, "icon": t.icon, "name": t.name, "why": t.why,
            "source_text": t.source_text, "source_ref": t.source_ref,
            "day_from": t.day_from, "day_to": t.day_to,
            "times_per_day": t.times_per_day, "interval_days": t.interval_days,
            "weight": float(t.weight), "source": "template",
        })

    raw += _overlay_tasks(surgery, day)

    if med := _medication_task(surgery, day, lang):
        raw.append(med)

    # 그날 이미 체크한 회차를 한 번의 쿼리로 붙입니다
    done = dict(
        TaskLog.objects
        .filter(surgery=surgery, date=surgery.date_of(day))
        .values_list("task_key", "done_count")
    )

    tasks = []
    for t in raw:
        item = {
            "key": t["key"],
            "icon": t["icon"],
            "name": pick(t["name"], lang),
            "times_per_day": t["times_per_day"],
            "done_count": min(done.get(t["key"], 0), t["times_per_day"]),
            "day_from": t["day_from"],
            "day_to": t["day_to"],
            "source": t["source"],
        }
        if t.get("subtitle"):
            item["subtitle"] = pick(t["subtitle"], lang)
        tasks.append(item)

    # ── 규칙 ────────────────────────────────────────────────
    rules = {"ok": [], "care": [], "no": []}
    qs = ActivityRuleTemplate.objects.filter(procedure_type=ptype).prefetch_related("phases")
    for r in qs:
        st = state_of(r, day)
        if st is None:
            continue
        rules[st["status"]].append({
            "key": r.key,
            "icon": r.icon,
            "name": pick(r.name, lang),
            "status": st["status"],
            "text": _fill(pick(st["text"], lang), surgery),
        })

    return {
        "tasks": tasks,
        "rules": {k: {"count": len(v), "items": v} for k, v in rules.items()},
    }


# ──────────────────────────────────────────────────────────────
# 완주율
# ──────────────────────────────────────────────────────────────
def completion(surgery, day=None, include_today=False):
    """
    루틴 완주율 = 완료 회차 합 / 필요 회차 합 (D+0 ~ day)

    오늘은 기본적으로 세지 않습니다. 아직 진행 중인 하루를 미이행으로
    잡으면 완주율이 사실과 달라집니다(기능명세서 3.9).

    interval_days는 무시하고 times_per_day로만 계산합니다.
    절개부 소독만 이 필드를 쓰는데, 매일 체크해도 틀린 안내는 아니라서
    v1에서는 로직을 갈라놓지 않기로 했습니다(PR 리뷰 포인트 2).
    """
    if day is None:
        day = surgery.day_of(date.today())
    last = day if include_today else day - 1
    if last < 0:
        return 0.0

    templates = list(CareTaskTemplate.objects.filter(procedure_type=surgery.procedure_type))
    logs = {
        (r["task_key"], r["date"]): r["done_count"]
        for r in TaskLog.objects.filter(surgery=surgery).values("task_key", "date", "done_count")
    }

    required = 0
    achieved = 0
    for d in range(0, last + 1):
        the_date = surgery.date_of(d)
        for t in templates:
            if not (t.day_from <= d <= t.day_to):
                continue
            required += t.times_per_day
            achieved += min(logs.get((t.key, the_date), 0), t.times_per_day)

        if med := _medication_task(surgery, d):
            required += med["times_per_day"]
            achieved += min(logs.get(("medication", the_date), 0), med["times_per_day"])

    return round(achieved / required, 3) if required else 0.0


# ──────────────────────────────────────────────────────────────
# 타임라인
# ──────────────────────────────────────────────────────────────
def timeline(surgery, lang=DEFAULT_LANG):
    """
    앞으로의 변화. 규칙 구간이 바뀌는 지점 + 예약 + 귀국 + 완주를
    D+N 순으로 모읍니다. 저장하지 않고 매번 계산합니다.

    type: unlock | care_start | visit | remote | return | complete
    """
    events = []

    qs = ActivityRuleTemplate.objects.filter(
        procedure_type=surgery.procedure_type
    ).prefetch_related("phases")
    for r in qs:
        phases = _phases_with_from(r)
        for prev, cur in zip(phases, phases[1:]):
            before, after = STATUS_RANK[prev["status"]], STATUS_RANK[cur["status"]]
            if after > before:
                kind = "unlock"
            elif after < before:
                kind = "care_start"
            else:
                continue
            events.append({
                "day": cur["day_from"],
                "date": surgery.date_of(cur["day_from"]),
                "type": kind,
                "badge": timeline_badge(kind, lang),
                "label": f"{pick(r.name, lang)} — {_fill(pick(cur['text'], lang), surgery)}",
                "rule_key": r.key,
            })

    for ap in surgery.appointments.all():
        d = surgery.day_of(ap.scheduled_at.date())
        events.append({
            "day": d,
            "date": ap.scheduled_at.date(),
            "type": ap.kind,                       # visit | remote
            "badge": timeline_badge(ap.kind, lang),
            "label": pick(ap.title, lang),
            "appointment_id": ap.id,
        })

    if surgery.return_date:
        events.append({
            "day": surgery.return_day,
            "date": surgery.return_date,
            "type": "return", "badge": timeline_badge("return", lang),
            "label": pick({
                "ko": "귀국 예정",
                "ja": "帰国予定",
                "en": "Scheduled return",
            }, lang),
        })

    end = surgery.program_days
    events.append({
        "day": end,
        "date": surgery.date_of(end),
        "type": "complete", "badge": timeline_badge("complete", lang),
        "label": pick({
            "ko": "케어 프로그램 완주",
            "ja": "ケアプログラム完了",
            "en": "Care program complete",
        }, lang),
    })

    events.sort(key=lambda e: (e["day"], e["type"]))
    return events


def next_unlock(surgery, day, lang=DEFAULT_LANG):
    """홈 상단 '다음 해금일'. 아직 오지 않은 unlock 이벤트 중 가장 가까운 것."""
    for ev in timeline(surgery, lang):
        if ev["type"] == "unlock" and ev["day"] > day:
            return ev
    return None


# ──────────────────────────────────────────────────────────────
# 검증
# ──────────────────────────────────────────────────────────────
def validate_task_key(surgery, day, task_key):
    """
    PUT /task-logs 에서 씁니다. 그날 실제로 있는 루틴인지 확인합니다.
    이 검증이 없으면 오타난 키가 조용히 저장되고, 나중에 완주율이 틀어집니다.

    'medication'도 반드시 통과해야 합니다 — care_items가 처방에서 합성하는
    키라서 CareTaskTemplate에는 없습니다(검토 R2).
    """
    keys = {t["key"] for t in care_items(surgery, day)["tasks"]}
    return task_key in keys
