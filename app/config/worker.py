"""RQ worker 진입점: cd app && python -m config.worker

작업 함수가 Django 모델을 쓰므로 django.setup() 뒤에 worker를 띄운다.
with_scheduler: enqueue_at/enqueue_in(예약 실행)을 이 worker가 처리한다(추가 의존성 없음).
"""
import os


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.conf import settings
    from rq import Worker

    from config.queue import get_queue

    if not settings.REDIS_URL:
        raise SystemExit("REDIS_URL 환경변수가 필요합니다.")
    queue = get_queue()
    Worker([queue], connection=queue.connection).work(
        with_scheduler=True, logging_level="INFO"
    )


if __name__ == "__main__":
    main()
