"""
apps/care/models.py — 도메인 C(시술) + D(예약) + E(처방)

[v2 변경점]
- prescriptions 앱을 care로 병합 (앱 7개 → 6개). Prescription은 Surgery와 1:1이라 같은 앱이 자연스럽다.
- Surgery.return_day 컬럼 삭제 → @property로 파생.
  파트너 초안의 D4 원칙("사실인 날짜를 저장하고 D+N은 파생")과 스스로 모순이었다.
  return_date만 저장하면 수술일이 정정돼도 자동으로 맞아떨어진다.

이 앱의 services.py가 프로젝트에서 가장 중요한 파일이다.
care_items() / state_of() / timeline() / completion() / stage_of()
엔드포인트에서 CareTaskTemplate.objects.filter()를 직접 쓰지 말 것.
"""
from datetime import date, timedelta

from django.conf import settings
from django.db import models
from django.core.files.storage import storages

from accounts.models import Clinic, ClinicStaff
from protocols.models import ProcedureType, VariableQuestion


def photo_storage():
    # 인스턴스가 아니라 콜러블을 넘겨야 마이그레이션에 스토리지 객체가 안 박힌다
    return storages["s3"]  # settings.py STORAGES["s3"]

class Surgery(models.Model):
    """환자 데이터 전체의 뿌리. 체크인·리포트·상담·처방이 전부 여기에 붙는다."""

    class CareStatus(models.TextChoices):
        ONBOARDING = "onboarding", "온보딩 중"
        ACTIVE = "active", "진행 중"
        COMPLETED = "completed", "완주"

    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="surgeries"
    )
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name="surgeries")
    surgeon = models.ForeignKey(
        ClinicStaff, on_delete=models.PROTECT, related_name="surgeries"
    )
    procedure_type = models.ForeignKey(
        ProcedureType, on_delete=models.PROTECT, related_name="surgeries"
    )

    # --- 병원만 쓸 수 있는 값 (환자 토큰으로 수정 불가) ---
    surgery_date = models.DateField()                             # D+0 기준점
    detail = models.JSONField(default=dict, blank=True)
    session_no = models.IntegerField(default=1)
    implant = models.JSONField(null=True, blank=True)             # 귀국 후 MRI·재수술에 필수
    anesthesia = models.CharField(max_length=30, blank=True)
    surgery_record = models.FileField(upload_to="surgery/", blank=True)  # 수술확인서 PDF
    registered_at = models.DateTimeField(auto_now_add=True)

    # --- 시스템이 채우는 값 ---
    program_days = models.IntegerField(default=120)               # ProcedureType에서 복사한 스냅샷
    care_status = models.CharField(
        max_length=12, choices=CareStatus.choices, default=CareStatus.ONBOARDING
    )

    # --- 환자가 쓸 수 있는 값은 이 둘뿐 ---
    return_date = models.DateField(null=True, blank=True)         # 항공권 날짜
    onboarded_at = models.DateTimeField(null=True, blank=True)

    consult_last_read_at = models.DateTimeField(null=True, blank=True)  # 환자가 마지막으로 읽은 상담 메시지

    class Meta:
        db_table = "surgery"
        constraints = [
            # 계정 1개 = 환자 × 시술 1건.
            # 재수술은 병원이 새 patient_code를 발급해 새 계정으로 받는다.
            # FK를 유지하는 건 나중에 통합 이력을 넣을 때 제약만 지우면 되게 하려는 것.
            models.UniqueConstraint(fields=["patient"], name="one_surgery_per_patient"),
        ]

    def __str__(self):
        return f"{self.patient.patient_code} / {self.procedure_type.code}"

    # --- D+N 변환 헬퍼. 이 두 개가 D4 결정의 실체다 ---
    def day_of(self, d: date) -> int:
        return (d - self.surgery_date).days

    def date_of(self, day: int) -> date:
        return self.surgery_date + timedelta(days=day)

    @property
    def return_day(self):
        return self.day_of(self.return_date) if self.return_date else None


class VariableAnswer(models.Model):
    """오버레이의 유일한 입력. 온보딩 완료 트랜잭션에서 저장 실패하면 반드시 롤백할 것."""

    surgery = models.ForeignKey(
        Surgery, on_delete=models.CASCADE, related_name="variable_answers"
    )
    question = models.ForeignKey(VariableQuestion, on_delete=models.PROTECT)
    value = models.JSONField()                                    # true / false
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "variable_answer"
        unique_together = [("surgery", "question")]


class Appointment(models.Model):
    class Kind(models.TextChoices):
        VISIT = "visit", "내원"
        REMOTE = "remote", "원격"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "예정"
        DONE = "done", "완료"
        MISSED = "missed", "미방문"

    surgery = models.ForeignKey(
        Surgery, on_delete=models.CASCADE, related_name="appointments"
    )
    scheduled_at = models.DateTimeField()                         # 사실. day_offset 컬럼 없음(D4)
    title = models.JSONField()
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.VISIT)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.SCHEDULED
    )

    class Meta:
        db_table = "appointment"
        ordering = ["scheduled_at"]


class Prescription(models.Model):
    """환자가 직접 촬영한 처방전. 병원 파이프라인으로 오지 않는 유일한 자료."""

    class OcrStatus(models.TextChoices):
        PENDING = "pending", "처리 중"
        DONE = "done", "완료"
        FAILED = "failed", "실패"

    surgery = models.OneToOneField(
        Surgery, on_delete=models.CASCADE, related_name="prescription"
    )
    source_image = models.ImageField(upload_to="prescriptions/", storage=photo_storage, blank=True)  # 환자가 업로드한 원본. OCR 실패 시 재업로드용
    ocr_status = models.CharField(
        max_length=8, choices=OcrStatus.choices, default=OcrStatus.PENDING
    )
    ocr_raw = models.JSONField(null=True, blank=True)             # 재파싱·디버깅용
    issued_date = models.DateField(null=True, blank=True)
    timing = models.CharField(max_length=20, blank=True)          # "식후"
    per_day = models.IntegerField(null=True, blank=True)          # 3
    total_days = models.IntegerField(null=True, blank=True)       # 6
    confirmed_at = models.DateTimeField(null=True, blank=True)    # 환자 더블체크 전엔 복약 체크 안 만듦

    class Meta:
        db_table = "prescription"

    # 만들지 않는 컬럼: 요양기관기호 · 질병분류기호 · 면허번호 · 교부번호
    # "환자분께 필요 없는 항목은 읽지 않습니다"를 스키마로 보장한다(D3와 같은 원리).


class PrescriptionItem(models.Model):
    prescription = models.ForeignKey(
        Prescription, on_delete=models.CASCADE, related_name="items"
    )
    seq = models.IntegerField()
    drug_name = models.CharField(max_length=120)
    category = models.CharField(max_length=40, blank=True)        # 항생제 / 위 보호제
    dose = models.CharField(max_length=20, blank=True)            # "1정" "1~2정"
    times_per_day = models.CharField(max_length=20, blank=True)   # "3회" / "필요 시 최대 4회"
    days = models.CharField(max_length=20, blank=True)            # "6일"
    usage = models.CharField(max_length=120, blank=True)
    is_prn = models.BooleanField(default=False)                   # 돈복이면 복약 체크를 만들지 않음

    class Meta:
        db_table = "prescription_item"
        ordering = ["seq"]