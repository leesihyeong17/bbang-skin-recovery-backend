# 관리자 clinic-data import

병원 콘솔 대신 AI가 안내문과 처방전을 구조화했다고 가정한 JSON을 한 번에 입력하는 API입니다.
기존 fixture와 seed command는 그대로 유지하며, 이 API는 시술 종류와 환자를 추가하거나
같은 자연키의 값을 수정합니다.

```http
POST /api/v1/admin/clinic-data/import
Authorization: Bearer <is_staff 관리자의 access token>
Content-Type: application/json
```

## 루트 구조

```json
{
  "schema_version": "1.0",
  "mode": "upsert",
  "dry_run": false,
  "clinic": {
    "code": "SEOUL-N-2",
    "name": {"ko": "두 번째 병원"},
    "address": {"ko": "서울"},
    "tel": "+82-2-111-2222",
    "reply_hours": "KST 10:00-19:00",
    "staff": [
      {
        "name": {"ko": "김원장"},
        "name_romaji": "KIM DOCTOR",
        "role": "director",
        "license_no": "제00000호"
      }
    ]
  },
  "protocols": [
    {
      "procedure": {
        "code": "blepharoplasty",
        "name": {"ko": "쌍꺼풀 수술", "ja": "二重まぶた手術"},
        "program_days": 60,
        "protocol_version": "v1.0",
        "stage_map": [{"to": 60, "name": {"ko": "회복"}}]
      },
      "care_tasks": [],
      "activity_rules": [],
      "variable_questions": [],
      "symptom_terms": []
    }
  ],
  "patients": [
    {
      "patient": {
        "patient_code": "NR-2608-0500",
        "name": {"ko": "신규 환자"},
        "name_romaji": "NEW PATIENT",
        "nationality": "JP",
        "dob": "1993-01-01",
        "lang": "ja"
      },
      "surgery": {
        "procedure_code": "blepharoplasty",
        "surgeon_name_romaji": "KIM DOCTOR",
        "surgery_date": "2026-08-16",
        "detail": {"ko": "자연유착"},
        "session_no": 1,
        "implant": null,
        "anesthesia": "Local Anesthesia",
        "return_date": null
      },
      "appointments": [],
      "prescription_draft": {
        "issued_date": "2026-08-16",
        "timing": "식후",
        "per_day": 3,
        "total_days": 5,
        "items": [
          {
            "seq": 1,
            "drug_name": "예시 항생제",
            "category": "항생제",
            "dose": "1정",
            "times_per_day": "3회",
            "days": "5일",
            "usage": "매 식후 복용",
            "is_prn": false
          }
        ]
      }
    }
  ]
}
```

`clinic`, `protocols`, `patients`는 선택 항목입니다. 기존 병원에 환자만 추가할 때는
`clinic` 대신 `"clinic_code": "SEOUL-N"`을 보냅니다. 시술 코드는 현재 DB에서 전역
고유하므로 병원별로 다른 프로토콜이면 코드에도 병원 구분을 넣어야 합니다.

## 저장 규칙

- 병원은 `code`, 의료진은 `clinic + name_romaji`로 upsert합니다.
- 시술은 `procedure.code`, 템플릿과 증상 용어는 `procedure + key`로 upsert합니다.
- 포함된 행동 규칙의 phase 목록만 전부 교체합니다.
- 환자는 `patient_code`, 수술은 환자, 예약은 `scheduled_at + kind`로 upsert합니다.
- 처방전 초안은 `ocr_raw`에 저장하고 `PrescriptionItem`은 환자 confirm 전까지 만들지 않습니다.
- 확정된 처방전은 관리자 import로 덮어쓸 수 없습니다.
- 요청 전체는 한 트랜잭션이며 한 항목이라도 실패하면 모두 롤백합니다.

`dry_run: true`는 같은 검증과 저장 경로를 실행한 뒤 롤백하며 `200`을 반환합니다. 실제
입력은 `201`을 반환합니다.

```json
{
  "valid": true,
  "dry_run": false,
  "summary": {
    "procedures": {"created": 1, "updated": 0},
    "patients": {"created": 1, "updated": 0},
    "prescription_drafts": {"created": 1, "updated": 0}
  }
}
```
