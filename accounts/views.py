"""
인증 · 계정 — 명세 9.1

회원가입이 없습니다. 병원이 발급한 patient_code + 생년월일 8자리로 들어옵니다.
비밀번호는 PatientManager.create_user()가 dob를 YYYYMMDD로 해시해 둔 값이라
authenticate()에 그대로 넘기면 됩니다.
"""
from django.contrib.auth import authenticate
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from config.utils import api_error, resolve_lang, t

from .models import Patient
from .serializers import PatientSerializer

from care.services import stage_of


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "login"

    def post(self, request):
        code = (request.data.get("patient_code") or "").strip()
        dob = (request.data.get("dob") or "").strip()      # 화면 입력 그대로 "19940512"

        if not code or not dob:
            return api_error("VALIDATION_ERROR", "환자 ID와 생년월일을 입력해 주세요")

        patient = authenticate(request, patient_code=code, password=dob)
        if patient is None:
            return api_error(
                "INVALID_CREDENTIALS", "ID 또는 생년월일이 일치하지 않습니다", 401
            )

        # Splash 선택 언어는 UI 우선값으로 처리하고, 환자 DB lang은 문맥용으로 남긴다.
        # 서버가 로그인 시점에 patient.lang를 덮어써서 UI 언어와 문맥 언어를 섞지 않는다.
        surgery = patient.surgery
        refresh = RefreshToken.for_user(patient)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "patient": PatientSerializer(patient).data,
                "surgery_id": surgery.id if surgery else None,
                # 프론트가 온보딩으로 보낼지 홈으로 보낼지 이걸로 분기한다
                "care_status": surgery.care_status if surgery else None,
            }
        )


class RefreshView(TokenRefreshView):
    """SimpleJWT 표준 동작 그대로. 에러 형식만 명세 9.0에 맞춘다.

    감싸지 않으면 만료 시 {"detail": ..., "code": "token_not_valid"}가 나가서
    프론트의 res.error.code 분기가 여기서만 깨진다.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except (InvalidToken, TokenError):
            return api_error("INVALID_CREDENTIALS", "다시 로그인해 주세요", 401)


class MeView(APIView):
    def get(self, request):
        patient = request.user
        lang = resolve_lang(request, default=patient.lang or "ko")
        data = {"patient": PatientSerializer(patient).data}

        surgery = patient.surgery
        if surgery is None:
            return Response(data)      # superuser 등 시술이 없는 계정

        today = timezone.localdate()
        day = surgery.day_of(today)

        data["surgery"] = {
            "id": surgery.id,
            "day": day,                      # D+N은 서버가 계산해서 내려준다 (D4)
            "date": today,
            "surgery_date": surgery.surgery_date,
            "procedure": t(surgery.procedure_type.name, lang),
            "detail": t(surgery.detail, lang),
            "surgeon": t(surgery.surgeon.name, lang),
            "program_days": surgery.program_days,
            "return_date": surgery.return_date,
            "return_day": surgery.return_day,   # 컬럼이 아니라 @property
            "stage": stage_of(surgery, day),
            "care_status": surgery.care_status,
            # 환자가 고칠 수 있는 필드. 프론트가 입력창을 열지 이걸로 판단한다
            "editable_fields": ["return_date"],
        }
        data["clinic"] = {
            "name": t(surgery.clinic.name, lang),
            "tel": surgery.clinic.tel,
            "reply_hours": surgery.clinic.reply_hours,
        }
        return Response(data)
