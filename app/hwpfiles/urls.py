"""config/urls.py 에서 path("s/<str:space_token>/", include("hwpfiles.urls")) 로 연결한다.
space_token은 SpaceMiddleware가 빼서 request.space 로 바꾼다(회원만, 다른 공간 404)."""
from django.urls import path

from . import views

urlpatterns = [
    path("hwp/upload", views.upload),
    path("hwp/<str:key>/status", views.status),
]
