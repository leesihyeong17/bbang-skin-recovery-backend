from copy import deepcopy
from datetime import date

from rest_framework.test import APITestCase

from accounts.models import Clinic, Patient
from checkins.models import SymptomTerm
from protocols.models import (
    ActivityRuleTemplate,
    CareTaskTemplate,
    ProcedureType,
    VariableQuestion,
)

from .models import Prescription, Surgery, VariableAnswer


def import_payload():
    return {
        "schema_version": "1.0",
        "mode": "upsert",
        "dry_run": False,
        "clinic": {
            "code": "IMPORT-HOSP",
            "name": {"ko": "임포트 병원", "ja": "インポート病院"},
            "address": {"ko": "서울"},
            "tel": "+82-2-111-2222",
            "reply_hours": "KST 10:00-19:00",
            "staff": [{
                "name": {"ko": "김테스트"},
                "name_romaji": "KIM TEST",
                "role": "director",
                "license_no": "TEST-1",
            }],
        },
        "protocols": [{
            "procedure": {
                "code": "import-rhinoplasty",
                "name": {"ko": "테스트 코성형"},
                "program_days": 30,
                "protocol_version": "v1.0",
                "stage_map": [{"to": 30, "name": {"ko": "회복"}}],
            },
            "care_tasks": [{
                "key": "ice", "icon": "🧊", "name": {"ko": "냉찜질"},
                "day_from": 0, "day_to": 3, "times_per_day": 4,
                "interval_days": None, "why": {"ko": "부기 관리"},
                "source_text": {"ko": "3일까지 냉찜질"},
                "source_ref": "2. 부기 관리", "weight": "1.00", "order": 1,
            }],
            "activity_rules": [{
                "key": "wash", "icon": "🧼", "name": {"ko": "세안"},
                "why": {"ko": "부목 보호"},
                "source_text": {"ko": "7일까지 세안 금지"},
                "source_ref": "4. 세안", "order": 1,
                "phases": [
                    {"day_to": 7, "status": "no", "text": {"ko": "금지"}, "order": 1},
                    {"day_to": 30, "status": "ok", "text": {"ko": "가능"}, "order": 2},
                ],
            }],
            "variable_questions": [],
            "symptom_terms": [{
                "key": "import-swelling", "name": {"ko": "부기"},
                "medical_term": {"ko": "부기 (edema)"},
                "snomed_code": "", "order": 1,
            }],
        }],
        "patients": [{
            "patient": {
                "patient_code": "IMPORT-PATIENT-1", "name": {"ko": "임포트 환자"},
                "name_romaji": "IMPORT PATIENT", "nationality": "JP",
                "dob": "1994-05-12", "lang": "ja",
            },
            "surgery": {
                "procedure_code": "import-rhinoplasty",
                "surgeon_name_romaji": "KIM TEST", "surgery_date": "2026-08-03",
                "detail": {"ko": "테스트 시술"}, "session_no": 1,
                "implant": None, "anesthesia": "IV Sedation",
                "return_date": "2026-08-17",
            },
            "appointments": [{
                "scheduled_at": "2026-08-08T14:00:00+09:00",
                "title": {"ko": "부목 제거"}, "kind": "visit", "status": "scheduled",
            }],
            "prescription_draft": {
                "issued_date": "2026-08-03", "timing": "식후",
                "per_day": 3, "total_days": 6,
                "items": [
                    {"seq": 1, "drug_name": "테스트 항생제", "category": "항생제",
                     "dose": "1정", "times_per_day": "3회", "days": "6일",
                     "usage": "매 식후 복용", "is_prn": False},
                    {"seq": 2, "drug_name": "테스트 진통제", "category": "진통제",
                     "dose": "1정", "times_per_day": "필요 시", "days": "3일",
                     "usage": "통증 시 복용", "is_prn": True},
                ],
            },
        }],
    }


