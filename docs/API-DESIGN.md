# 나란히 — API 설계 배경 문서

Base `/api/v1` · Django 6.0 + DRF + SimpleJWT · 인증 `Authorization: Bearer <access>`

> ## ⚠️ 이 문서는 계약이 아닙니다
>
> **프론트에 넘길 정본은 `8-10백엔드현황.md` 9장입니다.** 경로·파라미터·응답 필드는 그쪽을 보세요.
>
> 이 문서는 **"왜 이 모양인가"**를 담습니다. 초안 단계에서 먼저 쓰였고, 9장이 나온 뒤 경로 이름을 거기에 맞춰 정렬했습니다. 설계 이유가 필요할 때만 여세요.
>
> 계약이 두 곳에 있으면 반드시 어긋납니다. **엔드포인트를 추가하거나 바꿀 때는 9장만 고치세요.**

---

## 0. 공통 규약

### 에러 포맷

```json
{ "error": { "code": "PRESCRIPTION_REQUIRED", "message": "처방 정보를 먼저 등록해 주세요" } }
```

| HTTP | 코드 | 발생 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 입력값 오류 |
| 400 | `RETURN_DATE_OUT_OF_RANGE` | 귀국일이 D+1~D+120 밖 |
| 400 | `UNKNOWN_TASK_KEY` | 오늘 루틴에 없는 `task_key` |
| 401 | `INVALID_CREDENTIALS` | 환자 ID · 생년월일 불일치 |
| 403 | `ONBOARDING_REQUIRED` | 온보딩 미완료 |
| 409 | `CHECKIN_ALREADY_EXISTS` | 그날 체크인 이미 있음 |
| 409 | `LANG_ALREADY_LOCKED` | 언어 재변경 시도 |
| 409 | `PRESCRIPTION_REQUIRED` | 처방 미확정 상태로 STEP 2 진입 |

### 다국어 필드

서버가 이미 단일 언어로 풀어서 내려줍니다. 클라이언트가 `{ko, ja, en}`을 받는 일은 없습니다. 기준은 `Patient.lang`이고 **값이 없으면 `ko`로 폴백**합니다(fixture에 `en`이 없음).

예외는 상담뿐입니다. 원문 토글이 필요하므로 `body_original` / `body_translated`를 둘 다 내려줍니다.

### `day`와 `date`

API는 항상 **둘 다** 주고받습니다. 응답에는 `"day": 2, "date": "2026.08.05"`가 함께 들어갑니다.

다만 **DB에 저장되는 건 `date`뿐입니다.** `day_offset` 컬럼이 어디에도 없습니다(ERD D4). 뷰에서 변환합니다.

```python
# 요청: ?date=2026-08-05 (또는 데모용 ?day=2)
target_date = surgery.date_of(day)

# 응답: 저장된 date → day
{"day": surgery.day_of(checkin.date), "date": fmt(checkin.date)}
```

프론트엔드는 이 결정을 몰라도 됩니다. 계속 `day`로만 말하면 됩니다.

---

## 0.5 읽는 법

> 프로토타입을 만들지 않은 사람은 이 절을 먼저 읽으세요. 왜 응답이 이 모양인지가 여기 있습니다.

### 설계 원칙 3개

**원칙 1 — 화면 하나에 요청 하나.**

`GET /home` 응답이 비대해 보이는 건 의도입니다. 홈 화면에 필요한 값이 12가지쯤 되는데 엔드포인트를 쪼개면 요청이 6번 나가고, 로딩 상태를 6개 관리해야 합니다. 프론트엔드가 React를 새로 만드는 일주일 일정에서 이건 감당할 수 없습니다.

**원칙 2 — 계산은 서버에서 끝낸다.**

프로토타입은 계산을 프론트에서 합니다. `stateOf()`로 규칙 상태를 뽑고, `completion()`으로 이행률을 구하고, `nextUnlock()`으로 다음 해금일을 찾고, `phT()`로 `{RET}`를 치환합니다.

이걸 전부 서버로 옮깁니다. 이유가 두 개입니다.

- React로 이관할 때 프론트가 이 로직을 다시 쓰지 않아도 됩니다. 일정이 크게 줄어듭니다
- 로직이 한 곳에만 있으면 규칙을 고칠 때 두 군데를 안 고칩니다

그래서 응답에 `status`, `completion_pct`, `next_unlock`, 치환 완료된 `text`가 이미 들어 있습니다. 프론트에 남는 건 조건부 렌더링뿐입니다.

서버 쪽 계산은 `apps/care/services.py` 한 파일에 모입니다. `care_items()` `state_of()` `timeline()` `completion()` `stage_of()` 다섯 개이고, **모든 엔드포인트가 이것만 호출**합니다. 템플릿 테이블을 직접 쿼리하면 개인변수 오버레이가 빠집니다.

**원칙 3 — 다국어는 서버가 풀어서 내려준다.**

DB에는 `{"ko": "…", "ja": "…"}` 형태로 저장하지만, **API 응답에는 절대 나가지 않습니다.** 토큰의 `patient.lang`을 보고 서버가 한 언어로 풀어서 문자열만 보냅니다.

프론트엔드가 i18n 파일을 만들 필요가 없어집니다. 화면 5개 × 언어 3개 작업이 사라집니다.

예외는 상담 원문 토글 하나입니다. 여기만 `?view=original`로 한국어를 요청할 수 있습니다.

### 엔드포인트 ↔ 화면 매핑 (9장 기준)

| 엔드포인트 | 화면 | 언제 호출 | P |
|---|---|---|---|
| `POST /auth/login` `/auth/refresh` | 로그인 | 로그인 버튼 | P0 |
| `GET /me` | (전역) | 앱 진입 시 1회 | P0 |
| `GET /home?date=` | 홈 탭 | 진입 · 날짜 변경 | P0 |
| `PUT /task-logs` | 홈 탭 | 회차 칩 탭 | P0 |
| `GET /care-items/{kind}/{key}` | 홈 → 근거 시트 | 루틴·규칙 탭 | P0 |
| `POST /checkins` | 체크인 1 | 진입 | P0 |
| `POST /checkins/{id}/photos` | 체크인 1 | 촬영 3회 | P0 |
| `PUT /checkins/{id}/symptoms` | 체크인 2 | 눈금 저장 | P0 |
| `POST /checkins/{id}/complete` | 체크인 3 | 저장하기 | P0 |
| `GET /onboarding/status` | 온보딩 STEP 1 | 진입 | P1 |
| `POST /prescriptions/ocr` | STEP 1-b | 셔터 탭 | P1 |
| `POST /prescriptions/confirm` | STEP 1-c | `네, 맞아요` | P1 |
| `GET /onboarding/surgery` | STEP 2 | 진입 | P1 |
| `GET /onboarding/questions` | STEP 3 | 진입 | P1 |
| `POST /onboarding/complete` | 완료 화면 | `케어 루틴 만들기` | P1 |
| `GET /records/calendar` | 기록 · 캘린더 | 뷰 전환 | P1 |
| `GET /records/photos` | 기록 · 사진 타임라인 | 뷰 전환 | P1 |
| `GET /records/symptoms` | 기록 · 증상 흐름 | 뷰 전환 | P1 |
| `GET /records/day?date=` | 날짜 상세 시트 | 날짜 탭 | P1 |
| `POST /reports` `GET /reports/{id}` | 제출용 · 귀국용 | 뷰 전환 | P1 |
| `GET`·`POST /consult/messages` | 병원 탭 | 진입 · 전송 | P2 |
| `GET /clinic` `GET /appointments` | 병원 탭 | 진입 | P2 |
| `GET /notifications` | 알림 오버레이 | 종 아이콘 | P2 |
| `GET /schedule` `GET /schedule/day` | 전체 일정 | `전체 일정 보기` | P2 |

