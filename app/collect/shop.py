"""S1 상품 주소 → 품명·규격·단위·예상단가(예산 품목 취합의 받는 화면).

서버가 남의 주소를 대신 여는 곳이라 열린 프록시·SSRF가 되지 않게(리드 결정 S):
- HOSTS(직접 받아 보고 품명·가격이 읽힌 쇼핑몰)의 호스트만, https만, 포트·계정 정보가 붙은 주소는 받지 않는다.
- 리다이렉트는 따라가지 않고 직접 받아 한 번씩 같은 검사를 한다(MAX_HOPS번까지).
- 소켓 동작마다 TIMEOUT초, 조회 한 건 전체(리다이렉트 포함) DEADLINE초, 응답 MAX_BYTES까지. 같은 주소 결과는 Django cache에 둔다.
- 서버 전체에서 동시에 SLOTS건만 요청한다(gunicorn 자리를 쇼핑몰 응답 기다리기로 다 묶지 않게). 자리는 프로세스끼리 같이 보는
  caches["ratelimit"](서버는 Redis)의 cache.add 슬롯. 자리가 없거나 캐시가 죽었으면 기다리지 않고 '직접 적어 주세요'.
- 받는 사람 링크 안(submit.shop_fill)에서만 부르고, IP·링크당 시도 수(분·시간)는 거기서 센다.
- 주소·응답은 로그에 남기지 않는다(예외 문구에도 넣지 않는다).
쿠팡·G마켓 등은 서버 요청을 막는다(403·429). 우회하지 않고 BLOCKED 이름으로 '직접 적어 주세요'를 안내한다.
"""
import hashlib
import http.client
import html
import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

from django.core.cache import cache, caches

HOSTS = {  # 호스트 → 쇼핑몰 이름(안내 문구). 2026-11-10 맥에서 상품 페이지를 받아 확인한 곳만
    "i-screammall.co.kr": "아이스크림몰",
    "www.i-screammall.co.kr": "아이스크림몰",
    "www.daisomall.co.kr": "다이소몰",
    "www.11st.co.kr": "11번가",
    "www.s2b.kr": "학교장터",
    "www.officedepot.co.kr": "오피스디포",
    "www.yes24.com": "예스24",
    "www.aladin.co.kr": "알라딘",
}
BLOCKED = {  # 서버 요청을 막는 곳(도메인 끝) → 이름. 요청하지 않고 안내만
    "coupang.com": "쿠팡", "gmarket.co.kr": "G마켓", "auction.co.kr": "옥션", "naver.com": "네이버 쇼핑",
    "kyobobook.co.kr": "교보문고",
}
TIMEOUT = 5
DEADLINE = 8
SLOTS = 2
SLOT_TTL = DEADLINE + 4  # 죽은 프로세스가 쥔 자리도 이만큼 지나면 풀린다
CHUNK = 64 * 1024
BUSY = "지금은 쇼핑몰 주소를 읽는 사람이 많아요. 품명·단가를 직접 적어 주세요."
MAX_BYTES = 3 * 1024 * 1024
MAX_HOPS = 3
CACHE_OK, CACHE_FAIL = 6 * 3600, 600
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
REDIRECTS = (301, 302, 303, 307, 308)
FAILED = "{mall}에서 품명·가격을 읽지 못했어요. 품명·단가를 직접 적어 주세요."


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # 3xx는 HTTPError로 올라온다 → fetch가 직접 검사하고 따라간다


_opener = urllib.request.build_opener(_NoRedirect)


class Refused(Exception):
    """허용 목록 밖·https 아님 등. 문구는 받는 사람에게 그대로 보여 준다(주소는 넣지 않는다)."""


