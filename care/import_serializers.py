"""Validation contract for the admin clinic-data import endpoint."""

from rest_framework import serializers

from accounts.models import ClinicStaff, Patient
from care.models import Appointment
from protocols.models import ActivityRulePhase


class LocalizedJSONField(serializers.JSONField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not isinstance(value, dict) or not any(value.values()):
            raise serializers.ValidationError("언어별 값을 가진 JSON 객체여야 합니다")
        return value


class StaffImportSerializer(serializers.Serializer):
    name = LocalizedJSONField()
    name_romaji = serializers.CharField(max_length=60)
    role = serializers.ChoiceField(choices=ClinicStaff.Role.choices)
    license_no = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")


class ClinicImportSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20)
    name = LocalizedJSONField()
    address = serializers.JSONField(required=False, default=dict)
    tel = serializers.CharField(max_length=30)
    reply_hours = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    staff = StaffImportSerializer(many=True, required=False, default=list)


class StageImportSerializer(serializers.Serializer):
    to = serializers.IntegerField(min_value=0)
    name = LocalizedJSONField()


class ProcedureImportSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=30)
    name = LocalizedJSONField()
    program_days = serializers.IntegerField(min_value=1, max_value=3650, default=120)
    stage_map = StageImportSerializer(many=True, required=False, default=list)
    protocol_version = serializers.CharField(max_length=10, default="v1.0")

    def validate_stage_map(self, stages):
        boundaries = [stage["to"] for stage in stages]
        if boundaries != sorted(boundaries) or len(boundaries) != len(set(boundaries)):
            raise serializers.ValidationError("stage_map.to는 중복 없이 오름차순이어야 합니다")
        return stages


class CareTaskImportSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=30)
    icon = serializers.CharField(max_length=8, required=False, allow_blank=True, default="")
    name = LocalizedJSONField()
    day_from = serializers.IntegerField(min_value=0)
    day_to = serializers.IntegerField(min_value=0)
    times_per_day = serializers.IntegerField(min_value=1, max_value=24, default=1)
    interval_days = serializers.IntegerField(min_value=1, allow_null=True, required=False, default=None)
    why = LocalizedJSONField()
    source_text = LocalizedJSONField()
    source_ref = serializers.CharField(max_length=80)
    weight = serializers.DecimalField(max_digits=3, decimal_places=2, default="1.00")
    order = serializers.IntegerField(min_value=0, default=0)

    def validate(self, attrs):
        if attrs["day_from"] > attrs["day_to"]:
            raise serializers.ValidationError("day_from은 day_to보다 클 수 없습니다")
        return attrs


class RulePhaseImportSerializer(serializers.Serializer):
    day_to = serializers.IntegerField(min_value=0)
    status = serializers.ChoiceField(choices=ActivityRulePhase.Status.choices)
    text = LocalizedJSONField()
    order = serializers.IntegerField(min_value=0, default=0)


class ActivityRuleImportSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=30)
    icon = serializers.CharField(max_length=8, required=False, allow_blank=True, default="")
    name = LocalizedJSONField()
    why = LocalizedJSONField()
    source_text = LocalizedJSONField()
    source_ref = serializers.CharField(max_length=80)
    order = serializers.IntegerField(min_value=0, default=0)
    phases = RulePhaseImportSerializer(many=True, allow_empty=False)

    def validate_phases(self, phases):
        boundaries = [phase["day_to"] for phase in phases]
        if boundaries != sorted(boundaries) or len(boundaries) != len(set(boundaries)):
            raise serializers.ValidationError("phases.day_to는 중복 없이 오름차순이어야 합니다")
        return phases


class VariableQuestionImportSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=30)
    question = LocalizedJSONField()
    hint = serializers.JSONField(required=False, default=dict)
    answer_type = serializers.ChoiceField(choices=["bool"], default="bool")
    effects = serializers.JSONField(required=False, default=dict)
    order = serializers.IntegerField(min_value=0, default=0)


class SymptomTermImportSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=20)
    name = LocalizedJSONField()
    medical_term = LocalizedJSONField()
    snomed_code = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    order = serializers.IntegerField(min_value=0, default=0)


class ProtocolImportSerializer(serializers.Serializer):
    procedure = ProcedureImportSerializer()
    care_tasks = CareTaskImportSerializer(many=True, required=False, default=list)
    activity_rules = ActivityRuleImportSerializer(many=True, required=False, default=list)
    variable_questions = VariableQuestionImportSerializer(many=True, required=False, default=list)
    symptom_terms = SymptomTermImportSerializer(many=True, required=False, default=list)

    def validate(self, attrs):
        program_days = attrs["procedure"]["program_days"]
        stages = attrs["procedure"]["stage_map"]
        if stages and stages[-1]["to"] > program_days:
            raise serializers.ValidationError("stage_map.to가 program_days를 초과합니다")
        for task in attrs["care_tasks"]:
            if task["day_to"] > program_days:
                raise serializers.ValidationError(
                    f"care_tasks.{task['key']}.day_to가 program_days를 초과합니다"
                )
        for rule in attrs["activity_rules"]:
            if rule["phases"][-1]["day_to"] > program_days:
                raise serializers.ValidationError(
                    f"activity_rules.{rule['key']}.phases가 program_days를 초과합니다"
                )
        for field in ("care_tasks", "activity_rules", "variable_questions"):
            keys = [item["key"] for item in attrs[field]]
            if len(keys) != len(set(keys)):
                raise serializers.ValidationError(f"{field}에 중복 key가 있습니다")
        symptom_keys = [item["key"] for item in attrs["symptom_terms"]]
        if len(symptom_keys) != len(set(symptom_keys)):
            raise serializers.ValidationError("symptom_terms에 중복 key가 있습니다")
        return attrs


