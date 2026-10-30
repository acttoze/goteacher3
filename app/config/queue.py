"""작업 큐(RQ). D3 리마인드·E2 한글 변환이 쓴다.

    from config.queue import enqueue
    enqueue(some_task, arg)          # 지금 실행(default 큐)
    enqueue(convert, pk, queue_name="hwp")   # 한글 변환 전용 큐(오래 걸려도 D3 리마인드를 막지 않게)
    get_queue().enqueue_in(timedelta(minutes=5), some_task)  # 나중에(worker가 --with-scheduler)

REDIS_URL이 비어 있으면(로컬·CI) 큐 없이 그 자리에서 실행하고 None을 돌려준다.
worker는 `cd app && python -m config.worker [큐 이름…]`(ops/compose.yml goteacher-worker: default, goteacher-hwp: hwp).
"""
from django.conf import settings

QUEUE_NAME = "default"
HWP_QUEUE = "hwp"


def get_queue(name=QUEUE_NAME):
    from redis import Redis
    from rq import Queue

    return Queue(
        name,
        connection=Redis.from_url(settings.REDIS_URL),
        default_timeout=settings.RQ_JOB_TIMEOUT,
    )


def enqueue(func, *args, queue_name=QUEUE_NAME, **kwargs):
    if not settings.REDIS_URL:
        func(*args, **kwargs)
        return None
    return get_queue(queue_name).enqueue(func, *args, **kwargs)


def ping():
    """동작 확인용(부작용 없음): 서버에서 enqueue(ping) 뒤 job.result == "pong"."""
    return "pong"
