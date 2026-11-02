"""빈도 제한(G3 점검표 ⑥): 고정 창 카운터 하나.

    from config.ratelimit import allow, client_ip, TOO_MANY
    if not allow("submit", f"{client_ip(request)}:{c.link_token}", 30, 60):
        return ... status=429  # 화면마다 제 모양으로 TOO_MANY를 보여 준다

- 캐시는 settings.CACHES["ratelimit"](서버: 큐와 같은 Redis의 /1, 로컬: 프로세스 메모리). 키는 창 길이만큼 만료된다.
- 키에는 IP·토큰 원문 대신 해시를 넣는다. 캐시가 죽으면 막지 않는다(교사 실사용 우선).
- 한도는 교사 실사용을 막지 않게 넉넉히. 한 학교가 IP 하나(NAT)를 같이 쓰므로 대상(링크·사용자)과 묶어 센다.
"""
import hashlib
import ipaddress
import time

from django.core.cache import caches

TOO_MANY = "너무 자주 보냈어요. 잠시 뒤 다시 해 주세요."


def allow(scope, ident, limit, window, count=True):
    """scope·ident 조합이 이번 창(window초)에서 limit번 안이면 True(이번 요청도 센다).
    count=False면 세지 않고 보기만 한다(성공한 요청만 셀 때: 먼저 보고, 끝난 뒤 count=True로 한 번 센다)."""
    h = hashlib.sha256(f"{scope}:{ident}".encode()).hexdigest()[:32]
    key = f"rl:{scope}:{h}:{int(time.time() // window)}"
    cache = caches["ratelimit"]
    try:
        if not count:
            return (cache.get(key) or 0) < limit
        cache.add(key, 0, window + 1)
        return cache.incr(key) <= limit
    except Exception:  # 캐시 연결 끊김·방금 만료 등: 막지 않는다
        return True


def too_many(scope, ident, limit, window=60):
    """allow의 반대(넘었으면 True). 이번 요청도 센다."""
    return not allow(scope, ident, limit, window)


def client_ip(request):
    """Traefik 뒤 실제 주소: X-Forwarded-For의 마지막 값(Traefik이 붙인 것). 앞쪽 값은 사용자가 마음대로 넣을 수 있다.
    헤더가 없거나 주소 모양이 아니면 REMOTE_ADDR(로컬 실행)."""
    last = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[-1].strip()
    try:
        return str(ipaddress.ip_address(last))
    except ValueError:
        return request.META.get("REMOTE_ADDR", "")