class PrescriptionItemImportSerializer(serializers.Serializer):
    seq = serializers.IntegerField(min_value=1)
    drug_name = serializers.CharField(max_length=120)
    category = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    dose = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    times_per_day = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    days = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    usage = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    is_prn = serializers.BooleanField(required=False, default=False)


class PrescriptionDraftImportSerializer(serializers.Serializer):
    issued_date = serializers.DateField(required=False, allow_null=True)
    timing = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    per_day = serializers.IntegerField(min_value=1, max_value=24)
    total_days = serializers.IntegerField(min_value=1, max_value=365)
    items = PrescriptionItemImportSerializer(many=True, allow_empty=False)

    def validate_items(self, items):
        seqs = [item["seq"] for item in items]
        if len(seqs) != len(set(seqs)):
            raise serializers.ValidationError("items.seq는 중복될 수 없습니다")
        if len(items) > 50:
            raise serializers.ValidationError("약 항목은 최대 50개까지 입력할 수 있습니다")
        return items


class PatientImportSerializer(serializers.Serializer):
    patient_code = serializers.CharField(max_length=20)
    name = serializers.JSONField(required=False, default=dict)
    name_romaji = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    nationality = serializers.CharField(max_length=2, required=False, allow_blank=True, default="")
    dob = serializers.DateField()
    lang = serializers.ChoiceField(choices=Patient.Lang.choices, default=Patient.Lang.KO)


class SurgeryImportSerializer(serializers.Serializer):
    procedure_code = serializers.CharField(max_length=30)
    surgeon_name_romaji = serializers.CharField(max_length=60)
    surgery_date = serializers.DateField()
    detail = serializers.JSONField(required=False, default=dict)
    session_no = serializers.IntegerField(min_value=1, default=1)
    implant = serializers.JSONField(required=False, allow_null=True, default=None)
    anesthesia = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    return_date = serializers.DateField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        return_date = attrs.get("return_date")
        if return_date and return_date <= attrs["surgery_date"]:
            raise serializers.ValidationError("return_date는 surgery_date보다 이후여야 합니다")
        return attrs


class AppointmentImportSerializer(serializers.Serializer):
    scheduled_at = serializers.DateTimeField()
    title = LocalizedJSONField()
    kind = serializers.ChoiceField(choices=Appointment.Kind.choices, default=Appointment.Kind.VISIT)
    status = serializers.ChoiceField(
        choices=Appointment.Status.choices, default=Appointment.Status.SCHEDULED
    )


class PatientBundleImportSerializer(serializers.Serializer):
    patient = PatientImportSerializer()
    surgery = SurgeryImportSerializer()
    appointments = AppointmentImportSerializer(many=True, required=False, default=list)
    prescription_draft = PrescriptionDraftImportSerializer(required=False)


class ClinicDataImportSerializer(serializers.Serializer):
    schema_version = serializers.ChoiceField(choices=["1.0"], default="1.0")
    mode = serializers.ChoiceField(choices=["upsert"], default="upsert")
    dry_run = serializers.BooleanField(default=False)
    clinic_code = serializers.CharField(max_length=20, required=False)
    clinic = ClinicImportSerializer(required=False)
    protocols = ProtocolImportSerializer(many=True, required=False, default=list)
    patients = PatientBundleImportSerializer(many=True, required=False, default=list)

    def validate(self, attrs):
        clinic = attrs.get("clinic")
        clinic_code = attrs.get("clinic_code")
        if clinic and clinic_code and clinic["code"] != clinic_code:
            raise serializers.ValidationError("clinic.code와 clinic_code가 일치해야 합니다")
        if not clinic and not clinic_code and attrs["patients"]:
            raise serializers.ValidationError("환자 입력에는 clinic 또는 clinic_code가 필요합니다")
        if not clinic and not attrs["protocols"] and not attrs["patients"]:
            raise serializers.ValidationError("입력할 clinic, protocols 또는 patients가 필요합니다")

        procedure_codes = [item["procedure"]["code"] for item in attrs["protocols"]]
        if len(procedure_codes) != len(set(procedure_codes)):
            raise serializers.ValidationError("protocols에 중복 procedure.code가 있습니다")
        patient_codes = [item["patient"]["patient_code"] for item in attrs["patients"]]
        if len(patient_codes) != len(set(patient_codes)):
            raise serializers.ValidationError("patients에 중복 patient_code가 있습니다")
        return attrs


class PrescriptionConfirmSerializer(PrescriptionDraftImportSerializer):
    ocr_id = serializers.CharField(max_length=50)
