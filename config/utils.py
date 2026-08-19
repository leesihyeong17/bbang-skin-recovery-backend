"""
앱 공통 헬퍼. accounts · care · checkins · reports · consult가 같이 씁니다.

여기 있는 두 함수가 명세 9.0의 "언어는 서버가 골라서 문자열로" 와
"에러 형식"을 구현합니다. 각 앱에서 따로 만들지 마세요.
"""
from rest_framework.response import Response

VALID_LANGS = {"ko", "ja", "en"}


def t(field, lang="ko"):
    """다국어 JSONField를 문자열 하나로 푼다.

    DB에는 {"ko": "냉찜질", "ja": "冷やす"} 로 저장하지만
    응답에는 절대 dict를 내보내지 않는다. 해당 언어가 비어 있으면 ko로 폴백.
    (fixture에 en이 없으므로 English 선택 시 ko가 나간다)
    """
    if not isinstance(field, dict):
        return field or ""
    return field.get(lang) or field.get("ko") or ""


def resolve_lang(request, default="ko"):
    """UI 언어는 요청 스코프를 우선한다. 사용자 저장값은 fallback이다.

    스플래시 선택값이 화면 우선순위를 결정하고, patient.lang는 문맥/리포트용 값으로 남는다.
    """
    if request is None:
        return default

    data = getattr(request, "data", {}) or {}
    query = getattr(request, "query_params", {}) or {}
    meta = getattr(request, "META", {}) or {}

    for mapping, key_names in ((data, ("lang", "ui_lang", "language")),
                              (query, ("lang", "ui_lang", "language"))):
        if isinstance(mapping, dict):
            for key in key_names:
                value = mapping.get(key)
                if value in VALID_LANGS:
                    return value

    header_lang = meta.get("HTTP_X_UI_LANGUAGE") or meta.get("HTTP_X_LANG")
    if header_lang in VALID_LANGS:
        return header_lang

    accept = meta.get("HTTP_ACCEPT_LANGUAGE", "")
    if accept:
        candidate = accept.split(",")[0].split("-")[0].strip()
        if candidate in VALID_LANGS:
            return candidate

    user_lang = getattr(getattr(request, "user", None), "lang", None)
    if user_lang in VALID_LANGS:
        return user_lang

    return default


def api_error(code, message, status=400):
    """명세 9.0 에러 포맷.

        { "error": { "code": "...", "message": "..." } }

    code는 프론트 분기용, message는 사용자에게 그대로 보여줄 문장.
    """
    return Response({"error": {"code": code, "message": message}}, status=status)
