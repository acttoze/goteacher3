"""C6 합본 화면: 받는 사람 줄 × 칸, 내는 사람·제출 시각, 미제출 칩, 4초 폴링 부분 뷰.

불변식
- 합본(/s/<t>/c/<key>/)과 폴링 부분 뷰(/live)는 회원만, 다른 공간이면 404.
- 미제출 칩은 받는 사람 화면(submit._units)과 같은 기준이다(연수 이수는 다 채워야 냈음).
- 제출 값은 자동 이스케이프. 링크 칸은 http/https만 링크로 만든다.
- 주소에 순번 ID를 쓰지 않는다.
"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from spaces.models import Member, School, Space

from .models import Cell, Collection, Field, Row
from .submit import _units, save_cell


@override_settings(SITE_URL="https://t.goteacher.xyz")
class BoardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.ub = User.objects.create(username="google:a"), User.objects.create(username="google:b")
        cls.owner = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="박서준", cls="4-2")
        Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)
        cls.c = Collection.objects.create(space=cls.a, created_by=cls.owner, title="학예회 참가 인원",
                                          source=Collection.Source.SHEET, unit_head=["학년반", "담임"])
        cls.f_yes = Field.objects.create(collection=cls.c, pos=0, name="참가(명)", kind="number")
        cls.f_memo = Field.objects.create(collection=cls.c, pos=1, name="비고", kind="text")
        cls.r1 = Row.objects.create(collection=cls.c, pos=0, unit="4-1", who="김가람", member=cls.owner)
        cls.r2 = Row.objects.create(collection=cls.c, pos=1, unit="4-2", who="박서준", member=cls.m2)
        cls.r3 = Row.objects.create(collection=cls.c, pos=2, unit="4-3", who="이수빈")
        # 다른 공간
        cls.other = Collection.objects.create(space=cls.b, title="옆 학년", source=Collection.Source.SHEET,
                                              unit_head=["학년반", "담임"])
        Field.objects.create(collection=cls.other, pos=0, name="참가(명)", kind="number")

    def setUp(self):
        self.client.force_login(self.ua)

    def url(self, c=None, space=None, tail=""):
        c = c or self.c
        return f"/s/{(space or c.space).token}/c/{c.key}/{tail}"

    def submit(self, row, client=None, **data):
        return (client or Client()).post(f"/c/{self.c.link_token}/u/{row.key}", {"f0": "21", "f1": "", "by": "", **data})

    def test_detail_and_live_not_cached(self):
        """공용 PC에서 로그아웃 뒤 뒤로 가기로 합본이 보이지 않게 — 캐시 금지."""
        for tail in ("", "live"):
            r = self.client.get(self.url(tail=tail))
            self.assertEqual(r.status_code, 200)
            self.assertIn("no-store", r.get("Cache-Control", ""))

    # ---- 공간 격리
    def test_anonymous_redirected(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        r = self.client.get(self.url(tail="live"), HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r["HX-Redirect"], "/")

    def test_other_space_404(self):
        for tail in ("", "live"):
            self.assertEqual(self.client.get(self.url(self.other, tail=tail)).status_code, 404)
            # 내 공간 토큰 + 다른 공간의 모으기 키
            self.assertEqual(self.client.get(self.url(self.other, self.a, tail)).status_code, 404)
        self.client.force_login(self.ub)
        for tail in ("", "live"):
            self.assertEqual(self.client.get(self.url(tail=tail)).status_code, 404)

    def test_no_serial_id_in_page(self):
        r = self.client.get(self.url())
        body = r.content.decode()
        self.assertNotRegex(body, rf"/c/{self.c.pk}(?![\w-])")
        self.assertIn(f"/s/{self.a.token}/c/{self.c.key}/live", body)
        self.assertIn(f"https://t.goteacher.xyz/c/{self.c.link_token}", body)

    # ---- 두 PC 제출 → 폴링 부분 뷰에 반영
    def test_two_clients_submit_then_live_shows_both(self):
        r = self.client.get(self.url(tail="live"), HTTP_HX_REQUEST="true")
        self.assertContains(r, "<b>0</b> / 3반 냈어요", html=False)
        self.assertNotContains(r, "<html")  # 표·칩 영역만
        self.assertContains(r, 'hx-trigger="every 4s"')
        pc1, pc2 = Client(), Client()
        self.assertEqual(self.submit(self.r1, pc1, f0="21", by="김가람").status_code, 200)
        self.assertEqual(self.submit(self.r2, pc2, f0="19", f1="버스 1대", by="김가람").status_code, 200)
        r = self.client.get(self.url(tail="live"), HTTP_HX_REQUEST="true")
        self.assertContains(r, "<b>2</b> / 3반 냈어요")
        self.assertContains(r, ">21<")
        self.assertContains(r, ">19<")
        self.assertContains(r, "버스 1대")
        self.assertContains(r, ">40<")  # 숫자 칸 합계
        self.assertContains(r, "김가람 대신 냄")  # 4-2를 김가람이 대신 냄
        self.assertContains(r, "남은 반")
        self.assertContains(r, 'data-miss="4-3"')
        self.assertNotContains(r, 'data-miss="4-1"')

    def test_full_page_has_board_and_link(self):
        save_cell(self.r1, self.f_yes, "21", "김가람")
        r = self.client.get(self.url())
        self.assertContains(r, "학예회 참가 인원")
        self.assertContains(r, 'id="board"')
        self.assertContains(r, "받는 중")
        self.assertContains(r, "<b>1</b> / 3반 냈어요")
        self.assertContains(r, 'data-copy="#lk"')

    def test_all_done_state(self):
        for row in (self.r1, self.r2, self.r3):
            save_cell(row, self.f_yes, "1", "")
        r = self.client.get(self.url(tail="live"))
        self.assertContains(r, "다 걷힘")
        self.assertNotContains(r, "data-miss")

    # ---- 미제출 칩 = 받는 사람 화면 판정
    def test_miss_matches_submit_rule_matrix_partial(self):
        t = Collection.objects.create(space=self.a, title="연수", source=Collection.Source.PRESET,
                                      unit_head=["직위", "성명"])
        for i, n in enumerate(["청렴", "안전"]):
            Field.objects.create(collection=t, pos=i, name=n, kind="text")
        ta = Row.objects.create(collection=t, pos=0, unit="4학년 1반", who="김가람")
        tb = Row.objects.create(collection=t, pos=1, unit="4학년 2반", who="박서준")
        Client().post(f"/c/{t.link_token}/u/{ta.key}", {"f0": "A-1", "f1": ""})  # 부분 저장
        Client().post(f"/c/{t.link_token}/u/{tb.key}", {"f0": "B-1", "na1": "on"})  # 다 채움
        self.assertEqual(Cell.objects.filter(row=ta).count(), 1)
        r = self.client.get(self.url(t, tail="live"))
        undone = [u["row"].unit for u in _units(t) if not u["done"]]
        self.assertEqual(undone, ["4학년 1반"])
        self.assertContains(r, 'data-miss="4학년 1반"')
        self.assertNotContains(r, 'data-miss="4학년 2반"')
        self.assertContains(r, "<b>1</b> / 2명 냈어요")
        self.assertContains(r, "1/2칸")  # 부분 저장 표시

    def test_multi_lines_and_calc(self):
        m = Collection.objects.create(space=self.a, title="준비물", source=Collection.Source.PRESET,
                                      unit_head=["학년반", "담임"], multi=True)
        for i, (n, k) in enumerate([("물품명", "text"), ("수량", "number"), ("단가", "number"),
                                    ("금액", "calc"), ("구입 링크", "url")]):
            Field.objects.create(collection=m, pos=i, name=n, kind=k)
        mr = Row.objects.create(collection=m, pos=0, unit="4-1", who="김가람")
        Row.objects.create(collection=m, pos=1, unit="4-2", who="박서준")
        Client().post(f"/c/{m.link_token}/u/{mr.key}", {"line": ["", ""], "f0": ["풀", "가위"], "f1": ["2", "3"],
                                                        "f2": ["500", "1000"], "f4": ["https://shop.kr/a", ""]})
        r = self.client.get(self.url(m, tail="live"))
        self.assertContains(r, "가위")
        self.assertContains(r, "1,000")  # 금액 = 수량 × 단가
        self.assertContains(r, "4,000")  # 금액 합계
        self.assertContains(r, 'href="https://shop.kr/a"')
        self.assertContains(r, 'data-miss="4-2"')

    # ---- XSS
    def test_values_escaped_and_only_http_links(self):
        u = Collection.objects.create(space=self.a, title="링크", source=Collection.Source.SHEET,
                                      unit_head=["학년반", "담임"])
        uf = Field.objects.create(collection=u, pos=0, name="자료", kind="url")
        tf = Field.objects.create(collection=u, pos=1, name="메모", kind="text")
        ra = Row.objects.create(collection=u, pos=0, unit="4-1", who="<b>김</b>")
        rb = Row.objects.create(collection=u, pos=1, unit="4-2", who="박")
        save_cell(ra, uf, "javascript:alert(1)", "<script>x</script>")  # 서버 검사를 우회해 들어온 값이라도
        save_cell(ra, tf, "<img src=x onerror=alert(1)>", "")
        save_cell(rb, uf, "https://ok.kr/?a=1&b=<2>", "")
        r = self.client.get(self.url(u, tail="live"))
        body = r.content.decode()
        self.assertNotIn("<script>x", body)
        self.assertNotIn("<img src=x", body)
        self.assertNotIn("<b>김</b>", body)
        self.assertNotIn('href="javascript', body)
        self.assertIn("&lt;img src=x", body)
        self.assertIn('href="https://ok.kr/?a=1&amp;b=&lt;2&gt;"', body)
        self.assertIn('rel="noopener noreferrer"', body)


class BoardEdgeTests(BoardTests):
    """QA 추가: 토큰 바꿔 끼우기, 고치기 반영, 소수 합계, 링크 속성 탈출, 연수 '해당 없음'."""

    def test_anonymous_live_without_hx_redirects(self):
        self.client.logout()
        r = self.client.get(self.url(tail="live"))
        self.assertIn(r.status_code, (302, 204))
        self.assertNotContains(r, "학예회", status_code=r.status_code)

    def test_swapped_tokens_404(self):
        # 받는 링크 토큰을 모으기 키 자리에, 모으기 키를 공간 토큰 자리에
        for tail in ("", "live"):
            self.assertEqual(self.client.get(f"/s/{self.a.token}/c/{self.c.link_token}/{tail}").status_code, 404)
            self.assertEqual(self.client.get(f"/s/{self.c.key}/c/{self.c.key}/{tail}").status_code, 404)
            self.assertEqual(self.client.get(f"/s/{self.a.token}/c/{self.c.pk}/{tail}").status_code, 404)

    def test_live_is_get_only(self):
        self.assertEqual(self.client.post(self.url(tail="live")).status_code, 405)

    def test_resubmit_replaces_value_not_added(self):
        self.submit(self.r1, f0="21", by="")
        self.submit(self.r1, f0="30", by="")  # 고쳐서 다시 내기
        r = self.client.get(self.url(tail="live"))
        self.assertContains(r, ">30<")
        self.assertNotContains(r, ">21<")
        self.assertContains(r, "<b>1</b> / 3반 냈어요")
        self.assertContains(r, '<td class="r num">30</td>', html=False)  # 합계도 30(51 아님)

    def test_decimal_sum_and_comma(self):
        save_cell(self.r1, self.f_yes, "0.1", "")
        save_cell(self.r2, self.f_yes, "0.2", "")
        save_cell(self.r3, self.f_yes, "1000", "")
        r = self.client.get(self.url(tail="live"))
        self.assertContains(r, ">1,000<")
        self.assertContains(r, '<td class="r num">1,000.3</td>', html=False)

    def test_url_attribute_breakout_escaped(self):
        u = Collection.objects.create(space=self.a, title="링크2", source=Collection.Source.SHEET,
                                      unit_head=["학년반", "담임"])
        uf = Field.objects.create(collection=u, pos=0, name="자료", kind="url")
        ra = Row.objects.create(collection=u, pos=0, unit="4-1", who="김")
        rb = Row.objects.create(collection=u, pos=1, unit="4-2", who="박")
        save_cell(ra, uf, 'https://a.kr/"onmouseover="alert(1)', "")
        save_cell(rb, uf, " javascript:alert(1)//https://", "")
        body = self.client.get(self.url(u, tail="live")).content.decode()
        self.assertNotIn('"onmouseover="', body)
        self.assertIn('href="https://a.kr/&quot;onmouseover=&quot;alert(1)"', body)
        self.assertEqual(body.count("href="), 1)  # 앞에 공백 붙은 javascript는 링크가 아님

    def test_matrix_na_counts_as_done(self):
        t = Collection.objects.create(space=self.a, title="연수2", source=Collection.Source.PRESET,
                                      unit_head=["직위", "성명"])
        for i, n in enumerate(["청렴", "안전"]):
            Field.objects.create(collection=t, pos=i, name=n, kind="text")
        ta = Row.objects.create(collection=t, pos=0, unit="교사", who="김가람")
        Client().post(f"/c/{t.link_token}/u/{ta.key}", {"na0": "on", "na1": "on"})  # 모두 해당 없음
        r = self.client.get(self.url(t, tail="live"))
        self.assertEqual([u["done"] for u in _units(t)], [True])
        self.assertContains(r, "다 걷힘")
        self.assertContains(r, 'class="mx na"', count=2)
        self.assertNotContains(r, "<tfoot>")  # 연수 이수는 합계 줄 없음

    def test_multi_second_line_proxy_and_subtotal(self):
        m = Collection.objects.create(space=self.a, title="준비물2", source=Collection.Source.PRESET,
                                      unit_head=["학년반", "담임"], multi=True)
        for i, (n, k) in enumerate([("물품명", "text"), ("수량", "number"), ("단가", "number"), ("금액", "calc")]):
            Field.objects.create(collection=m, pos=i, name=n, kind=k)
        mr = Row.objects.create(collection=m, pos=0, unit="4-1", who="김가람")
        Client().post(f"/c/{m.link_token}/u/{mr.key}", {"line": ["", ""], "f0": ["풀", "자"], "f1": ["2", "0"],
                                                        "f2": ["500", "300"], "by": "박서준"})
        r = self.client.get(self.url(m, tail="live"))
        self.assertContains(r, ">자<")  # 둘째 줄도 표에
        self.assertContains(r, 'rowspan="2"')
        self.assertContains(r, '<td class="r num">1,000</td>', html=False)  # 금액 합계 = 2×500 + 0×300
        self.assertContains(r, "박서준 대신 냄")
        self.assertContains(r, "<b>1</b> / 1반 냈어요")

