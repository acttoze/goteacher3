"""config/urls.py 에서 path("s/<str:space_token>/", include("repairs.urls")) 로 연결한다."""
from django.urls import path

from . import vendor, views

urlpatterns = [
    path("it", views.it_index),  # 정보수리방(정보 요청만). 쓰기·처리는 아래 repairs/ 주소를 함께 쓴다
    path("it/<str:key>", views.it_detail),
    path("repairs", views.index),
    path("repairs/new", views.new),
    path("repairs/vendor", vendor.issue),  # K2 업체 계정(아래 <key>보다 앞에)
    path("repairs/vendor/link", vendor.link),
    path("repairs/vendor/revoke", vendor.revoke),
    path("repairs/vendor/log", vendor.access_log),
    path("repairs/vendor/preview", vendor.preview),
    path("repairs/<str:key>", views.detail),
    path("repairs/<str:key>/state", views.state),
    path("repairs/<str:key>/edit", views.edit),
    path("repairs/<str:key>/delete", views.delete),
    path("repairs/<str:key>/photo", views.photo_file),
    path("repairs/<str:key>/photo/delete", views.delete_photo),
    path("school/members/<str:key>/repair", views.set_handler),
]
