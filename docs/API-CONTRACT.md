# 나란히 백엔드 인계 문서

작성일 2026-08-10 · 레포 `jayanjiyo/bbang-skin-recovery-backend` · 브랜치 `jy`
이 문서 하나로 이어받을 수 있게 정리했습니다. **9장 API 명세가 프론트에 넘길 계약입니다.**

---

## 1. 지금 어디까지 왔나

### ✅ 끝난 것

| | 내용 |
|---|---|
| 환경 | Django 6.0.7 · DRF 3.18 · SimpleJWT 5.5 · django-storages · boto3 · pillow · mysqlclient |
| 인프라 | EC2 · RDS(MySQL, SSH 터널 경유) · S3 버킷 · CORS |
| 앱 | `accounts` `protocols` `care` `checkins` `reports` `consult` (루트 배치) |
| **모델 20개 정의 + `migrate` 완료** | `AUTH_USER_MODEL = "accounts.Patient"` 적용됨 |
| superuser | `admin-0000` 생성 확인 (`Patient code`를 물어보는 것으로 설정 검증 완료) |
| 시딩 데이터 | `seed_protocol.json` 49건 + `seed_demo.py` 작성 완료 |
| API 명세 v2 | 이 문서 9장 |

### ⬜ 안 끝난 것

| 우선 | 내용 | 비고 |
|---|---|---|
| 🔴 | **`loaddata` 미실행** | fixture 파일은 있고 아직 안 넣음 |
| 🔴 | **S3 스모크 테스트 미실행** | 여기서 막히면 체크인 API가 통째로 막힘 |
| 🔴 | **`consult` 모델에 `attached_checkin` FK 미추가** | 6장 R3 참조 |
| 🔴 | `care/services.py` 미작성 | **프로젝트에서 가장 중요한 파일** |
| 🔴 | API 전부 미구현 | serializer · view · url 없음 |
| 🟡 | `django-storages` settings 연결 안 됨 | 패키지는 설치됨 |
| 🟡 | 기능명세서 v3의 "기기에 보관" 문구 미수정 | 6장 R1 — 문서 쪽 수정 |

### 일정

```
8/10  지유(작성자) 작업 → 여기까지 밀어놓음
8/11  파트너 이어받아 마감
8/12  프론트 시작 ← 9장 API 명세가 이날 필요한 전부
```

**프론트에는 서버 완성을 기다리게 하지 말고 9장 명세를 먼저 넘기세요.** 목 데이터로 화면을 다 만들 수 있습니다.

---

## 2. 로컬 실행

```bash
git checkout jy
git pull origin jy
poetry install
```

### 시크릿

`config/settings.py`가 JSON 시크릿 파일을 읽습니다. **git에 없으므로 직접 만들어야 합니다.**
키 이름은 `config/secrets.example.json`을 참고하세요(없으면 만들어서 커밋해 주세요).

⚠️ JSON은 마지막 항목 뒤 쉼표를 허용하지 않습니다. 문법 검사:

```bash
poetry run python -m json.tool config/secrets.json
```

### 마이그레이션 · 시딩

```bash
poetry run python manage.py migrate
poetry run python manage.py loaddata seed_protocol
poetry run python manage.py seed_demo
```

시딩 파일 위치:

```
protocols/fixtures/seed_protocol.json
care/management/__init__.py              ← 빈 파일
care/management/commands/__init__.py     ← 빈 파일
care/management/commands/seed_demo.py
```

### 확인

```bash
poetry run python manage.py shell -c "
from protocols.models import *
from care.models import Surgery
print('루틴', CareTaskTemplate.objects.count(), '/ 규칙', ActivityRuleTemplate.objects.count(), '/ 구간', ActivityRulePhase.objects.count())
s = Surgery.objects.first(); print('D+0', s.surgery_date, '/ 귀국 D+', s.return_day)
"
```

기대값: `루틴 7 / 규칙 10 / 구간 24` · `D+0 2026-08-03 / 귀국 D+14`

### 데모 계정

```
patient_code : NR-2608-0417
dob          : 1994-05-12   (로그인 입력은 19940512 8자리)
```

---

## 3. 확정된 설계 결정

초안(`naranhi-ERD.md`)의 D1~D6은 **전부 유지**합니다. 그 위에 얹은 결정만 적습니다.

### A-1. 저장 정책 — 전부 서버 저장 ★

프로토타입 HTML의 "이 기기에만 저장" 문구는 **백엔드가 없어서 생긴 임시 표현**이었습니다. 실제로는 서버에 저장합니다.

- 사진 · 파일 → **S3**, DB에는 키만
- 버킷은 **퍼블릭 차단 유지**, 만료되는 presigned URL로만 열람
- **단 처방전 원본 사진은 저장하지 않습니다** — OCR 추출 후 삭제

마지막 항목이 중요합니다. 발표에서 "민감정보를 최소한만 보관한다"의 실증이 됩니다.

### A-2. `Patient`를 `AUTH_USER_MODEL`로

초안은 "Django `User`를 안 쓰고 `Patient`를 인증 주체로"였는데, 그러면 커스텀 authentication class를 직접 만들어야 합니다. `Patient`가 `AbstractBaseUser`를 상속하면 SimpleJWT · `request.user` · `permission_classes`가 전부 따라옵니다.

- `USERNAME_FIELD = "patient_code"`
- 비밀번호 = `dob`를 `YYYYMMDD`로 만들어 `set_password()` 해시 저장
- **프론트가 보는 인터페이스는 초안과 동일** (`{patient_code, dob}`)

### A-3. `Surgery.return_day` 컬럼 삭제 → `@property`

