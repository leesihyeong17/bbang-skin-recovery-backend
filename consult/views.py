"""병원 탭 — 계약 9.7 · 9.8

Clinic · Appointment 조회와 상담. 모델은 accounts · care에 있고 여기는 뷰만 둔다.
"""
from django.shortcuts import render
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils import timezone

from care.models import Appointment
from care.services import care_items
from .models import ConsultMessage
from checkins.models import Checkin
from reports.models import RecoveryReport
from .translation import translate


# Create your views here.
from config.utils import api_error, t

DOC_EXPIRE = 600 #s3로 옮기면 사용

SCOPE_NOTICE = {
    "ko": "나란히는 상담 · 교육 · 기록 전달까지 제공하며, 진단과 처방은 내원이 필요합니다.",
    "ja": "ナランヒは相談・教育・記録の共有までを提供します。診断と処方には受診が必要です。",
}

DOC_LABELS = {
    "surgery_record": {"ko": "수술기록", "ja": "手術記録"},
    "instructions": {"ko": "수술 후 주의사항 안내문", "ja": "術後の注意事項"},
}

ROLE_SUFFIX = {
    "director": {"ko": "원장", "ja": "院長"},
    "intl_coordinator": {"ko": "국제진료 코디네이터", "ja": "国際診療コーディネーター"},
}


def _doc(key, filefield, lang):
    """파일이 실제로 있을 때만 항목을 만든다.

    FileField가 비어 있는데 .url을 부르면 ValueError가 난다.
    시딩에 PDF를 안 넣었으면 목록에서 빠지는 게 맞다.
    """
    if not filefield:
        return None
    return {
        "key": key,
        "label": t(DOC_LABELS[key], lang),
        "url": filefield.storage.url(filefield.name),
    }

def _appointment_status(appointment, now):
    """저장된 상태를 존중하되, 지난 '예정'만 완료로 본다(계약 843행).

    done · missed는 병원이 명시적으로 넣은 값이므로 덮어쓰지 않는다.
    자동 전환은 scheduled 하나뿐이다.
    """
    if appointment.status != Appointment.Status.SCHEDULED:
        return appointment.status
    return (
        Appointment.Status.DONE
        if appointment.scheduled_at <= now
        else Appointment.Status.SCHEDULED
    )

#GET Notification
NOTI_BODY_LIMIT = 80

REPLY_TITLE = {
    "ko": "{name}님이 답변했어요",
    "ja": "{name}さんが返信しました",
}

ROUTINE_TITLE = {
    "ko": "{name} · {left}회 남았어요",
    "ja": "{name} · あと{left}回",
}

ROUTINE_BODY = {
    "ko": "D+{day} · 하루 {per}회",
    "ja": "D+{day} · 1日{per}回",
}


def _message_body(message, lang):
    """환자 언어에 맞는 본문. 번역이 그 언어면 번역문, 아니면 원문."""
    if message.body_translated and message.lang_translated == lang:
        return message.body_translated
    return message.body_original

class ClinicView(APIView):
    def get(self, request):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        lang = request.user.lang
        clinic = surgery.clinic
        surgeon = surgery.surgeon

        # "김서준 원장" — 이름과 직함을 서버에서 붙인다. 언어마다 어순이 달라
        # 프론트가 조립하면 일본어에서 어색해진다.
        suffix = t(ROLE_SUFFIX.get(surgeon.role, {}), lang)
        surgeon_label = f"{t(surgeon.name, lang)} {suffix}".strip()

        documents = [
            _doc("surgery_record", surgery.surgery_record, lang),
            _doc("instructions", surgery.procedure_type.source_document, lang),
        ]

        return Response(
            {
                "name": t(clinic.name, lang),
                "surgeon": surgeon_label,
                "tel": clinic.tel,
                "reply_hours": clinic.reply_hours,
                "documents": [d for d in documents if d],
                "scope_notice": t(SCOPE_NOTICE, lang),
            }
        )

