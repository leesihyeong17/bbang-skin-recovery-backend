from django.contrib import admin
from django.utils import timezone

from .models import ConsultMessage
from .translation import translate
from .views import CLINIC_LANG


@admin.register(ConsultMessage)
class ConsultMessageAdmin(admin.ModelAdmin):
    list_display = ("surgery", "sender_type", "sender_staff", "created_at")
    list_filter = ("sender_type",)
    # autocomplete_fields는 Checkin·ClinicStaff·RecoveryReport·Surgery 쪽에
    # admin 등록 + search_fields가 있어야 동작한다(admin.E039). 지금 다
    # 비어있어서 기본 select 박스로 둔다.
    fields = (
        "surgery", "sender_type", "sender_staff", "body_original",
        "attached_report", "attached_checkin",
        "body_translated", "lang_translated", "translated_at", "translation_engine",
    )
    readonly_fields = ("body_translated", "lang_translated", "translated_at", "translation_engine")

    def save_model(self, request, obj, form, change):
        # 새로 쓰는 병원 답변만 번역한다. 기존 메시지를 admin에서 다시 저장해도
        # 번역을 덮어쓰지 않는다 — "저장 시 1회"를 지킨다.
        if not change and obj.sender_type == ConsultMessage.SenderType.STAFF:
            obj.lang_original = CLINIC_LANG
            target = obj.surgery.patient.lang
            translated, engine = translate(obj.body_original, CLINIC_LANG, target)
            obj.body_translated = translated
            obj.lang_translated = target if translated else ""
            obj.translated_at = timezone.now() if translated else None
            obj.translation_engine = engine
        super().save_model(request, obj, form, change)