초안 D4가 "사실인 날짜를 저장하고 D+N은 파생"인데 이 컬럼만 예외였습니다. `return_date`만 저장하고 `day_of()`로 계산합니다. **응답에는 그대로 `"return_day": 14`가 나갑니다.**

`return_date`는 이 서비스에서 **환자가 직접 입력해 확정하는 유일한 값**입니다. `Surgery`의 나머지 필드는 전부 병원 등록값이거나 시스템 계산값이므로, serializer의 `read_only_fields`에서 이것 하나만 빼면 됩니다.

### A-4. 안내문 PDF가 값의 유일한 기준

프로토타입 HTML과 안내문이 다르면 **무조건 안내문**입니다. 따로 만든 산출물이라 안 맞는 게 정상입니다.

### A-5. 표준 양식은 "나란히의 정규 형식"

병원에 "우리 양식으로 다시 써주세요"는 업무 부담입니다. **병원은 기존 안내문 PDF를 그대로 주고, 나란히가 표준 양식으로 옮긴 뒤 병원이 검수**하는 구조입니다. 이러면 병원 부담이 없고 의료 정보의 책임 소재도 병원에 남습니다.

**이번 MVP에서는 파싱 코드도 병원 어드민도 만들지 않습니다.** 시딩 데이터로 동작하고, 양식은 발표 자료로만 보여줍니다.

### A-6. AI는 번역만 필수

| 기능 | AI 필요 | 대안 |
|---|---|---|
| 안내문 → D+N 규칙 | ❌ | 표준 양식 + 시딩 |
| 리포트 문장 생성 | 선택 | **규칙 기반 로직을 먼저 만들고 AI는 다듬기만** |
| 처방전 OCR | 선택 | 환자 수동 입력 폴백 |
| 번역 | ✅ | 대체 불가 (상담은 실시간) |

리포트를 규칙 기반으로 먼저 만들어두면 **데모 중 AI API가 죽어도 화면이 비지 않습니다.**

---

## 4. 스키마 — 초안 25개 → 20개

### 삭제한 5개

| 삭제 | 이유 |
|---|---|
| `Notification` | 웹앱은 iOS 사파리 푸시가 사실상 불가 |
| `PhotoAccessLog` | 병원 스태프 화면이 없어 '열람 주체'가 없음 |
| `ReportApproval` | 같은 이유. **`Appointment`에서 파생 가능** (6장 Y1) |
| `ConsultThread` | `Surgery`와 1:1에 컬럼이 `status` 하나 → 흡수 |
| `ConsultAttachment` | 제네릭 관계는 2일 일정에 위험 → FK 2개로 대체 |

`PhotoAccessLog` · `ReportApproval`은 **설계로는 옳습니다.** 발표에서 "다음 단계"로 설명하세요.

### 앱 구성

```
accounts/   Clinic, ClinicStaff, Patient(AUTH_USER_MODEL)
protocols/  ProcedureType, CareTaskTemplate,
            ActivityRuleTemplate, ActivityRulePhase, VariableQuestion
care/       Surgery, VariableAnswer, Appointment, Prescription, PrescriptionItem
            services.py ← care_items() timeline() completion() state_of() stage_of()
checkins/   Checkin, SymptomTerm, CheckinSymptom, CheckinPhoto, TaskLog
reports/    RecoveryReport
consult/    ConsultMessage
```

### 절대 만들면 안 되는 컬럼 (초안 D3)

```
severity · severity_label · risk_level · is_normal · is_abnormal · grade · warning_level · recovery_score
```

컬럼이 없으면 실수로 노출할 수도 없습니다. 기능명세서 1.2 "판정하지 않는다"를 스키마로 보장하는 장치입니다.

---

## 5. 시딩 데이터

| 테이블 | 건수 |
|---|---|
| Clinic · ClinicStaff · ProcedureType | 1 · 1 · 1 |
| CareTaskTemplate | 7 |
| ActivityRuleTemplate · ActivityRulePhase | 10 · 24 |
| VariableQuestion · SymptomTerm | 2 · 3 |
| (`seed_demo`) Patient · Surgery · Appointment | 1 · 1 · 3 |

언어는 `ko` / `ja`. `en`은 비어 있습니다.

### 프로토타입과 달라진 3건 (안내문 기준)

| 항목 | 프로토타입 | fixture | 근거 |
|---|---|---|---|
| 절개부 소독 | `per: 2` (하루 2회) | `times_per_day=1`, `interval_days=2` | 안내문 1항 "1~2일 **간격**" |
| 코 안 세척 | `from: 3` | `day_from=0` | 안내문에 시작일 명시 없음 |
| 색조화장 | `{to:14, no}` | `{to:13, no}` | 안내문 4항 "14일~ **가능**" |

### 초안 정정표의 오류

초안 `naranhi-ERD.md` 13장이 "재확인 필요"로 표시한 항목들은 **프로토타입의 `src` 문자열을 원문으로 착각한 것**입니다. 실제 안내문 PDF에는 표로 명시되어 있습니다.

| 규칙 | 초안 | 실제 안내문 | 결론 |
|---|---|---|---|
| 비행 | "숫자 근거 없음" | 9항 `0~10 금지 / 11~21 주의 / 22~ 가능` | 프로토타입 값이 맞음 |
| 안경 | "근거 약함" | 7항 `0~28 / 29~60 / 61~` | 맞음 |
| 코 마사지 | "`to:55`로 고칠 것" | 7항 `0~56 금지, 57~ 가능` | **`to:56`이 맞음. 초안 정정이 틀림** |

**`source_text`는 프로토타입의 `src`가 아니라 안내문 PDF에서 직접 전사했습니다.** 프로토타입 인용문은 요약·의역된 상태라 그대로 쓰면 "원문을 고치지 않는다"는 원칙이 데이터 레벨에서 깨집니다.