`P0 + P1`이면 `D+2 홈 → 체크인 → 기록` 시연이 가능합니다.

### 초안에서 이름이 바뀐 것

이 문서의 이전 판과 9장이 다릅니다. **9장이 맞습니다.**

| 초안 | 9장 | 왜 9장이 나은가 |
|---|---|---|
| `GET /home?day=2` | `GET /home?date=2026-08-05` | D4(날짜가 사실)와 일관 |
| `/care-plan/tasks/{key}` + `/rules/{key}` | `/care-items/{kind}/{key}` | 근거 시트가 한 화면이라 하나로 충분 |
| `PUT /care-plan/task-logs` | `PUT /task-logs` | `care-plan` 개념이 사라졌으므로 |
| `PATCH /checkins/{id}` | `POST /checkins/{id}/complete` | 의도가 드러남 |
| `GET /checkins` 통합 | `/records/calendar` `/photos` `/symptoms` `/day` | 기록 탭이 실제로 뷰 4개 |
| `/care-plan/schedule` | `/schedule?year&month` + `/schedule/day?date` | 캘린더가 월 단위 조회 |
| `/onboarding/documents` | `/onboarding/status` | 문서 테이블이 없어졌으므로 |
| `/prescriptions/{id}/confirm` | `/prescriptions/confirm` | 환자당 처방이 1개(O2O) |
| `PATCH /me/language` | `POST /auth/login`의 `lang` 필드 | 스플래시에서 언어 고르고 바로 로그인하므로 왕복 1회가 줄어듦 |
| 로그인 응답 `state: "onboarding_required"` | `care_status: "onboarding"` | `Surgery.care_status` 컬럼과 이름·값이 그대로 일치 |
| `POST /prescriptions/retake` | `POST /prescriptions/ocr` 재호출 | 확정 전이면 재호출이 곧 재촬영. 상태 하나가 줄어듦 |

### 만들지 않는 것과 대체 방법

| 원래 있던 것 | 대체 |
|---|---|
| `GET /documents` · `/documents/{id}/download` | `GET /onboarding/status`가 만료 URL 포함해 반환 |
| `Notification` 테이블 | `GET /notifications`가 미완료 루틴 + 미읽음 답변에서 파생 |
| `MilestoneTemplate` 테이블 | `/schedule`의 `events`가 규칙 구간에서 파생 |
| `PatientTask` / `PatientRule` 테이블 | `care_items()`가 템플릿 + 답변으로 계산 |
| `ReportApproval` 테이블 | 병원 확인 이력을 `Appointment`에서 파생 |
| `POST /reports/{id}/pdf` | `GET /reports/{id}/pdf`. `@media print` HTML 뷰로 대체 가능 |
| `POST /onboarding/return-date/preview` | `GET /onboarding/questions`가 비행 규칙 구간을 함께 주고 프론트가 로컬 계산 |

### 호출 시퀀스

**첫 실행 (온보딩)**

```
스플래시    (언어 선택만. 서버 호출 없음)
로그인      POST  /auth/login                 → { lang: "ja" } 동봉 · 이후 잠금
                                              → care_status: "onboarding"
            GET   /me                         → 환자·병원·시술 기본 정보

STEP 1      GET   /onboarding/status          → 자료 3종 조립 결과 (테이블 조회 아님)
                                                 처방 status: "not_registered"
  촬영      POST  /prescriptions/ocr          → 5종 추출 · confirmed_at 아직 null
  확인      POST  /prescriptions/confirm      → 확정 + 원본 사진 삭제
                                                 이후 care_items()가 복약 항목 합성

STEP 2      GET   /onboarding/surgery         → editable_fields: ["return_date"]
STEP 3      GET   /onboarding/questions       → 질문 2개 + 귀국일 기본값
완료        POST  /onboarding/complete        → 답변 · 귀국일 저장 (+예약 추가)
```

`POST /onboarding/complete`가 DB에 쓰는 건 셋입니다. **하나라도 실패하면 전체 롤백**해야 합니다. `VariableAnswer`가 없으면 개인변수 오버레이가 조용히 사라집니다.

**매일 사용**

```
홈 진입     GET  /home?date=2026-08-05        → 오버뷰 + 루틴 + 규칙 3분류
칩 탭       PUT  /task-logs                   → { done_count, completion_pct }
카드 탭     GET  /care-items/task/antisep     → 병원 안내문 원문 시트
규칙 탭     GET  /care-items/rule/flight      → 같은 엔드포인트, kind만 다름

체크인 1    POST /checkins                    → checkin_id 발급
            POST /checkins/{id}/photos × 3    → front · left · right
체크인 2    PUT  /checkins/{id}/symptoms      → 눈금 3항목
체크인 3    POST /checkins/{id}/complete      → 완료 + 완주율 반환
```

체크인이 4번 호출로 나뉜 이유는 중간 이탈 때문입니다. 사진만 찍고 앱을 닫아도 그 상태가 서버에 남아야 하고, 다시 들어오면 이어서 해야 합니다.

**리포트를 병원에 전달**

```
기록 탭     GET  /records/calendar?year&month → 캘린더 + 귀국 박스
            GET  /records/photos              → 사진 타임라인
            GET  /records/symptoms?days=14    → 증상 흐름 막대
제출용 뷰   POST /reports                     → { kind: "submission", lang: "ko" }
전달 버튼   POST /consult/messages            → attached_report + attached_checkin
```

프로토타입의 `S.pending` 상태가 이 흐름입니다. 기록 탭에서 `병원에 전달하기`를 누르면 상담 화면으로 이동하면서 첨부가 미리 붙은 상태가 되고, 환자는 `전송`만 누릅니다.

첨부는 FK 2개로 3종을 커버합니다. 복약 이행 기록이 리포트 본문(`body.care_adherence`)에 이미 들어 있기 때문입니다.

---

## 1. 인증 · 계정

### `POST /auth/login`

```json
{ "patient_code": "NR-2608-0417", "dob": "19940512", "lang": "ja" }
```

`dob`는 화면 입력 그대로 8자리 문자열입니다. 하이픈을 넣으면 `PatientManager.create_user()`가 만든 해시와 어긋납니다.

```json
{
  "access": "eyJ…", "refresh": "eyJ…",
  "patient": {
    "patient_code": "NR-2608-0417",
    "name": "사토 유이", "name_romaji": "SATO YUI",
    "nationality": "JP", "lang": "ja", "lang_locked": false
  },
  "care_status": "onboarding",
  "surgery_id": 1
}
```

`care_status`: `onboarding` / `active` / `completed` — `Surgery.care_status` 컬럼 값 그대로입니다.

언어는 **별도 엔드포인트가 아니라 이 요청의 `lang` 필드**로 정합니다. 스플래시에서 고른 값을 로그인에 실어 보내면 왕복이 한 번 줄고, 잠긴 뒤에는 서버가 조용히 무시합니다(`lang_locked_at` 기준). 초안의 `PATCH /me/language`와 `LANG_ALREADY_LOCKED` 에러는 폐기했습니다.

### `POST /auth/refresh`

표준 SimpleJWT. 단 실패 응답은 9.0 에러 포맷으로 감쌉니다 — 기본 `{"detail":…}`가 나가면 프론트의 `error.code` 분기가 여기서만 깨집니다.

### `GET /me`

```json
{
  "patient": { "…" },
  "clinic": { "name": "서울 N성형외과의원", "tel": "+82-2-000-0000", "reply_hours": "KST 10:00-19:00" },
  "surgery": {
    "id": 1,
    "procedure": "코성형 (융비술)",
    "detail": "실리콘 보형물 삽입 + 자가진피 비주 연장 · 1회차",
    "surgery_date": "2026-08-03",
    "surgeon": "김서준 원장",
    "program_days": 120,
    "return_date": "2026-08-17",
    "return_day": 14,
    "care_status": "active",
    "editable_fields": ["return_date"]
  }
}
```

