from django.urls import path

from .views import LoginView, MeView, RefreshView

# 경로에 끝 슬래시를 붙이지 않습니다. 프론트도 슬래시 없이 호출해야 합니다.
urlpatterns = [
    path("auth/login", LoginView.as_view(), name="login"),
    path("auth/refresh", RefreshView.as_view(), name="refresh"),
    path("me", MeView.as_view(), name="me"),
]
