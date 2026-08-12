from rest_framework import serializers
from .models import Checkin

class CheckinSerializer(serializers.ModelSerializer):
    day = serializers.SerializerMethodField() #D+N 변환 헬퍼. day_of()를 쓰면 D+N으로 변환 가능
    class Meta:
        model = Checkin
        fields = ['id', 'date', 'day', 'completed_at']
        read_only_fields = ['completed_at']
        extra_kwargs = {
            'date': {'required': False},
        } #없으면 오늘 날짜로

    def get_day(self, obj) -> int:
        return obj.surgery.day_of(obj.date)