class ClinicDataImportTests(APITestCase):
    url = "/api/v1/admin/clinic-data/import"

    def setUp(self):
        self.admin = Patient.objects.create_superuser(
            patient_code="IMPORT-ADMIN", dob=date(1980, 1, 1)
        )

    def test_patient_cannot_call_admin_import(self):
        patient = Patient.objects.create_user(
            patient_code="NORMAL-PATIENT", dob=date(1990, 1, 1)
        )
        self.client.force_authenticate(patient)
        response = self.client.post(self.url, import_payload(), format="json")
        self.assertEqual(response.status_code, 403)

    def test_import_creates_nested_protocol_patient_and_pending_rx(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(self.url, import_payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Clinic.objects.filter(code="IMPORT-HOSP").count(), 1)
        procedure = ProcedureType.objects.get(code="import-rhinoplasty")
        self.assertEqual(CareTaskTemplate.objects.filter(procedure_type=procedure).count(), 1)
        rule = ActivityRuleTemplate.objects.get(procedure_type=procedure, key="wash")
        self.assertEqual(rule.phases.count(), 2)
        patient = Patient.objects.get(patient_code="IMPORT-PATIENT-1")
        prescription = Prescription.objects.get(surgery__patient=patient)
        self.assertIsNone(prescription.confirmed_at)
        self.assertEqual(prescription.items.count(), 0)
        self.assertEqual(prescription.ocr_raw["provider"], "admin_import")

    def test_same_payload_is_idempotent(self):
        self.client.force_authenticate(self.admin)
        payload = import_payload()
        self.client.post(self.url, payload, format="json")
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Clinic.objects.filter(code="IMPORT-HOSP").count(), 1)
        self.assertEqual(ProcedureType.objects.filter(code="import-rhinoplasty").count(), 1)
        self.assertEqual(Patient.objects.filter(patient_code="IMPORT-PATIENT-1").count(), 1)
        self.assertEqual(Surgery.objects.filter(patient__patient_code="IMPORT-PATIENT-1").count(), 1)

    def test_same_symptom_key_can_be_used_by_multiple_procedures(self):
        self.client.force_authenticate(self.admin)
        payload = import_payload()
        second = deepcopy(payload["protocols"][0])
        second["procedure"]["code"] = "import-blepharoplasty"
        second["procedure"]["name"] = {"ko": "테스트 눈성형"}
        payload["protocols"].append(second)

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(SymptomTerm.objects.filter(key="import-swelling").count(), 2)

    def test_dry_run_rolls_everything_back(self):
        self.client.force_authenticate(self.admin)
        payload = import_payload()
        payload["dry_run"] = True
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["dry_run"])
        self.assertFalse(Clinic.objects.filter(code="IMPORT-HOSP").exists())
        self.assertFalse(Patient.objects.filter(patient_code="IMPORT-PATIENT-1").exists())

    def test_imported_question_effect_adds_localized_overlay_task(self):
        self.client.force_authenticate(self.admin)
        payload = import_payload()
        payload["protocols"][0]["variable_questions"] = [{
            "key": "contact_lens_user",
            "question": {"ko": "평소 콘택트렌즈를 착용하시나요?"},
            "hint": {"ko": "수술 후에는 안경을 사용해주세요."},
            "answer_type": "bool",
            "effects": {
                "on_true": [{
                    "type": "add_task",
                    "key": "use_glasses",
                    "day_from": 0,
                    "day_to": 14,
                    "times_per_day": 1,
                    "icon": "👓",
                    "name": {"ko": "안경 사용 확인"},
                    "why": {"ko": "각막 자극을 줄이기 위한 루틴입니다."},
                    "source_text": {"ko": "수술 후 14일까지 콘택트렌즈 착용 금지"},
                    "source_ref": "수술 후 주의사항 · 콘택트렌즈",
                }],
                "on_false": [],
            },
            "order": 1,
        }]

        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)

        patient = Patient.objects.get(patient_code="IMPORT-PATIENT-1")
        surgery = Surgery.objects.get(patient=patient)
        question = VariableQuestion.objects.get(
            procedure_type=surgery.procedure_type,
            key="contact_lens_user",
        )
        VariableAnswer.objects.create(surgery=surgery, question=question, value=True)

        self.client.force_authenticate(patient)
        home = self.client.get("/api/v1/home?day=0")
        self.assertEqual(home.status_code, 200, home.data)
        task = next(item for item in home.data["tasks"] if item["key"] == "use_glasses")
        self.assertEqual(task["name"], "안경 사용 확인")
        self.assertEqual(task["source"], "overlay")

        detail = self.client.get("/api/v1/care-items/task/use_glasses?day=0")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["why"], "각막 자극을 줄이기 위한 루틴입니다.")
        self.assertEqual(detail.data["source_ref"], "수술 후 주의사항 · 콘택트렌즈")

    def test_add_task_effect_requires_key_and_day_range(self):
        self.client.force_authenticate(self.admin)
        payload = import_payload()
        payload["protocols"][0]["variable_questions"] = [{
            "key": "invalid_effect",
            "question": {"ko": "잘못된 효과인가요?"},
            "effects": {"on_true": [{"type": "add_task"}]},
        }]

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("key", response.data["error"]["message"])

    def test_confirmed_prescription_cannot_be_overwritten_and_import_rolls_back(self):
        self.client.force_authenticate(self.admin)
        payload = import_payload()
        self.client.post(self.url, payload, format="json")
        prescription = Prescription.objects.get(surgery__patient__patient_code="IMPORT-PATIENT-1")
        from django.utils import timezone
        prescription.confirmed_at = timezone.now()
        prescription.save(update_fields=["confirmed_at"])

        changed = deepcopy(payload)
        changed["clinic"]["name"] = {"ko": "롤백되어야 하는 이름"}
        response = self.client.post(self.url, changed, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Clinic.objects.get(code="IMPORT-HOSP").name["ko"], "임포트 병원")