`Surgery` 하나에 시술 정보와 케어 프로그램 상태가 다 들어 있습니다. 별도 `care_plan` 객체는 없습니다.

`editable_fields`를 응답에 담아두면 프론트가 어느 필드에 입력창을 열지 하드코딩하지 않아도 됩니다. 온보딩 STEP 2의 `수정 불가` 표시도 이 값으로 그립니다.

---

## 2. 온보딩

> **이 화면이 뭔가.** 환자가 처음 앱에 들어와서 `D+120 케어 루틴`을 만드는 과정입니다. 3스텝입니다.
>
> - **STEP 1 자료 수신** — 병원이 보낸 자료 3종을 확인하고, **처방전만 환자가 직접 촬영**합니다. 처방전은 법적으로 약국 제출용이라 병원에서 앱으로 넘어오지 않기 때문입니다
> - **STEP 2 시술 확인** — 병원이 등록한 시술 정보가 맞는지 확인합니다. **수정은 불가**합니다. 이 값이 D+120 루틴 전체의 기준이라 환자가 못 고치게 막습니다
> - **STEP 3 개인 변수** — 안내문이 `~인 경우`로 적어둔 부분을 직접 묻습니다. 패킹 여부, 콧볼 축소 여부, 그리고 **귀국일**
>
> 온보딩이 끝나도 루틴·규칙 테이블은 만들어지지 않습니다. 답변과 귀국일만 저장하고, 화면에 뜨는 목록은 요청마다 `care_items()`가 계산합니다(ERD D1 참조).

### `GET /onboarding/status` — STEP 1 자료 수신

```json
{
  "documents": [
    { "kind": "surgery_record",  "label": "수술기록 (PDF)",
      "received": true, "url": "https://…?X-Amz-Expires=60" },
    { "kind": "aftercare_guide", "label": "수술 후 주의사항 안내문",
      "received": true, "url": "https://…?X-Amz-Expires=60" },
    { "kind": "appointment",     "label": "예약 일정 (D+5, D+7)",
      "received": true, "url": null }
  ],
  "prescription": {
    "status": "not_registered",
    "reason": "약국 제출용이라 병원에서 전송되지 않아요",
    "summary": null
  }
}
```

**이 목록은 테이블 조회가 아니라 조립입니다.** `ClinicDocument` 테이블이 없습니다.

| kind | `received` 판정 | `url` |
|---|---|---|
| `surgery_record` | `bool(surgery.surgery_record)` | 환자별 파일 |
| `aftercare_guide` | `bool(surgery.procedure_type.source_document)` | 시술별 공통 파일 |
| `appointment` | `surgery.appointments.exists()` | `null` (파일 아님) |

`label`의 `(D+5, D+7)` 부분은 `Appointment`를 훑어 `surgery.day_of(a.scheduled_at.date())`로 만듭니다. 하드코딩하지 마세요.

`prescription.status`: `not_registered` / `ocr_pending` / `extracted` / `confirmed`

### `POST /prescriptions/ocr`

`multipart/form-data` · `image` 필드

```json
{
  "prescription_id": 7,
  "ocr_status": "done",
  "issued_date": "2026-08-03",
  "timing": "식후",
  "per_day": 3,
  "total_days": 6,
  "items": [
    { "seq": 1, "drug_name": "세프카펜피복실염산염정 100mg", "category": "항생제",
      "dose": "1정", "times_per_day": "3회", "days": "6일",
      "usage": "매 식후 30분 경구 복용", "usage_short": "식후 30분", "is_prn": false },
    { "seq": 5, "drug_name": "아세트아미노펜정 500mg", "category": "진통제",
      "dose": "1~2정", "times_per_day": "필요 시 최대 4회", "days": "4일",
      "usage": "통증 시 4시간 간격 경구 복용 (돈복)", "usage_short": "통증 시 · 4시간 간격", "is_prn": true }
  ],
  "fixed_count": 4,
  "prn_count": 1,
  "not_extracted": ["요양기관기호", "질병분류기호", "면허번호", "교부번호"]
}
```

`not_extracted`를 응답에 담는 이유는 UI가 `환자분께 필요 없는 항목은 읽지 않습니다`를 그대로 표시하기 때문입니다. 하드코딩하지 말고 서버가 알려주게 하세요.

이 시점에는 아직 `confirmed_at`이 null이고 복약 체크가 생성되지 않았습니다.

### `POST /prescriptions/confirm`

Body 없음. 환자 더블체크 완료. `Prescription`이 `Surgery`와 O2O라 `{id}`가 필요 없습니다.

```json
{
  "confirmed": true,
  "medication_task": {
    "key": "medication",
    "name": "처방약 4종",
    "day_from": 0, "day_to": 5,
    "times_per_day": 3,
    "timing": "식후",
    "summary": "세프카펜피복실정 외 3종 · 하루 3회 · 6일분 · 필요 시 1종"
  }
}
```

여기서 세 가지가 동시에 일어납니다.

1. `confirmed_at` 기록
2. **`source_image` 삭제** — 처방전 원본은 보관하지 않습니다(ERD D5)
3. 이후 `care_items()`가 복약 항목을 런타임 합성. 테이블에 행이 생기는 게 아닙니다

⚠️ **`PUT /task-logs`의 `task_key` 검증 목록에 `medication`을 반드시 포함하세요.** 빠지면 복약 체크가 `UNKNOWN_TASK_KEY`로 튕깁니다. `is_prn=True` 약은 개수에서 제외합니다.

재촬영은 별도 엔드포인트가 아니라 **`POST /prescriptions/ocr` 재호출**입니다. `confirm` 전이면 이전 `ocr_id`가 폐기되고 새 키가 나갑니다. 초안의 `POST /prescriptions/retake`는 폐기했습니다 — 서버에 "재촬영 대기" 상태를 두면 저장하지 않는다는 원칙과 어긋납니다.

### `GET /onboarding/surgery` — STEP 2 시술 확인

```json
{
  "procedure": "코성형 (융비술)",
  "detail": "실리콘 보형물 삽입 + 자가진피 비주 연장 · 1회차",
  "surgery_date": "2026-08-03",
  "clinic": "서울 N성형외과의원",
  "surgeon": "김서준 원장",
  "patient": "사토 유이 (SATO YUI)",
  "editable": false,
  "correction_contact": "서울 N성형외과의원 국제진료팀"
}
```

`editable: false`가 고정입니다. 환자가 PATCH를 시도하면 `READONLY_FIELD`.

### `GET /onboarding/questions` — STEP 3 개인 변수

```json
{
  "questions": [
    { "key": "pack", "question": "코 안에 흰 솜(패킹)이 들어 있나요?",
      "hint": "있으면 제거 일정이 루틴에 추가됩니다. 병원에서 \"2~3일 후 뺀다\"고 안내받았다면 있는 것입니다.",
      "answer_type": "bool" },
    { "key": "alar", "question": "콧볼 축소를 함께 하셨나요?",
      "hint": "했다면 콧볼 실밥 제거(D+10)가 별도로 잡힙니다.",
      "answer_type": "bool" }
  ],
  "return_date": {
    "default": "2026-08-17",
    "default_day": 14,
    "default_source": "병원 예약 기록 기준",
    "min": "2026-08-04", "max": "2026-12-01"
  }
}
```

### 귀국일 프리뷰는 엔드포인트를 만들지 않습니다

프로토타입의 `retNote`는 날짜를 고를 때마다 경고 문구가 바뀝니다.

