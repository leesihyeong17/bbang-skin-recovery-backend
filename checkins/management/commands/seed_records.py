"""
checkins/management/commands/seed_records.py

seed_demo가 만든 환자 위에 **회복 기록**을 얹습니다. 시연용입니다.

    poetry run python manage.py loaddata seed_protocol
    poetry run python manage.py seed_demo
    poetry run python manage.py seed_records --reset

seed_demo(A 소유)를 건드리지 않으려고 별도 커맨드로 뒀습니다. 머지 충돌이 없습니다.

만드는 것:
  - 체크인 D+0 ~ 어제 (증상 3항목, 회복 곡선을 따라감)
  - TaskLog (완주율이 100%가 아니게, 후반부에 빠지는 패턴)
  - 처방 1건 + 약 5종 → 홈에 복약 루틴이 뜨고 완주율에 포함됨
  - 병원 문서 2종 (안내문 PDF를 수술확인서 자리에도 대용으로 사용)
  - 예약 시각 정정 (UTC로 저장돼 밤 11시로 보이던 것)

사진은 만들지 않습니다. 실제 얼굴 사진이 없어 플레이스홀더가 되고,
S3에 36개를 올려야 해서 느립니다. 시연에서 직접 촬영하세요.
"""
from datetime import datetime, time

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from care.models import Appointment, Prescription, PrescriptionItem
from care.services import completion
from checkins.models import Checkin, CheckinSymptom, SymptomTerm, TaskLog
from consult.models import ConsultMessage
from protocols.models import CareTaskTemplate
from reports.models import RecoveryReport

PATIENT_CODE = "NR-2608-0417"
INSTRUCTIONS_PDF = "docs/수술 후 주의사항 안내문.pdf"

# ──────────────────────────────────────────────────────────────
# 증상 곡선 — D+0부터 하루씩. 부기는 D+1에 피크 후 완만히 내려간다.
# 리포트의 symptom_flow가 이 모양에서 문장을 만든다.
# ──────────────────────────────────────────────────────────────
SYMPTOM_CURVE = {
    "swelling": [3, 5, 5, 4, 4, 3, 3, 3, 2, 2, 2, 2, 2, 2],
    "pain":     [4, 4, 3, 3, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1],
    "bruise":   [2, 4, 4, 3, 3, 2, 2, 1, 1, 1, 1, 1, 1, 1],
}

# 처방 — 계약 9.2의 OCR 예시와 같은 구성. 4종 정기 + 1종 돈복
RX_ITEMS = [
    (1, "세프카펨피복실염산염정 100mg", "항생제", "1정", "3회", "6일", "매 식후 30분 경구 복용", False),
    (2, "록소프로펜나트륨정 60mg", "소염진통제", "1정", "3회", "6일", "매 식후 30분 경구 복용", False),
    (3, "알마게이트정 500mg", "위 보호제", "1정", "3회", "6일", "매 식후 30분 경구 복용", False),
    (4, "브로멜라인장용정", "부기 완화", "1정", "3회", "6일", "매 식후 30분 경구 복용", False),
    (5, "아세트아미노펜정 500mg", "해열진통제", "1~2정", "필요 시 최대 4회", "4일",
     "통증 시 4시간 간격 경구 복용 (돈복)", True),
]

# 예약 시각 정정 — seed_demo가 넣은 값이 UTC로 해석돼 밤 11시로 보였다
APPOINTMENT_TIMES = {5: time(14, 0), 7: time(14, 0), 30: time(11, 0)}


def _done_count(key, day, times_per_day):
    """그날 그 루틴을 몇 회 했는지. 100%가 아니게, 사람이 실제로 빠뜨리는 패턴으로.

    귀국이 다가오며 준비로 바빠져 후반부 이행이 떨어지는 그림입니다.
    리포트에 ok · part · miss 세 종류가 모두 나오도록 의도적으로 섞었습니다.
    """
    if key == "tape":                       # D+8~ 시작한 루틴. 절반만
        return times_per_day if day in (8, 10) else 0
    if key == "warm":
        if day in (9, 10):                  # 이틀 통째로 빠짐
            return 0
        return times_per_day - 1 if day in (7, 8) else times_per_day
    if key == "walk":                       # 하루 3회가 부담스러운 루틴
        if day >= 9:
            return 1
        return 2 if day >= 1 else times_per_day
    if key == "saline":
        return 2 if day >= 6 else times_per_day
    if key == "sleep45":
        return 0 if day == 11 else times_per_day
    if key == "medication":
        return times_per_day - 1 if day in (4, 5) else times_per_day
    return times_per_day                    # antisep · ice는 전일 완료


