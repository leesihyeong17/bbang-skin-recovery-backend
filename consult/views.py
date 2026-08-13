"""병원 탭 — 계약 9.7 · 9.8

Clinic · Appointment 조회와 상담. 모델은 accounts · care에 있고 여기는 뷰만 둔다.
"""
from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response


# Create your views here.
from config.utils import api_error, t

DOC_EXPIRE = 600 #s3로 옮기면 사용

SCOPE_NOTICE = {
    "ko": "나란히는 상담 · 교육 · 기록 전달까지 제공하며, 진단과 처방은 내원이 필요합니다.",
    "ja": "ナランヒは相談・教育・記録の共有までを提供します。診断と処方には受診が必要です。",
}

DOC_LABELS = {
    "surgery_record": {"ko": "수술기록", "ja": "手術記録"},
    "instructions": {"ko": "수술 후 주의사항 안내문", "ja": "術後の注意事項"},
}

ROLE_SUFFIX = {
    "director": {"ko": "원장", "ja": "院長"},
    "intl_coordinator": {"ko": "국제진료 코디네이터", "ja": "国際診療コーディネーター"},
}


def _doc(key, filefield, lang):
    """파일이 실제로 있을 때만 항목을 만든다.

    FileField가 비어 있는데 .url을 부르면 ValueError가 난다.
    시딩에 PDF를 안 넣었으면 목록에서 빠지는 게 맞다.
    """
    if not filefield:
        return None
    return {
        "key": key,
        "label": t(DOC_LABELS[key], lang),
        "url": filefield.storage.url(filefield.name),
    }


class ClinicView(APIView):
    def get(self, request):
        surgery = request.user.surgery
        if surgery is None:
            return api_error("VALIDATION_ERROR", "등록된 시술이 없습니다")

        lang = request.user.lang
        clinic = surgery.clinic
        surgeon = surgery.surgeon

        # "김서준 원장" — 이름과 직함을 서버에서 붙인다. 언어마다 어순이 달라
        # 프론트가 조립하면 일본어에서 어색해진다.
        suffix = t(ROLE_SUFFIX.get(surgeon.role, {}), lang)
        surgeon_label = f"{t(surgeon.name, lang)} {suffix}".strip()

        documents = [
            _doc("surgery_record", surgery.surgery_record, lang),
            _doc("instructions", surgery.procedure_type.source_document, lang),
        ]

        return Response(
            {
                "name": t(clinic.name, lang),
                "surgeon": surgeon_label,
                "tel": clinic.tel,
                "reply_hours": clinic.reply_hours,
                "documents": [d for d in documents if d],
                "scope_notice": t(SCOPE_NOTICE, lang),
            }
        )