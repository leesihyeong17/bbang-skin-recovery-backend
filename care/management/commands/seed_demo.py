"""
care/management/commands/seed_demo.py

데모용 환자·시술·예약을 만듭니다.
Patient는 비밀번호 해시가 필요해서 fixture(loaddata)로는 못 넣고 여기서 만듭니다.

사용:
    poetry run python manage.py loaddata seed_protocol      # 먼저 마스터 데이터
    poetry run python manage.py seed_demo                   # 그 다음 데모 환자

다시 돌려도 안전합니다(get_or_create). 값을 갈아엎고 싶으면 --reset을 붙이세요.
"""
from datetime import date, datetime, time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Clinic, ClinicStaff
from care.models import Appointment, Surgery
from protocols.models import ProcedureType

Patient = get_user_model()

PATIENT_CODE = "NR-2608-0417"
DOB = date(1994, 5, 12)
SURGERY_DATE = date(2026, 8, 3)
RETURN_DATE = date(2026, 8, 17)          # D+14. 환자가 온보딩에서 직접 입력하는 유일한 값


class Command(BaseCommand):
    help = "데모 환자 · 시술 · 예약 생성"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="기존 데모 환자를 지우고 다시 만듭니다")

    @transaction.atomic
    def handle(self, *args, **options):
        clinic = Clinic.objects.get(code="SEOUL-N")
        surgeon = ClinicStaff.objects.get(clinic=clinic, name_romaji="KIM SEOJUN")
        ptype = ProcedureType.objects.get(code="rhinoplasty")

        if options["reset"]:
            Patient.objects.filter(patient_code=PATIENT_CODE).delete()
            self.stdout.write("기존 데모 환자 삭제")

        patient = Patient.objects.filter(patient_code=PATIENT_CODE).first()
        if patient is None:
            patient = Patient.objects.create_user(
                patient_code=PATIENT_CODE,
                dob=DOB,
                clinic=clinic,
                name={"ko": "사토 유이", "ja": "佐藤 唯"},
                name_romaji="SATO YUI",
                nationality="JP",
                lang="ja",
                lang_locked_at=timezone.now(),
            )
            self.stdout.write(self.style.SUCCESS(f"환자 생성: {PATIENT_CODE} / {DOB}"))
        else:
            self.stdout.write(f"환자 이미 있음: {PATIENT_CODE}")

        surgery, created = Surgery.objects.get_or_create(
            patient=patient,
            defaults=dict(
                clinic=clinic,
                surgeon=surgeon,
                procedure_type=ptype,
                surgery_date=SURGERY_DATE,
                detail={"ko": "실리콘 보형물 삽입 + 자가진피 비주 연장 · 1회차",
                        "ja": "シリコンインプラント挿入＋自家真皮による鼻柱延長・1回目"},
                session_no=1,
                implant={"material": "silicone", "graft": "autologous_dermis"},
                anesthesia="IV Sedation",
                program_days=ptype.program_days,
                return_date=RETURN_DATE,
                care_status=Surgery.CareStatus.ACTIVE,
                onboarded_at=timezone.now(),
            ),
        )
        self.stdout.write(("시술 생성" if created else "시술 이미 있음")
                          + f": D+0 = {SURGERY_DATE}, 귀국 D+{surgery.return_day}")

        # 안내문 12번 표 기준
        APPTS = [
            (5,  {"ko": "부목 제거", "ja": "ギプス除去"},   Appointment.Kind.VISIT,  time(14, 0)),
            (7,  {"ko": "비주 실밥 제거", "ja": "鼻柱の抜糸"}, Appointment.Kind.VISIT,  time(14, 0)),
            (30, {"ko": "경과 진찰", "ja": "経過診察"},     Appointment.Kind.REMOTE, time(11, 0)),
        ]
        for day, title, kind, at in APPTS:
            when = timezone.make_aware(
                datetime.combine(surgery.date_of(day), at)
            )
            Appointment.objects.get_or_create(
                surgery=surgery, scheduled_at=when,
                defaults=dict(title=title, kind=kind),
            )
        self.stdout.write(f"예약 {len(APPTS)}건 확인 완료")

        self.stdout.write(self.style.SUCCESS(
            f"\n완료 — 로그인 테스트: patient_code={PATIENT_CODE}, dob={DOB}"
        ))
