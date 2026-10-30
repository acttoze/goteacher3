"""D2 독촉: 미제출 칩을 누르면 그 반·사람용 문구, '한 번에 독촉 문구 복사'(prototype collectDetail #remindAll·.miss button).

불변식
- 독촉은 합본을 볼 수 있는 회원 누구나(prototype: c.mine 조건 없음). 다른 공간이면 404, 손님은 로그인으로.
- 문구는 prototype 그대로('티로드' → '고티처'), 링크는 SITE_URL. 칩 문구는 그 줄로 바로 가는 링크(/c/<링크>/u/<줄 키>).
- 낸 줄은 칩·한 번에 문구에 없다. 연수 이수는 안 한 연수만 적는다.
- 값·제목은 자동 이스케이프. 문구는 4초 폴링(live)에서도 같은 기준으로 갱신된다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from spaces.models import Member, School, Space

from .models import Collection, Field, Row
from .presets import PRESETS
from .submit import NA, save_cell

SEOUL = ZoneInfo("Asia/Seoul")
SITE = "https://t.goteacher.xyz"


@override_settings(SITE_URL=SITE)
class RemindTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        school = School.objects.create(neis_atpt="S10", neis_code="9999999", name="고티처초")
        cls.a = Space.objects.create(school=school, grade=4, name="4학년")
        cls.b = Space.objects.create(school=school, grade=5, name="5학년")
        User = get_user_model()
        cls.ua, cls.u2, cls.ub = (User.objects.create(username=f"google:{n}") for n in "a2b")
        cls.owner = Member.objects.create(space=cls.a, name="김가람", cls="4-1", user=cls.ua)
        cls.m2 = Member.objects.create(space=cls.a, name="박서준", cls="4-2", user=cls.u2)
        Member.objects.create(space=cls.b, name="박다온", cls="5-1", user=cls.ub)
        cls.c = Collection.objects.create(space=cls.a, created_by=cls.owner, title="학예회 참가 인원",
                                          source=Collection.Source.SHEET, unit_head=["학년반", "담임"])
        cls.f = Field.objects.create(collection=cls.c, pos=0, name="참가(명)", kind="number")
        cls.r1 = Row.objects.create(collection=cls.c, pos=0, unit="4-1", who="김가람", member=cls.owner)
        cls.r2 = Row.objects.create(collection=cls.c, pos=1, unit="4-2", who="박서준", member=cls.m2)
        cls.r3 = Row.objects.create(collection=cls.c, pos=2, unit="4-3", who="이수빈")
        cls.other = Collection.objects.create(space=cls.b, title="옆 학년", source=Collection.Source.SHEET,
                                              unit_head=["학년반", "담임"])

    def setUp(self):
        self.client.force_login(self.ua)

    def url(self, c=None, tail=""):
        c = c or self.c
        return f"/s/{c.space.token}/c/{c.key}/{tail}"

    def body(self, c=None, tail="", client=None):
        r = (client or self.client).get(self.url(c, tail))
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def link(self, c=None):
        return f"{SITE}/c/{(c or self.c).link_token}"

    # ---- 칩 하나
    def test_chip_copies_row_message(self):
        save_cell(self.r1, self.f, "21", "")
        body = self.body()
        msg = f"박서준 선생님, 「학예회 참가 인원」 4-2 칸이 아직 비어 있어요. 마감 미정\n{self.link()}/u/{self.r2.key}"
        self.assertIn(f'data-text="{msg}"', body)
        self.assertIn('data-done="4-2 박서준 쪽 문구를 복사했어요."', body)
        self.assertNotIn(f"/u/{self.r1.key}", body)  # 낸 반은 칩이 없다

    def test_chip_uses_due_text(self):
        Collection.objects.filter(pk=self.c.pk).update(due=datetime(2027, 11, 3, 17, 0, tzinfo=SEOUL))
        self.assertIn("4-2 칸이 아직 비어 있어요. 11/3(수) 17:00\n", self.body())

    def test_chip_is_button(self):
        body = self.body()
        self.assertIn('<button class="btn sec chip" type="button" data-miss="4-2"', body)

    def test_people_mode_label(self):
        c = Collection.objects.create(space=self.a, created_by=self.owner, title="연락처 확인",
                                      source=Collection.Source.SHEET, unit_head=["직위", "성명"])
        Field.objects.create(collection=c, pos=0, name="내선", kind="text")
        r = Row.objects.create(collection=c, pos=0, unit="4학년 2반", who="박서준", member=self.m2)
        body = self.body(c)
        self.assertIn(f"박서준 선생님, 「연락처 확인」 선생님 칸이 아직 비어 있어요. 마감 미정\n{self.link(c)}/u/{r.key}", body)
        self.assertIn('data-done="박서준 쪽 문구를 복사했어요."', body)
        self.assertIn("아직 다 못 채운 분: 박서준\n마감 미정 · 링크에서 내 칸만 채워 주세요.", body)

    def test_matrix_lists_missing_trainings(self):
        spec = PRESETS["training"]
        c = Collection.objects.create(space=self.a, created_by=self.owner, title=spec["title"],
                                      source=Collection.Source.PRESET, unit_head=spec["unit_head"])
        fs = [Field.objects.create(collection=c, pos=i, name=n, kind="text") for i, n in enumerate(["성희롱", "청렴", "안전"])]
        r = Row.objects.create(collection=c, pos=0, unit="4학년 1반", who="김가람", member=self.owner)
        save_cell(r, fs[0], "A-1", "")
        save_cell(r, fs[2], NA, "")  # 해당 없음은 한 것으로
        body = self.body(c)
        self.assertIn(f"김가람 선생님, 「{spec['title']}」 아직 이수번호가 없는 연수: 청렴 마감 미정\n", body)
        self.assertIn("덜 마친 사람 1 독촉 문구를 복사했어요.", body)
        self.assertIn("링크에서 내 줄만 채워 주세요.", body)

    # ---- 한 번에
    def test_all_message(self):
        save_cell(self.r1, self.f, "21", "")
        body = self.body()
        msg = (f"[고티처] 「학예회 참가 인원」 아직 다 못 채운 분: 4-2 박서준, 4-3 이수빈\n"
               f"마감 미정 · 링크에서 우리 반 칸만 채워 주세요.\n{self.link()}")
        self.assertIn(f'id="nudgeAll" data-done="미제출 2 독촉 문구를 복사했어요.">{msg}</p>', body)
        self.assertIn('data-copy="#nudgeAll"', body)

    def test_all_done_nothing_to_copy(self):
        for r in (self.r1, self.r2, self.r3):
            save_cell(r, self.f, "1", "")
        body = self.body()
        self.assertIn('id="nudgeAll" data-done="모두 냈어요. 독촉할 반이 없어요."></p>', body)
        self.assertNotIn("data-miss", body)

    def test_live_keeps_messages_fresh(self):
        body = self.body(tail="live")
        self.assertIn('id="nudgeAll"', body)
        save_cell(self.r2, self.f, "3", "")
        self.assertNotIn(f"/u/{self.r2.key}", self.body(tail="live"))

    def test_many_missing_show_18(self):
        rows = [Row(collection=self.c, pos=10 + i, unit=f"4-{10 + i}", who=f"선생{i}") for i in range(20)]
        Row.objects.bulk_create(rows)
        body = self.body()
        self.assertEqual(body.count("data-miss="), 18)
        self.assertIn("외 5개 반", body)
        self.assertIn("미제출 23 독촉", body)  # 한 번에 문구는 모두

    # ---- 권한·격리·이스케이프
    def test_other_member_can_remind(self):
        c2 = Client()
        c2.force_login(self.u2)
        self.assertIn('id="nudgeAll"', self.body(client=c2))

    def test_other_space_404(self):
        c2 = Client()
        c2.force_login(self.ub)
        for tail in ("", "live"):
            self.assertEqual(c2.get(self.url(tail=tail)).status_code, 404)
        self.assertEqual(self.client.get(f"/s/{self.a.token}/c/{self.other.key}/").status_code, 404)

    def test_anonymous_redirected(self):
        self.assertEqual(Client().get(self.url()).status_code, 302)

    def test_escaped(self):
        Collection.objects.filter(pk=self.c.pk).update(title='<b>"x"</b>')
        Row.objects.filter(pk=self.r3.pk).update(who="<i>a</i>")
        body = self.body()
        self.assertNotIn("<b>\"x\"</b>", body)
        self.assertNotIn("<i>a</i>", body)
        self.assertIn("&lt;i&gt;a&lt;/i&gt; 선생님", body)

    def test_first_message_still_copyable_without_card(self):
        """쪽지 카드는 design에 없다 → 받는 링크 카드의 단추로 합침(1536×750 첫 화면)."""
        body = self.body()
        self.assertNotIn("받는 선생님에게 갈 쪽지", body)
        self.assertIn('data-copy="#msg"', body)
        self.assertIn(f"{self.link()}\n\n보낸 사람: 김가람 (4-1)", body)

    # ---- QA 경계 사례
    def test_qa_due_text_has_weekday_like_prototype(self):
        """QA 실패(D2): prototype TR.dueText = c.due = TR.fmt(d)+' '+t → '11/3(수) 17:00'(요일 포함).
        구현도 같은 표기(_due_text, D1 안내 문구와 공유)."""
        Collection.objects.filter(pk=self.c.pk).update(due=datetime(2027, 11, 3, 17, 0, tzinfo=SEOUL))
        body = self.body()
        self.assertIn("4-2 칸이 아직 비어 있어요. 11/3(수) 17:00\n", body)
        self.assertIn("\n11/3(수) 17:00 · 링크에서 우리 반 칸만 채워 주세요.", body)

    def test_qa_matrix_untouched_row_lists_all(self):
        spec = PRESETS["training"]
        c = Collection.objects.create(space=self.a, created_by=self.owner, title=spec["title"],
                                      source=Collection.Source.PRESET, unit_head=spec["unit_head"])
        for i, n in enumerate(["성희롱", "청렴", "안전"]):
            Field.objects.create(collection=c, pos=i, name=n, kind="text")
        Row.objects.create(collection=c, pos=0, unit="4학년 2반", who="박서준", member=self.m2)
        body = self.body(c)
        self.assertIn("박서준 선생님, 「" + spec["title"] + "」 아직 이수번호가 없는 연수: 성희롱, 청렴, 안전 마감 미정\n", body)
        self.assertIn("아직 다 못 채운 분: 박서준\n마감 미정 · 링크에서 내 줄만 채워 주세요.", body)

    def test_qa_multi_label_unit_and_who(self):
        """prototype items: rowLabel = unit + who, missLabel '미제출 반'. 칸 문구는 hr이면 그 반."""
        c = Collection.objects.create(space=self.a, created_by=self.owner, title="물품 신청", multi=True,
                                      source=Collection.Source.SHEET, unit_head=["학년반", "담임"])
        Field.objects.create(collection=c, pos=0, name="물품", kind="text")
        r = Row.objects.create(collection=c, pos=0, unit="4-2", who="박서준", member=self.m2)
        body = self.body(c)
        self.assertIn(f"박서준 선생님, 「물품 신청」 4-2 칸이 아직 비어 있어요. 마감 미정\n{self.link(c)}/u/{r.key}", body)
        self.assertIn("[고티처] 「물품 신청」 아직 다 못 채운 분: 4-2 박서준\n", body)
        self.assertIn("미제출 반 1 독촉 문구를 복사했어요.", body)

    def test_qa_no_old_brand(self):
        self.assertNotIn("티로드", self.body())
        self.assertNotIn("티로드", self.body(tail="live"))
