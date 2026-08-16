"""Atomic, idempotent importer shared by the admin JSON endpoint."""

from collections import defaultdict
from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from rest_framework import serializers

from accounts.models import Clinic, ClinicStaff, Patient
from checkins.models import SymptomTerm
from protocols.models import (
    ActivityRulePhase,
    ActivityRuleTemplate,
    CareTaskTemplate,
    ProcedureType,
    VariableQuestion,
)

from .models import Appointment, Prescription, Surgery


class ImportSummary:
    def __init__(self):
        self.counts = defaultdict(lambda: {"created": 0, "updated": 0})

    def record(self, resource, created):
        key = "created" if created else "updated"
        self.counts[resource][key] += 1

    def add_created(self, resource, count):
        self.counts[resource]["created"] += count

    def as_dict(self):
        return dict(self.counts)


def _json_ready(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _upsert_clinic(data, summary):
    staff_rows = data.pop("staff", [])
    clinic, created = Clinic.objects.update_or_create(
        code=data.pop("code"), defaults=data
    )
    summary.record("clinics", created)

    for row in staff_rows:
        defaults = dict(row)
        name_romaji = defaults.pop("name_romaji")
        _, staff_created = ClinicStaff.objects.update_or_create(
            clinic=clinic,
            name_romaji=name_romaji,
            defaults=defaults,
        )
        summary.record("staff", staff_created)
    return clinic


def _upsert_protocol(bundle, clinic, summary):
    procedure_data = dict(bundle["procedure"])
    code = procedure_data.pop("code")
    if clinic is not None:
        procedure_data["source_clinic"] = clinic
    procedure, created = ProcedureType.objects.update_or_create(
        code=code, defaults=procedure_data
    )
    summary.record("procedures", created)

    for row in bundle["care_tasks"]:
        values = dict(row)
        key = values.pop("key")
        _, child_created = CareTaskTemplate.objects.update_or_create(
            procedure_type=procedure, key=key, defaults=values
        )
        summary.record("care_tasks", child_created)

    for row in bundle["activity_rules"]:
        values = dict(row)
        phases = values.pop("phases")
        key = values.pop("key")
        rule, child_created = ActivityRuleTemplate.objects.update_or_create(
            procedure_type=procedure, key=key, defaults=values
        )
        summary.record("activity_rules", child_created)

        # A phase has no stable natural key. An included rule owns a complete
        # phase list, so replacing only this list is deterministic and safe.
        rule.phases.all().delete()
        ActivityRulePhase.objects.bulk_create([
            ActivityRulePhase(rule=rule, **phase) for phase in phases
        ])
        summary.add_created("activity_rule_phases", len(phases))

    for row in bundle["variable_questions"]:
        values = dict(row)
        key = values.pop("key")
        _, child_created = VariableQuestion.objects.update_or_create(
            procedure_type=procedure, key=key, defaults=values
        )
        summary.record("variable_questions", child_created)

    for row in bundle["symptom_terms"]:
        values = dict(row)
        key = values.pop("key")
        _, child_created = SymptomTerm.objects.update_or_create(
            procedure_type=procedure, key=key, defaults=values
        )
        summary.record("symptom_terms", child_created)


def _upsert_patient(bundle, clinic, summary):
    patient_data = dict(bundle["patient"])
    patient_code = patient_data.pop("patient_code")
    dob = patient_data.pop("dob")
    patient = Patient.objects.filter(patient_code=patient_code).first()
    if patient is None:
        patient = Patient.objects.create_user(
            patient_code=patient_code,
            dob=dob,
            clinic=clinic,
            **patient_data,
        )
        patient_created = True
    else:
        patient_created = False
        dob_changed = patient.dob != dob
        patient.clinic = clinic
        patient.dob = dob
        for field, value in patient_data.items():
            setattr(patient, field, value)
        if dob_changed:
            patient.set_password(dob.strftime("%Y%m%d"))
        patient.save()
    summary.record("patients", patient_created)

    surgery_data = dict(bundle["surgery"])
    procedure_code = surgery_data.pop("procedure_code")
    surgeon_name = surgery_data.pop("surgeon_name_romaji")
    try:
        procedure = ProcedureType.objects.get(code=procedure_code)
    except ProcedureType.DoesNotExist as exc:
        raise serializers.ValidationError(
            f"환자 {patient_code}: 시술 코드 '{procedure_code}'가 없습니다"
        ) from exc
    try:
        surgeon = ClinicStaff.objects.get(
            clinic=clinic, name_romaji=surgeon_name
        )
    except ClinicStaff.DoesNotExist as exc:
        raise serializers.ValidationError(
            f"환자 {patient_code}: 의료진 '{surgeon_name}'이 없습니다"
        ) from exc

    surgery_defaults = {
        **surgery_data,
        "clinic": clinic,
        "surgeon": surgeon,
        "procedure_type": procedure,
        "program_days": procedure.program_days,
    }
    return_date = surgery_defaults.get("return_date")
    if return_date and return_date > surgery_defaults["surgery_date"] + timedelta(
        days=procedure.program_days
    ):
        raise serializers.ValidationError(
            f"환자 {patient_code}: return_date가 프로그램 기간을 초과합니다"
        )
    surgery, surgery_created = Surgery.objects.update_or_create(
        patient=patient, defaults=surgery_defaults
    )
    summary.record("surgeries", surgery_created)

    for row in bundle["appointments"]:
        values = dict(row)
        scheduled_at = values.pop("scheduled_at")
        kind = values.pop("kind")
        _, appointment_created = Appointment.objects.update_or_create(
            surgery=surgery,
            scheduled_at=scheduled_at,
            kind=kind,
            defaults=values,
        )
        summary.record("appointments", appointment_created)

    if "prescription_draft" in bundle:
        existing = Prescription.objects.filter(surgery=surgery).first()
        if existing and existing.confirmed_at:
            raise serializers.ValidationError(
                f"환자 {patient_code}: 이미 확정된 처방전은 덮어쓸 수 없습니다"
            )

        draft = _json_ready(dict(bundle["prescription_draft"]))
        ocr_id = f"tmp-{uuid4()}"
        prescription, rx_created = Prescription.objects.update_or_create(
            surgery=surgery,
            defaults={
                "ocr_status": Prescription.OcrStatus.DONE,
                "ocr_raw": {
                    "ocr_id": ocr_id,
                    "draft": draft,
                    "provider": "admin_import",
                },
                "issued_date": None,
                "timing": "",
                "per_day": None,
                "total_days": None,
                "confirmed_at": None,
            },
        )
        # A pending draft must never expose stale confirmed/detail rows.
        prescription.items.all().delete()
        summary.record("prescription_drafts", rx_created)


def import_clinic_data(validated_data):
    """Import one clinic bundle and return a summary.

    Every mutation is rolled back for dry-run or for any validation failure.
    """
    payload = dict(validated_data)
    dry_run = payload.get("dry_run", False)
    summary = ImportSummary()

    with transaction.atomic():
        clinic_data = payload.get("clinic")
        if clinic_data:
            clinic = _upsert_clinic(dict(clinic_data), summary)
        else:
            clinic_code = payload.get("clinic_code")
            clinic = None
            if clinic_code:
                try:
                    clinic = Clinic.objects.get(code=clinic_code)
                except Clinic.DoesNotExist as exc:
                    raise serializers.ValidationError(
                        f"병원 코드 '{clinic_code}'가 없습니다"
                    ) from exc

        for protocol in payload.get("protocols", []):
            _upsert_protocol(protocol, clinic, summary)

        if payload.get("patients") and clinic is None:
            raise serializers.ValidationError("환자 입력에 사용할 병원을 찾을 수 없습니다")
        for patient in payload.get("patients", []):
            _upsert_patient(patient, clinic, summary)

        if dry_run:
            transaction.set_rollback(True)

    return {
        "valid": True,
        "dry_run": dry_run,
        "summary": summary.as_dict(),
    }