### 파생 타임라인 검증 결과

```
D+8   세안 가능          D+29  안경·사우나 주의 시작 / 운동·음주·코풀기·옆눕기 해제
D+11  비행 주의 시작      D+57  코 마사지 가능
D+14  색조화장 가능       D+61  안경 일반 착용
D+15  운동 주의 구간      D+91  사우나 해제
D+22  비행 제한 해제      D+120 완주
```

구간 24개의 순서 · 마지막 경계(120) · 상태 역행 여부를 전부 검사했고 문제 없습니다.

---

## 6. 기능명세서 v3 대조 결과

### 🔴 R1. "기기에 보관한다" 원칙 — **문서를 고쳐야 합니다**

명세서 1.2와 4장이 여전히 단말 보관 전제입니다. 우리는 서버 저장이므로 **v3가 발표에 쓰이면 문서와 구현이 어긋납니다.**

| 현재 | 바꿀 문구 |
|---|---|
| "기기에 보관한다" | **"최소한만 보관한다"** |
| "사진·기록은 단말에 보관하고, 병원에 전송할 때만 업로드한다" | "환자 본인만 접근할 수 있는 비공개 저장소에 보관하고, 만료되는 서명 URL로만 열람한다. 처방전 원본 사진은 추출 후 보관하지 않는다." |

4장 "보관 위치"도 `단말` → `서버(비공개)`. **처방전 사진의 "전송하지 않음"은 그대로 유지**하세요.

### 🔴 R2. 복약 루틴이 fixture에 없음

명세서 3.5에서 복약은 **홈 루틴 체크 항목이고 완주율에 포함**됩니다. 그런데 처방은 환자마다 달라 마스터 템플릿에 못 넣습니다.

`care_items()`가 `Prescription`을 읽어 **런타임에 합성**해야 합니다.

```python
if rx := getattr(surgery, "prescription", None):
    if rx.confirmed_at:
        items.append({
            "key": "medication",
            "name": f"처방약 {rx.items.filter(is_prn=False).count()}종",
            "day_from": 0,
            "day_to": (rx.total_days or 1) - 1,
            "times_per_day": rx.per_day or 1,
            "source": "prescription",
        })
```

⚠️ **`PUT /task-logs`의 `task_key` 검증에 `medication`을 반드시 포함하세요.** 빠지면 복약 체크가 400으로 튕깁니다. `is_prn=True` 약은 개수에서 제외합니다.

### 🔴 R3. 상담 첨부 3종 — 모델 수정 필요 (**아직 미반영**)

명세서 3.10: 회복 경과 기록 · 오늘 사진 3컷 · 복약 이행 기록을 함께 첨부.

`consult/models.py`의 `attached_report` 아래에 추가하세요.

```python
    attached_checkin = models.ForeignKey(
        "checkins.Checkin", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="consult_messages",
    )
```

```bash
poetry run python manage.py makemigrations consult && poetry run python manage.py migrate
```

복약 이행 기록은 리포트 본문(`body.care_adherence`)에 들어가므로 FK 2개면 3종이 커버됩니다.

### 🔴 R4. 언어 3종 vs fixture 2종

명세서 3.1은 한국어 / 日本語 / English인데 fixture는 `ko`/`ja`뿐입니다.

**데모에서 English 선택지를 비활성**하는 쪽을 권합니다(명세서 5장에 다국어 미완성이 이미 적혀 있음). 서버에는 **없으면 `ko`로 폴백**하는 한 줄을 넣어두세요.

### 🟡 Y1. "병원 확인 이력" — 테이블 없이 파생

명세서 3.9의 요소인데 `ReportApproval`을 지웠습니다. `Appointment`에서 계산하면 됩니다.

```
확인 일자     = 가장 최근 status=done 예약일
다음 갱신 예정 = 가장 가까운 status=scheduled 예약일
```

명세서도 "내원할 때마다 병원이 확인·갱신"이라 의미가 정확히 일치합니다.

### 🟡 Y2. `Checkin.note` — 명세서에 메모 입력이 없음

체크인은 사진 → 상태 → 저장 3스텝뿐입니다. **필드는 유지하되 화면에서 안 씁니다.** 지금 지우면 나중에 마이그레이션이 하나 더 생깁니다.

### 🟡 Y3. `ClinicStaff.license_no`

명세서 4장 "수집하지 않는 항목"에 면허번호가 있습니다. 취지는 "처방전에서 읽지 않는다"에 가깝지만, **값이 있으면 심사에서 물어볼 수 있으니 빈 문자열로 두세요.** 화면에 안 씁니다.

### ⚪️ W1. 알림 — 푸시는 불가, **목록 화면은 필요**

명세서 1.2에 "알림은 2종만"이 원칙이고 3.5에 종 아이콘 오버레이가 있습니다. 웹앱이라 푸시 발송은 못 하지만, 목록은 테이블 없이 파생됩니다.

```
루틴 알림 = 오늘 care_items() 중 미완료 항목
병원 답변 = ConsultMessage(sender_type="staff", 미읽음)
```

`Notification` 테이블은 만들지 않되 **`GET /notifications`는 만드세요.**

### ✅ 확인된 일치

환자ID+생년월일 2요소 · 언어 1회 고정 · STEP3 "3가지" · 루틴 7 / 규칙 10 · 3그룹 판정 · D+2 금지 10개 / D+30 가능 7·주의 2·금지 1 · 회복점수 없음 · 5칸 눈금 · 사진 3컷 · 하루 1회 체크인 · 근거 시트 3단 구성 · `is_prn` 제외 · 미수집 항목 · 증상 이름만 용어 변환 · 상담 원문/번역 병행 · 예약 D+5·D+7·D+30 · D+0~D+120

