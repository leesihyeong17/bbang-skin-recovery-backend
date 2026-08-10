"""
apps/protocols/models.py — 도메인 B (프로토콜 마스터)

나란히 운영자만 건드리는 마스터 데이터. 모든 환자가 공유한다.
환자별 복사본을 만들지 않는다(D1) — care_items()가 요청마다 계산한다.

[v2 변경점]
- 없음. 파트너 초안 그대로.
- 다만 source_text / source_ref는 NOT NULL 유지 (blank=True 주지 말 것).
  원문 없는 항목이 생기면 "원문을 고치지 않는다"는 서비스 약속이 깨진다.
"""
from django.db import models

from accounts.models import Clinic


class ProcedureType(models.Model):
    code = models.CharField(max_length=30, unique=True)           # rhinoplasty
    name = models.JSONField()
    program_days = models.IntegerField(default=120)
    stage_map = models.JSONField(default=list)                    # [{"to":3,"name":{...}}, ...]
    protocol_version = models.CharField(max_length=10, default="v1.0")
    source_document = models.FileField(upload_to="protocols/", blank=True)
    source_clinic = models.ForeignKey(
        Clinic, on_delete=models.SET_NULL, null=True, blank=True, related_name="protocols"
    )

    class Meta:
        db_table = "procedure_type"

    def __str__(self):
        return self.code


class CareTaskTemplate(models.Model):
    """루틴 템플릿 7건. '하세요' 쪽. 환자가 체크하고 이행률에 들어간다."""

    procedure_type = models.ForeignKey(
        ProcedureType, on_delete=models.CASCADE, related_name="task_templates"
    )
    key = models.CharField(max_length=30)                         # antisep, ice, warm...
    icon = models.CharField(max_length=8, blank=True)
    name = models.JSONField()
    day_from = models.IntegerField()
    day_to = models.IntegerField()
    times_per_day = models.IntegerField(default=1)
    # [논의필요] 소독은 안내문상 "1~2일 간격"이라 하루 횟수가 아니다.
    # interval_days를 추가할지, times_per_day=1로 두고 why에 적을지 파트너와 결정.
    interval_days = models.IntegerField(
        null=True, blank=True, help_text="N일 간격 수행. 채우면 times_per_day보다 우선"
    )
    why = models.JSONField()                                      # 나란히가 덧붙인 설명
    source_text = models.JSONField()                              # 병원 원문 그대로 (NOT NULL)
    source_ref = models.CharField(max_length=80)                  # 원문 위치 (NOT NULL)
    weight = models.DecimalField(max_digits=3, decimal_places=2, default=1.00)
    order = models.IntegerField(default=0)

    class Meta:
        db_table = "care_task_template"
        unique_together = [("procedure_type", "key")]
        ordering = ["order"]

    def __str__(self):
        return f"{self.procedure_type.code}:{self.key}"


class ActivityRuleTemplate(models.Model):
    """규칙 템플릿 10건. '하지 마세요' 쪽. 체크 대상이 아니고 이행률에 안 들어간다."""

    procedure_type = models.ForeignKey(
        ProcedureType, on_delete=models.CASCADE, related_name="rule_templates"
    )
    key = models.CharField(max_length=30)                         # wash, flight, glasses...
    icon = models.CharField(max_length=8, blank=True)
    name = models.JSONField()
    why = models.JSONField()
    source_text = models.JSONField()
    source_ref = models.CharField(max_length=80)
    order = models.IntegerField(default=0)

    class Meta:
        db_table = "activity_rule_template"
        unique_together = [("procedure_type", "key")]
        ordering = ["order"]

    def __str__(self):
        return f"{self.procedure_type.code}:{self.key}"


class ActivityRulePhase(models.Model):
    """규칙 구간 24건. day_from이 없는 게 포인트 — 앞 구간 day_to+1이 시작이다."""

    class Status(models.TextChoices):
        NO = "no", "금지"
        CARE = "care", "주의"
        OK = "ok", "가능"

    rule = models.ForeignKey(
        ActivityRuleTemplate, on_delete=models.CASCADE, related_name="phases"
    )
    day_to = models.IntegerField()                                # 이 날까지 이 상태
    status = models.CharField(max_length=4, choices=Status.choices)
    text = models.JSONField()                                     # {RET} 플레이스홀더 사용 가능
    order = models.IntegerField(default=0)                        # day_to 오름차순

    class Meta:
        db_table = "activity_rule_phase"
        ordering = ["order"]

    def __str__(self):
        return f"{self.rule.key} ~D+{self.day_to} {self.status}"


class VariableQuestion(models.Model):
    """개인 변수 질문 2건. 안내문이 '~인 경우'로 적어둔 부분을 온보딩에서 직접 묻는다."""

    procedure_type = models.ForeignKey(
        ProcedureType, on_delete=models.CASCADE, related_name="variable_questions"
    )
    key = models.CharField(max_length=30)                         # pack, alar
    question = models.JSONField()
    hint = models.JSONField(default=dict, blank=True)
    answer_type = models.CharField(max_length=10, default="bool")
    effects = models.JSONField(default=dict, blank=True)          # D6. v1은 해석기 없이 if 하드코딩
    order = models.IntegerField(default=0)

    class Meta:
        db_table = "variable_question"
        unique_together = [("procedure_type", "key")]
        ordering = ["order"]

    def __str__(self):
        return self.key