```
D+10 이전   ⚠ D+10까지는 비행이 금지 구간이라 병원과 먼저 상의해야 합니다
D+11~21    귀국일이 비행 주의 구간에 들어갑니다 — 기내 수분과 2시간마다 보행이 필요해요
D+22 이후   귀국일이 비행 제한이 해제된 뒤라 이동 자체는 부담이 적어요
```

**`GET /onboarding/questions`가 비행 규칙의 구간을 함께 내려주면 프론트가 로컬 계산**할 수 있습니다. 날짜를 고를 때마다 서버를 부르면 반응이 느립니다.

```json
"flight_phases": [
  { "until_day": 10,  "status": "no",   "message": "⚠ D+10까지는 비행이 금지 구간이라…" },
  { "until_day": 21,  "status": "care", "message": "귀국일이 비행 주의 구간에 들어갑니다 —…" },
  { "until_day": 120, "status": "ok",   "message": "귀국일이 비행 제한이 해제된 뒤라…" }
]
```

### `POST /onboarding/complete`

```json
{
  "answers": [ { "key": "pack", "value": true }, { "key": "alar", "value": false } ],
  "return_date": "2026-08-17"
}
```

```json
{
  "surgery_id": 1,
  "procedure": "코성형 (융비술)",
  "program": { "from": "D+0", "to": "D+120" },
  "built": {
    "care_tasks": 8,
    "activity_rules": 10,
    "timeline_events": 14,
    "medication": { "from": "D+0", "to": "D+5" },
    "return": { "day": 14, "date": "2026.08.17" }
  },
  "added_by_variables": [
    { "type": "task", "name": "패킹 제거 확인", "day_from": 2, "day_to": 3 },
    { "type": "appointment", "day": 3, "text": "패킹 제거 (내원)" }
  ]
}
```

`added_by_variables`를 따로 내려주면 완료 화면에서 "당신에게만 추가된 항목"을 보여줄 수 있습니다. 프로토타입엔 없지만 개인화 체감이 크게 올라갑니다.

**`built`의 숫자는 저장된 행 수가 아닙니다.** 루틴·규칙·타임라인은 테이블이 없고 `care_items()` / `timeline()` 파생 결과입니다. 이 응답은 그 길이를 세서 보여주는 것뿐입니다.

이 엔드포인트가 실제로 DB에 쓰는 건 셋입니다. 하나라도 실패하면 **전체 롤백**해야 합니다. `VariableAnswer`가 없으면 개인변수 오버레이가 조용히 사라지기 때문입니다.

```python
with transaction.atomic():
    save_variable_answers(surgery, answers)      # VariableAnswer 2행
    save_return_date(surgery, return_date)       # Surgery.return_date, return_day
    create_variable_appointments(surgery, answers)  # Appointment (패킹 제거 등)
    surgery.onboarded_at = now()
    surgery.care_status = "active"
    surgery.save()
```

---

## 3. 홈

> **이 화면이 뭔가.** 환자가 매일 여는 메인 화면입니다. 크게 세 덩어리입니다.
>
> - **오버뷰 카드** — `D+2`, `1단계 · 초기 안정`, 진행률 바, 그리고 4칸 지표(`다시 할 수 있게 된 것 3/10` · `루틴 완주율 78%` · `다음 해금 D+5` · `귀국 D-12`)
> - **오늘 혼자 하셔야 할 것** — 루틴 카드. 하루 n회를 회차 칩으로 체크합니다. 카드를 탭하면 병원 안내문 원문이 열립니다
> - **오늘 해도 될까?** — 규칙 10종을 `가능` / `주의` / `금지` 세 그룹으로 나눠 보여줍니다
>
> 루틴과 규칙의 차이가 중요합니다. 루틴은 "하세요"(체크 대상, 이행률에 반영), 규칙은 "하지 마세요"(조회만). ERD 1장 용어집에 정리돼 있습니다.

### `GET /home?date=2026-08-05`

`date` 생략 시 오늘. 이 하나로 홈 화면 전체가 채워집니다.

한 번에 홈 화면 전체를 내려줍니다. 프로토타입 `scrHome()`이 참조하는 모든 값입니다.

```json
{
  "day": 2,
  "date": "2026.08.05",
  "stage": { "no": 1, "name": "초기 안정", "total": 6 },
  "progress_pct": 2,
  "program_days": 120,
  "overview": {
    "unlocked": { "done": 0, "total": 10, "label": "다시 할 수 있게 된 것" },
    "completion_pct": 78,
    "next_unlock": { "day": 5, "name": "부목 제거", "kind": "visit" },
    "return": { "days_left": 12, "date": "2026.08.17", "day": 14 }
  },
  "checkin": { "done": false, "hint": "사진 3컷 · 오늘 상태 · 40초" },
  "today_tasks": {
    "items_total": 5, "items_done": 3,
    "list": [
      { "key": "antisep", "icon": "🧴", "name": "절개부 소독",
        "times_per_day": 2, "done_count": 1,
        "day_from": 1, "day_to": 7,
        "why": "실밥 뽑기 전까지 감염을 막는 유일한 방어선이에요. 식염수로 적신 면봉 → 연고 순서." }
    ]
  },
  "medication": {
    "active": true, "name": "처방약 4종", "times_per_day": 3, "done_count": 2,
    "timing": "식후", "day_from": 0, "day_to": 5
  },
  "rules": {
    "ok":   [ { "key": "walk_short", "icon": "🚶", "name": "짧은 보행", "text": "제한 없음" } ],
    "care": [ ],
    "no":   [ { "key": "wash", "icon": "🧼", "name": "세안", "text": "부목 젖으면 안 돼요 · 물티슈로만" },
              { "key": "flight", "icon": "✈️", "name": "장거리 비행", "text": "기압 변화로 출혈 위험 — 금지" } ]
  }
}
```

`rules`를 `ok`/`care`/`no`로 **서버에서 이미 그룹핑**해 내려줍니다. 프로토타입 `rulesGrouped()`가 그렇게 그리고 있고, 클라이언트가 상태를 재계산하면 로직이 두 곳에 생깁니다.

### `GET /care-items/{kind}/{key}` — 근거 시트

`kind`는 `task` 또는 `rule`. 초안은 엔드포인트를 둘로 나눴는데, **화면에서는 같은 하단 시트**라 하나로 합쳤습니다.

**`kind=task`**

```json
{
  "kind": "task",
  "key": "antisep", "icon": "🧴", "name": "절개부 소독",
  "times_per_day": 1, "interval_days": 2, "day_from": 1, "day_to": 7,
  "why": "실밥 뽑기 전까지 감염을 막는 유일한 방어선이에요…",
  "source": {
    "text": "자가소독은 멸균거즈와 생리식염수, 항생제 연고를 사용하고, 실밥 제거 전까지 1~2일 간격으로 시행합니다.",
    "ref": "수술 후 주의사항 · 1항",
    "clinic": "서울 N성형외과의원",
    "protocol_version": "v1.0",
    "document_url": "https://…?X-Amz-Expires=60"
  }
}
```

**`kind=rule`**

```json
{
  "kind": "rule",
  "key": "flight", "icon": "✈️", "name": "장거리 비행",
  "current": { "status": "no", "text": "기압 변화로 출혈 위험 — 금지" },
  "phases": [
    { "until_day": 10,  "status": "no",   "text": "기압 변화로 출혈 위험 — 금지" },
    { "until_day": 21,  "status": "care", "text": "귀국일(D+14)이 이 구간 · 기내 수분과 보행 필수" },
    { "until_day": 120, "status": "ok",   "text": "제한 없음" }
  ],
  "ok_from": 22,
  "why": "기내 기압이 낮아지면 코 안 혈관이 다시 열릴 수 있어요…",
  "source": { "…" }
}
```