class Command(BaseCommand):
    help = "데모 환자의 회복 기록(체크인 · 루틴 이행 · 처방 · 문서) 생성"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true",
            help="기존 체크인·이행기록·리포트·상담을 지우고 다시 만듭니다",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        from accounts.models import Patient

        patient = Patient.objects.filter(patient_code=PATIENT_CODE).first()
        if patient is None:
            raise CommandError("데모 환자가 없습니다. 먼저 seed_demo를 실행하세요.")

        surgery = patient.surgery
        if surgery is None:
            raise CommandError("데모 환자에게 시술이 없습니다.")

        today = timezone.localdate()
        last_day = surgery.day_of(today) - 1        # 어제까지 기록한다
        if last_day < 0:
            raise CommandError("수술일이 오늘 이후입니다. 기록을 만들 구간이 없습니다.")

        if options["reset"]:
            self._reset(surgery)

        self._fix_appointments(surgery)
        self._prescription(surgery)
        self._documents(surgery)
        self._checkins(surgery, last_day)
        self._task_logs(surgery, last_day)

        rate = completion(surgery, surgery.day_of(today))
        self.stdout.write(self.style.SUCCESS(
            f"\n완료: D+0 ~ D+{last_day} 기록 생성, 완주율 {rate:.0%}"
        ))

    # ──────────────────────────────────────────────────────
    def _reset(self, surgery):
        """구간 밖 쓰레기 데이터까지 함께 정리한다."""
        counts = {
            "체크인": surgery.checkins.all().delete()[0],
            "이행기록": TaskLog.objects.filter(surgery=surgery).delete()[0],
            "리포트": RecoveryReport.objects.filter(surgery=surgery).delete()[0],
            "상담": ConsultMessage.objects.filter(surgery=surgery).delete()[0],
        }
        surgery.consult_last_read_at = None
        surgery.save(update_fields=["consult_last_read_at"])
        self.stdout.write("초기화: " + " · ".join(f"{k} {v}" for k, v in counts.items()))

    def _fix_appointments(self, surgery):
        """UTC로 저장돼 밤 11시로 보이던 예약 시각을 KST 기준으로 옮긴다."""
        fixed = 0
        for appointment in surgery.appointments.all():
            local = timezone.localtime(appointment.scheduled_at)
            day = surgery.day_of(local.date())
            want_time = APPOINTMENT_TIMES.get(day)
            if want_time is None or local.time() == want_time:
                continue
            appointment.scheduled_at = timezone.make_aware(
                datetime.combine(local.date(), want_time)
            )
            appointment.save(update_fields=["scheduled_at"])
            fixed += 1
        self.stdout.write(f"예약 시각 정정 {fixed}건")

    def _prescription(self, surgery):
        """확정된 처방이 있어야 care_items()가 복약 루틴을 합성한다."""
        rx, _ = Prescription.objects.update_or_create(
            surgery=surgery,
            defaults=dict(
                ocr_status=Prescription.OcrStatus.DONE,
                issued_date=surgery.surgery_date,
                timing="식후",
                per_day=3,
                total_days=6,
                confirmed_at=timezone.now(),
            ),
        )
        rx.items.all().delete()
        PrescriptionItem.objects.bulk_create([
            PrescriptionItem(
                prescription=rx, seq=seq, drug_name=name, category=category,
                dose=dose, times_per_day=tpd, days=days, usage=usage, is_prn=prn,
            )
            for seq, name, category, dose, tpd, days, usage, prn in RX_ITEMS
        ])
        self.stdout.write(f"처방 {len(RX_ITEMS)}종 (정기 4 · 돈복 1) · D+0~D+5")

    def _documents(self, surgery):
        """안내문 PDF를 병원 문서 자리에 넣는다. 수술확인서는 대용이다."""
        from pathlib import Path

        from django.conf import settings

        source = Path(settings.BASE_DIR) / INSTRUCTIONS_PDF
        if not source.exists():
            self.stdout.write(self.style.WARNING(f"문서 건너뜀: {INSTRUCTIONS_PDF} 없음"))
            return

        ptype = surgery.procedure_type
        if not ptype.source_document:
            with source.open("rb") as f:
                ptype.source_document.save("instructions.pdf", File(f), save=True)
        if not surgery.surgery_record:
            with source.open("rb") as f:
                surgery.surgery_record.save("surgery_record.pdf", File(f), save=True)
        self.stdout.write("병원 문서 2종 등록")

    def _checkins(self, surgery, last_day):
        terms = {t.key: t for t in SymptomTerm.objects.filter(
            procedure_type=surgery.procedure_type
        )}

        for day in range(0, last_day + 1):
            checkin, _ = Checkin.objects.update_or_create(
                surgery=surgery,
                date=surgery.date_of(day),
                defaults=dict(
                    completed_at=timezone.make_aware(
                        datetime.combine(surgery.date_of(day), time(21, 0))
                    ),
                ),
            )
            for key, curve in SYMPTOM_CURVE.items():
                term = terms.get(key)
                if term is None or day >= len(curve):
                    continue
                CheckinSymptom.objects.update_or_create(
                    checkin=checkin, term=term, defaults={"level": curve[day]},
                )
        self.stdout.write(f"체크인 {last_day + 1}건 · 증상 3항목씩")

    def _task_logs(self, surgery, last_day):
        templates = list(CareTaskTemplate.objects.filter(
            procedure_type=surgery.procedure_type
        ))
        rx = getattr(surgery, "prescription", None)
        med_last = (rx.total_days - 1) if rx and rx.total_days else -1

        rows = []
        for day in range(0, last_day + 1):
            the_date = surgery.date_of(day)

            for tpl in templates:
                if not (tpl.day_from <= day <= tpl.day_to):
                    continue
                rows.append(TaskLog(
                    surgery=surgery, task_key=tpl.key, date=the_date,
                    done_count=_done_count(tpl.key, day, tpl.times_per_day),
                ))

            if rx and rx.confirmed_at and 0 <= day <= med_last:
                rows.append(TaskLog(
                    surgery=surgery, task_key="medication", date=the_date,
                    done_count=_done_count("medication", day, rx.per_day or 1),
                ))

        TaskLog.objects.filter(surgery=surgery).delete()
        TaskLog.objects.bulk_create(rows)
        self.stdout.write(f"루틴 이행 기록 {len(rows)}건")
