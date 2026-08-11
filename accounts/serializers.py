from rest_framework import serializers

from config.utils import t

from .models import Patient


class PatientSerializer(serializers.ModelSerializer):
    """응답용. name은 다국어 JSON이므로 문자열로 풀어서 내보낸다."""

    name = serializers.SerializerMethodField()
    lang_locked = serializers.BooleanField(read_only=True)

    class Meta:
        model = Patient
        fields = (
            "patient_code",
            "name",
            "name_romaji",
            "nationality",
            "lang",
            "lang_locked",
        )

    def get_name(self, obj) -> str:
        return t(obj.name, obj.lang)
