"""W3 시작 보드 QA 보강: 구현 테스트에 없는 경계(권한 전환·기관·재발급 범위·실제 로그인·소속 값이 권한이 아님)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache, caches
from django.test import Client
from django.utils import timezone

from accounts.views import GUEST_ORIGIN

from .merge import open_school
from .models import Member, School, Space
from .test_w3_board import SITE, Base, account, board_html, client, copies, grade_space, seat
from .views import INVITE_GROUP

User = get_user_model()
BOARD = 'id="startBoard"'


def id_account(username="newteacher", password="pw-for-w3-qa-1"):
    user = User(username=username, first_name="새선생")
    user.set_password(password)
    user.save()
    return user, password


class WhoEdges(Base):
    def test_every_manager_sees_it_and_demoted_does_not(self):
        Member.objects.filter(pk=self.peer.pk).update(is_manager=True)
        self.assertContains(client(self.peer.user).get(self.url), BOARD)
        Member.objects.filter(pk=self.peer.pk).update(is_manager=False)
        self.assertNotContains(client(self.peer.user).get(self.url), BOARD)

    def test_last_free_day_still_shows(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate())
        self.assertContains(self.c.get(self.url), BOARD)

    def test_operator_confirmed_school_loses_board(self):
        open_school(self.school, timezone.localdate() + timedelta(days=400), manager=self.lead, identity_checked=True)
        self.school.refresh_from_db()
        self.assertIsNone(self.school.self_opened_at)
        self.assertNotContains(self.c.get(self.url), BOARD)

    def test_unmerged_grade_space_has_no_board(self):
        m = Member.objects.in_space(self.g5).first()
        r = client(m.user).get(f"/s/{self.g5.token}/", follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, BOARD)

    def test_outsiders_cannot_open_the_school_page(self):
        other = School.objects.create(neis_atpt="J10", neis_code="7530010", name="남의초등학교")
        _, (stranger,) = grade_space(other, 2, "남의교사")
        for c in (client(stranger.user), client(account("무소속")), Client()):
            r = c.get(self.url)
            self.assertIn(r.status_code, (302, 403, 404))
            self.assertNotIn(BOARD, r.content.decode())

    def test_org_has_no_board_even_when_flagged(self):
        org = School.objects.create(neis_atpt="E10", neis_code="", name="협력과", plan="school",
                                    paid_until=timezone.localdate() + timedelta(days=30), self_opened_at=timezone.now())
        space = Space.objects.create(school=org, kind=Space.Kind.ORG, grade=0, name="협력과")
        boss = Member.objects.create(space=space, user=account("기관장"), name="기관장", cls="미배정", is_manager=True)
        c = client(boss.user)
        r = c.get(f"/s/{space.token}/school", follow=True)
        self.assertNotContains(r, BOARD)
        r = c.post(f"/s/{space.token}/invite", headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, BOARD)


class ReissueScope(Base):
    def post(self, c):
        return c.post(f"/s/{self.hub.token}/invite", headers={"HX-Request": "true"})

    def test_member_cannot_reissue_or_get_messages(self):
        r = self.post(client(self.peer.user))
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(BOARD, r.content.decode())

    def test_paid_school_reissue_has_no_board(self):
        School.objects.filter(pk=self.school.pk).update(self_opened_at=None)
        r = self.post(self.c)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, BOARD)

    def test_expired_school_reissue_has_no_board(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=timezone.localdate() - timedelta(days=1))
        r = self.post(self.c)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, BOARD)

    def test_old_cell_link_is_dead_after_reissue(self):
        old = self.hub.invite_token
        self.post(self.c)
        self.assertEqual(Client().get(f"/j/{old}?g=g4").status_code, 404)

    def test_reissue_keeps_numbers_and_fold_state(self):
        seat(self.hub, "강일학", "g1", "1-1", "1반")
        seat(self.hub, "교무님", "office", "교무실", "교무부장")
        text = self.post(self.c).content.decode()
        head = text[text.rindex("<details", 0, text.index(BOARD)):]
        self.assertNotIn(" open", head[:head.index(">")])
        self.assertIn('<b class="num">3</b>개', head)


class NumberEdges(Base):
    def cell_after(self, *people):
        for name, cls in people:
            seat(self.hub, name, "g4", cls)
        return self.cell("g4")

    def test_same_class_twice_counts_one_class_two_people(self):
        c = self.cell_after(("동반", "4-1"))
        self.assertEqual((c["filled"], c["total"], c["n"]), (2, 5, 3))

    def test_class_not_in_neis_is_not_filled(self):
        c = self.cell_after(("없는반", "4-9"))
        self.assertEqual((c["filled"], c["total"], c["n"]), (2, 5, 3))

    def test_school_group_order_keeps_all_ten(self):
        Space.objects.filter(pk=self.hub.pk).update(group_order="office,g6")
        got = [c["g"] for c in self.board()["cells"]]
        self.assertEqual(got[:2], ["office", "g6"])
        self.assertEqual(len(got), 10)

    def test_peer_line_absent_when_no_unmerged_grade(self):
        Space.objects.filter(pk=self.g5.pk).delete()
        self.assertNotIn("동학년 사용 중", board_html(self.c.get(self.url)))

    def test_messages_never_carry_peer_names(self):
        for msg in copies(board_html(self.c.get(self.url))):
            self.assertNotIn("박오학", msg)
            self.assertNotIn("최오학", msg)
            self.assertIn(SITE, msg)


class JoinEdges(Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        caches["ratelimit"].clear()

    def go(self, c, query="?g=office"):
        c.get(f"/j/{self.hub.invite_token}{query}")

    def picked(self, c):
        return c.get("/join").context.get("group")

    def login(self, c, username, password):
        r = c.post("/auth/login", {"username": username, "password": password, "next": "/join"})
        self.assertEqual(r.status_code, 302, r.content.decode()[:300])

    def test_post_never_falls_back_to_link_group(self):
        user = account("새선생")
        c = client(user)
        self.go(c)
        r = c.post("/join", {"role": "교무", "name": "새선생"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Member.objects.in_space(self.hub).filter(user=user).exists())

    def test_posted_group_wins_over_link_group(self):
        user = account("새선생")
        c = client(user)
        self.go(c)
        c.post("/join", {"group": "g4", "cls": "4-3", "name": "새선생"})
        self.assertEqual(Member.objects.in_space(self.hub).get(user=user).group, "g4")

    def test_cleared_after_joining(self):
        c = client(account("새선생"))
        self.go(c)
        c.post("/join", {"group": "office", "role": "교무", "name": "새선생"})
        self.assertNotIn(INVITE_GROUP, c.session)

    def test_link_of_another_space_clears_it(self):
        c = client(account("새선생"))
        self.go(c)
        c.get(f"/j/{self.g5.invite_token}")
        self.assertNotIn(INVITE_GROUP, c.session)

    def test_org_link_ignores_it(self):
        org = School.objects.create(neis_atpt="E10", neis_code="", name="협력과", plan="school",
                                    paid_until=timezone.localdate() + timedelta(days=30))
        space = Space.objects.create(school=org, kind=Space.Kind.ORG, grade=0, name="협력과")
        c = client(account("새선생"))
        c.get(f"/j/{space.invite_token}?g=g4")
        self.assertNotIn(INVITE_GROUP, c.session)

    def test_real_login_from_anonymous_keeps_it(self):
        user, pw = id_account()
        c = Client()
        self.go(c)
        self.login(c, user.username, pw)
        self.assertEqual(self.picked(c), "office")

    def test_guest_logging_in_keeps_it(self):
        """손님은 login이 세션을 비운다. 초대는 _enter가 이어 주므로 소속도 이어져야 한다."""
        guest = User.objects.create(username="guest:w3qa", first_name="손님")
        user, pw = id_account()
        c = client(guest)
        s = c.session
        s[GUEST_ORIGIN] = guest.pk
        s.save()
        self.go(c)
        self.login(c, user.username, pw)
        self.assertEqual(self.picked(c), "office")
