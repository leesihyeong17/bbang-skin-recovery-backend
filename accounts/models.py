"""
apps/accounts/models.py — 도메인 A (조직 · 환자)

[v2 변경점]
- Patient를 AUTH_USER_MODEL로 승격 (AbstractBaseUser 상속)
  → SimpleJWT · permission_classes · request.user를 커스텀 인증 없이 그대로 사용
- ClinicStaff.user (O2O User) 삭제 — 병원 스태프 화면을 만들지 않으므로 로그인 계정 불필요

[!!] settings.py에 반드시 넣고, 첫 migrate 전에 설정할 것:
     AUTH_USER_MODEL = "accounts.Patient"
     이미 migrate를 돌렸다면 RDS의 DB를 drop 후 재생성하는 게 가장 빠릅니다.
"""
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class Clinic(models.Model):
    code = models.CharField(max_length=20, unique=True)          # SEOUL-N
    name = models.JSONField()                                     # {"ko":…, "ja":…, "en":…}
    address = models.JSONField(default=dict, blank=True)
    tel = models.CharField(max_length=30)                         # 국가번호 포함 +82-2-...
    reply_hours = models.CharField(max_length=50, blank=True)     # "KST 10:00-19:00"

    class Meta:
        db_table = "clinic"

    def __str__(self):
        return self.name.get("ko", self.code)


class ClinicStaff(models.Model):
    """로그인 계정이 아님. 리포트·상담에 이름을 찍기 위한 데이터."""

    class Role(models.TextChoices):
        DIRECTOR = "director", "원장"
        COORDINATOR = "intl_coordinator", "국제진료 코디네이터"

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="staff")
    name = models.JSONField()
    name_romaji = models.CharField(max_length=60)                 # KIM SEOJUN
    role = models.CharField(max_length=20, choices=Role.choices)
    license_no = models.CharField(max_length=40, blank=True)

    class Meta:
        db_table = "clinic_staff"

    def __str__(self):
        return self.name_romaji


class PatientManager(BaseUserManager):
    def create_user(self, patient_code, dob, **extra):
        if not patient_code:
            raise ValueError("patient_code is required")
        user = self.model(patient_code=patient_code, dob=dob, **extra)
        # 비밀번호 = 생년월일 YYYYMMDD. 해시로 저장되므로 평문 비교를 하지 않는다.
        user.set_password(dob.strftime("%Y%m%d") if hasattr(dob, "strftime") else str(dob))
        user.save(using=self._db)
        return user

    def create_superuser(self, patient_code, dob, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(patient_code, dob, **extra)


class Patient(AbstractBaseUser, PermissionsMixin):
    class Lang(models.TextChoices):
        KO = "ko", "한국어"
        JA = "ja", "日本語"
        EN = "en", "English"

    # clinic은 운영자(superuser) 계정 때문에 null 허용. 실제 환자는 항상 채워짐.
    clinic = models.ForeignKey(
        Clinic, on_delete=models.PROTECT, related_name="patients", null=True, blank=True
    )
    patient_code = models.CharField(max_length=20, unique=True)   # NR-2608-0417 = 로그인 ID
    name = models.JSONField(default=dict, blank=True)
    name_romaji = models.CharField(max_length=60, blank=True)     # 여권명 SATO YUI
    nationality = models.CharField(max_length=2, blank=True)      # ISO JP
    dob = models.DateField(null=True, blank=True)                 # 로그인 비밀번호 원본
    lang = models.CharField(max_length=2, choices=Lang.choices, default=Lang.KO)
    lang_locked_at = models.DateTimeField(null=True, blank=True)  # 한 번 정하면 잠금

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "patient_code"
    REQUIRED_FIELDS = ["dob"]

    objects = PatientManager()

    class Meta:
        db_table = "patient"

    def __str__(self):
        return self.patient_code

    @property
    def lang_locked(self) -> bool:
        return self.lang_locked_at is not None