class PrescriptionAdminDraftFlowTests(APITestCase):
    import_url = "/api/v1/admin/clinic-data/import"
    ocr_url = "/api/v1/prescriptions/ocr"
    confirm_url = "/api/v1/prescriptions/confirm"

    def setUp(self):
        admin = Patient.objects.create_superuser(
            patient_code="OCR-ADMIN", dob=date(1980, 1, 1)
        )
        self.client.force_authenticate(admin)
        response = self.client.post(self.import_url, import_payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.patient = Patient.objects.get(patient_code="IMPORT-PATIENT-1")
        self.client.force_authenticate(self.patient)

    def test_patient_reads_and_confirms_admin_draft(self):
        draft_response = self.client.get(self.ocr_url)
        self.assertEqual(draft_response.status_code, 200)
        self.assertEqual(draft_response.data["regular_count"], 1)
        self.assertEqual(draft_response.data["prn_count"], 1)

        draft = draft_response.data
        response = self.client.post(
            self.confirm_url, {"ocr_id": draft["ocr_id"]}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        prescription = Prescription.objects.get(surgery__patient=self.patient)
        self.assertIsNotNone(prescription.confirmed_at)
        self.assertEqual(prescription.per_day, 3)
        self.assertEqual(prescription.items.count(), 2)
        self.assertEqual(prescription.items.get(seq=1).drug_name, "테스트 항생제")
        status_response = self.client.get("/api/v1/onboarding/status")
        self.assertTrue(status_response.data["can_proceed"])

    def test_stale_ocr_id_is_rejected_after_admin_reimport(self):
        old_id = self.client.get(self.ocr_url).data["ocr_id"]
        admin = Patient.objects.get(patient_code="OCR-ADMIN")
        self.client.force_authenticate(admin)
        self.client.post(self.import_url, import_payload(), format="json")
        self.client.force_authenticate(self.patient)
        response = self.client.post(
            self.confirm_url, {"ocr_id": old_id}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "OCR_DRAFT_EXPIRED")