`source`는 항상 채워집니다. null이 나오면 데이터 오류입니다. `{RET}` 치환도 서버에서 끝냅니다.

### `PUT /task-logs`

```json
{ "date": "2026-08-05", "task_key": "antisep", "done_count": 2 }
```

```json
{ "task_key": "antisep", "done_count": 2, "completion_pct": 81 }
```

증분(+1)이 아니라 절대값을 받습니다. 칩을 탭할 때마다 서버 상태와 동기화되므로 멱등하고, 오프라인 후 재동기화도 안전합니다.

`TaskLog`는 `task_key`를 FK가 아니라 문자열로 저장합니다(루틴 테이블이 없으므로). **그래서 검증을 코드에서 해야 합니다.**

```python
valid = {t.key: t for t in care_items(surgery).tasks}   # medication 포함됨
if task_key not in valid:
    raise ValidationError({"task_key": "UNKNOWN_TASK_KEY"})
task, day = valid[task_key], surgery.day_of(date)
if not (task.day_from <= day <= task.day_to):
    raise ValidationError({"date": "이 루틴의 활성 구간이 아닙니다"})
```

이게 없으면 오타난 키가 조용히 저장되고 완주율이 어긋납니다. **`care_items()`를 거쳐야 `medication`이 포함됩니다.** 템플릿만 조회하면 복약 체크가 전부 튕깁니다.

### `GET /schedule?year=2026&month=8` — 전체 일정

`전체 일정` 화면의 월 캘린더와 `앞으로의 변화` 타임라인을 한 번에 반환합니다. 날짜 상세는 `GET /schedule/day?date=`로 따로 받습니다.

```json
{
  "program": { "from_day": 0, "to_day": 120, "surgery_date": "2026-08-03" },
  "events": [
    { "day": 3,  "date": "2026.08.06", "kind": "visit",
      "text": "패킹 제거 (내원)", "detail": null },
    { "day": 5,  "date": "2026.08.08", "kind": "visit",
      "text": "부목 제거 (내원)", "detail": null },
    { "day": 8,  "date": "2026.08.11", "kind": "unlock",
      "text": "세안 가능", "detail": "제한 없음" },
    { "day": 11, "date": "2026.08.14", "kind": "care",
      "text": "장거리 비행 주의", "detail": "귀국일(D+14)이 이 구간 · 기내 수분과 보행 필수" },
    { "day": 14, "date": "2026.08.17", "kind": "return",
      "text": "귀국", "detail": null },
    { "day": 29, "date": "2026.09.01", "kind": "unlock",
      "text": "코 풀기 · 음주·흡연 · 옆으로 눕기 가능", "detail": null },
    { "day": 120, "date": "2026.12.01", "kind": "end",
      "text": "케어 프로그램 완주", "detail": null }
  ],
  "checkin_days": [0, 1, 2],
  "visit_days": [3, 5, 7, 30]
}
```

**`events`는 저장된 테이블이 아니라 파생 결과입니다.** 규칙 구간 경계 + 예약 + 귀국일 + 프로그램 종료일에서 매번 계산합니다. ERD 1장 `타임라인` 절에 계산 함수가 있습니다.

`kind` 5종.

| kind | 언제 생기나 | 배지 |
|---|---|---|
| `unlock` | 규칙 상태가 `→ ok`로 바뀌는 경계 | 해금 |
| `care` | 규칙 상태가 `no → care`로 바뀌는 경계 | 주의 |
| `visit` | `Appointment` | 내원 |
| `return` | `Surgery.return_day` | 귀국 |
| `end` | `ProcedureType.program_days` | 완주 |

`detail`은 해당 구간의 안내 문구입니다. 같은 날 여러 규칙이 묶인 경우(`코 풀기 · 음주·흡연 · 옆으로 눕기`)에는 어느 문구를 쓸지 애매하므로 `null`을 내려보냅니다.

`checkin_days`와 `visit_days`는 캘린더 셀에 점을 찍기 위한 날짜 배열입니다. 프론트가 `events`를 순회해서 만들 수도 있지만, 캘린더는 월별로 그려지므로 미리 뽑아주는 게 편합니다.

기록 탭의 캘린더는 이것과 별개로 `GET /records/calendar`를 씁니다. 같은 월 캘린더지만 **표시하는 게 다릅니다** — 전체 일정은 내원일과 귀국일에 점을 찍고, 기록 탭은 체크인한 날에 점을 찍습니다.

---

## 4. 체크인

> **이 화면이 뭔가.** 환자가 매일 남기는 기록입니다. 3스텝, 40초 분량입니다.
>
> - **STEP 1 변화 기록** — 사진 3각도(정면·좌측·우측). 반투명 얼굴 가이드를 겹쳐서 **매일 같은 각도**를 유도합니다. AI 없이 UI로 데이터 품질을 확보하는 장치입니다
> - **STEP 2 오늘 상태** — 증상 3종(`부기` `통증` `멍`)을 5칸 눈금으로. **숫자를 화면에 표시하지 않습니다**
> - **STEP 3 완료** — 홈에서 체크한 자가 케어가 자동으로 함께 기록됩니다. 여기서 다시 입력하지 않습니다
>
> **왜 숫자를 안 보여주나.** 숫자를 붙이면 중증도 평가가 되고, 그건 의료행위입니다. DB에는 1~5를 저장하지만(그래프를 그려야 하므로) 목록·리포트 응답에서는 제외합니다. ERD D3 참조.
>
> **왜 4번 호출로 나뉘나.** 중간 이탈 때문입니다. 사진만 찍고 앱을 닫아도 그 상태가 서버에 남아야 하고, 다시 들어오면 이어서 해야 합니다.

### `POST /checkins`

```json
{ "date": "2026-08-05" }
```

```json
{ "checkin_id": 31, "day": 2, "date": "2026-08-05",
  "photos": [], "symptoms": [], "completed": false,
  "required_angles": ["front", "left", "right"],
  "symptom_terms": [
    { "key": "swelling", "name": "부기" },
    { "key": "pain",     "name": "통증" },
    { "key": "bruise",   "name": "멍" }
  ] }
```

이미 완료된 체크인이 있으면 `409 CHECKIN_ALREADY_EXISTS`. 진행 중(`completed_at`이 null)이면 그대로 반환해서 이어서 하게 합니다.

### `POST /checkins/{id}/photos`

`multipart/form-data` · `angle`, `image`

```json
{ "angle": "front", "uploaded_at": "2026-08-05T09:12:00+09:00",
  "photos_done": 1, "photos_total": 3 }
```

같은 `angle` 재업로드는 덮어씁니다(재촬영).

### `PUT /checkins/{id}/symptoms`

```json
{ "items": [ { "key": "swelling", "level": 2 }, { "key": "pain", "level": 1 }, { "key": "bruise", "level": 2 } ] }
```

```json
{ "saved": 3, "all_recorded": true }
```

**응답에 등급 라벨이나 판정을 담지 않습니다.** `all_recorded`만 알려주고 UI는 "기록됨"만 표시합니다.

### `POST /checkins/{id}/complete`

Body 없음. 명세서 3.7의 체크인은 사진 → 상태 → 저장 3스텝뿐이라 **메모 입력 화면이 없습니다.**

```json
{
  "checkin_id": 31, "day": 2, "completed": true,
  "summary": { "photos": 3, "symptoms": 3, "care_tasks_logged": 4 },
  "completion_pct": 81
}
```

`Checkin.note` 컬럼은 남겨두되 이번 화면에서는 쓰지 않습니다. 지금 지우면 나중에 마이그레이션이 하나 더 생깁니다.