class AppointmentListView(APIView):
    def get(self, request):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        lang = request.user.lang
        now = timezone.now()

        items = []
        for a in surgery.appointments.all():        # Meta.ordering = ["scheduled_at"]
            # UTC로 저장되므로 .date()를 바로 쓰면 KST 09시 이전이 전날로 밀린다.
            local = timezone.localtime(a.scheduled_at)
            items.append(
                {
                    "id": a.id,
                    "day": surgery.day_of(local.date()),
                    "date": local.date(),
                    "scheduled_at": local,
                    "title": t(a.title, lang),
                    "kind": a.kind,
                    "status": _appointment_status(a, now),
                }
            )

        return Response({"items": items})

class NotificationListView(APIView):
    """미완료 루틴 + 미읽음 병원 답변
    """

    def get(self, request):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        lang = request.user.lang
        items = []

        # ── 병원 답변 ────────────────────────────────────────
        last_read = surgery.consult_last_read_at
        replies = (
            surgery.consult_messages.filter(
                sender_type=ConsultMessage.SenderType.STAFF
            )
            .select_related("sender_staff")
            .order_by("-created_at")[:20]
        )
        for msg in replies:
            # consult_last_read_at이 null이면 전부 미읽음(계약 9.8)
            unread = last_read is None or msg.created_at > last_read
            staff = msg.sender_staff
            name = t(staff.name, lang) if staff else t(surgery.clinic.name, lang)
            body = _message_body(msg, lang)
            items.append(
                {
                    "type": "clinic_reply",
                    "title": t(REPLY_TITLE, lang).format(name=name),
                    "body": body[:NOTI_BODY_LIMIT],
                    "created_at": msg.created_at,
                    "unread": unread,
                }
            )

        # ── 오늘 미완료 루틴 ─────────────────────────────────
        today = timezone.localdate()
        day = surgery.day_of(today)
        if 0 <= day <= surgery.program_days:
            for task in care_items(surgery, day, lang)["tasks"]:
                left = task["times_per_day"] - task["done_count"]
                if left <= 0:
                    continue
                items.append(
                    {
                        "type": "routine",
                        "title": t(ROUTINE_TITLE, lang).format(
                            name=task["name"], left=left
                        ),
                        "body": t(ROUTINE_BODY, lang).format(
                            day=day, per=task["times_per_day"]
                        ),
                        "unread": False,
                    }
                )

        return Response({"items": items})

CLINIC_LANG = "ko"          # 병원이 읽고 쓰는 언어

LANG_LABEL = {"ko": "한국어", "ja": "日本語", "en": "English"}

TAG = {
    "mine_original":   {"ko": "내 문장 · {lang} 원문", "ja": "自分の文 · {lang} 原文"},
    "mine_translated": {"ko": "내 문장 · 번역해서 전달", "ja": "自分の文 · 翻訳して送信"},
    "clinic_original": {"ko": "병원 답변 · 원문", "ja": "病院の返信 · 原文"},
    "clinic_translated": {
        "ko": "실시간 번역 · {src} → {dst}",
        "ja": "リアルタイム翻訳 · {src} → {dst}",
    },
}


def _tag(message, lang):
    """발신자 × 보는 언어 4조합. 프론트에 두면 틀리므로 서버가 만든다(계약 828행)."""
    mine = message.sender_type == ConsultMessage.SenderType.PATIENT
    same = message.lang_original == lang

    if mine and same:
        return t(TAG["mine_original"], lang).format(lang=LANG_LABEL.get(lang, lang))
    if mine:
        return t(TAG["mine_translated"], lang)
    if same:
        return t(TAG["clinic_original"], lang)
    return t(TAG["clinic_translated"], lang).format(
        src=LANG_LABEL.get(message.lang_original, message.lang_original),
        dst=LANG_LABEL.get(lang, lang),
    )


