from django.apps import AppConfig


class HwpfilesConfig(AppConfig):
    name = "hwpfiles"
    verbose_name = "한글 양식 파일"

    def ready(self):
        from . import retention

        retention.connect()  # 행이 지워지면 디스크 파일도(SourceFile·ResultFile·library.FormDoc)