def check(url):
    """요청해도 되는 주소면 그 쇼핑몰 이름, 아니면 Refused."""
    try:
        u = urlsplit(url.strip())
        host, port = (u.hostname or ""), u.port
    except ValueError:
        raise Refused("주소를 읽지 못했어요. 상품 페이지 주소를 그대로 붙여넣어 주세요.")
    for dom, name in BLOCKED.items():
        if host == dom or host.endswith("." + dom):
            raise Refused(f"{name}은 주소로 읽을 수 없어요. 품명·단가를 직접 적어 주세요.")
    if u.scheme != "https" or host not in HOSTS or port is not None or u.username or u.password:
        raise Refused("이 쇼핑몰은 주소로 읽을 수 없어요. 품명·단가를 직접 적어 주세요.")
    return HOSTS[host]


class _Late(OSError):
    pass


def _left(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise _Late("시간이 지났어요.")
    return left


def _read(resp, deadline):
    """MAX_BYTES+1까지 조각으로 읽는다. 조각마다 남은 시간을 소켓에 다시 건다(전체 DEADLINE을 넘기지 않게)."""
    sock = getattr(getattr(getattr(resp, "fp", None), "raw", None), "_sock", None)
    out = b""
    while len(out) <= MAX_BYTES:
        left = _left(deadline)
        if sock is not None and sock.fileno() != -1:  # 다 읽으면 http.client가 소켓을 닫는다
            sock.settimeout(min(TIMEOUT, left))
        part = resp.read(min(CHUNK, MAX_BYTES + 1 - len(out)))
        if not part:
            break
        out += part
    _left(deadline)
    return out


def fetch(url, deadline=None):
    """(마지막 주소, 글자). 리다이렉트마다 check를 다시 거친다. 실패는 Refused 또는 OSError(시간 초과 포함)."""
    deadline = deadline or time.monotonic() + DEADLINE
    for _ in range(MAX_HOPS + 1):
        check(url)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Language": "ko-KR,ko"})
        try:
            with _opener.open(req, timeout=min(TIMEOUT, _left(deadline))) as resp:
                body = _read(resp, deadline)
                charset = resp.headers.get_content_charset()
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location") if e.code in REDIRECTS else None
            e.close()
            if not loc:
                raise OSError("쇼핑몰이 응답하지 않았어요.") from None
            url = urljoin(url, loc)
            continue
        if len(body) > MAX_BYTES:
            raise OSError("페이지가 너무 커요.")
        return url, _decode(body, charset)
    raise OSError("다른 주소로 너무 여러 번 넘어갔어요.")


def _take_slot():
    """서버 전체 동시 요청 자리 하나(키) 또는 None. 캐시가 죽었으면 None(요청하지 않는다)."""
    try:
        slots = caches["ratelimit"]
        for i in range(SLOTS):
            key = f"shop:slot:{i}"
            if slots.add(key, 1, SLOT_TTL):
                return key
    except Exception:
        return None
    return None


def _give_slot(key):
    try:
        caches["ratelimit"].delete(key)
    except Exception:
        pass  # SLOT_TTL 뒤 저절로 풀린다


def _decode(body, charset):
    if not charset:
        m = re.search(rb'charset=["\']?([A-Za-z0-9_-]+)', body[:4096])
        charset = m.group(1).decode() if m else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _text(v):
    return re.sub(r"\s+", " ", html.unescape(str(v or ""))).strip()[:200]


def _price(v):
    try:
        n = int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _item(name, price, spec="", unit=""):
    name, price = _text(name), _price(price)
    if not name or price is None:
        return None
    return {"name": name, "spec": _text(spec), "unit": _text(unit)[:20], "price": price}


def _jsonld(page):
    """schema.org Product(다이소몰·11번가·알라딘·예스24)."""
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(m.group(1), strict=False)
        except (ValueError, RecursionError):
            continue
        for d in data if isinstance(data, list) else [data]:
            types = d.get("@type") if isinstance(d, dict) else None
            if "Product" not in (types if isinstance(types, list) else [types]):
                continue
            offers = d.get("offers")
            offers = offers[0] if isinstance(offers, list) and offers else offers
            it = _item(d.get("name"), offers.get("price") if isinstance(offers, dict) else None)
            if it:
                return it
    return None


def _meta(page, prop):
    m = re.search(r'<meta[^>]+property=["\']%s["\'][^>]+content=["\']([^"\']*)' % re.escape(prop), page)
    return m.group(1) if m else ""


def _recopick(page):
    """추천 위젯 메타(오피스디포)."""
    return _item(_meta(page, "recopick:title"), _meta(page, "recopick:price"))


def _js_str(s):
    """Next.js가 JS 문자열 안에 넣은 JSON 글자(\\"…\\") 한 겹을 푼다."""
    try:
        return json.loads(f'"{s}"')
    except ValueError:
        return s


def _iscream(page, url):
    """아이스크림몰(Next.js): 주소의 상품 번호 묶음에서 goodsNm·salePrc(판매가)."""
    m = re.search(r"/goods/detail/(\d+)", url)
    if not m:
        return None
    at = page.find(f'\\"goodsNo\\":\\"{m.group(1)}\\",\\"lgcGoodsNo')
    if at < 0:
        return None
    block = page[at:at + 20000]
    name = re.search(r'\\"goodsNm\\":\\"((?:[^"\\]|\\\\.)*?)\\"', block)
    price = re.search(r'\\"salePrc\\":(\d+)', block)
    return _item(_js_str(name.group(1)) if name else "", price.group(1) if price else None)


def _s2b(page):
    """학교장터 견적서: 물품명·제시 단가는 스크립트 변수, 규격·판매단위는 표."""
    name = re.search(r"var goodsName = '([^']*)'", page)
    price = re.search(r"var salesPrice = '(\d+)'", page)
    spec = re.search(r'<strong>모델명 / 규격</strong>.*?<td class="f12_c3">(.*?)</td>', page, re.S)
    unit = re.search(r'<strong>주문수량 / 판매단위</strong>.*?<td class="f12_c3">(.*?)</td>', page, re.S)
    unit = re.sub(r"<[^>]+>", " ", unit.group(1)).replace("&nbsp;", " ").rsplit("/", 1)[-1] if unit else ""
    return _item(name.group(1) if name else "", price.group(1) if price else None,
                 re.sub(r"<[^>]+>", " ", spec.group(1)) if spec else "", unit)


def parse(url, page):
    """쇼핑몰별 파서. 품명·가격이 다 있어야 결과(dict), 아니면 None."""
    host = urlsplit(url).hostname
    if host in ("i-screammall.co.kr", "www.i-screammall.co.kr"):
        return _iscream(page, url)
    if host == "www.s2b.kr":
        return _s2b(page)
    if host == "www.officedepot.co.kr":
        return _recopick(page)
    return _jsonld(page)


def lookup(url):
    """(결과 dict 또는 None, 안내 문구). 결과: name·spec·unit·price(원, 정수). 같은 주소는 캐시."""
    url = url.strip().split("#")[0]
    try:
        mall = check(url)
    except Refused as e:
        return None, str(e)
    key = "shop:" + hashlib.sha256(url.encode()).hexdigest()
    hit = cache.get(key)
    if hit is not None:
        return hit.get("item"), hit["msg"]
    slot = _take_slot()
    if slot is None:
        return None, BUSY  # 캐시에 남기지 않는다(자리가 나면 다시 읽는다)
    try:
        final, page = fetch(url)
        item = parse(final, page)
    except Refused as e:
        item, msg = None, str(e)
    except (OSError, ValueError, http.client.HTTPException):
        item, msg = None, FAILED.format(mall=mall)
    else:
        msg = f"{mall}에서 읽었어요. 수량을 고치고, 값이 다르면 직접 고쳐 주세요." if item else FAILED.format(mall=mall)
    finally:
        _give_slot(slot)
    cache.set(key, {"item": item, "msg": msg}, CACHE_OK if item else CACHE_FAIL)
    return item, msg