---

## 7. 남은 결정 3건

### ① `interval_days`를 실제로 쓸지

절개부 소독만 이 필드를 씁니다. `completion()`이 해석하려면 로직이 갈라집니다.
**권장: 무시하고 `times_per_day=1`로만 계산.** 매일 체크해도 틀린 안내는 아닙니다.

### ② 부목 제거일 5일 vs 8일 — **병원 확인 필요**

안내문 **내부가 모순**입니다.
- 12번 표: 수술 후 **5일** 부목 제거 → `Appointment`를 D+5로
- 1번 본문: "부목 제거 후(수술 후 **8일**)부터 3주간 테이핑" → `tape` 루틴을 D+8로

D+5~D+7 3일 공백이 생깁니다. **둘 다 안내문에 적힌 그대로 넣었습니다.** 데모는 D+2 화면이라 시연에는 영향 없습니다.

### ③ "수술 후 4주간"의 경계 — **병원 확인 필요**

음주·흡연, 코 풀기, 옆으로 눕기, 상체 45° 수면 4건이 전부 이 표현입니다. D+27인지 D+28인지 문서로는 판정 불가라 **일단 D+28로 통일**했습니다. 값 하나씩만 고치면 됩니다.

---

## 8. 협업 규칙

- 브랜치는 각자 사용, `main`에 PR로 머지
- 작업 전 `git merge main`으로 최신 반영
- **시크릿 파일은 절대 커밋 금지.** `git status`로 확인 후 `git add`
- `poetry add`로 패키지 추가 (`pip install` 금지 — 상대 환경에서 재현 안 됨)
- 기능 하나 끝나면 커밋. 되돌릴 지점을 만들어 둘 것

---

## 9. API 명세 v2 — 프론트에 넘길 계약

Base URL `/api/v1` · 기준: 기능명세서 v3 · 모델 v2 · 파트너 초안 `naranhi-API-SPEC.md`

**이 장이 8/12에 프론트가 필요한 전부입니다.** 서버 구현을 기다리지 말고 먼저 넘기세요.

### 9.0 공통 규약

#### 인증

```
Authorization: Bearer <access_token>
```

SimpleJWT. `AUTH_USER_MODEL = accounts.Patient`이므로 `request.user`가 곧 환자입니다.
`/auth/login`을 제외한 모든 엔드포인트는 인증 필수.

#### 언어 — 서버가 골라서 문자열로 내려줍니다 ★

DB의 다국어 필드는 `{"ko": "...", "ja": "..."}` JSON이지만, **응답에는 문자열 하나만 담습니다.**

```json
"name": "냉찜질"        ← ✅ 이렇게
"name": {"ko": "냉찜질", "ja": "冷やす"}   ← ❌ 이렇게 아님
```

기준은 `Patient.lang`입니다. 프론트가 언어 분기를 안 해도 되고, 응답 크기도 줄어듭니다.
해당 언어 값이 없으면 **`ko`로 폴백**합니다.

**예외는 상담뿐입니다.** 원문 토글이 필요하므로 `body_original` / `body_translated`를 둘 다 내려줍니다.

#### D+N — 서버가 계산해서 내려줍니다

프론트는 날짜 계산을 하지 않습니다. 모든 응답에 `day`(D+N)와 `date`가 함께 들어갑니다.

#### 에러 형식

```json
{ "error": { "code": "PRESCRIPTION_REQUIRED", "message": "처방 정보를 먼저 등록해 주세요" } }
```

| HTTP | 코드 | 상황 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 입력값 오류 |
| 400 | `RETURN_DATE_OUT_OF_RANGE` | 귀국일이 D+1~D+120 밖 |
| 400 | `UNKNOWN_TASK_KEY` | 오늘 루틴에 없는 `task_key` |
| 401 | `INVALID_CREDENTIALS` | ID · 생년월일 불일치 |
| 403 | `ONBOARDING_REQUIRED` | 온보딩 미완료 |
| 409 | `CHECKIN_ALREADY_EXISTS` | 그날 체크인 이미 있음 |

---

### 9.1 인증 · 계정 — 명세서 3.1

#### `POST /auth/login`

```json
// 요청
{ "patient_code": "NR-2608-0417", "dob": "19940512", "lang": "ja" }
```

`dob`는 화면 입력 그대로 **8자리 문자열**. `lang`은 스플래시에서 고른 값으로, **최초 로그인 시에만 반영**되고 이후에는 무시됩니다(명세서: 앱 내 재변경 불가).

```json
// 200
{
  "access": "...", "refresh": "...",
  "patient": { "patient_code": "NR-2608-0417", "name": "佐藤 唯", "lang": "ja", "lang_locked": true },
  "surgery_id": 1,
  "care_status": "onboarding"        // onboarding | active | completed
}
```

`care_status`로 온보딩 화면으로 보낼지 홈으로 보낼지 프론트가 분기합니다.

#### `POST /auth/refresh`

SimpleJWT 표준. `{ "refresh": "..." }` → `{ "access": "..." }`

#### `GET /me`

```json
{
  "patient": { "patient_code": "...", "name": "佐藤 唯", "name_romaji": "SATO YUI",
               "nationality": "JP", "lang": "ja", "lang_locked": true },
  "surgery": { "id": 1, "day": 2, "date": "2026-08-05",
               "surgery_date": "2026-08-03", "return_date": "2026-08-17", "return_day": 14,
               "program_days": 120, "stage": "초기 안정", "care_status": "active" },
  "clinic": { "name": "서울 N성형외과의원", "tel": "+82-2-000-0000", "reply_hours": "KST 10:00-19:00" }
}
```

---

