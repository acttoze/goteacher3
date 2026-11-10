"""S1 예산 품목 취합 — 만들기(강조 카드·한 곳당 예산), 상품 주소로 채우기(shop), 합본 합계·초과, 에듀파인 엑셀(.xls).

불변식
- 서버 요청은 허용 목록 호스트·https만. 리다이렉트도 한 번씩 같은 검사(사설 IP·http·밖 호스트로 넘어가면 멈춘다). 테스트는 네트워크를 쓰지 않는다.
- 상품 주소 읽기는 예산 품목 취합의 받는 링크 안에서만(다른 취합·없는 링크 404), 저장하지 않는다.
- 에듀파인 엑셀은 원본 양식(fixtures/edufine/품목내역(통합).xls)과 시트 이름·머리글·서식·칸 너비가 같고, 수량·예상단가는 숫자 칸.
- 공간 격리: 다른 공간의 취합 키로 만들기·내보내기는 404.
"""
import email.message
import gzip
import io
import urllib.error
from pathlib import Path
from unittest import mock

import xlrd
from django.contrib.auth import get_user_model
from django.core.cache import cache, caches
from django.test import TestCase

from spaces.models import Member, School, Space

from . import shop
from .export import edufine_rows
from .models import Collection, Field, Row
from .presets import EDUFINE, PRESETS
from .submit import save_cell

FIX = Path(__file__).parent / "fixtures"
PAGES = {  # 2026-11-10에 받아 둔 상품 페이지(fixtures/shop/*.html.gz) → 주소, 기대 결과
    "iscream": ("https://i-screammall.co.kr/goods/detail/11468777",
                {"name": "업사이클링 환경교육 자개 키캡키링 만들기 키트 5인 set (수업자료 제공)", "spec": "", "unit": "", "price": 12000}),
    "daiso": ("https://www.daisomall.co.kr/pd/pdr/SCR_PDR_0001?pdNo=1030486",
              {"name": "[펫] 어항용흡착치어부화통", "spec": "", "unit": "", "price": 2000}),
    "11st": ("https://www.11st.co.kr/products/2114275539",
             {"name": "캐릭터 색연필/싸인펜모음 12색~24색까지 다양하게", "spec": "", "unit": "", "price": 4000}),
    "s2b": ("https://www.s2b.kr/S2BNCustomer/rema100No.do?forwardName=detail&f_re_estimate_code=201711036675272",
            {"name": "색연필 문화 목색연필 12색 틴케이스 세트", "spec": "HK-색연필-12c / 12자루입 / 유성 원목 색연필 / 나무 색연필",
             "unit": "개", "price": 5280}),
    "officedepot": ("https://www.officedepot.co.kr/item/itemView.do?itemId=515181",
                    {"name": "제트스트림(흑/0.7/SXN-150-07/1자루/MITSUBISHI)", "spec": "", "unit": "", "price": 1500}),
    "yes24": ("https://www.yes24.com/Product/Goods/197068949", {"name": "가호", "spec": "", "unit": "", "price": 16650}),
    "aladin": ("https://www.aladin.co.kr/shop/wproduct.aspx?ItemId=405346906",
               {"name": "[수입] 스트라빈스키 : 봄의 제전 & 시편 교향곡", "spec": "", "unit": "", "price": 24700}),
}


def page(name):
    return gzip.open(FIX / "shop" / f"{name}.html.gz").read()


class Resp:
    """urllib 응답 흉내(200)."""

    def __init__(self, body, charset=None):
        self.body = body
        self.headers = email.message.Message()
        self.headers["Content-Type"] = f"text/html; charset={charset}" if charset else "text/html"

    def read(self, n=-1):
        part, self.body = (self.body, b"") if n < 0 else (self.body[:n], self.body[n:])
        return part

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def redirect(url, to, code=302):
    h = email.message.Message()
    h["Location"] = to
    return urllib.error.HTTPError(url, code, "moved", h, io.BytesIO(b""))


