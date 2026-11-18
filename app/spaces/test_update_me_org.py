"""T2 보정 QA: 설정 > 내 정보 저장(update_me)이 기관 회원 소속을 덮지 않고, 학교·동학년 동작은 그대로인지 경계 사례로 본다.
기존 테스트에 있는 것(기관 일반 요청 cls 단독, 학교 일반 요청 반 이동·이력)은 다시 쓰지 않는다."""
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, TestCase

from .models import Member, MemberLog, School, Space

User = get_user_model()
UNTIL = date(2099, 2, 28)
HX = {"HX-Request": "true"}


def member(space, sub, name, **kw):
    user = User.objects.create(username=f"google:{sub}", first_name=name)
    return Member.objects.create(space=space, user=user, name=name, **kw)


def as_(m):
    c = Client()
    c.force_login(m.user)
    return c


def place(m):
    m.refresh_from_db()
    return m.name, m.cls, m.group, m.position


class Base(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        p = mock.patch("spaces.neis.class_names", return_value=["1", "2", "3"])
        p.start()
        self.addCleanup(p.stop)

    def paid(self, name, code, kind, grade=0):
        school = School.objects.create(neis_atpt="E10", neis_code=code, name=name, plan="school", paid_until=UNTIL)
        return Space.objects.create(school=school, kind=kind, grade=grade, name=name)

    def me(self, space):
        return f"/s/{space.token}/settings/me"


class OrgKeepsPlaceTests(Base):
    def setUp(self):
        super().setUp()
        self.org = self.paid("학교마을협력과", "", Space.Kind.ORG)
        self.org.org_groups = [{"k": "t1", "n": "혁신팀"}]
        self.org.save(update_fields=["org_groups"])
        self.m = member(self.org, "kim", "김", cls="4-1", group="t1", position="주무관")
        self.c = as_(self.m)

    def test_htmx_same_school_shaped_class_keeps_group_and_position(self):
        r = self.c.post(self.me(self.org), {"cls": "4-1"}, headers=HX)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(place(self.m), ("김", "4-1", "t1", "주무관"))
        self.assertFalse(MemberLog.objects.in_space(self.org).exists())

    def test_plain_request_leaves_no_log(self):
        self.assertEqual(self.c.post(self.me(self.org), {"cls": "4-1"}).status_code, 302)
        self.assertEqual(place(self.m), ("김", "4-1", "t1", "주무관"))
        self.assertFalse(MemberLog.objects.in_space(self.org).exists())

    def test_every_school_class_shape_is_ignored(self):
        for cls in ("1-1", "6-3", "2-전담"):
            Member.objects.filter(pk=self.m.pk).update(cls=cls, group="t1", position="주무관")
            for headers in ({}, HX):
                self.c.post(self.me(self.org), {"cls": cls}, headers=headers)
                self.assertEqual(place(self.m), ("김", cls, "t1", "주무관"), (cls, headers))

    def test_member_without_group_does_not_get_a_grade_group(self):
        Member.objects.filter(pk=self.m.pk).update(group="", position="")
        for headers in ({}, HX):
            self.c.post(self.me(self.org), {"cls": "4-1"}, headers=headers)
            self.assertEqual(place(self.m), ("김", "4-1", "", ""), headers)

    def test_name_and_class_together_saves_name_only_changes_name(self):
        for name, headers in (("김하나", {}), ("김두울", HX)):
            before = MemberLog.objects.in_space(self.org).count()
            self.c.post(self.me(self.org), {"name": name, "cls": "4-1"}, headers=headers)
            self.assertEqual(place(self.m), (name, "4-1", "t1", "주무관"), headers)
            self.assertEqual(MemberLog.objects.in_space(self.org).count(), before + 1)  # 이름 이력만
            self.assertEqual(MemberLog.objects.in_space(self.org).latest("pk").after, name)

    def test_name_only_keeps_place(self):
        self.c.post(self.me(self.org), {"name": "김새이름"}, headers=HX)
        self.assertEqual(place(self.m), ("김새이름", "4-1", "t1", "주무관"))

    def test_cannot_move_to_another_class_shaped_value(self):
        r = self.c.post(self.me(self.org), {"cls": "5-2"}, headers=HX)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.c.post(self.me(self.org), {"cls": "5-2"}).status_code, 302)
        self.assertEqual(place(self.m), ("김", "4-1", "t1", "주무관"))
        self.assertFalse(MemberLog.objects.in_space(self.org).exists())

    def test_manager_is_treated_the_same(self):
        boss = member(self.org, "boss", "관리", cls="4-2", group="t1", position="과장", is_manager=True)
        as_(boss).post(self.me(self.org), {"cls": "4-2"}, headers=HX)
        self.assertEqual(place(boss), ("관리", "4-2", "t1", "과장"))

    def test_other_space_token_is_404_and_changes_nothing(self):
        other = self.paid("다른기관", "", Space.Kind.ORG)
        school = self.paid("먼초등학교", "7010001", Space.Kind.SCHOOL)
        for space in (other, school):
            self.assertEqual(self.c.post(self.me(space), {"name": "침입", "cls": "4-1"}, headers=HX).status_code, 404)
        self.assertEqual(place(self.m), ("김", "4-1", "t1", "주무관"))