### 9.2 온보딩 — 명세서 3.2 ~ 3.4

#### `GET /onboarding/status` — STEP 1 자료 수신

```json
{
  "documents": [
    { "key": "surgery_record", "label": "수술기록 (PDF)", "received": true,  "url": "<presigned>" },
    { "key": "instructions",   "label": "수술 후 주의사항 안내문", "received": true, "url": "<presigned>" },
    { "key": "appointments",   "label": "예약 일정 (D+5, D+7)", "received": true, "url": null }
  ],
  "prescription": { "registered": false, "summary": null },
  "can_proceed": false
}
```

`can_proceed`가 `false`면 다음 단계 버튼 비활성(명세서: 처방 없으면 복약 루틴을 만들 수 없어 진행 차단).

#### `POST /prescriptions/ocr` — 명세서 3.3

`multipart/form-data` · `image` 필드. **저장하지 않고 추출 결과만 반환합니다.**

```json
{
  "ocr_id": "tmp-8f3a...",         // 확인 단계에서 되돌려줄 임시 키
  "issued_date": "2026-08-03",
  "timing": "식후", "per_day": 3, "total_days": 6,
  "items": [
    { "seq": 1, "drug_name": "세프카펨피복실염산염정 100mg", "category": "항생제",
      "dose": "1정", "times_per_day": "3회", "days": "6일",
      "usage": "매 식후 30분 경구 복용", "is_prn": false },
    { "seq": 5, "drug_name": "아세트아미노펜정 500mg", "category": "해열진통제",
      "dose": "1~2정", "times_per_day": "필요 시 최대 4회", "days": "4일",
      "usage": "통증 시 4시간 간격 경구 복용 (돈복)", "is_prn": true }
  ]
}
```

명세서 4장 준수: **요양기관기호 · 질병분류기호 · 면허번호 · 교부번호는 추출하지 않습니다.**

#### `POST /prescriptions/confirm`

확인 화면에서 수정한 값을 그대로 받아 저장합니다.

```json
{ "ocr_id": "tmp-8f3a...", "timing": "식후", "per_day": 3, "total_days": 6, "items": [ ... ] }
```

`201` → `{ "id": 1, "confirmed_at": "...", "regular_count": 4, "prn_count": 1 }`

> **처방전 원본 사진은 저장하지 않습니다.** 추출이 끝나면 임시 파일을 삭제합니다(명세서 4장).

#### `GET /onboarding/surgery` — STEP 2 시술 확인 (읽기 전용 5행)

```json
{
  "rows": [
    { "label": "시술",  "value": "코성형 (융비술)" },
    { "label": "상세",  "value": "실리콘 보형물 삽입 + 자가진피 비주 연장 · 1회차" },
    { "label": "수술일", "value": "2026-08-03" },
    { "label": "병원",  "value": "서울 N성형외과의원 · 김서준 원장" },
    { "label": "환자",  "value": "사토 유이 (SATO YUI)" }
  ],
  "editable": false,
  "notice": "정보가 다르면 병원 국제진료팀으로 문의해 주세요"
}
```

#### `GET /onboarding/questions` — STEP 3

```json
{
  "questions": [
    { "key": "pack", "question": "코 안에 흰 솜(패킹)이 들어 있나요?",
      "hint": "...", "answer_type": "bool" },
    { "key": "alar", "question": "콧볼 축소를 함께 하셨나요?",
      "hint": "...", "answer_type": "bool" }
  ],
  "return_date": { "min": "2026-08-04", "max": "2026-12-01", "default": null }
}
```

`min`/`max`는 D+1 ~ D+120 (명세서 3.4).

#### `POST /onboarding/complete`

```json
{ "answers": { "pack": true, "alar": false }, "return_date": "2026-08-17" }
```

```json
// 200 — 명세서 3.4 "생성 결과 요약"
{
  "care_status": "active",
  "summary": {
    "routine_count": 7, "rule_count": 10,
    "unlock_event_count": 14, "visit_count": 3,
    "medication_period": "D+0 ~ D+5",
    "return_day": 14, "return_date": "2026-08-17"
  }
}
```

⚠️ 답변 저장과 상태 전환은 **하나의 트랜잭션**입니다. 중간 실패 시 전부 롤백.

---

### 9.3 홈 — 명세서 3.5

#### `GET /home?date=2026-08-05`

`date` 생략 시 오늘. 이 하나로 홈 화면 전체가 채워집니다.

```json
{
  "day": 2, "date": "2026-08-05",
  "stage": "초기 안정", "progress": 0.017,

  "summary": {
    "unlocked_count": 0, "unlocked_total": 10,
    "completion_rate": 0.60,
    "next_unlock": { "day": 8, "date": "2026-08-11", "label": "세안 가능" },
    "return_dn": 12, "return_date": "2026-08-17"
  },

  "checkin": { "completed": false, "checkin_id": null },

  "tasks": [
    { "key": "ice", "icon": "🧊", "name": "냉찜질",
      "times_per_day": 4, "done_count": 2,
      "day_from": 0, "day_to": 3, "source": "template" },
    { "key": "medication", "icon": "💊", "name": "처방약 4종",
      "subtitle": "하루 3회 · 매 식후 · D+0~D+5",
      "times_per_day": 3, "done_count": 1,
      "day_from": 0, "day_to": 5, "source": "prescription" }
  ],

  "rules": {
    "ok":   { "count": 0,  "items": [] },
    "care": { "count": 0,  "items": [] },
    "no":   { "count": 10, "items": [
      { "key": "wash", "icon": "🧼", "name": "세안", "status": "no",
        "text": "부목 젖으면 안 돼요 · 물티슈로만" }
    ] }
  }
}
```

