from django.urls import path

from . import schedule, views

urlpatterns = [
    path("today", views.index),
    path("schedule", schedule.index),  # R1 일정(주간)
    path("today/parse", views.preview),
    path("today/events", views.add),
    path("today/e/<str:key>", views.edit),
    path("today/e/<str:key>/delete", views.delete),
]