---

## 4.5 기록 조회 — `/records/*`

초안은 `GET /checkins` 하나로 통합했는데, 기록 탭이 실제로 뷰 4개라 나눴습니다. 뷰를 전환할 때마다 필요한 것만 받습니다.

### `GET /records/calendar?year=2026&month=8`

```json
{
  "return_box": { "date": "2026-08-17", "day": 14, "dn": 12,
                  "flight_status": "no",
                  "flight_text": "기압 변화로 출혈 위험 — 금지" },
  "days": [ { "date": "2026-08-05", "day": 2, "has_checkin": true } ]
}
```

**오늘 이후 날짜에는 `has_checkin`을 내리지 않습니다.** 미래 날짜가 "기록 없음"으로 보이면 안 됩니다.

`return_box`는 화면 상단의 `귀국까지 D-12` 배너입니다. 비행 규칙의 현재 상태를 같이 담아서 `귀국일은 비행 금지 구간입니다`를 표시합니다.

### `GET /records/photos`

```json
{ "items": [ { "checkin_id": 1, "day": 0, "date": "2026-08-03",
               "photos": { "front": "<url>", "left": "<url>", "right": "<url>" } } ] }
```

URL은 60초 만료 presigned입니다.

### `GET /records/symptoms?days=14`

그래프 전용. **여기서만 `level`이 나갑니다.**

```json
{
  "range": { "from_day": 0, "to_day": 2 },
  "series": [
    { "key": "swelling", "name": "부기",
      "points": [ {"day":0,"level":1}, {"day":1,"level":3}, {"day":2,"level":2} ] }
  ],
  "display_rule": "눈금 높이만 표시 · 수치 라벨 금지"
}
```

`display_rule`을 응답에 넣는 건 방어적이지만 유용합니다. 프론트가 실수로 숫자를 찍으면 리뷰에서 잡힙니다.

### `GET /records/day?date=2026-08-05` — 날짜 상세 시트

```json
{
  "day": 2, "date": "2026-08-05",
  "terms": ["부기 (edema)", "통증 (pain)", "반상출혈 (ecchymosis)"],
  "photos": { "front": "<url>", "left": "<url>", "right": "<url>" },
  "care_done": [ { "key": "ice", "name": "냉찜질", "done": 4, "per": 4 } ]
}
```

**`level`을 담지 않고 선택된 증상 이름만** 내려줍니다. 그것도 `medical_term`으로 변환해서요. 프로토타입 `termsOf()`와 같은 원칙입니다.

---

## 5. 리포트

> **이 화면이 뭔가.** `기록` 탭에 뷰가 3개 있는데, 이름이 비슷해서 헷갈립니다.
>
> | 탭 | 뭔가 | 언어 | 받는 사람 | 엔드포인트 |
> |---|---|---|---|---|
> | 체크인 기록 | 내가 남긴 원본 (캘린더·사진·눈금 그래프) | 환자 언어 | 환자 본인 | `GET /records/*` |
> | 제출용 기록 | 기록을 문장으로 정리 | 한국어 / 日本語 선택 | 시술한 한국 병원 | `POST /reports` `kind: submission` |
> | 귀국용 요약 | 시술 개요 + 남은 금기 | 환자 모국어 고정 | **귀국 후 현지 의료진** | `POST /reports` `kind: repatriation` |
>
> 세 번째가 이 서비스의 차별점입니다. 일본 클리닉이 한국 성형 후 환자를 받을 때 실제로 요구하는 서류(`紹介状`, 봉합 부위 사진)의 보완 문서입니다. 다만 **진단서가 아니라 환자 자가기록 요약**이라는 표기가 문서에 반드시 들어가야 합니다.
>
> **왜 저장하나.** 병원에 전달한 리포트는 그 시점 내용으로 고정되어야 합니다. 나중에 재생성해서 문장이 바뀌면 안 됩니다. 그리고 `ai_model`·`prompt_version`을 남겨야 재현과 감사가 됩니다.

### `POST /reports`

```json
{ "kind": "submission", "lang": "ko", "day_from": 0, "day_to": 2 }
```

```json
{
  "report_id": 12,
  "kind": "submission",
  "lang": "ko",
  "title": "회복 경과 기록 (의료기관 제출용)",
  "subtitle": "Patient-Reported Recovery Log",
  "meta": {
    "procedure": "코성형 (융비술)",
    "detail": "실리콘 보형물 삽입 + 자가진피 비주 연장 · 1회차",
    "clinic": "서울 N성형외과의원", "surgeon": "김서준", "surgery_date": "2026-08-03",
    "range": "D+0 – D+2",
    "checkin_count": 3, "photo_count": 9, "completion_pct": 81
  },
  "body": {
    "symptom_flow": [
      { "kind": "ok",   "text": "부기 — D+1에 가장 높게 기록된 뒤 D+2부터 낮은 위치로 유지" },
      { "kind": "part", "text": "통증 — D+0 대비 D+2에 더 높은 위치로 기록" },
      { "kind": "info", "text": "D+2 — 코막힘 (nasal congestion) 직접 기록" }
    ],
    "care_adherence": [
      { "kind": "ok",   "text": "냉찜질 — D+0~D+2 3일 모두 이행 (하루 4회 기준)" },
      { "kind": "part", "text": "절개부 소독 — D+1~D+2 중 1일 완료, 나머지 날은 일부만 이행 (하루 2회 기준)" },
      { "kind": "miss", "text": "온찜질 — D+6부터 2일 이행 기록 없음 (D+4~D+10 중 3일 완료)", "first_missed_day": 6 }
    ],
    "events": [ { "kind": "info", "text": "D+5 — 부목 제거 (내원)" } ]
  },
  "disclaimer": [
    "위 정리는 환자의 체크인 기록과 자가 케어 체크 기록을 문장으로 옮긴 것으로, 의료진의 진단·소견이 아닙니다.",
    "나란히는 중증도·등급·정상 여부를 판정하거나 표시하지 않습니다.",
    "원문 메모·사진·눈금 원본은 그대로 보존되며, 병원은 원본을 함께 열어볼 수 있습니다."
  ],
  "approvals": [
    { "day": 0, "text": "수술기록 및 프로토콜 등록 — 서울 N성형외과의원" }
  ],
  "next_approval": { "day": 5, "text": "다음 갱신 예정: D+5 부목 제거 내원 시" },
  "ai": { "model": null, "prompt_version": "rule-1.0" }
}
```

`body[].kind`는 프로토타입 `symLines()` / `careLines()`와 동일하게 **4종 고정**입니다.

| kind | 의미 | 예 |
|---|---|---|
| `ok` | 전량 이행 · 증상이 낮은 위치로 유지 | `3일 모두 이행` |
| `part` | 일부 이행 · 증상이 더 높은 위치로 기록 | `나머지 날은 일부만 이행` |
| `miss` | 이행 기록 없음 | `D+6부터 2일 이행 기록 없음` |
| `info` | 중립 서술 · 메모 · 일정 | `코막힘 직접 기록` |

`warning`, `abnormal`, `risk`, `danger`를 추가하지 마세요 — 그 순간 판정이 됩니다. `part`가 증상에 붙을 때도 "더 높은 위치로 기록"이라는 **관찰 서술**이고 "악화"가 아닙니다. 문구를 그렇게 유지하세요.

`miss` 항목에는 `first_missed_day`를 함께 내려줍니다. `오늘`은 아직 진행 중이므로 미이행 집계에서 제외해야 하는데(프로토타입 `miss.filter(x => x.d0 < S.day)`), 클라이언트가 그 판단을 하려면 날짜가 필요합니다.