**`tasks[].source`가 `prescription`이면 처방에서 합성된 복약 루틴입니다.** 마스터 템플릿에 없습니다.
`is_prn` 약은 개수에서 제외됩니다(명세서: 완주율 계산에서 제외).

#### `PUT /task-logs`

```json
{ "task_key": "ice", "date": "2026-08-05", "done_count": 3 }
```

**절대값으로 받습니다**(증감 아님). 같은 요청을 두 번 보내도 결과가 같습니다.
`task_key`는 그날 `tasks[]`에 있는 키만 허용, 없으면 `400 UNKNOWN_TASK_KEY`.

```json
{ "task_key": "ice", "date": "2026-08-05", "done_count": 3, "completion_rate": 0.75 }
```

#### `GET /care-items/{kind}/{key}` — 근거 시트 (명세서 3.5)

`kind` = `task` | `rule`. 명세서 순서 그대로 내려줍니다.

```json
{
  "kind": "rule", "key": "ex", "icon": "🏃", "name": "운동",
  "current": { "status": "no", "text": "혈압 상승 — 금지" },
  "why": "혈압이 오르면 부기와 출혈이 함께 돌아옵니다...",
  "phases": [
    { "day_from": 0,  "day_to": 14,  "status": "no",   "text": "혈압 상승 — 금지", "current": true },
    { "day_from": 15, "day_to": 28,  "status": "care", "text": "가벼운 산책 · 스트레칭까지", "current": false },
    { "day_from": 29, "day_to": 120, "status": "ok",   "text": "접촉 스포츠는 D+90 이후", "current": false }
  ],
  "source_text": "수술 후 0~14일 금지 운동 금지 — 혈압 상승 시 부기·출혈 재발 위험 / ...",
  "source_ref": "수술 후 주의사항 · 5. 운동 및 일상 활동"
}
```

`day_from`은 DB에 없습니다 — **앞 구간 `day_to + 1`로 서버가 계산해서 내려줍니다.**
`source_text`는 병원 원문이라 **번역하지 않고 한국어 그대로** 내려갑니다(명세서 1.2 원칙).

---

### 9.4 전체 일정 — 명세서 3.6

#### `GET /schedule?year=2026&month=8`

```json
{
  "range": { "min": "2026-08", "max": "2026-12" },
  "markers": [
    { "date": "2026-08-08", "day": 5,  "type": "visit",  "label": "부목 제거" },
    { "date": "2026-08-17", "day": 14, "type": "return", "label": "귀국 예정" }
  ],
  "upcoming": [
    { "day": 5,   "date": "2026-08-08", "type": "visit",       "label": "부목 제거", "badge": "내원" },
    { "day": 8,   "date": "2026-08-11", "type": "unlock",      "label": "세안 가능", "badge": "해금" },
    { "day": 11,  "date": "2026-08-14", "type": "care_start",  "label": "비행 주의 구간 시작", "badge": "주의" },
    { "day": 14,  "date": "2026-08-17", "type": "return",      "label": "귀국 (일본)", "badge": "귀국" },
    { "day": 120, "date": "2026-12-01", "type": "complete",    "label": "케어 프로그램 완주", "badge": "완주" }
  ]
}
```

`markers`는 **내원 · 귀국 두 종류만** 넣습니다(명세서: 그 외 마커는 가독성 때문에 제외).
`upcoming`은 `timeline()`이 규칙 구간 전이에서 파생합니다.

#### `GET /schedule/day?date=2026-08-11`

날짜 탭 시 시트. `/home`과 같은 구조에서 `checkin` · `summary`를 뺀 형태입니다.

---

### 9.5 체크인 — 명세서 3.7

#### `POST /checkins`

```json
{ "date": "2026-08-06" }
```

이미 있으면 `409` + 기존 `id` 반환. 하루 1회 제약.

#### `POST /checkins/{id}/photos`

`multipart/form-data` · `angle` (`front`|`left`|`right`) + `image`.
같은 각도를 다시 올리면 **덮어씁니다**(명세서: 다시 찍기).

```json
{ "angle": "front", "url": "<presigned, 10분>", "taken_at": "..." , "photo_count": 1 }
```

#### `PUT /checkins/{id}/symptoms`

```json
{ "symptoms": { "swelling": 4, "pain": 3, "bruise": 2 } }
```

3항목 모두 필수(명세서: 3항목 모두 선택해야 저장 가능). 1~5.

#### `POST /checkins/{id}/complete`

사진 3컷 + 증상 3항목이 다 있어야 성공. `completed_at`이 찍힙니다.

```json
{ "completed_at": "...", "day": 3, "completion_rate": 0.75 }
```

> **응답 어디에도 수치 · 정상/이상 판정을 넣지 않습니다**(명세서 1.2 · 3.7).

---

### 9.6 기록 — 명세서 3.8 ~ 3.9

#### `GET /records/calendar?year=2026&month=8`

```json
{
  "return_box": { "date": "2026-08-17", "day": 14, "dn": 12,
                  "flight_status": "no", "flight_text": "기압 변화로 출혈 위험 — 금지" },
  "days": [ { "date": "2026-08-05", "day": 2, "has_checkin": true } ]
}
```

오늘 이후 날짜에는 `has_checkin`을 내리지 않습니다.

#### `GET /records/photos`

```json
{ "items": [ { "checkin_id": 1, "day": 0, "date": "2026-08-03",
               "photos": { "front": "<url>", "left": "<url>", "right": "<url>" } } ] }
```

#### `GET /records/symptoms?days=14`

```json
{
  "terms": [
    { "key": "swelling", "name": "부기",
      "points": [ { "day": 0, "level": 3 }, { "day": 1, "level": 5 } ] }
  ]
}
```

