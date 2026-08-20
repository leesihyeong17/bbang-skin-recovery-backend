"""
apps/reports/models.py — 도메인 G (리포트)

[v2 변경점]
- ReportApproval 삭제. 병원 스태프 화면을 만들지 않으므로 '승인하는 사람'이 없다.
  프로토타입의 "✓ 병원 확인 이력" 블록은 이번 데모에서 고정 문구로 표시하고,
  발표에서 "다음 단계로 병원 어드민에서 확인 이력을 남기는 구조"라고 설명한다.

[생성 전략 — 2일 일정에서 중요]
프로토타입의 문장 생성 로직(피크일 탐지 · 연속 미이행 집계)이 이미 규칙 기반으로 완성돼 있다.
그걸 Python으로 먼저 이식해서 폴백으로 두고, AI는 문장을 다듬는 역할만 시킬 것.
그러면 데모 중 AI API가 죽어도 리포트가 빈 화면으로 남지 않는다.
"""
from django.db import models

from care.models import Surgery


class RecoveryReport(models.Model):
    class Kind(models.TextChoices):
        SUBMISSION = "submission", "제출용 기록"       # 시술한 한국 병원에게
        REPATRIATION = "repatriation", "귀국용 요약"   # 귀국 후 현지 의료진에게

    surgery = models.ForeignKey(Surgery, on_delete=models.CASCADE, related_name="reports")
    kind = models.CharField(max_length=14, choices=Kind.choices)
    lang = models.CharField(max_length=2)
    day_from = models.IntegerField()
    day_to = models.IntegerField()
    body = models.JSONField()          # {overview, checkin_days, symptom_flow, routines, weekly_summary, events, next_week}
    meta = models.JSONField(default=dict, blank=True)   # 체크인 건수 · 사진 컷수 · 완주율
    ai_model = models.CharField(max_length=40, blank=True)
    ai_prompt_version = models.CharField(max_length=10, blank=True)
    pdf_file = models.FileField(upload_to="reports/", blank=True)   # P2. 2일 안에는 생략 가능
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "recovery_report"
        ordering = ["-generated_at"]

    # body 안의 kind는 ok / part / miss / info 4종 고정.
    # warning · abnormal · risk · danger를 추가하면 그 순간 '판정'이 되고 의료행위가 된다.