"""W3 학교 시작 보드: 직접 연 학교의 관리자에게만, 소속 10칸과 칸별 초대 문구."""
import html
import re
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from collect.recipients import audience_rows
from legacy.models import LegacyMap

from .merge import open_free, open_school
from .models import Member, School, Space

User = get_user_model()
SITE = "https://t.example"
CLASSES = {4: ["1", "2", "3", "4", "5"], 5: ["1", "2", "3"]}  # 나머지 학년은 NEIS 없음


def names(atpt, code, grade, year=None):
    return CLASSES.get(grade, [])


def account(name):
    return User.objects.create(username=f"google:{name}", first_name=name)


def client(user):
    c = Client()
    c.force_login(user)
    return c


def grade_space(school, grade, *people):
    space = Space.objects.create(school=school, grade=grade, name=f"{grade}학년")
    return space, [Member.objects.create(space=space, user=account(n), name=n, cls=f"{grade}-{i}")
                   for i, n in enumerate(people, 1)]


def seat(hub, name, group, cls, position=""):
    return Member.objects.create(space=hub, user=account(name), name=name, group=group, cls=cls, position=position)


def board_html(response):
    text = response.content.decode()
    start = text.index('id="startBoard"')
    return text[text.rindex("<details", 0, start):text.index("</details>", start)]


def copies(fragment):
    return [html.unescape(t) for t in re.findall(r'data-text="([^"]*)"', fragment)]


@override_settings(SITE_URL=SITE)
class Base(TestCase):
    def setUp(self):
        patcher = mock.patch("spaces.neis.class_names", side_effect=names)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.school = School.objects.create(neis_atpt="J10", neis_code="7530001", name="새솔초등학교")
        self.g4, (self.lead, self.peer) = grade_space(self.school, 4, "김대표", "이동료")
        self.g5, _ = grade_space(self.school, 5, "박오학", "최오학")
        self.hub = open_free(self.g4, self.lead)
        self.school.refresh_from_db()
        self.lead.refresh_from_db()
        self.url = f"/s/{self.hub.token}/school"
        self.c = client(self.lead.user)
        self.link = f"{SITE}/j/{self.hub.invite_token}"

    def board(self):
        return self.c.get(self.url).context["board"]

    def cell(self, g):
        return next(x for x in self.board()["cells"] if x["g"] == g)


class Who(Base):
    def test_manager_sees_board_instead_of_alone_guide(self):
        Member.objects.in_space(self.hub).exclude(pk=self.lead.pk).delete()
        r = self.c.get(self.url)
        self.assertContains(r, 'id="startBoard"')
        self.assertNotContains(r, "아직 혼자예요")

    def test_on_both_tabs(self):
        self.assertContains(self.c.get(self.url + "?tab=duty"), 'id="startBoard"')

    def test_member_does_not_see_it(self):
        r = client(self.peer.user).get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'id="startBoard"')
        self.assertNotContains(r, "교무부장 선생님께 알리기")

    def _paid_school(self, code):
        school = School.objects.create(neis_atpt="J10", neis_code=code, name="정식초등학교")
        space, (m,) = grade_space(school, 3, f"정식{code}")
        hub = open_school(school, date(2030, 1, 1), manager=m)
        m.refresh_from_db()
        return school, hub, m

    def test_paid_school_keeps_old_screen(self):
        _, hub, m = self._paid_school("7530002")
        r = client(m.user).get(f"/s/{hub.token}/school")
        self.assertNotContains(r, 'id="startBoard"')
        self.assertContains(r, "아직 혼자예요")

    def test_migrated_school_has_no_board(self):
        school, hub, m = self._paid_school("7530003")
        LegacyMap.objects.create(kind="school", legacy_id=1, obj_id=school.pk)
        r = client(m.user).get(f"/s/{hub.token}/school")
        self.assertNotContains(r, 'id="startBoard"')

    def test_expired_school_is_fine_and_has_no_board(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        r = self.c.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'id="startBoard"')
        self.assertNotContains(r, "아직 혼자예요")