class SchoolFollowsClassTests(Base):
    def setUp(self):
        super().setUp()
        self.hub = self.paid("새솔초등학교", "7531234", Space.Kind.SCHOOL)
        self.m = member(self.hub, "t", "김도윤", cls="5-1", group="g5", position="1반")
        self.c = as_(self.m)

    def test_htmx_move_follows_class_and_logs(self):
        r = self.c.post(self.me(self.hub), {"cls": "4-2"}, headers=HX)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(place(self.m), ("김도윤", "4-2", "g4", "2반"))
        log = MemberLog.objects.in_space(self.hub).get()
        self.assertEqual((log.by, log.name, log.before, log.after), ("김도윤", "김도윤", "5학년 1반", "4학년 2반"))

    def test_name_and_class_together_logs_both(self):
        self.c.post(self.me(self.hub), {"name": "김새롬", "cls": "4-2"}, headers=HX)
        self.assertEqual(place(self.m), ("김새롬", "4-2", "g4", "2반"))
        self.assertEqual(sorted(MemberLog.objects.in_space(self.hub).values_list("after", flat=True)),
                         sorted(["4학년 2반", "김새롬"]))

    def test_same_class_leaves_no_log(self):
        self.c.post(self.me(self.hub), {"cls": "5-1"}, headers=HX)
        self.assertEqual(place(self.m), ("김도윤", "5-1", "g5", "1반"))
        self.assertFalse(MemberLog.objects.in_space(self.hub).exists())

    def test_rejected_class_changes_nothing(self):
        r = self.c.post(self.me(self.hub), {"cls": "교무실"}, headers=HX)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(place(self.m), ("김도윤", "5-1", "g5", "1반"))
        self.assertFalse(MemberLog.objects.in_space(self.hub).exists())

    def test_office_staff_name_save_keeps_place(self):
        o = member(self.hub, "o", "교무샘", cls="교무실", group="office", position="교무")
        as_(o).post(self.me(self.hub), {"name": "교무쌤"}, headers=HX)
        self.assertEqual(place(o), ("교무쌤", "교무실", "office", "교무"))


class GradeSpaceUnchangedTests(Base):
    def setUp(self):
        super().setUp()
        school = School.objects.create(neis_atpt="J10", neis_code="7531235", name="한빛초등학교")
        self.g4 = Space.objects.create(school=school, grade=4, name="한빛초 4학년")
        self.m = member(self.g4, "a", "송민재", cls="4-1")
        self.c = as_(self.m)

    def test_class_and_name_save_without_group_position_or_log(self):
        for data, headers, want in (({"cls": "4-2"}, HX, ("송민재", "4-2", "", "")),
                                    ({"cls": "4-3"}, {}, ("송민재", "4-3", "", "")),
                                    ({"name": "송새롬"}, HX, ("송새롬", "4-3", "", "")),
                                    ({"name": "송하나", "cls": "4-전담"}, {}, ("송하나", "4-전담", "", ""))):
            self.c.post(self.me(self.g4), data, headers=headers)
            self.assertEqual(place(self.m), want, data)
        self.assertFalse(MemberLog.objects.exists())

    def test_same_class_same_name_is_still_refused(self):
        member(self.g4, "b", "정하은", cls="4-2")
        r = self.c.post(self.me(self.g4), {"name": "정하은", "cls": "4-2"}, headers=HX)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(place(self.m), ("송민재", "4-1", "", ""))

    def test_other_grade_class_is_refused(self):
        self.assertEqual(self.c.post(self.me(self.g4), {"cls": "5-1"}, headers=HX).status_code, 400)
        self.assertEqual(place(self.m), ("송민재", "4-1", "", ""))