**`approvals`는 `ReportApproval` 테이블이 아니라 `Appointment`에서 파생됩니다.** 확인 일자는 `status="done"`인 가장 최근 예약, 다음 갱신 예정은 `status="scheduled"`인 가장 가까운 예약입니다.

**`ai.model`이 `null`인 이유.** 이 문장들은 규칙 기반으로 생성됩니다(프로토타입 `symLines()` / `careLines()` 이식). LLM으로 다듬는 건 선택이고, 안 써도 화면이 그대로 나옵니다. 명세서 5장이 "실서비스에서 필요한 것"으로 꼽은 네 가지에 리포트 생성이 없습니다 — 규칙으로 충분하다고 본 겁니다.

### `POST /reports` — `kind: "repatriation"`

귀국용 요약. 언어가 환자 모국어로 고정됩니다.

```json
{
  "report_id": 13, "kind": "repatriation", "lang": "ja",
  "title": "施術記録サマリー（医療機関提示用）",
  "lines": [
    "・施術: 鼻形成術（シリコンインプラント挿入＋自家真皮による鼻柱延長）1回目",
    "・施術日: 2026-08-03 / 現在 D+2",
    "・施術機関: ソウルN美容外科クリニック (Seoul, KR) +82-2-000-0000",
    "・担当医: KIM SEOJUN（院長）",
    "・経過: チェックイン記録に基づく自動要約（D+0〜D+2）",
    "・注意: D+28まで飲酒・喫煙および鼻をかむ行為を回避 / D+60まで眼鏡の常用を回避 / D+90までサウナ・温泉を回避",
    "・次回受診: D+30 遠隔経過診察（予定）"
  ],
  "note": [
    "本書は診断書ではなく、患者の記録に基づく情報提供文書です。",
    "診断・処方は現地医療機関の判断に従ってください。"
  ],
  "ips_mapping": {
    "history_of_procedures": "施術 · 施術日 · 施術機関",
    "plan_of_care": "注意",
    "patient_story": "経過"
  }
}
```

`注意` 줄은 하드코딩이 아니라 **`ok_from > current_day`인 규칙을 자동 수집**해서 만듭니다. 프로토콜이 바뀌면 문장도 따라 바뀝니다.

`ips_mapping`은 발표용입니다. 국제 표준 대응을 데이터로 보여줄 수 있습니다.

### `GET /reports?kind=` · `GET /reports/{id}`

목록과 단건. 기록 탭에서 지난 리포트를 다시 열 때 씁니다.

### `GET /reports/{id}/pdf?langs=ko,ja`

`application/pdf`. 두 언어를 넘기면 한 파일에 순차 배치합니다. 프로토타입의 `한국어 + 日本語 함께 PDF로 저장`.

P2라서 `@media print` HTML 뷰로 대체해도 됩니다. 그 경우 **경로는 그대로 두고** `text/html`을 반환합니다 — 프론트 링크를 바꾸지 않기 위함입니다.

---

## 6. 상담

> **이 화면이 뭔가.** `병원` 탭의 원격 상담입니다. 환자는 일본어로 쓰고 원장은 한국어로 답하는데, 양쪽이 자기 언어로 읽습니다.
>
> 화면에 `日本語 / 원문` 토글이 있어서 환자가 번역을 못 믿을 때 한국어 원문을 볼 수 있습니다. 그래서 **원문과 번역을 DB에 양쪽 다 저장**합니다. 번역만 저장하면 의료 분쟁 시 증거가 없어집니다.
>
> 전송 시 자료 3종(회복 경과 기록 · 오늘 사진 3컷 · 복약 이행 기록)이 자동으로 함께 붙습니다. 환자가 첨부를 고민하지 않아도 병원이 맥락을 갖고 답할 수 있습니다.
>
> **범위 제한이 화면에 명시됩니다.** `의료진 상담·교육·기록 전달까지 제공됩니다. 진단과 처방은 내원이 필요하며, 나란히는 어떤 경우에도 판단이나 진단을 하지 않습니다.` 이 문구가 의료법 경계선입니다.

### `GET /clinic`

`ConsultThread` 테이블이 없으므로 병원 카드 정보를 여기서 받습니다.

```json
{
  "clinic": "서울 N성형외과의원",
  "staff": { "name": "김서준", "role": "국제진료팀", "reply_hours": "KST 10:00-19:00" },
  "procedure": "코성형 (융비술)", "surgery_date": "2026-08-03",
  "tel": "+82-2-000-0000",
  "documents": [ { "kind": "surgery_record", "label": "수술기록", "url": "…" } ],
  "scope_notice": "의료진 상담·교육·기록 전달까지 제공됩니다. 진단과 처방은 내원이 필요합니다."
}
```

### `GET /consult/messages?view=translated`

`view`: `translated` (환자 언어) / `original` (원문)

```json
{
  "view": "translated",
  "messages": [
    { "id": 1, "sender": "patient", "day": 1,
      "body": "鼻先がD+1より硬くなった感じですが、正常でしょうか？",
      "tag": "내 문장 · 日本語 원문",
      "created_at": "2026-08-04T18:02:00+09:00" },
    { "id": 2, "sender": "staff", "sender_name": "김서준", "day": 1,
      "body": "添付された記録と写真を確認しました。初期の瘢痕組織が定着する過程で…",
      "tag": "실시간 번역 · 한국어 → 日本語",
      "created_at": "2026-08-04T18:24:00+09:00" }
  ]
}
```

`tag`를 서버가 생성합니다. `sender` × `view` 조합에 따라 4가지 문구가 나오는데(프로토타입 `msgTag` 로직), 클라이언트에 두면 반드시 틀립니다.

첨부 목록 조회 엔드포인트는 없습니다. `ConsultAttachment` 테이블을 FK 2개로 대체했으므로, 프론트가 이미 갖고 있는 `report_id`와 `checkin_id`를 그대로 넘기면 됩니다.

### `POST /consult/messages`

```json
{
  "body": "運動してもいいですか？",
  "lang": "ja",
  "attached_report_id": 12,
  "attached_checkin_id": 31
}
```

```json
{
  "id": 5, "sender": "patient", "day": 2,
  "body_original": "運動してもいいですか？",
  "body_translated": "운동해도 될까요?",
  "translated_to": "ko",
  "attached_label": "회복 경과 기록 D+0~D+2",
  "created_at": "2026-08-05T10:31:00+09:00"
}
```

원문과 번역을 둘 다 저장하고 둘 다 반환합니다. 번역은 **저장 시 한 번만** 하고 결과를 보관하세요. 조회할 때마다 API를 부르면 비용과 지연이 쌓입니다.

첨부는 FK 2개로 명세서 3.10의 3종을 커버합니다. 복약 이행 기록이 리포트 본문에 이미 들어 있기 때문입니다. `attached_checkin_id`는 **아직 모델에 없는 필드**라 마이그레이션이 필요합니다.

### `GET /appointments`

```json
{
  "items": [
    { "day": 5,  "title": "부목 제거",        "kind": "visit",  "scheduled_at": "2026-08-08T14:00:00+09:00", "status": "scheduled" },
    { "day": 7,  "title": "실밥 제거",        "kind": "visit",  "scheduled_at": "2026-08-10T14:00:00+09:00", "status": "scheduled" },
    { "day": 30, "title": "경과 진찰 (원격)", "kind": "remote", "scheduled_at": "2026-09-02T14:00:00+09:00", "status": "scheduled", "after_return": true }
  ]
}
```

`after_return`은 `day > return_day` 계산 결과입니다. 귀국 후 일정임을 UI가 강조할 수 있습니다.

### 문서 열람은 별도 엔드포인트가 없습니다

병원 탭의 `수술기록 · 안내문 원문 보기`는 `GET /clinic`의 `documents` 배열을 씁니다. 60초 만료 URL이 이미 들어 있습니다.