class Numbers(Base):
    def setUp(self):
        super().setUp()
        seat(self.hub, "정사반", "g4", "4-전담", "전담")
        seat(self.hub, "강일학", "g1", "1-1", "1반")
        seat(self.hub, "교무님", "office", "교무실", "교무부장")

    def test_ten_cells_in_school_order(self):
        cells = self.board()["cells"]
        self.assertEqual([c["label"] for c in cells], ["1학년", "2학년", "3학년", "4학년", "5학년", "6학년", "교무실", "행정실", "급식소", "기타/지원"])

    def test_head_counts(self):
        b = self.board()
        self.assertEqual((b["n"], b["started"]), (5, 3))
        self.assertEqual(b["end"], self.school.paid_until)
        h = board_html(self.c.get(self.url))
        self.assertIn("새솔초등학교 · 2028년 2월 29일까지 무료", h)

    def test_grade_cell_counts_classes_not_subject_teachers(self):
        c = self.cell("g4")
        self.assertEqual((c["filled"], c["total"], c["n"]), (2, 5, 3))
        self.assertIn("2/5반", board_html(self.c.get(self.url)))

    def test_no_neis_shows_people_only(self):
        c = self.cell("g1")
        self.assertEqual((c["total"], c["n"]), (0, 1))
        self.assertNotIn("/0반", board_html(self.c.get(self.url)))

    def test_neis_failure_does_not_break(self):
        from spaces.neis import Failed
        with mock.patch("spaces.neis.class_names", return_value=Failed()):
            r = self.c.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("/5반", board_html(r))

    def test_other_cells_are_people(self):
        self.assertEqual(self.cell("office")["n"], 1)
        self.assertEqual(self.cell("meal")["n"], 0)

    def test_no_percent_gauge_or_ranking(self):
        h = board_html(self.c.get(self.url))
        for word in ("%", "<progress", "순위", "1위"):
            self.assertNotIn(word, h)


class Peers(Base):
    def test_unmerged_grade_count_without_names(self):
        self.assertEqual(self.cell("g5")["peers"], 2)
        r = self.c.get(self.url)
        self.assertIn("동학년 사용 중 2명", board_html(r))
        self.assertNotContains(r, "박오학")
        self.assertNotContains(r, "최오학")

    def test_moved_grade_is_not_counted(self):
        self.assertEqual(self.cell("g4")["peers"], 0)

    def test_other_school_is_not_counted(self):
        other = School.objects.create(neis_atpt="J10", neis_code="7530009", name="다른초등학교")
        grade_space(other, 3, "남의선생")
        self.assertEqual(self.cell("g3")["peers"], 0)


class Messages(Base):
    def test_head_teacher_message(self):
        msg = self.board()["head_msg"]
        for part in ("새솔초등학교", "김대표", "고티처", "1년 무료", "2028년 2월 29일", "자동 결제", "다른 학년", "관리자", self.link):
            self.assertIn(part, msg)

    def test_cell_message_has_link_group_and_guide(self):
        msg = self.cell("g4")["msg"]
        for part in ("4학년", f"{self.link}?g=g4", "로그인", "소속", "취합 제출은", "로그인 없이"):
            self.assertIn(part, msg)
        self.assertIn(f"{self.link}?g=office", self.cell("office")["msg"])

    def test_plain_paragraphs_without_bullets_or_prices(self):
        b = self.board()
        for msg in [b["head_msg"], *[c["msg"] for c in b["cells"]]]:
            for line in msg.splitlines():
                self.assertNotRegex(line, r"^\s*([-*•·>—–]|\d+[.)])")
            self.assertNotRegex(msg, r"\d\s*(만\s*)?원|상당|특가|학교 확인|재직|메일")

    def test_page_has_every_message_as_copy_text(self):
        got = copies(board_html(self.c.get(self.url)))
        self.assertEqual(len(got), 11)
        self.assertEqual(got[0], self.board()["head_msg"])

    def test_values_are_escaped(self):
        School.objects.filter(pk=self.school.pk).update(name='<img src=x>"학교')
        Member.objects.filter(pk=self.lead.pk).update(name='<b>"김')
        h = board_html(self.c.get(self.url))
        self.assertNotIn("<img src=x>", h)
        self.assertNotIn('<b>"김', h)
        self.assertIn('<img src=x>"학교', copies(h)[0])

    def test_reissue_updates_messages_in_same_response(self):
        old = self.link
        r = self.c.post(f"/s/{self.hub.token}/invite", headers={"HX-Request": "true"})
        self.hub.refresh_from_db()
        new = f"{SITE}/j/{self.hub.invite_token}"
        self.assertNotEqual(new, old)
        text = r.content.decode()
        self.assertIn('hx-swap-oob="true"', text)
        got = copies(text[text.index('id="startBoard"') - 200:])
        self.assertEqual(len(got), 11)
        self.assertTrue(all(new in m and old not in m for m in got))

    def test_reissue_then_reload(self):
        self.c.post(f"/s/{self.hub.token}/invite")
        self.hub.refresh_from_db()
        self.assertIn(self.hub.invite_token, self.board()["head_msg"])


