"""작업 큐(RQ). D3 리마인드·E2 한글 변환이 쓴다.

    from config.queue import enqueue
    enqueue(some_task, arg)          # 지금 실행
    get_queue().enqueue_in(timedelta(minutes=5), some_task)  # 나중에(worker가 --with-scheduler)

REDIS_URL이 비어 있으면(로컬·CI) 큐 없이 그 자리에서 실행하고 None을 돌려준다.
worker는 `cd app && python -m config.worker`(ops/compose.yml goteacher-worker).
"""
from django.conf import settings

QUEUE_NAME = "default"


def get_queue():
    from redis import Redis
    from rq import Queue

    return Queue(
        QUEUE_NAME,
        connection=Redis.from_url(settings.REDIS_URL),
        default_timeout=settings.RQ_JOB_TIMEOUT,
    )


def enqueue(func, *args, **kwargs):
    if not settings.REDIS_URL:
        func(*args, **kwargs)
        return None
    return get_queue().enqueue(func, *args, **kwargs)


def ping():
    """동작 확인용(부작용 없음): 서버에서 enqueue(ping) 뒤 job.result == "pong"."""
    return "pong"