`ClinicDocument` 테이블이 없으므로 `/documents/{id}/download` 같은 경로도 없습니다.

---

## 7. 알림

> **테이블이 없습니다.** 웹앱이라 iOS 사파리 푸시가 사실상 불가해서 발송 스케줄을 저장할 이유가 없습니다. 다만 명세서 3.5에 헤더 종 아이콘과 오버레이가 있으므로 **조회 엔드포인트는 만들어야 합니다.**

### `GET /notifications`

초안은 `groups[]`로 2단 중첩했는데, 9장은 **평평한 `items[]`**입니다. 종류가 2개뿐이라 그룹 헤더는 프론트가 `type`으로 묶으면 되고, 중첩이 사라지면 `unread` 판정이 항목 단위로 내려갑니다.

```json
{
  "items": [
    { "type": "clinic_reply", "title": "김서준 원장님이 답변했어요",
      "body": "초기 반흔 조직이 자리잡는 과정에서 흔히 느끼는 감각입니다…",
      "created_at": "…", "unread": true },
    { "type": "routine", "title": "절개부 소독 · 1회 남았어요",
      "body": "D+2 · 하루 1회", "unread": false }
  ]
}
```

`type`은 `clinic_reply` / `routine` 2종 고정입니다. 초안의 `doctor_reply`가 아니라 `clinic_reply`인 게 9장 기준입니다 — 답변 주체가 개인 의사가 아니라 병원이라는 뜻이 담깁니다.

둘 다 파생입니다.

```
루틴 알림   care_items() 중 오늘 done_count < times_per_day 인 항목
병원 답변   ConsultMessage(sender_type="staff",
                          created_at > surgery.consult_last_read_at)
```

`items[].type`을 2종으로 제한하는 게 명세서 1.2 원칙의 구현입니다. 테이블 `choices`가 하던 방어를 serializer가 대신합니다.

### `POST /consult/read`

상담 화면에 들어갈 때 호출합니다. `Surgery.consult_last_read_at = now()`.

`Notification` 테이블이 없으므로 알림 개별 읽음 처리가 아니라 **상담 스레드 전체를 읽음 처리**합니다. `consult_last_read_at`은 아직 모델에 없는 필드입니다.

---

## 8. 병원 어드민 — 만들지 않습니다

초안에는 병원용 API 8개가 있었는데 전부 뺐습니다. **Django Admin으로 커버**합니다.

병원 스태프 로그인 계정도 만들지 않습니다. 이 결정이 `PhotoAccessLog`(열람 주체 없음)와 `ReportApproval`(승인 주체 없음)을 지운 이유이기도 합니다.

데모에서 병원 답변이 필요하면 Django Admin에서 `ConsultMessage`를 `sender_type="staff"`로 하나 넣으면 됩니다. 앱 6개의 `admin.py`가 아직 비어 있으니 **등록만 해두세요.** 앱당 10줄이고, 시딩 검증에도 훨씬 편합니다.

---

## 9. 외부 연동 어댑터

인터페이스를 먼저 고정하고 구현을 갈아끼울 수 있게 합니다.

```python
# care/ocr/base.py
class PrescriptionOCR(Protocol):
    def extract(self, image: bytes) -> PrescriptionDraft: ...

# consult/translation/base.py
class Translator(Protocol):
    def translate(self, text: str, src: str, dst: str, register: str) -> str: ...
    # register: "casual" (환자용) | "clinical" (의료진용)

# reports/generator/base.py
class ReportGenerator(Protocol):
    def summarize(self, ctx: ReportContext) -> ReportBody: ...
```

### 이번에 실제로 붙이는 건 번역 하나입니다

| 기능 | 이번 데모 | 실서비스 | 근거 |
|---|---|---|---|
| 상담 번역 | **실연동** | 필수 | 명세서 5장. 대체 불가 |
| 처방전 OCR → 텍스트 | 사전 추출 고정값 | 필수 | 명세서 5장 |
| 처방전 텍스트 → 필드 | 사전 추출 고정값 | **LLM** | 명세서 5장 `특히 "필요 시 · 돈복" 판별` |
| 리포트 문장 | **규칙 기반** | 규칙 유지 | 명세서 5장이 리포트를 대체 대상에서 뺌 |

**리포트에 LLM을 쓰지 않는 게 의도입니다.** 명세서 3.9가 요구하는 문장은 `체크인 눈금 값의 전후 비교`라고 데이터 출처에 직접 적혀 있습니다. 피크일 탐지·방향 판정·미이행 일수 세기는 전부 결정론이고, LLM을 쓰면 시키지 않아도 `호전되고 있습니다` 같은 판단어가 섞입니다. 1.2의 첫 번째 원칙과 충돌합니다.

**OCR에서 진짜 어려운 건 텍스트 추출이 아니라 그 다음입니다.** `필요시` `통증 시` `PRN` `頓服`이 제각각 나오는데 `is_prn`을 틀리면 진통제가 매일 복약 체크로 올라가고 완주율이 망가집니다. 여기가 LLM 자리입니다.

발표에서 물으면 이렇게 답하세요.

> OCR은 어댑터로 분리했고 이번 데모는 실제 처방전에서 사전 추출한 값을 씁니다. 이 기능의 난이도는 텍스트 추출이 아니라 "필요 시 복용" 판별이라, 거기에 LLM을 쓰는 구조로 설계했습니다.

### 리포트 생성 프롬프트 하드 제약

LLM으로 문장을 다듬을 때만 해당합니다.

```
- 중증도, 등급, 정상/비정상 여부를 절대 서술하지 말 것
- 눈금 숫자를 문장에 쓰지 말 것 ("3단계" ✕ → "한 칸 내려갔습니다" ○)
- 진단명을 추론하지 말 것
- 입력에 없는 사실을 만들지 말 것
- 출력 kind는 ok / part / miss / info 네 가지만 사용할 것
```

### 실패 시 폴백

- OCR 실패 → 수동 입력 폼
- 번역 실패 → 원문 그대로 전달 + `번역 실패 · 원문으로 전달됨` 태그
- 리포트 → 애초에 규칙 기반이라 실패할 게 없음

데모 중에 API가 죽어도 플로우가 끊기지 않습니다. 이게 폴백을 넣는 진짜 이유입니다. **D-1에 번역 API를 일부러 끊어보고 검증하세요.**

---

## 10. 구현 순서

현황 9.9의 P0/P1/P2를 따릅니다. BE 2명이면 이렇게 나눕니다.

| | 먼저 | 그 다음 |
|---|---|---|
| **A** | `care/services.py` (반나절) | `/home` `PUT /task-logs` `/care-items` `/schedule` `/onboarding/*` |
| **B** | 인증 3종 + 체크인 4종 + S3 | `/records/*` `POST /reports` `/consult/*` `/clinic` `/appointments` `/notifications` |

체크인 계열은 `services.py` 의존이 `task_key` 검증 하나뿐이라 병렬로 갈 수 있습니다. A가 `services.py`를 끝내는 순간 나머지가 다 풀립니다.

### 코드 치기 전에

```
□ 시딩 파일 확보 — 어느 브랜치에도 커밋 안 돼 있음
□ secrets.example.json 커밋
□ S3 스모크 테스트 (막히면 체크인이 통째로 막힘)
□ 마이그레이션 3건 한 번에
      ConsultMessage.attached_checkin
      Surgery.consult_last_read_at
      Prescription.source_image.blank=True
□ interval_days를 completion()에 반영할지 결정 (1줄)
□ admin.py 6개 등록
```

3번까지 되면 홈 화면이 살아납니다. 거기서 데모 시나리오를 한 번 돌려보고 나머지를 붙이세요.
