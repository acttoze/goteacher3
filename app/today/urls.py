from django.urls import path

from . import print_view, schedule, views, week_paste

urlpatterns = [
    path("today", views.index),
    path("schedule", schedule.index),  # R1 일정(주간)
    path("schedule/print", print_view.sheet),  # H6 인쇄(학교 공간)
    path("schedule/paste", week_paste.page),  # H6 주간계획 표 붙여넣기(학교 공간)
    path("schedule/paste/preview", week_paste.preview),
    path("today/parse", views.preview),
    path("today/events", views.add),
    path("today/e/<str:key>", views.edit),
    path("today/e/<str:key>/delete", views.delete),
    path("today/duty", schedule.duty_add),  # 일정 학교 탭 복무 칸(QC 2026-11-06)
    path("today/duty/<str:key>/delete", schedule.duty_delete),
]