def _attachments(message, surgery):
    """첨부 2종을 목록으로. 제네릭 관계 대신 FK 2개를 쓴 결과다."""
    out = []
    if message.attached_report_id:
        out.append({
            "kind": "report",
            "id": message.attached_report_id,
            "label": message.attached_label,
        })
    if message.attached_checkin_id:
        checkin = message.attached_checkin
        out.append({
            "kind": "checkin",
            "id": checkin.id,
            "day": surgery.day_of(checkin.date),
            "date": checkin.date,
            "label": message.attached_label,
        })
    return out


def _attach_label(report, checkin, surgery, lang):
    """"회복 경과 기록 D+0~D+2" 같은 스냅샷 문구. 나중에 리포트가 바뀌어도 남는다."""
    if report:
        return t(
            {"ko": "회복 경과 기록 D+{a}~D+{b}", "ja": "回復経過の記録 D+{a}~D+{b}"}, lang
        ).format(a=report.day_from, b=report.day_to)
    if checkin:
        return t(
            {"ko": "오늘 사진 D+{d}", "ja": "本日の写真 D+{d}"}, lang
        ).format(d=surgery.day_of(checkin.date))
    return ""

def _message_payload(message, surgery, lang):
    """GET·POST가 같은 모양을 내려주도록 한 곳에서 만든다."""
    return {
        "id": message.id,
        "sender_type": message.sender_type,
        # created_at은 UTC 저장이라 localtime()을 거쳐야 D+N이 안 밀린다
        "day": surgery.day_of(timezone.localtime(message.created_at).date()),
        # 상담만 원문·번역문을 둘 다 내린다(규칙 6의 유일한 예외)
        "body_original": message.body_original,
        "lang_original": message.lang_original,
        "body_translated": message.body_translated,
        "lang_translated": message.lang_translated,
        "tag": _tag(message, lang),
        "attachments": _attachments(message, surgery),
        "created_at": message.created_at,
    }

class ConsultMessageView(APIView):
    def get(self, request):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        lang = request.user.lang
        messages = (
            surgery.consult_messages
            .select_related("attached_checkin")
            .all()                       # Meta.ordering = ["created_at"]
        )

        return Response({
            "messages": [
                _message_payload(msg, surgery, lang) for msg in messages
            ]
        })

    def post(self, request):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        body = (request.data.get("body") or "").strip()
        if not body:
            return api_error("VALIDATION_ERROR", "내용을 입력해 주세요")

        attach = request.data.get("attach") or {}
        if not isinstance(attach, dict):
            return api_error("VALIDATION_ERROR", "attach 형식이 올바르지 않습니다")

        # 남의 리포트·체크인 id를 붙일 수 없게 소유권을 확인한다
        report = checkin = None
        if rid := attach.get("report_id"):
            report = RecoveryReport.objects.filter(id=rid, surgery=surgery).first()
            if report is None:
                return api_error("NOT_FOUND", "첨부할 기록을 찾을 수 없습니다", 404)
        if cid := attach.get("checkin_id"):
            checkin = Checkin.objects.filter(id=cid, surgery=surgery).first()
            if checkin is None:
                return api_error("NOT_FOUND", "첨부할 체크인을 찾을 수 없습니다", 404)

        lang = request.user.lang
        translated, engine = translate(body, lang, CLINIC_LANG)

        message = ConsultMessage.objects.create(
            surgery=surgery,
            sender_type=ConsultMessage.SenderType.PATIENT,
            body_original=body,
            lang_original=lang,
            body_translated=translated,
            lang_translated=CLINIC_LANG if translated else "",
            translated_at=timezone.now() if translated else None,
            translation_engine=engine,
            attached_report=report,
            attached_checkin=checkin,
            attached_label=_attach_label(report, checkin, surgery, lang),
        )

        return Response(
            _message_payload(message, surgery, lang),
            status=status.HTTP_201_CREATED,
        )

class ConsultReadView(APIView):
    def post(self, request):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        surgery.consult_last_read_at = timezone.now()
        surgery.save(update_fields=["consult_last_read_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)