class FakeOpener:
    """주소 → 응답(Resp) 또는 예외. 부른 주소를 적어 둔다."""

    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def open(self, req, timeout=None):
        self.seen.append(req.full_url)
        assert 0 < timeout <= shop.TIMEOUT
        self.timeouts = getattr(self, "timeouts", []) + [timeout]
        r = self.routes[req.full_url]
        if isinstance(r, Exception):
            raise r
        return r


class ShopParserTests(TestCase):
    def test_fixtures(self):
        for name, (url, want) in PAGES.items():
            with self.subTest(name):
                self.assertEqual(shop.parse(url, shop._decode(page(name), None)), want)

    def test_page_without_product(self):
        self.assertIsNone(shop.parse("https://www.11st.co.kr/products/1", "<html><title>없음</title></html>"))
        self.assertIsNone(shop.parse("https://i-screammall.co.kr/goods/detail/1", page("iscream").decode()))  # 다른 상품 번호


class ShopFetchTests(TestCase):
    def setUp(self):
        cache.clear()

    def run_with(self, routes, url):
        fake = FakeOpener(routes)
        with mock.patch.object(shop, "_opener", fake):
            return shop.lookup(url), fake.seen

    def test_refused_without_request(self):
        """허용 목록 밖·http·포트·계정 정보·IP 주소는 요청하지 않는다."""
        for url, word in [("https://www.coupang.com/vp/products/1", "쿠팡은"), ("https://m.coupang.com/x", "쿠팡은"),
                          ("https://item.gmarket.co.kr/Item?goodsCode=1", "G마켓"),
                          ("https://smartstore.naver.com/a/products/1", "네이버"),
                          ("http://www.11st.co.kr/products/2114275539", "읽을 수 없어요"),
                          ("https://www.11st.co.kr:8443/products/1", "읽을 수 없어요"),
                          ("https://u:p@www.11st.co.kr/products/1", "읽을 수 없어요"),
                          ("https://10.0.0.1/", "읽을 수 없어요"), ("https://127.0.0.1/", "읽을 수 없어요"),
                          ("https://www.11st.co.kr.evil.example/products/1", "읽을 수 없어요"),
                          ("https://evil.example/?u=https://www.11st.co.kr/", "읽을 수 없어요"),
                          ("file:///etc/passwd", "읽을 수 없어요"), ("javascript:alert(1)", "읽을 수 없어요"),
                          ("https://[::1]/", "읽을 수 없어요"), ("https://[zz/", "주소를 읽지 못했어요")]:
            with self.subTest(url):
                (item, msg), seen = self.run_with({}, url)
                self.assertIsNone(item)
                self.assertIn(word, msg)
                self.assertIn("직접", msg) if word != "주소를 읽지 못했어요" else None
                self.assertEqual(seen, [])

    def test_reads_allowed(self):
        url, want = PAGES["iscream"]
        (item, msg), seen = self.run_with({url: Resp(page("iscream"), "utf-8")}, url)
        self.assertEqual((item, seen), (want, [url]))
        self.assertIn("아이스크림몰에서 읽었어요", msg)

    def test_euc_kr_page(self):
        url, want = PAGES["s2b"]
        (item, _), _ = self.run_with({url: Resp(page("s2b"))}, url)  # 머리글에 charset이 없으면 meta로
        self.assertEqual(item, want)

    def test_redirect_to_private_or_outside_refused(self):
        url = PAGES["11st"][0]
        for to in ["https://10.0.0.1/admin", "https://169.254.169.254/latest/meta-data/", "http://www.11st.co.kr/products/2114275539",
                   "https://localhost/", "https://evil.example/", "//10.0.0.1/x"]:
            with self.subTest(to):
                cache.clear()
                (item, msg), seen = self.run_with({url: redirect(url, to)}, url)
                self.assertIsNone(item)
                self.assertIn("직접 적어 주세요", msg)
                self.assertEqual(seen, [url])  # 넘어간 곳은 요청하지 않는다

    def test_redirect_inside_allowlist_followed(self):
        url, (to, want) = "https://www.11st.co.kr/products/old", PAGES["11st"]
        (item, _), seen = self.run_with({url: redirect(url, "/products/2114275539", 301), to: Resp(page("11st"), "utf-8")}, url)
        self.assertEqual((item, seen), (want, [url, to]))

    def test_too_many_redirects(self):
        a, b = "https://www.11st.co.kr/products/a", "https://www.11st.co.kr/products/b"
        (item, msg), seen = self.run_with({a: redirect(a, b), b: redirect(b, a)}, a)
        self.assertIsNone(item)
        self.assertEqual(len(seen), shop.MAX_HOPS + 1)

    def test_size_limit_and_errors(self):
        url = PAGES["11st"][0]
        big = Resp(b"x" * (shop.MAX_BYTES + 1))
        for r in [big, urllib.error.URLError("timeout"), TimeoutError(), redirect(url, "", 403)]:
            with self.subTest(r):
                cache.clear()
                (item, msg), _ = self.run_with({url: r}, url)
                self.assertIsNone(item)
                self.assertIn("11번가에서 품명·가격을 읽지 못했어요", msg)

    def test_server_wide_slots(self):
        """동시 요청 자리(SLOTS)가 다 차 있으면 기다리지 않고 요청 없이 '직접 적어 주세요'. 끝나면 자리를 돌려준다."""
        url, want = PAGES["daiso"]
        slots = caches["ratelimit"]
        keys = [f"shop:slot:{i}" for i in range(shop.SLOTS)]
        try:
            for k in keys:
                self.assertTrue(slots.add(k, 1, 30))
            (item, msg), seen = self.run_with({url: Resp(page("daiso"))}, url)
            self.assertEqual((item, msg, seen), (None, shop.BUSY, []))
            slots.delete(keys[0])  # 하나 비면 읽는다
            (item, _), seen = self.run_with({url: Resp(page("daiso"))}, url)
            self.assertEqual((item, seen), (want, [url]))
            self.assertFalse(slots.add(keys[1], 1, 30))  # 다른 자리는 그대로
            self.assertTrue(slots.add(keys[0], 1, 30))  # 쓴 자리는 돌려줬다
        finally:
            slots.delete_many(keys)

    def test_slot_released_on_error(self):
        url = PAGES["11st"][0]
        self.run_with({url: urllib.error.URLError("x")}, url)
        self.assertTrue(all(caches["ratelimit"].add(f"shop:slot:{i}", 1, 1) for i in range(shop.SLOTS)))
        caches["ratelimit"].delete_many([f"shop:slot:{i}" for i in range(shop.SLOTS)])

    def test_slot_cache_down_refuses(self):
        url = PAGES["11st"][0]
        down = mock.MagicMock()
        down.__getitem__.return_value.add.side_effect = ConnectionError("down")
        with mock.patch.object(shop, "caches", down):
            (item, msg), seen = self.run_with({url: Resp(page("11st"))}, url)
        self.assertEqual((item, msg, seen), (None, shop.BUSY, []))

    def test_total_deadline_across_redirects(self):
        """전체 시간(리다이렉트 포함)이 DEADLINE을 넘으면 다음 요청을 하지 않는다. 소켓 시간은 남은 시간보다 길지 않다."""
        a, b = "https://www.11st.co.kr/products/a", PAGES["11st"][0]
        clock = iter([100.0, 100.0, 107.0, 108.5])  # 시작·첫 요청·둘째 요청 전(1초 남음)·읽기 전(지남)
        fake = FakeOpener({a: redirect(a, b), b: Resp(page("11st"))})
        with mock.patch.object(shop, "_opener", fake), mock.patch.object(shop.time, "monotonic", lambda: next(clock)):
            item, msg = shop.lookup(a)
        self.assertIsNone(item)
        self.assertIn("읽지 못했어요", msg)
        self.assertEqual(fake.seen, [a, b])
        self.assertEqual(fake.timeouts, [shop.TIMEOUT, 1.0])

    def test_total_deadline_while_reading(self):
        """조각으로 읽는 사이 시간이 지나면 멈춘다(느린 응답이 자리를 오래 잡지 않게)."""
        url = PAGES["11st"][0]
        t = [0.0]

        def tick():
            t[0] += 1.0
            return t[0]

        body = b"x" * (shop.CHUNK * 20)
        with mock.patch.object(shop.time, "monotonic", tick):
            (item, msg), seen = self.run_with({url: Resp(body)}, url)
        self.assertIsNone(item)
        self.assertEqual(seen, [url])

    def test_socket_closed_after_full_read(self):
        """http.client는 다 읽으면 소켓을 닫는다. 남은 시간을 다시 걸려다 실패하지 않는다(실제 요청에서 찾은 문제)."""
        import socket
        url, want = PAGES["11st"]
        resp = Resp(page("11st"), "utf-8")
        sock = socket.socket()
        sock.close()
        resp.fp = mock.Mock(raw=mock.Mock(_sock=sock))
        (item, _), _ = self.run_with({url: resp}, url)
        self.assertEqual(item, want)

    def test_cached(self):
        url, want = PAGES["daiso"]
        self.run_with({url: Resp(page("daiso"))}, url)
        (item, _), seen = self.run_with({}, url + "#review")  # 같은 주소(조각 무시)는 다시 요청하지 않는다
        self.assertEqual((item, seen), (want, []))


class BudgetBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.ub = User.objects.create(username="google:a"), User.objects.create(username="google:b")
        cls.ma = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="박서준", cls="4-2")
        cls.mb = Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)

    def setUp(self):
        cache.clear()
        self.client.force_login(self.ua)

    def make(self, space=None, budget=150000, title="2학기 학습준비물"):
        space = space or self.a
        c = Collection.objects.create(space=space, created_by=self.ma if space == self.a else self.mb, title=title,
                                      source=Collection.Source.BUDGET, unit_head=["학년반", "담임"], multi=True, budget=budget)
        for i, (n, k) in enumerate(PRESETS["supplies"]["fields"]):
            Field.objects.create(collection=c, pos=i, name=n, kind=k)
        Row.objects.create(collection=c, pos=0, unit="4-1", who="김가람", member=self.ma)
        Row.objects.create(collection=c, pos=1, unit="4-2", who="박서준", member=self.m2)
        return c

    def fill(self, row, *items):
        """items: (내용, 규격, 단위, 수량, 예상단가, 주소). 첫 물품은 명부 줄, 나머지는 같은 받는 곳 새 줄."""
        c = row.collection
        fs = {f.name: f for f in c.fields.all()}
        for i, item in enumerate(items):
            r = row if i == 0 else Row.objects.create(collection=c, pos=10 + i, unit=row.unit, who=row.who, member=row.member)
            for name, v in zip(EDUFINE + ["상품 주소"], item):
                if v:
                    save_cell(r, fs[name], v, row.who)


