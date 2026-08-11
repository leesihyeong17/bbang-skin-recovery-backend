"""
apps/consult/models.py — 도메인 H (상담)

[v2 변경점]
- ConsultThread 삭제 → Surgery와 1:1이고 status 하나뿐이라 ConsultMessage.surgery로 흡수.
- ConsultAttachment 삭제 → kind + object_id 제네릭 관계는 2일 일정에서 위험하다.
  데모에서 첨부되는 건 "회복 경과 기록(리포트)" 하나뿐이므로 FK 한 개로 대체.
- Notification 앱 전체 삭제 → 웹앱이라 푸시 알림을 보낼 수 없다.

[번역]
원문과 번역문을 양쪽 다 저장한다. 이유가 두 개다.
1. 화면 토글 — 환자가 번역을 못 믿을 때 한국어 원문을 볼 수 있어야 한다
2. 분쟁 — 번역 오류로 문제가 생기면 원문이 유일한 증거다
저장 시 한 번 번역하고 결과를 보관한다. 조회할 때마다 API를 부르지 말 것.
"""
from django.db import models

from accounts.models import ClinicStaff
from care.models import Surgery
from reports.models import RecoveryReport


class ConsultMessage(models.Model):
    class SenderType(models.TextChoices):
        PATIENT = "patient", "환자"
        STAFF = "staff", "병원"

    surgery = models.ForeignKey(
        Surgery, on_delete=models.CASCADE, related_name="consult_messages"
    )
    sender_type = models.CharField(max_length=8, choices=SenderType.choices)
    sender_staff = models.ForeignKey(
        ClinicStaff, on_delete=models.SET_NULL, null=True, blank=True
    )

    body_original = models.TextField()
    lang_original = models.CharField(max_length=2)
    body_translated = models.TextField(blank=True)
    lang_translated = models.CharField(max_length=2, blank=True)
    translated_at = models.DateTimeField(null=True, blank=True)
    translation_engine = models.CharField(max_length=30, blank=True)

    # 데모의 "병원에 전달하기" — 리포트를 대화창에 올리는 기능
    attached_report = models.ForeignKey(
        RecoveryReport, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="consult_messages",
    )

    attached_checkin = models.ForeignKey(
        "checkins.Checkin", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="consult_messages",
    )
    
    attached_label = models.CharField(max_length=120, blank=True)  # "회복 경과 기록 D+0~D+2" 스냅샷

    created_at = models.DateTimeField(auto_now_add=True)           # D+2 표시의 근거

    class Meta:
        db_table = "consult_message"
        ordering = ["created_at"]

    # 발신자 × 보는중 조합의 태그 문구("실시간 번역 · 한국어 → 日本語" 등)는
    # 반드시 서버에서 만든다. 4가지 조합이라 클라이언트에 두면 틀린다.