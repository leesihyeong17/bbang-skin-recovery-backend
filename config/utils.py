"""
앱 공통 헬퍼. accounts · care · checkins · reports · consult가 같이 씁니다.

여기 있는 두 함수가 명세 9.0의 "언어는 서버가 골라서 문자열로" 와
"에러 형식"을 구현합니다. 각 앱에서 따로 만들지 마세요.
"""
from rest_framework.response import Response


def t(field, lang="ko"):
    """다국어 JSONField를 문자열 하나로 푼다.

    DB에는 {"ko": "냉찜질", "ja": "冷やす"} 로 저장하지만
    응답에는 절대 dict를 내보내지 않는다. 해당 언어가 비어 있으면 ko로 폴백.
    (fixture에 en이 없으므로 English 선택 시 ko가 나간다)
    """
    if not isinstance(field, dict):
        return field or ""
    return field.get(lang) or field.get("ko") or ""


def api_error(code, message, status=400):
    """명세 9.0 에러 포맷.

        { "error": { "code": "...", "message": "..." } }

    code는 프론트 분기용, message는 사용자에게 그대로 보여줄 문장.
    """
    return Response({"error": {"code": code, "message": message}}, status=status)