class BudgetCreateTests(BudgetBase):
    def post(self, space=None, **data):
        return self.client.post(f"/s/{(space or self.a).token}/c/new", data)

    def test_card_on_new_screen(self):
        r = self.client.get(f"/s/{self.a.token}/c/new")
        self.assertContains(r, 'class="card budget-card"')
        self.assertContains(r, "에듀파인 엑셀")
        self.assertContains(r, 'name="budget_go"')
        self.assertNotContains(r, "모으기")

    def test_create_with_budget(self):
        r = self.post(budget_go="1", budget="150,000원", budget_title="2학기 학습준비물", src="paste", due_d="2026-11-20")
        c = Collection.objects.get()
        self.assertRedirects(r, f"/s/{self.a.token}/c/{c.key}/", fetch_redirect_response=False)
        self.assertEqual((c.source, c.budget, c.title, c.multi, c.created_by), ("budget", 150000, "2학기 학습준비물", True, self.ma))
        self.assertEqual([f.name for f in c.fields.all()], EDUFINE + ["금액", "상품 주소"])
        self.assertEqual([r.unit for r in c.rows.all()], ["4-1", "4-2"])
        self.assertIsNotNone(c.due)

    def test_create_without_budget(self):
        self.post(budget_go="1", budget="")
        c = Collection.objects.get()
        self.assertEqual((c.budget, c.title), (None, "학급 예산 품목 취합"))

    def test_bad_budget(self):
        for v in ["십만", "-1", "0", "1.5", "99999999999"]:
            with self.subTest(v):
                r = self.post(budget_go="1", budget=v)
                self.assertEqual(r.status_code, 400)
                self.assertContains(r, "예산은 원 단위 숫자로", status_code=400)
        self.assertFalse(Collection.objects.exists())

    def test_other_space_404(self):
        self.assertEqual(self.post(self.b, budget_go="1", budget="1000").status_code, 404)
        self.assertFalse(Collection.objects.exists())

    def test_again_keeps_budget(self):
        c = self.make()
        r = self.client.post(f"/s/{self.a.token}/c/{c.key}/again")
        self.assertEqual(r.status_code, 303)
        c2 = Collection.objects.exclude(pk=c.pk).get()
        self.assertEqual((c2.source, c2.budget), ("budget", 150000))


