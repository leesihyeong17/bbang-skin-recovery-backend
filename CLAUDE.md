# 나란히 (Naranhi) — 백엔드

한국에서 성형수술을 받은 해외 환자가 **귀국 후에도 병원의 회복 프로토콜을 따라갈 수 있게** 하는 애프터케어 앱의 API 서버입니다.

수술 당일을 `D+0`으로 두고 `D+120`까지, 오늘 뭘 해야 하는지 / 뭘 해도 되는지 / 오늘 상태가 어땠는지를 매일 같은 형식으로 쌓습니다. 쌓인 기록은 병원 상담에 자동 첨부되고, 귀국 후에는 현지 의료기관에 보여줄 요약 문서가 됩니다.

데모 케이스: 코성형(융비술) · 일본인 환자 사토 유이 · 수술일 2026-08-03 · 귀국 D+14

---

## 절대 규칙

**전부 어겨도 에러가 안 나고 조용히 틀립니다.** 코드를 쓰기 전에 여기부터 확인하세요.

### 1. 엔드포인트에서 템플릿을 직접 쿼리하지 말 것

```python
CareTaskTemplate.objects.filter(...)      # ✗ 개인변수 오버레이가 빠진다
care_items(surgery).tasks                 # ○
```

이 환자의 루틴·규칙은 **테이블이 없습니다.** 템플릿 + `VariableAnswer`로 요청마다 계산합니다. `care_items()`를 거치지 않으면 그 화면만 개인화가 사라지고, 복약 항목(`medication`)도 빠집니다.

### 2. 판정하는 컬럼을 만들지 말 것

```
severity · severity_label · risk_level · is_normal · is_abnormal · grade
warning_level · recovery_score
```

중증도·등급·정상 여부를 표시하면 의료행위로 해석될 소지가 있습니다. 컬럼이 없으면 실수로 노출할 수도 없습니다. `CheckinSymptom.level`(1~5)은 그래프용으로만 존재하며 **목록·리포트 응답에는 담지 않습니다.**

### 3. `day_offset` 컬럼을 만들지 말 것

체크인은 실제 달력 날짜에 일어난 사건이고 `D+2`는 해석입니다. 저장하는 건 `date`, D+N은 파생합니다.

```python
surgery.day_of(date)     # date → 2
surgery.date_of(2)       # 2 → date
```

API는 `day`와 `date`를 항상 둘 다 주고받습니다. 변환은 뷰에서 합니다.

### 4. `source_text` · `source_ref`는 NOT NULL

병원 안내문 **원문 그대로**입니다. 요약하거나 의역하면 의료 정보 변조가 됩니다. 원문 없는 항목은 존재할 수 없습니다.

원문은 `수술 후 주의사항 안내문.pdf`에서 직접 전사한 것입니다. 프로토타입 HTML의 `src` 필드는 요약본이라 쓰면 안 됩니다.

### 5. 리포트 `body`의 `kind`는 4종 고정

```
ok · part · miss · info
```

`warning` `abnormal` `risk` `danger`를 추가하지 마세요. 그 순간 판정이 됩니다. `part`가 증상에 붙을 때도 "더 높은 위치로 기록"이라는 **관찰 서술**이지 "악화"가 아닙니다.

### 6. 다국어는 `config.utils.t()` 하나만

DB에는 `{"ko": "…", "ja": "…"}`로 저장하지만 **응답에는 문자열만** 나갑니다. 기준은 `Patient.lang`, 없으면 `ko` 폴백. 각 앱에서 따로 만들지 마세요.

예외는 상담뿐입니다. 원문 토글이 필요해서 `body_original` / `body_translated`를 둘 다 내려줍니다.

### 7. 경로에 끝 슬래시 없음

`/api/v1/auth/login` 이지 `/login/` 이 아닙니다. Django가 POST를 리다이렉트하면 body가 날아갑니다.

### 8. API 정본은 `docs/API-CONTRACT.md`

프론트에 넘긴 계약입니다. 엔드포인트를 추가하거나 바꿀 때 **여기만** 고치세요. `docs/API-DESIGN.md`는 왜 그렇게 설계했는지를 담은 배경 문서이지 계약이 아닙니다.

---

## 실행

```bash
poetry install
poetry run python manage.py migrate
poetry run python manage.py loaddata seed_protocol
poetry run python manage.py seed_demo
poetry run python manage.py runserver
```

`config/secrets.json`이 필요합니다. git에 없으니 팀원에게 받으세요.

```json
{
  "SECRET_KEY": "...",
  "DB_HOST": "...", "DB_PORT": "3306", "DB_USER": "...", "DB_PW": "...",
  "AWS_ACCESS_KEY_ID": "...", "AWS_SECRET_ACCESS_KEY": "..."
}
```

문법 검사: `poetry run python -m json.tool config/secrets.json`

시딩 확인 기대값: `루틴 7 / 규칙 10 / 구간 24` · `D+0 2026-08-03 / 귀국 D+14`

**패키지는 `poetry add`로 추가하세요.** `pip install`은 lock에 안 남아 상대 환경에서 재현이 안 됩니다.

---

## 구조

```
accounts/   Clinic · ClinicStaff · Patient(AUTH_USER_MODEL)
protocols/  ProcedureType · CareTaskTemplate · ActivityRuleTemplate
            ActivityRulePhase · VariableQuestion        ← 나란히 소유 마스터
care/       Surgery · VariableAnswer · Appointment
            Prescription · PrescriptionItem
            services.py                                  ← 가장 중요한 파일
checkins/   Checkin · SymptomTerm · CheckinSymptom · CheckinPhoto · TaskLog
reports/    RecoveryReport
consult/    ConsultMessage
config/     settings · urls · utils(t, api_error)
```

