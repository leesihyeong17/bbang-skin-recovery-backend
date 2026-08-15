from rest_framework import serializers
from .models import Checkin

class CheckinSerializer(serializers.ModelSerializer):
    day = serializers.SerializerMethodField() #D+N 변환 헬퍼. day_of()를 쓰면 D+N으로 변환 가능
    class Meta:
        model = Checkin
        fields = ['id', 'date', 'day', 'completed_at']
        read_only_fields = ['completed_at']
        extra_kwargs = {
            'date': {'required': False},# date 필드는 Checkin 생성 시 자동으로 설정되므로, 클라이언트에서 제공할 필요가 없음
        } 

    def get_day(self, obj) -> int:
        return obj.surgery.day_of(obj.date)

class CheckinSymptomSerializer(serializers.Serializer):
    symptoms = serializers.DictField(
        child=serializers.IntegerField(min_value=1, max_value=5), 
        ) 

    def validate_symptoms(self, value): # value는 {term_key: level} 딕셔너리. term_key는 SymptomTerm.key
        terms = self.context["terms"] # view에서 context={"terms": terms}로 넘겨줌. terms는 SymptomTerm.key 리스트
        if set(terms) - set(value):
            raise serializers.ValidationError("모든 증상 항목을 입력해야 합니다.")
        if set(value) - set(terms):
            raise serializers.ValidationError("알 수 없는 증상 항목이 있습니다.")

        return value