class BudgetRecvTests(BudgetBase):
    def test_form_shows_budget_and_shop_input(self):
        c = self.make()
        r = self.client.get(f"/c/{c.link_token}/u/{c.rows.first().key}")
        self.assertContains(r, 'data-budget="150000"')
        self.assertContains(r, "150,000")
        self.assertContains(r, f'hx-post="/c/{c.link_token}/shop"')
        self.assertContains(r, "data-shop")

    def test_shop_fill(self):
        c = self.make()
        row = c.rows.first()
        url, want = PAGES["s2b"]
        with mock.patch.object(shop, "lookup", return_value=(want, "학교장터에서 읽었어요.")) as lk:
            r = self.client.post(f"/c/{c.link_token}/shop", {"line": row.key, "f0": "", "f1": "", "f2": "", "f3": "", "f4": "", "f6": url})
        lk.assert_called_once_with(url)
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('value="색연필 문화 목색연필 12색 틴케이스 세트"', body)
        self.assertIn('value="HK-색연필-12c / 12자루입 / 유성 원목 색연필 / 나무 색연필"', body)
        self.assertIn('name="f3" inputmode="decimal" autocomplete="off" value="1"', body)  # 수량 기본 1
        self.assertIn('value="5280"', body)
        self.assertIn(f'value="{row.key}"', body)
        self.assertIn("학교장터에서 읽었어요", body)
        self.assertFalse(c.rows.first().cells.exists())  # 저장하지 않는다

    def test_shop_fill_keeps_typed_quantity_and_unit_default(self):
        c = self.make()
        item = dict(PAGES["daiso"][1])
        with mock.patch.object(shop, "lookup", return_value=(item, "ok")):
            r = self.client.post(f"/c/{c.link_token}/shop", {"line": "", "f3": "5", "f2": "", "f6": PAGES["daiso"][0]})
        body = r.content.decode()
        self.assertIn('name="f3" inputmode="decimal" autocomplete="off" value="5"', body)
        self.assertIn('name="f2" autocomplete="off" value="개"', body)

    def test_shop_fill_failure_keeps_values(self):
        c = self.make()
        r = self.client.post(f"/c/{c.link_token}/shop", {"f0": "색종이", "f6": "https://www.coupang.com/vp/products/1"})
        body = r.content.decode()
        self.assertIn("쿠팡은 주소로 읽을 수 없어요", body)
        self.assertIn('value="색종이"', body)

    def test_shop_fill_only_budget_and_real_link(self):
        c = self.make()
        other = Collection.objects.create(space=self.a, title="참가", source=Collection.Source.SHEET, unit_head=["학년반", "담임"])
        with mock.patch.object(shop, "lookup") as lk:
            self.assertEqual(self.client.post(f"/c/{other.link_token}/shop", {"f0": "https://www.11st.co.kr/products/1"}).status_code, 404)
            self.assertEqual(self.client.post("/c/nolink/shop", {}).status_code, 404)
            self.assertEqual(self.client.get(f"/c/{c.link_token}/shop").status_code, 405)
        lk.assert_not_called()

    def test_shop_fill_foreign_line_key_dropped(self):
        c, other = self.make(), self.make(self.b)
        with mock.patch.object(shop, "lookup", return_value=(None, "x")):
            r = self.client.post(f"/c/{c.link_token}/shop", {"line": other.rows.first().key, "f6": "https://www.11st.co.kr/p"})
        self.assertNotIn(other.rows.first().key, r.content.decode())

    def test_shop_rate_limited(self):
        c = self.make()
        with mock.patch("collect.submit.allow", return_value=False), mock.patch.object(shop, "lookup") as lk:
            r = self.client.post(f"/c/{c.link_token}/shop", {"f6": PAGES["11st"][0]})
        lk.assert_not_called()
        self.assertContains(r, "너무 자주")

    def test_shop_per_minute_limit(self):
        """IP·링크당 분당 SHOP_PER_MIN번(시간당 상한과 함께). 넘으면 요청하지 않는다."""
        from .submit import SHOP_PER_MIN
        c = self.make()
        with mock.patch.object(shop, "lookup", return_value=(None, "x")) as lk:
            for _ in range(SHOP_PER_MIN):
                self.client.post(f"/c/{c.link_token}/shop", {"f6": "https://www.11st.co.kr/products/1?a"})
            r = self.client.post(f"/c/{c.link_token}/shop", {"f6": "https://www.11st.co.kr/products/1?b"})
        self.assertEqual(lk.call_count, SHOP_PER_MIN)
        self.assertContains(r, "너무 자주")

    def test_number_digits_capped(self):
        """숫자 칸은 정수 12자리·소수 4자리까지(엑셀 내보내기·계산이 다룰 크기)."""
        c = self.make()
        row = c.rows.first()
        base = {"line": [row.key], "f0": ["풀"], "f1": [""], "f2": ["개"], "f4": ["800"], "f6": [""]}
        r = self.client.post(f"/c/{c.link_token}/u/{row.key}", {**base, "f3": ["9" * 13]})
        self.assertContains(r, "숫자가 너무 커요", status_code=400)
        r = self.client.post(f"/c/{c.link_token}/u/{row.key}", {**base, "f3": ["1.23456"]})
        self.assertEqual(r.status_code, 400)
        r = self.client.post(f"/c/{c.link_token}/u/{row.key}", {**base, "f3": ["000" + "9" * 12]})
        self.assertEqual(r.status_code, 200)

    def test_submit_spec_optional_and_amount(self):
        """규격은 비워도 낸다. 금액 = 수량 × 예상단가(합본 계산)."""
        c = self.make()
        row = c.rows.first()
        r = self.client.post(f"/c/{c.link_token}/u/{row.key}", {"line": [row.key, ""], "f0": ["색종이", "풀"], "f1": ["", "35g"],
                                                                "f2": ["묶음", "개"], "f3": ["3", "10"], "f4": ["1,500", "800"],
                                                                "f6": ["", ""], "by": "김가람"})
        self.assertEqual(r.status_code, 200, r.content.decode()[:500])
        self.assertEqual(edufine_rows(c), [["색종이", "", "묶음", "3", "1500"], ["풀", "35g", "개", "10", "800"]])


