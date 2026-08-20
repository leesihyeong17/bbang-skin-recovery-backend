# 나란히 (Naranhi) — 백엔드

> 2026 멋쟁이사자처럼 애니멀리그 **빵긋빵긋조** 프로젝트

한국에서 성형수술을 받은 해외 환자가 **귀국 후에도 병원의 회복 프로토콜을 그대로 따라갈 수 있게** 돕는 애프터케어 앱의 API 서버입니다.

수술 당일을 `D+0`으로 두고 `D+120`까지, 오늘 뭘 해야 하는지 · 뭘 해도 되는지 · 오늘 상태가 어땠는지를 매일 같은 형식으로 기록합니다. 쌓인 기록은 병원 상담에 자동 첨부되고, 귀국 후에는 현지 의료기관에 보여줄 요약 문서로도 쓰입니다.

**데모 케이스**: 코성형(융비술) · 일본인 환자 사토 유이 · 수술일 2026-08-03 · 귀국 D+14

**배포**: [https://naranhi.duckdns.org](https://naranhi.duckdns.org)

---

## 왜 만들었나

해외에서 성형수술을 받은 환자는 귀국 후 병원의 회복 안내를 종이 한 장, 카카오톡 메시지 몇 개로만 받고 알아서 따라가야 합니다. 시차·언어 장벽 때문에 애매한 부분을 물어보기도 어렵고, 병원 입장에서도 환자가 실제로 얼마나 잘 지키고 있는지 알 방법이 없습니다.

나란히는 병원의 회복 프로토콜(안내문·경과 규칙·예약 일정)을 구조화해 환자에게 매일 같은 화면으로 제공하고, 환자의 체크인 기록을 자동으로 정리해 병원 상담과 귀국 후 현지 진료에 그대로 활용할 수 있게 합니다.

## 주요 API

| 도메인 | 엔드포인트 | 설명 |
|---|---|---|
| 인증 | `POST /api/v1/auth/login` `POST /api/v1/auth/refresh` `GET /api/v1/me` | JWT 로그인 |
| 온보딩 | `GET /api/v1/onboarding/status` `GET /api/v1/onboarding/surgery` `GET /api/v1/onboarding/questions` `POST /api/v1/onboarding/complete` | 시술 확인 · 개인변수 응답 |
| 처방전 OCR | `POST /api/v2/prescriptions/ocr` | 처방전 사진 업로드 → AI(Vision) 약 정보 추출 |
| 처방전 확정 | `POST /api/v1/prescriptions/confirm` | 환자 확인 후 복약 루틴 생성 |
| 홈 | `GET /api/v1/home` `PUT /api/v1/task-logs` `GET /api/v1/care-items/{kind}/{key}` | 오늘의 루틴·금기, 이행 체크 |
| 체크인 | `POST /api/v1/checkins` `POST /api/v1/checkins/{id}/photos` `PUT /api/v1/checkins/{id}/symptoms` `POST /api/v1/checkins/{id}/complete` | 증상·사진·완료 기록 |
| 기록 | `GET /api/v1/records/calendar` `GET /api/v1/records/day` `GET /api/v1/records/symptoms` `GET /api/v1/records/photos` | 캘린더·그래프·날짜별 상세 |
| 리포트 | `POST /api/v1/reports` `GET /api/v1/reports` `GET /api/v1/reports/{id}` | 주간 회복 리포트 · 귀국용 요약 생성 (AI 문장 다듬기 포함) |
| 병원 | `GET /api/v1/clinic` `GET /api/v1/appointments` `GET /api/v1/notifications` | 병원 정보 · 예약 · 알림 |
| 상담 | `POST /api/v1/consult/messages` `GET /api/v1/consult/messages` `POST /api/v1/consult/read` | 환자–병원 실시간 번역 메시징 |
| 일정 | `GET /api/v1/schedule` `GET /api/v1/schedule/day` | 예약·루틴 통합 타임라인 |

## 주요 기능

- **온보딩** — 병원이 등록한 시술 정보 확인, 처방전 사진 촬영 → AI(OpenAI Vision) OCR로 약 정보 자동 추출 → 환자 확인 후 확정
- **홈 / 오늘 할 일** — 수술일 기준 오늘 해야 할 루틴·금기 사항을 매일 같은 형식으로 제공
- **체크인** — 증상 3항목 기록 + 사진(S3, EXIF 자동 제거) + 루틴 이행 체크
- **기록** — 캘린더·증상 그래프·날짜별 상세 기록 조회
- **주간 회복 리포트** — 체크인·이행률을 규칙 기반으로 문장화하고, AI로 자연스럽게 다듬어 병원 제출용 문서 생성
- **귀국용 요약** — 현지 의료기관에 보여줄 시술 개요 + 회복 경과 요약
- **병원 상담** — 환자–병원 실시간 번역 메시징(OpenAI), 리포트/체크인 기록 첨부
- **일정 · 알림** — 예약·루틴 만료를 하나의 타임라인으로 통합

## 기술 스택

| 구분 | 내용 |
|---|---|
| 언어/프레임워크 | Python 3.12 · Django 6.0 · Django REST Framework |
| 인증 | djangorestframework-simplejwt (JWT) |
| DB | MySQL (AWS RDS) |
| 스토리지 | 로컬(문서) + AWS S3(환자 사진, presigned URL) |
| AI | OpenAI API — 상담 번역 · 리포트 문장 다듬기 · 처방전 OCR(Vision) |
| 배포 | AWS EC2 · nginx · gunicorn · Let's Encrypt |
| 패키지 관리 | Poetry |

## 구조

```
accounts/   Clinic · ClinicStaff · Patient (AUTH_USER_MODEL)
protocols/  ProcedureType · CareTaskTemplate · ActivityRuleTemplate · VariableQuestion
care/       Surgery · VariableAnswer · Appointment · Prescription · PrescriptionItem
            services.py   ← 전 엔드포인트가 공유하는 계산 로직의 중심
            ocr.py         ← 처방전 사진 → 약 정보 추출 (OpenAI Vision)
checkins/   Checkin · SymptomTerm · CheckinSymptom · CheckinPhoto · TaskLog
reports/    RecoveryReport · builder.py(문장 생성) · polish.py(AI 다듬기)
consult/    ConsultMessage · translation.py(실시간 번역)
config/     settings · urls · utils(다국어 · 에러 포맷 공통 헬퍼)
```

`Surgery`가 도메인의 뿌리입니다 — 체크인·리포트·상담·처방이 전부 여기 FK로 연결됩니다. 계정 1개는 환자 1명 × 시술 1건으로 고정되어 있습니다(재수술은 병원이 새 계정을 발급).

## 실행 방법

```bash
poetry install
poetry run python manage.py migrate
poetry run python manage.py runserver
```

DB·AWS·OpenAI 키 등은 `secrets.json`(git에 포함되지 않음)으로 관리합니다.

```json
{
  "SECRET_KEY": "...",
  "DB_HOST": "...", "DB_PORT": "3306", "DB_USER": "...", "DB_PW": "...",
  "AWS_ACCESS_KEY_ID": "...", "AWS_SECRET_ACCESS_KEY": "...", "S3_BUCKET": "...",
  "OPENAI_API_KEY": "...", "OPENAI_MODEL": "..."
}
```