class Fold(Base):
    def is_open(self):
        h = board_html(self.c.get(self.url))
        return bool(re.match(r"<details[^>]*\sopen[\s>]", h))

    def test_two_cells_open_three_folded(self):
        seat(self.hub, "강일학", "g1", "1-1", "1반")  # 4학년과 합쳐 2칸
        self.assertTrue(self.is_open())
        seat(self.hub, "교무님", "office", "교무실", "교무부장")
        self.assertFalse(self.is_open())


class NextSteps(Base):
    def test_all_staff_collection_is_not_offered_while_roster_is_small(self):
        """전 교직원 취합의 줄은 만들 때의 명부로 고정된다: 2명이면 2줄이고, 나중에 들어온 사람은 줄이 없다."""
        self.assertEqual(len(audience_rows(self.hub, {"mode": "all"})), 2)
        self.assertNotIn("/c/new", board_html(self.c.get(self.url)))

    def test_points_to_existing_manager_path(self):
        self.assertIn("관리자", board_html(self.c.get(self.url)))
        self.assertIn("「관리」", board_html(self.c.get(self.url)))

    def test_forbidden_words(self):
        h = board_html(self.c.get(self.url))
        for word in ("학교 확인", "재직", "투어", "메일", "모으기", "티로드"):
            self.assertNotIn(word, h)


class Join(Base):
    def open(self, c, query):
        r = c.get(f"/j/{self.hub.invite_token}{query}")
        self.assertRedirects(r, "/join", fetch_redirect_response=False)
        return c.get("/join")

    def pressed(self, response):
        return re.findall(r'aria-pressed="true"\s+hx-get="/join" hx-vals=\'\{"group": "(\w+)"\}', response.content.decode())

    def test_group_is_preselected_with_its_classes(self):
        r = self.open(client(account("새선생")), "?g=g4")
        self.assertEqual(self.pressed(r), ["g4"])
        self.assertContains(r, "5반")

    def test_token_only_link_picks_nothing(self):
        self.assertEqual(self.pressed(self.open(client(account("새선생")), "")), [])

    def test_bad_values_are_ignored(self):
        for i, bad in enumerate(("?g=g9", "?g=", "?g=%3Cscript%3E", "?g=G4")):
            r = self.open(client(account(f"새선생{i}")), bad)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(self.pressed(r), [], bad)

    def test_survives_login(self):
        c = Client()
        r = self.open(c, "?g=office")
        self.assertContains(r, "로그인")
        c.force_login(account("새선생"))
        self.assertEqual(self.pressed(c.get("/join")), ["office"])

    def test_plain_link_clears_earlier_pick(self):
        c = client(account("새선생"))
        self.open(c, "?g=g4")
        self.assertEqual(self.pressed(self.open(c, "")), [])

    def test_person_can_change_it_and_it_gives_no_right(self):
        user = account("새선생")
        c = client(user)
        self.open(c, "?g=g4")
        self.assertEqual(self.pressed(c.get("/join", {"group": "g5"})), ["g5"])
        r = c.post("/join", {"group": "office", "role": "교무", "name": "새선생"})
        self.assertEqual(r.status_code, 302)
        m = Member.objects.in_space(self.hub).get(user=user)
        self.assertEqual((m.group, m.is_manager), ("office", False))

    def test_grade_space_invite_ignores_it(self):
        r = client(account("새선생")).get(f"/j/{self.g5.invite_token}?g=g4", follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'hx-vals=\'{"group"')
