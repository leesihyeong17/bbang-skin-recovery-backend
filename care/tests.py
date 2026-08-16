from copy import deepcopy
from datetime import date

from rest_framework.test import APITestCase

from accounts.models import Clinic, Patient
from checkins.models import SymptomTerm
from protocols.models import ActivityRuleTemplate, CareTaskTemplate, ProcedureType

from .models import Prescription, Surgery


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

    def test_patient_reads_admin_draft_and_confirms_edited_values(self):
        draft_response = self.client.get(self.ocr_url)
        self.assertEqual(draft_response.status_code, 200)
        self.assertEqual(draft_response.data["regular_count"], 1)
        self.assertEqual(draft_response.data["prn_count"], 1)

        draft = draft_response.data
        items = deepcopy(draft["items"])
        items[0]["drug_name"] = "환자가 확인한 항생제"
        response = self.client.post(self.confirm_url, {
            "ocr_id": draft["ocr_id"], "issued_date": draft["issued_date"],
            "timing": "식후 30분", "per_day": 2,
            "total_days": draft["total_days"], "items": items,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        prescription = Prescription.objects.get(surgery__patient=self.patient)
        self.assertIsNotNone(prescription.confirmed_at)
        self.assertEqual(prescription.per_day, 2)
        self.assertEqual(prescription.items.count(), 2)
        self.assertEqual(prescription.items.get(seq=1).drug_name, "환자가 확인한 항생제")
        status_response = self.client.get("/api/v1/onboarding/status")
        self.assertTrue(status_response.data["can_proceed"])

    def test_stale_ocr_id_is_rejected_after_admin_reimport(self):
        old_id = self.client.get(self.ocr_url).data["ocr_id"]
        admin = Patient.objects.get(patient_code="OCR-ADMIN")
        self.client.force_authenticate(admin)
        self.client.post(self.import_url, import_payload(), format="json")
        self.client.force_authenticate(self.patient)
        current = self.client.get(self.ocr_url).data
        response = self.client.post(self.confirm_url, {
            "ocr_id": old_id, "issued_date": current["issued_date"],
            "timing": current["timing"], "per_day": current["per_day"],
            "total_days": current["total_days"], "items": current["items"],
        }, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "OCR_DRAFT_EXPIRED")