class BudgetBoardTests(BudgetBase):
    def test_totals_and_over(self):
        c = self.make(budget=10000)
        r1, r2 = c.rows.all()
        self.fill(r1, ("색종이", "", "묶음", "3", "1500", "https://www.11st.co.kr/products/1"), ("풀", "", "개", "2", "800", ""))
        self.fill(r2, ("가위", "", "개", "10", "2000", ""))
        body = self.client.get(f"/s/{self.a.token}/c/{c.key}/").content.decode()
        self.assertIn("6,100", body)  # 4500 + 1600
        self.assertIn("3,900 남음", body)
        self.assertIn("20,000", body)
        self.assertIn("10,000 초과", body)
        self.assertIn("예산 초과 1반", body)
        self.assertIn("edufine.xls", body)

    def test_no_xls_button_for_other_collections(self):
        c = Collection.objects.create(space=self.a, created_by=self.ma, title="참가", source=Collection.Source.SHEET,
                                      unit_head=["학년반", "담임"])
        body = self.client.get(f"/s/{self.a.token}/c/{c.key}/").content.decode()
        self.assertNotIn("edufine.xls", body)
        self.assertNotIn('<th class="r">반 합계</th>', body)


class EdufineXlsTests(BudgetBase):
    def xls(self, c, space=None):
        return self.client.get(f"/s/{(space or c.space).token}/c/{c.key}/edufine.xls")

    def test_same_format_as_original(self):
        c = self.make()
        r1, r2 = c.rows.all()
        self.fill(r1, ("색종이", "15x15", "묶음", "3", "1500", "https://www.11st.co.kr/products/1"), ("풀", "", "개", "2", "800", ""))
        self.fill(r2, ("=SUM(A1)", "", "개", "1", "12000", ""))
        r = self.xls(c)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/vnd.ms-excel")
        self.assertIn("filename*=utf-8''2%ED%95%99%EA%B8%B0%20%ED%95%99%EC%8A%B5%EC%A4%80%EB%B9%84%EB%AC%BC_%ED%92%88%EB%AA%A9%EB%82%B4%EC%97%AD.xls",
                      r["Content-Disposition"])
        self.assertIn("no-store", r["Cache-Control"])
        got = xlrd.open_workbook(file_contents=r.content, formatting_info=True)
        want = xlrd.open_workbook(str(FIX / "edufine" / "품목내역(통합).xls"), formatting_info=True)
        self.assertEqual(got.biff_version, 80)  # 진짜 .xls(BIFF8)
        self.assertEqual(got.sheet_names(), want.sheet_names())
        gs, ws = got.sheet_by_index(0), want.sheet_by_index(0)
        self.assertEqual(gs.row_values(0), ws.row_values(0))
        self.assertEqual(gs.default_row_height, ws.default_row_height)
        for i in range(ws.ncols):
            self.assertEqual(gs.colinfo_map[i].width, ws.colinfo_map[i].width)
            gx, wx = got.xf_list[gs.cell_xf_index(0, i)], want.xf_list[ws.cell_xf_index(0, i)]
            gf, wf = got.font_list[gx.font_index], want.font_list[wx.font_index]
            self.assertEqual((gf.name, gf.height, gf.weight, gf.bold, gf.colour_index, gf.italic, gf.underline_type),
                             (wf.name, wf.height, wf.weight, wf.bold, wf.colour_index, wf.italic, wf.underline_type))
            for part in ("alignment", "border", "background"):
                g, w = dict(vars(getattr(gx, part))), dict(vars(getattr(wx, part)))
                if part == "border" and w["left_line_style"] == 0:  # 선 없는 변의 색은 보이지 않는다(xlwt는 0으로 쓴다, 원본은 8)
                    g.pop("left_colour_index"), w.pop("left_colour_index")
                self.assertEqual(g, w, (i, part))
            self.assertEqual(got.format_map[gx.format_key].format_str, want.format_map[wx.format_key].format_str)
            for ci in (8, 9, 10):
                self.assertEqual(got.colour_map[ci], want.colour_map[ci])
        # 데이터 줄: 합본 순서, 글자 3칸 + 숫자 2칸, 받는 곳·주소·꼬리말 없음, 수식으로 바뀌지 않음
        self.assertEqual(gs.nrows, 4)
        self.assertEqual(gs.ncols, 5)
        self.assertEqual([gs.row_values(i) for i in range(1, 4)],
                         [["색종이", "15x15", "묶음", 3.0, 1500.0], ["풀", "", "개", 2.0, 800.0], ["=SUM(A1)", "", "개", 1.0, 12000.0]])
        self.assertEqual([gs.cell_type(1, i) for i in range(5)], [xlrd.XL_CELL_TEXT] * 3 + [xlrd.XL_CELL_NUMBER] * 2)
        self.assertNotIn(b"11st", r.content)

    def test_only_budget_collections(self):
        c = Collection.objects.create(space=self.a, created_by=self.ma, title="참가", source=Collection.Source.SHEET,
                                      unit_head=["학년반", "담임"])
        self.assertEqual(self.xls(c).status_code, 404)

    def test_other_space_404(self):
        mine, theirs = self.make(), self.make(self.b)
        self.assertEqual(self.xls(theirs, space=self.a).status_code, 404)  # 남의 키를 내 공간 주소로
        self.assertEqual(self.xls(mine, space=self.b).status_code, 404)  # 회원 아닌 공간
        self.client.logout()
        self.assertNotEqual(self.xls(mine).status_code, 200)

    def test_huge_old_value_written_as_text(self):
        """제출 상한 전에 들어간 아주 큰 수도 500이 아니라 글자 칸으로."""
        c = self.make()
        self.fill(c.rows.first(), ("풀", "", "개", "9" * 400, "800", ""))
        r = self.xls(c)
        self.assertEqual(r.status_code, 200)
        gs = xlrd.open_workbook(file_contents=r.content).sheet_by_index(0)
        self.assertEqual((gs.cell_type(1, 3), gs.cell_value(1, 3)), (xlrd.XL_CELL_TEXT, "9" * 400))
        self.assertEqual(gs.cell_type(1, 4), xlrd.XL_CELL_NUMBER)

    def test_empty_collection_header_only(self):
        r = self.xls(self.make())
        gs = xlrd.open_workbook(file_contents=r.content).sheet_by_index(0)
        self.assertEqual((gs.nrows, gs.row_values(0)), (1, EDUFINE))


