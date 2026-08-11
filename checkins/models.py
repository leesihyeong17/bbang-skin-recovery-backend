"""
apps/checkins/models.py — 도메인 F (체크인 · 이행 기록)

리포트의 원재료. 도메인 G는 여기 있는 데이터를 문장으로 바꾸는 것뿐이다.

[v2 변경점]
- PhotoAccessLog 삭제. 병원 스태프 화면을 만들지 않으므로 '열람하는 주체'가 없다.
  발표에서는 설계 로드맵으로만 설명한다.

[절대 만들지 말 것 — D3]
severity / severity_label / risk_level / is_normal / is_abnormal / grade / warning_level
컬럼이 없으면 실수로 노출할 수도 없다. level(1~5)은 그래프 응답에만 담는다.
"""
from django.db import models

from care.models import Surgery
from protocols.models import ProcedureType
from django.core.files.storage import storages


def photo_storage():
    # 인스턴스가 아니라 콜러블을 넘겨야 마이그레이션에 스토리지 객체가 안 박힌다
    return storages["s3"]  # settings.py STORAGES["s3"]

class Checkin(models.Model):
    """하루치 기록 묶음. 사진 3컷 → 증상 3항목 → 저장, 3스텝."""

    surgery = models.ForeignKey(Surgery, on_delete=models.CASCADE, related_name="checkins")
    date = models.DateField()                                     # 사실. day_offset 컬럼 없음(D4)
    note = models.TextField(blank=True)
    note_lang = models.CharField(max_length=2, blank=True)        # 병원 전달 시 번역 방향 결정
    completed_at = models.DateTimeField(null=True, blank=True)    # null = 진행 중

    class Meta:
        db_table = "checkin"
        unique_together = [("surgery", "date")]                   # 하루에 하나만
        indexes = [models.Index(fields=["surgery", "date"])]
        ordering = ["-date"]


class SymptomTerm(models.Model):
    """증상 어휘 마스터 3건. name(환자용)과 medical_term(의료진용)을 나눠 갖는 게 핵심."""

    key = models.CharField(max_length=20, unique=True)            # swelling / pain / bruise
    name = models.JSONField()                                     # 부기 / 腫れ
    medical_term = models.JSONField()                             # 부기 (edema) / 반상출혈 (ecchymosis)
    snomed_code = models.CharField(max_length=20, blank=True)     # IPS 국제표준 대응. MVP는 비워둠
    procedure_type = models.ForeignKey(
        ProcedureType, on_delete=models.CASCADE, null=True, blank=True,
        related_name="symptom_terms",
    )
    order = models.IntegerField(default=0)

    class Meta:
        db_table = "symptom_term"
        ordering = ["order"]

    def __str__(self):
        return self.key


class CheckinSymptom(models.Model):
    checkin = models.ForeignKey(Checkin, on_delete=models.CASCADE, related_name="symptoms")
    term = models.ForeignKey(SymptomTerm, on_delete=models.PROTECT)
    level = models.SmallIntegerField()                            # 1~5. 목록 응답에는 담지 않는다

    class Meta:
        db_table = "checkin_symptom"
        unique_together = [("checkin", "term")]


class CheckinPhoto(models.Model):
    class Angle(models.TextChoices):
        FRONT = "front", "정면"
        LEFT = "left", "좌측"
        RIGHT = "right", "우측"

    checkin = models.ForeignKey(Checkin, on_delete=models.CASCADE, related_name="photos")
    angle = models.CharField(max_length=6, choices=Angle.choices)
    image = models.ImageField(upload_to="checkins/", storage=photo_storage)
    taken_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "checkin_photo"
        unique_together = [("checkin", "angle")]                  # 각도당 1장, 재촬영은 덮어쓰기


class TaskLog(models.Model):
    """
    루틴 이행 기록. 완주율의 유일한 소스.

    task_key가 FK가 아니라 문자열인 게 의도된 설계다.
    이건 이벤트 로그라서 프로토콜이 나중에 바뀌어 루틴이 사라져도 기록은 남아야 한다.
    대신 PUT /task-logs에서 task_key가 care_items(surgery).tasks에 있는지 검증할 것.
    없으면 400. 이 검증이 빠지면 오타난 키가 조용히 저장된다.
    """

    surgery = models.ForeignKey(Surgery, on_delete=models.CASCADE, related_name="task_logs")
    task_key = models.CharField(max_length=30)                    # antisep / medication
    date = models.DateField()                                     # 사실. day_offset 없음(D4)
    done_count = models.SmallIntegerField(default=0)              # 절대값으로 받는다 (멱등)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "task_log"
        unique_together = [("surgery", "task_key", "date")]
        indexes = [models.Index(fields=["surgery", "date"])]