### `care/services.py`

프로토타입이 프론트에서 하던 계산이 전부 여기 모입니다. **모든 엔드포인트가 이것만 호출합니다.**

| 함수 | 하는 일 |
|---|---|
| `care_items(surgery)` | 템플릿 + 개인변수 오버레이 + 복약 합성 → 루틴·규칙 목록 |
| `state_of(rule, day)` | 구간을 훑어 `day <= day_to`인 첫 phase 반환 |
| `timeline(surgery)` | 규칙 전이 + 예약 + 귀국일 + 종료일 → 일정 이벤트 |
| `completion(surgery, day)` | 완주율. `min()` 사용, 오늘은 미이행으로 세지 않음 |
| `stage_of(surgery, day)` | `stage_map`으로 6단계 이름 |

### `Surgery`가 뿌리입니다

체크인·리포트·상담·처방이 전부 여기 FK로 붙습니다. 별도 `CarePlan` 테이블은 없습니다.

환자가 쓸 수 있는 필드는 `return_date`와 `onboarded_at` **둘뿐**입니다. 나머지는 병원 등록값이라 환자 토큰으로 수정할 수 없어야 합니다.

### 테이블 없이 계산하는 것들

| 개념 | 어떻게 |
|---|---|
| 이 환자의 루틴·규칙 | `care_items()` |
| 일정 타임라인 | `timeline()` |
| 귀국일 D+N | `Surgery.return_day` `@property` |
| 병원 확인 이력 | `Appointment`의 `done`/`scheduled` |
| 알림 | 미완료 루틴 + 미읽음 병원 답변 |
| 병원 문서 목록 | `Surgery.surgery_record` + `ProcedureType.source_document` |

---

## 스토리지

**기본은 로컬, 환자 사진만 S3.** `STORAGES`에 `s3` 백엔드가 따로 있고 모델에서 필드 단위로 지정합니다.

```python
from django.core.files.storage import storages

def photo_storage():
    return storages["s3"]

image = models.ImageField(upload_to="checkins/", storage=photo_storage)
```

인스턴스가 아니라 **콜러블**을 넘겨야 마이그레이션에 스토리지 객체가 안 박힙니다.

사진 업로드 시 **EXIF를 반드시 제거**하세요. 회전 정정과 GPS 삭제가 한 번에 됩니다.

```python
from PIL import Image, ImageOps
img = ImageOps.exif_transpose(Image.open(uploaded))
img.thumbnail((1600, 1600))
img.save(dest, "JPEG", quality=85)
```

처방전 원본 사진은 **OCR 확정 후 삭제**합니다. 민감정보 최소 보관 원칙입니다.

---

## 현재 상태

### 된 것
- 모델 20개 + 마이그레이션
- EC2 · RDS(MySQL, SSH 터널) · S3 버킷 · CORS
- `AUTH_USER_MODEL = accounts.Patient`
- 인증 3종 — `POST /auth/login` · `POST /auth/refresh` · `GET /me` (**미검증**)

### 안 된 것
- `care/services.py` 미작성
- 나머지 API 전부 (온보딩 · 홈 · 체크인 · 기록 · 리포트 · 상담)
- `admin.py` 6개 전부 비어 있음

### 반영 안 된 마이그레이션 3건

```python
ConsultMessage.attached_checkin = FK("checkins.Checkin", null=True, blank=True)
Surgery.consult_last_read_at    = DateTimeField(null=True, blank=True)
Prescription.source_image       = ImageField(..., blank=True)   # 확정 후 삭제하므로
```

### 임시 코드
`accounts/views.py`의 `_stage_of()`는 `services.stage_of()`가 나오면 지울 것.

---

## 작업 규칙

- 브랜치는 각자 사용, `main`에 PR로 머지
- 작업 전 `git merge main`
- **`config/secrets.json`은 절대 커밋 금지.** `git add` 전에 `git status`로 확인
- 기능 하나 끝나면 커밋

### 역할 분담 (BE 2명)

| | 담당 |
|---|---|
| **A** | `services.py` → `/home` `/task-logs` `/care-items` `/schedule` `/onboarding/*` |
| **B** | 인증 · 체크인 · S3 → `/records/*` `/reports` `/consult/*` `/clinic` `/appointments` `/notifications` |

체크인 계열은 `services.py` 의존이 `task_key` 검증 하나뿐이라 병렬로 갑니다.

---

## 문서

| 파일 | 뭘 담나 |
|---|---|
| `docs/API-CONTRACT.md` | **프론트에 넘긴 계약.** 여기만 고칠 것 |
| `docs/ERD.md` | 도메인·컬럼 해설과 설계 결정 6개 |
| `docs/API-DESIGN.md` | API가 왜 이 모양인지 (배경) |
| `나란히_기능명세서_v3.pdf` | 화면별 기능 정의. 값이 다르면 이게 우선 |
| `수술 후 주의사항 안내문.pdf` | **프로토콜 값의 유일한 기준** |

값이 서로 다르면 우선순위는 **안내문 PDF > 기능명세서 > 프로토타입 HTML** 입니다. 프로토타입은 따로 만든 산출물이라 안 맞는 게 정상입니다.

### 병원 확인이 필요한 미해결 2건

- 부목 제거일 D+5 vs D+8 — 안내문 내부가 모순됨. 일단 둘 다 적힌 대로 넣음
- "수술 후 4주간"이 D+27인지 D+28인지 — 일단 D+28로 통일