**막대 높이로만 표시하므로 `level`은 내려도 되지만, 등급 라벨은 만들지 않습니다.**

#### `GET /records/day?date=2026-08-15` — 날짜 상세 시트

```json
{
  "day": 12, "date": "2026-08-15", "photos": {...},
  "symptoms": [ { "name": "부기", "medical_term": "부기 (edema)", "level": 3 } ],
  "tasks_done":    [ { "name": "냉찜질", "done_count": 4, "times_per_day": 4 } ],
  "tasks_missing": [ { "name": "압박 테이핑 교체", "done_count": 2, "times_per_day": 3 } ]
}
```

`medical_term`은 **이름만 변환하고 등급은 변환하지 않습니다**(명세서 3.8).

**`tasks_done`은 그날 회차를 다 채운 루틴입니다.** 부분 이행(3회 중 2회)은 `tasks_missing`에 들어가고, 진행도를 보여줄 수 있게 양쪽 다 `done_count` · `times_per_day`를 담습니다. 프론트가 완료 색상을 칠하는 기준이 "전부 완료"이므로 경계를 여기에 맞췄습니다.

#### `POST /reports` — 제출용 · 귀국용 (명세서 3.9)

```json
{ "kind": "submission", "day_from": 0, "day_to": 14, "lang": "ja" }
```

`kind` = `submission`(제출용) | `repatriation`(귀국용 일본어 요약)

```json
{
  "id": 3, "kind": "submission", "lang": "ja", "day_from": 0, "day_to": 14,
  "meta": { "checkin_count": 12, "photo_count": 36, "completion_rate": 0.78,
            "procedure": "코성형 (융비술)", "clinic": "서울 N성형외과의원",
            "confirmed_at": "2026-08-08", "next_confirm_at": "2026-08-10" },
  "body": {
    "symptom_flow": [
      { "kind": "info", "text": "부기 — D+1에 가장 높게 기록된 뒤 D+3부터 낮은 위치로 유지" }
    ],
    "care_adherence": [
      { "kind": "ok",   "text": "냉찜질 — D+0~D+3 전일 완료" },
      { "kind": "part", "text": "압박 테이핑 교체 — D+12 이행 기록 없음 (3일 중 2일 완료)" }
    ],
    "events": [ { "kind": "info", "text": "D+14 — 귀국 (일본)" } ]
  },
  "disclaimer": "본 문서는 환자 자가 보고 기반이며 진단서가 아닙니다."
}
```

**`body[].kind`는 `ok` / `part` / `miss` / `info` 4종 고정입니다.** `warning` · `abnormal` · `risk`를 추가하면 그 순간 판정이 됩니다.

`meta.confirmed_at` / `next_confirm_at`은 **`Appointment`에서 파생합니다** — 가장 최근 `done` 예약일 / 가장 가까운 `scheduled` 예약일.

> **생성 전략:** 규칙 기반 로직으로 문장을 먼저 만들고, AI는 다듬기만 합니다. AI 호출이 실패해도 리포트가 비지 않습니다.

#### `GET /reports/{id}` · `GET /reports?kind=`

---

### 9.7 병원 — 명세서 3.10

#### `GET /clinic`

```json
{
  "name": "서울 N성형외과의원", "surgeon": "김서준 원장",
  "tel": "+82-2-000-0000", "reply_hours": "KST 10:00-19:00",
  "documents": [ { "key": "surgery_record", "label": "수술기록", "url": "<presigned>" },
                 { "key": "instructions",   "label": "수술 후 주의사항 안내문", "url": "<presigned>" } ],
  "scope_notice": "나란히는 상담 · 교육 · 기록 전달까지 제공하며, 진단과 처방은 내원이 필요합니다."
}
```

#### `GET /consult/messages`

```json
{
  "messages": [
    { "id": 1, "sender_type": "patient", "day": 2,
      "body_original": "腫れがひどくなった気がします。正常でしょうか？", "lang_original": "ja",
      "body_translated": "부기가 심해진 것 같습니다. 정상일까요?", "lang_translated": "ko",
      "tag": "내 문장 · 日本語 원문",
      "attachments": [], "created_at": "..." }
  ]
}
```

**`tag`는 서버가 만듭니다.** 발신자 × 보는 언어 4조합이라 프론트에 두면 틀립니다.

`day`는 `created_at`을 KST로 변환해 파생합니다. `attachments[]`는 `{ "kind": "report"|"checkin", "id": 3, "label": "회복 경과 기록 D+0~D+2" }` 형태이고, `kind: "checkin"`에는 `day`·`date`가 함께 담깁니다. 본문은 `/reports/{id}` 또는 `/records/day`로 따로 조회합니다.

#### `POST /consult/messages`

```json
{ "body": "運動してもいいですか？", "attach": { "report_id": 3, "checkin_id": 7 } }
```

`attach`는 선택. 명세서의 첨부 3종은 `report_id`(회복 경과 + 복약 이행) + `checkin_id`(오늘 사진 3컷)로 커버됩니다.

`201`로 **방금 만든 메시지 하나를 `GET`과 같은 형식으로** 돌려줍니다. 프론트가 재조회 없이 말풍선을 그릴 수 있습니다.

```json
{ "id": 4, "sender_type": "patient", "day": 2,
  "body_original": "運動してもいいですか？", "lang_original": "ja",
  "body_translated": "운동해도 될까요?", "lang_translated": "ko",
  "tag": "내 문장 · 日本語 원문",
  "attachments": [ { "kind": "checkin", "id": 7, "day": 2, "date": "2026-08-05",
                     "label": "오늘 사진 D+2" } ],
  "created_at": "..." }
```