class OldSuppliesStillWork(BudgetBase):
    """S1 전에 만든 '2학기 학습준비물 신청'(PRESET, 물품명·단가·구입 링크)은 그대로 동작한다(예산 기능은 붙지 않음)."""

    def test_old_preset(self):
        c = Collection.objects.create(space=self.a, created_by=self.ma, title="2학기 학습준비물 신청", source=Collection.Source.PRESET,
                                      unit_head=["학년반", "담임"], multi=True)
        names = [("물품명", "text"), ("수량", "number"), ("단가", "number"), ("금액", "calc"), ("구입 링크", "url")]
        for i, (n, k) in enumerate(names):
            Field.objects.create(collection=c, pos=i, name=n, kind=k)
        row = Row.objects.create(collection=c, pos=0, unit="4-1", who="김가람", member=self.ma)
        r = self.client.post(f"/c/{c.link_token}/u/{row.key}", {"line": [row.key], "f0": ["풀"], "f1": ["2"], "f2": ["800"], "f4": [""]})
        self.assertEqual(r.status_code, 200)
        body = self.client.get(f"/s/{self.a.token}/c/{c.key}/").content.decode()
        self.assertIn("1,600", body)
        self.assertNotIn("edufine.xls", body)
        self.assertEqual(self.client.get(f"/s/{self.a.token}/c/{c.key}/edufine.xls").status_code, 404)
