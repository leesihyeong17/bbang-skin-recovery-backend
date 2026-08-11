"""URL configuration for config project."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("care.urls")),
    # 앱이 준비되는 대로 아래를 열어주세요
    # path("api/v1/", include("checkins.urls")),
    # path("api/v1/", include("reports.urls")),
    # path("api/v1/", include("consult.urls")),
]

# 로컬 저장 파일(안내문 PDF · 수술확인서) 개발용 서빙.
# 사진은 S3라 여기 안 걸립니다.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