번역은 **저장 시 1회** 수행하고 결과를 보관합니다. 조회할 때마다 번역 API를 부르지 않습니다. 번역에 실패하면 `body_translated`와 `lang_translated`가 빈 문자열로 나가고, 원문만 표시하면 됩니다.

#### `POST /consult/read`

상담 화면에 들어갈 때 호출합니다. 요청 body 없음, `204`.

`Surgery.consult_last_read_at = now()`로 **스레드 전체를 읽음 처리**합니다. `Notification` 테이블이 없어 알림 개별 읽음이 아니라 스레드 단위입니다. `GET /notifications`의 `clinic_reply.unread`가 이 값에서 파생하므로, 이 호출이 없으면 배지가 꺼지지 않습니다.

#### `GET /appointments`

```json
{ "items": [ { "id": 1, "day": 5, "date": "2026-08-08",
               "scheduled_at": "2026-08-08T10:00:00+09:00",
               "title": "부목 제거", "kind": "visit", "status": "scheduled" } ] }
```

`scheduled_at`은 KST 오프셋이 붙은 ISO 문자열입니다. 시각까지 필요한 화면을 위해 원본을 함께 내리고, `day`·`date`는 KST 기준으로 파생합니다.

`status`는 `scheduled_at`이 지나면 서버가 `done`으로 계산해서 내려줍니다. **자동 전환은 `scheduled` → `done` 하나뿐입니다.** `missed`는 병원이 명시적으로 넣은 값이므로 덮어쓰지 않습니다.

---

### 9.8 알림 — 명세서 1.2 · 3.5

#### `GET /notifications`

푸시는 웹앱이라 불가하지만 **목록 화면은 명세서에 있습니다.** 테이블 없이 파생합니다.

```json
{
  "items": [
    { "type": "clinic_reply", "title": "김서준 원장님이 답변했어요",
      "body": "...", "created_at": "...", "unread": true },
    { "type": "routine", "title": "짧은 보행 · 3회 남았어요",
      "body": "D+30 · 하루 3회", "unread": false }
  ]
}
```

**이 2종만 보냅니다.** 해금 소식 · 마케팅 · 응원 메시지는 만들지 않습니다.

`clinic_reply.unread`는 `ConsultMessage.created_at > Surgery.consult_last_read_at`으로 파생합니다. `consult_last_read_at`이 `null`이면 전부 미읽음이고, 이 값을 갱신하는 건 `POST /consult/read` 하나뿐입니다. 읽은 답변도 목록에는 남고 `unread: false`로만 구분합니다.

`routine` 항목에는 `created_at`이 없고 `unread`는 항상 `false`입니다. 미읽음 배지는 병원 답변에만 붙습니다.

---

### 9.9 구현 순서

| 우선 | 엔드포인트 | 근거 |
|---|---|---|
| **P0** | `/auth/login` `/auth/refresh` `/me` | 전부가 여기 의존 |
| **P0** | `/home` `PUT /task-logs` `/care-items/{kind}/{key}` | 앱의 중심 화면 |
| **P0** | `/checkins` + photos + symptoms + complete | 데이터 생산 |
| **P1** | `/onboarding/*` `/prescriptions/ocr` `/prescriptions/confirm` | 최초 1회 — 시연에선 시딩으로 우회 가능 |
| **P1** | `/records/*` `POST /reports` | 기록 탭 |
| **P2** | `/consult/*` `/clinic` `/appointments` `/notifications` | 병원 탭 |
| **P2** | `/schedule` `/reports/{id}/pdf` | 여유 시 |

**시연 시나리오가 D+2 홈 → 체크인 → 기록이라면 P0 + P1만으로 발표가 가능합니다.**


---

## 10. 이어받는 사람이 먼저 할 것

순서대로 하시면 됩니다. 앞 단계가 안 되면 뒤가 의미 없습니다.

1. **S3 스모크 테스트** — 여기서 막히면 체크인 API가 통째로 막힙니다. 제일 먼저 확인하세요
2. `loaddata seed_protocol` + `seed_demo` — 데이터 없이는 아무 API도 못 만듭니다
3. **R3 모델 수정** (`attached_checkin`) + `migrate` — 지금이 제일 쌉니다
4. `django-storages` settings 연결
5. **`care/services.py`** — `care_items()` `state_of()` `timeline()` `completion()` `stage_of()`
   `/home` 응답의 거의 전부가 여기서 나옵니다. **엔드포인트에서 `CareTaskTemplate.objects.filter()`를 직접 쓰지 마세요**
6. 인증 3종 → `/home` → `PUT /task-logs` → 체크인
7. 나머지는 9.9장 우선순위대로

### 막히면 확인할 것

| 증상 | 원인 |
|---|---|
| `json.decoder.JSONDecodeError` | 시크릿 JSON 문법 (마지막 쉼표 · 작은따옴표 · 주석) |
| `ModuleNotFoundError: rest_framework.simplejwt` | 앱 이름은 `rest_framework_simplejwt` (점 아님) |
| `ImportError: cannot import name 'Surgery'` | 해당 앱 `models.py`가 비어 있음 |
| `Manager isn't available; 'auth.User' has been swapped` | `User`를 직접 import — `settings.AUTH_USER_MODEL` 사용 |
| DB 연결 끊김 | SSH 터널이 죽었는지 확인 |
| `AccessDenied` (S3) | IAM에 `PutObject`/`GetObject`/`DeleteObject` 누락 |
| `PermanentRedirect` (S3) | 버킷 리전과 설정 리전 불일치 |

트레이스백은 길어도 **내 프로젝트 경로가 나오는 줄부터** 읽으면 됩니다. 마지막 세 줄이 어디서 · 무슨 코드가 · 왜 실패했는지를 담고 있습니다.
