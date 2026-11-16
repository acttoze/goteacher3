"""V3 QA 보강: 구현 테스트(test_legacy_readonly)에 없는 경계.

- 본문 상한을 넘긴 업로드(_body_guard가 POST를 읽지 않고 뷰를 부르는 길)도 403, 데이터 그대로.
- 이관 학교의 개인(나만) 일정도 일정이므로 고치기·본문·지우기 403.
- 같은 이관 학교의 동학년 공간은 학교 공간이 아니므로 그대로 쓸 수 있다(현재 동작 고정).
"""
from django.test import override_settings

from legacy.models import LegacyMap
from notices.models import Notice
from notices.views import MAX_REQUEST
from spaces.models import Member, Space

from .models import Event, EventComment
from .test_legacy_readonly import HX, ON, LegacyBase


@ON
class QaBlockedEdges(LegacyBase):
    def test_oversized_upload_is_403_not_form(self):
        big = {"CONTENT_LENGTH": str(MAX_REQUEST + 1)}
        before = (self.e.files.count(), EventComment.objects.count(), Notice.objects.count())
        for url in (self.url(self.e, "/files"), self.url(self.e, "/comments"), f"{self.base}/board/new",
                    f"{self.base}/board/{self.n.key}/edit"):
            r = self.as_(self.boss).post(url, {"body": "x"}, headers=HX, **big)
            self.assertEqual(r.status_code, 403, url)
        self.assertEqual((self.e.files.count(), EventComment.objects.count(), Notice.objects.count()), before)

    def test_personal_event_in_migrated_school_is_locked(self):
        mine = Event.objects.create(space=self.hub, by=self.t41, date=self.e.date, title="혼자일", scope="me")
        c = self.as_(self.t41)
        for tail, data in (("", {"date": mine.date.isoformat(), "slot": "", "place": ""}), ("/body", {"body": "x"}),
                           ("/delete", {})):
            self.assertEqual(c.post(self.url(mine, tail), data, headers=HX).status_code, 403, tail)
        r = c.post(f"{self.base}/today/events", {"line": "오늘 2블록 혼자", "view": "week", "scope": "me"}, headers=HX)
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Event.objects.filter(pk=mine.pk, body="").exists())


@override_settings(LEGACY_READONLY=True)
class QaGradeSpaceOfMigratedSchool(LegacyBase):
    def test_grade_space_writes_still_open(self):
        g = Space.objects.create(school=self.school, grade=4, name="4학년")
        m = Member.objects.create(space=g, user=self.t41.user, name="사일", cls="4-1")
        self.assertTrue(LegacyMap.objects.filter(kind="school", obj_id=self.school.pk).exists())
        self.assertFalse(g.legacy_readonly)
        c = self.as_(m)
        r = c.post(f"/s/{g.token}/today/events", {"line": "오늘 2블록 협의"}, headers=HX)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(Event.objects.filter(space=g).exists())
        self.assertNotIn("예전 고티처에서", c.get(f"/s/{g.token}/schedule").content.decode())
