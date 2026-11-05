"""H1 학교 플랜 켜기: 동학년 합치기·옛 주소 넘기기·합친 뒤 R1 화면. NEIS는 목(5개 반)."""
import shutil
import tempfile
from datetime import date
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.files.base import ContentFile
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from collect.models import Cell, Collection, Field, Notification, PushSubscription, Row
from hwpfiles.models import ResultFile, SourceFile
from today.models import Event

from . import merge, views
from .merge import open_school
from .models import Member, School, Space, absorb_guest
from .views import INVITE

User = get_user_model()
UNTIL = date(2027, 2, 28)


def fill(space, member, tag):
    """공간 하나에 FK 모델을 한 건씩. 파일은 실제로 저장한다(합친 뒤 내려받기 확인)."""
    c = Collection.objects.create(space=space, created_by=member, title=f"{tag} 취합", source="sheet",
                                  unit_head=["학년반", "담임"])
    f = Field.objects.create(collection=c, pos=0, name="인원", kind="number")
    row = Row.objects.create(collection=c, pos=0, unit=member.cls, who=member.name, member=member)
    Cell.objects.create(row=row, field=f, value="3", by=member.name)
    Event.objects.create(space=space, by=member, date=timezone.localdate(), title=f"{tag} 일정")
    Notification.objects.create(space=space, member=member, row=row, kind="d1", day=date(2026, 11, 3),
                                text="마감", link=f"/c/{c.link_token}/u/{row.key}")
    Notification.objects.create(space=space, member=member, kind="seat", day=date(2026, 11, 3),
                                text="새 기기", link=f"/s/{space.token}/")
    PushSubscription.objects.create(space=space, member=member, endpoint=f"https://push.example/{tag}",
                                    p256dh="p", auth="a")
    sf = SourceFile.objects.create(space=space, created_by=member, name=f"{tag}.hwpx", kind="hwpx", size=5, stored=5,
                                   status="done", collection=c)
    sf.original.save("x", ContentFile(f"{tag}-src".encode()))
    rf = ResultFile.objects.create(space=space, source=sf, collection=c, created_by=member, fmt="hwpx", status="done",
                                   stored=5)
    rf.hwpx.save("hwpx", ContentFile(f"{tag}-out".encode()))
    return c, row, sf, rf


class MergeBase(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        p = mock.patch("spaces.neis.class_names", return_value=["1", "2", "3", "4", "5"])
        p.start()
        self.addCleanup(p.stop)
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        o = override_settings(MEDIA_ROOT=media)
        o.enable()
        self.addCleanup(o.disable)

        self.school = School.objects.create(neis_atpt="J10", neis_code="7531234", name="새솔초등학교")
        self.g4 = Space.objects.create(school=self.school, grade=4, name="새솔초 4학년")
        self.g5 = Space.objects.create(school=self.school, grade=5, name="새솔초 5학년")
        self.u4 = User.objects.create(username="google:4", first_name="송민재")
        self.u4b = User.objects.create(username="google:42", first_name="정하은")
        self.u5 = User.objects.create(username="google:5", first_name="김도윤")
        self.m4 = Member.objects.create(space=self.g4, user=self.u4, name="송민재", cls="4-1")
        self.m4b = Member.objects.create(space=self.g4, user=self.u4b, name="정하은", cls="4-2")
        self.m5 = Member.objects.create(space=self.g5, user=self.u5, name="김도윤", cls="5-1")
        self.c4, self.r4, self.sf4, self.rf4 = fill(self.g4, self.m4, "사학년")
        self.c5, self.r5, self.sf5, self.rf5 = fill(self.g5, self.m5, "오학년")

        # 다른 학교(그대로 있어야 한다)
        self.far_school = School.objects.create(neis_atpt="B10", neis_code="7010001", name="먼초등학교")
        self.far = Space.objects.create(school=self.far_school, grade=4, name="먼초 4학년")
        self.far_user = User.objects.create(username="google:far", first_name="다른학교")
        self.far_member = Member.objects.create(space=self.far, user=self.far_user, name="다른학교", cls="4-1")
        self.far_c, *_ = fill(self.far, self.far_member, "먼학교")

    def snapshot(self):
        """학교 전체 건수. 합치기 전후로 같아야 한다(공간만 바뀐다)."""
        spaces = self.school.spaces.all()
        return {m.__name__: m.objects.filter(space__in=spaces).count()
                for m in (Member, Collection, Event, Notification, PushSubscription, SourceFile, ResultFile)}


class OpenSchoolTests(MergeBase):
    def test_plan_on_makes_one_school_space_with_grade_zero(self):
        hub = open_school(self.school, UNTIL, self.m4)
        self.school.refresh_from_db()
        self.assertEqual((self.school.plan, self.school.paid_until), ("school", UNTIL))
        self.assertEqual((hub.kind, hub.grade, hub.merged_into_id), ("school", 0, None))
        self.assertEqual(hub.name, "새솔초등학교")
        self.assertEqual(Space.objects.filter(school=self.school, kind="school").count(), 1)

    def test_every_space_fk_moved_counts_match(self):
        before = self.snapshot()
        hub = open_school(self.school, UNTIL, self.m4)
        self.assertEqual(self.snapshot(), before)
        for fk in merge._fks_to(Space):  # 앞으로 생길 모델까지: 옛 공간에 남은 FK가 하나도 없어야 한다
            if fk.model is Space:
                continue
            for old in (self.g4, self.g5):
                self.assertFalse(fk.model._base_manager.filter(**{fk.name: old}).exists(), fk)
        self.assertEqual(Member.objects.in_space(hub).count(), 3)
        self.assertEqual(Collection.objects.in_space(hub).count(), 2)
        self.g4.refresh_from_db()
        self.assertEqual(self.g4.merged_into, hub)

    def test_group_filled_from_grade_and_keys_kept(self):
        open_school(self.school, UNTIL, self.m4)
        self.m4.refresh_from_db()
        self.m5.refresh_from_db()
        self.assertEqual((self.m4.group, self.m5.group), ("g4", "g5"))
        self.assertEqual(self.m4.cls, "4-1")

    def test_other_school_untouched(self):
        before = list(Collection.objects.filter(space=self.far).values_list("pk", "space_id"))
        open_school(self.school, UNTIL, self.m4)
        self.far.refresh_from_db()
        self.far_member.refresh_from_db()
        self.far_school.refresh_from_db()
        self.assertEqual((self.far.merged_into_id, self.far.kind, self.far_member.group, self.far_school.plan),
                         (None, "grade", "", "free"))
        self.assertEqual(list(Collection.objects.filter(space=self.far).values_list("pk", "space_id")), before)

    def test_twice_is_same_and_sweeps_late_rows(self):
        hub = open_school(self.school, UNTIL, self.m4)
        before = self.snapshot()
        spaces = sorted(self.school.spaces.values_list("pk", "merged_into_id"))
        self.assertEqual(open_school(self.school, UNTIL, self.m4).pk, hub.pk)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(self.school.spaces.values_list("pk", "merged_into_id")), spaces)
        # 합치는 순간 옛 공간 화면에서 막 쓴 일정·새로 생긴 동학년도 다시 돌리면 들어온다
        late = Event.objects.create(space=self.g4, date=timezone.localdate(), title="늦게 쓴 일정")
        g2 = Space.objects.create(school=self.school, grade=2, name="새솔초 2학년")
        m2 = Member.objects.create(space=g2, name="늦은 선생님", cls="2-1")
        open_school(self.school, UNTIL, self.m4)
        late.refresh_from_db()
        m2.refresh_from_db()
        g2.refresh_from_db()
        self.assertEqual((late.space_id, m2.space_id, m2.group, g2.merged_into_id), (hub.pk, hub.pk, "g2", hub.pk))

    def test_failure_rolls_everything_back(self):
        before = self.snapshot()
        real, calls = merge._merge_one, []

        def boom(old, hub):
            real(old, hub)
            calls.append(old)
            if len(calls) == 2:  # 두 번째 동학년까지 옮긴 뒤 실패
                raise RuntimeError("중간 실패")

        with mock.patch("spaces.merge._merge_one", side_effect=boom), self.assertRaises(RuntimeError):
            open_school(self.school, UNTIL, self.m4)
        self.assertEqual(len(calls), 2)
        self.school.refresh_from_db()
        self.assertEqual((self.school.plan, self.school.paid_until), ("free", None))
        self.assertFalse(Space.objects.filter(school=self.school, kind="school").exists())
        self.assertFalse(self.school.spaces.exclude(merged_into=None).exists())
        self.assertEqual(self.snapshot(), before)
        self.m4.refresh_from_db()
        self.assertEqual((self.m4.space_id, self.m4.group), (self.g4.pk, ""))

    def test_same_user_in_two_grades_becomes_one_seat(self):
        """손님을 구글에 이으면(absorb_guest) 한 사용자가 두 동학년에 있을 수 있다."""
        guest = User.objects.create(username="guest:x", first_name="김도윤")
        dup = Member.objects.create(space=self.g4, user=guest, name="김도윤", cls="4-3")
        c, row, sf, rf = fill(self.g4, dup, "겹친자리")
        absorb_guest(guest, self.u5)
        dup.refresh_from_db()
        self.assertEqual(dup.user, self.u5)

        hub = open_school(self.school, UNTIL, self.m5)
        seats = Member.objects.in_space(hub).filter(user=self.u5)
        self.assertEqual(seats.count(), 1)
        keep = seats.get()
        self.assertTrue(keep.is_manager)
        c.refresh_from_db()
        row.refresh_from_db()
        sf.refresh_from_db()
        rf.refresh_from_db()
        self.assertEqual((c.created_by, row.member, sf.created_by, rf.created_by), (keep, keep, keep, keep))
        self.assertEqual(Notification.objects.filter(row=row).get().member, keep)
        self.assertEqual(Event.objects.get(title="겹친자리 일정").by, keep)  # related_name="+" FK
        self.assertEqual(PushSubscription.objects.get(endpoint="https://push.example/겹친자리").member, keep)
        self.assertEqual(Notification.objects.in_space(hub).filter(member__isnull=True).count(), 0)
        self.assertEqual(Member.objects.in_space(hub).count(), 3)

    def test_manager_dropped_as_duplicate_passes_to_kept_seat(self):
        dup = Member.objects.create(space=self.g5, name="겹침", cls="5-2")
        Member.objects.filter(pk=dup.pk).update(user=self.u4)
        dup.refresh_from_db()
        hub = open_school(self.school, UNTIL, dup)
        self.assertTrue(Member.objects.in_space(hub).get(user=self.u4).is_manager)

    def test_null_user_seats_are_kept(self):
        Member.objects.create(space=self.g4, user=None, name="빈자리1", cls="4-3")
        Member.objects.create(space=self.g5, user=None, name="빈자리2", cls="5-3")
        hub = open_school(self.school, UNTIL, self.m4)
        self.assertEqual(Member.objects.in_space(hub).filter(user=None).count(), 2)

    def test_manager_must_be_in_this_school(self):
        with self.assertRaises(ValueError):
            open_school(self.school, UNTIL, self.far_member)
        self.school.refresh_from_db()
        self.assertEqual(self.school.plan, "free")

    def test_stored_links_point_at_new_space(self):
        hub = open_school(self.school, UNTIL, self.m4)
        seat = Notification.objects.in_space(hub).filter(kind="seat", member=self.m4).get()
        self.assertEqual(seat.link, f"/s/{hub.token}/")
        far = Notification.objects.in_space(self.far).get(kind="seat")
        self.assertEqual(far.link, f"/s/{self.far.token}/")

    def test_only_one_school_space_per_school_by_constraint(self):
        open_school(self.school, UNTIL, self.m4)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Space.objects.create(school=self.school, grade=0, kind="school", name="둘째")

    def test_kind_and_grade_must_agree(self):
        for kw in ({"grade": 0, "kind": "grade"}, {"grade": 3, "kind": "school"}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Space.objects.create(school=self.far_school, name="x", **kw)


class CommandTests(MergeBase):
    def run_cmd(self, *args):
        out = StringIO()
        call_command("school_plan", *args, stdout=out)
        return out.getvalue()

    def test_on_needs_manager_when_school_has_none(self):
        with self.assertRaisesMessage(CommandError, "--manager"):
            self.run_cmd("7531234", "--on", "--until", "2027-02-28")
        self.school.refresh_from_db()
        self.assertEqual(self.school.plan, "free")
        self.run_cmd("7531234", "--on", "--until", "2027-02-28", f"--manager={self.m4.key}")
        self.run_cmd("7531234", "--on", "--until", "2028-02-28")  # 관리자가 있으면 기한 연장에 없어도 된다
        self.school.refresh_from_db()
        self.assertEqual(self.school.paid_until, date(2028, 2, 28))

    def test_manager_must_be_google_member(self):
        guest = User.objects.create(username="guest:g", first_name="손님")
        seat = Member.objects.create(space=self.g4, user=guest, name="손님", cls="4-3")
        empty = Member.objects.create(space=self.g4, user=None, name="빈자리", cls="4-4")
        for m in (seat, empty):
            with self.assertRaises(CommandError):
                self.run_cmd("7531234", "--on", "--until", "2027-02-28", f"--manager={m.key}")
            with self.assertRaises(ValueError):
                open_school(self.school, UNTIL, m)
        self.school.refresh_from_db()
        self.assertEqual(self.school.plan, "free")
        self.assertFalse(Member.objects.filter(is_manager=True).exists())

    def test_remove_manager(self):
        self.run_cmd("7531234", "--on", "--until", "2027-02-28", f"--manager={self.m4.key}")
        with self.assertRaisesMessage(CommandError, "마지막 관리자"):
            self.run_cmd("7531234", f"--remove-manager={self.m4.key}")
        self.run_cmd("7531234", "--on", "--until", "2027-02-28", f"--manager={self.m5.key}")
        self.run_cmd("7531234", f"--remove-manager={self.m4.key}")
        self.assertFalse(Member.objects.get(pk=self.m4.pk).is_manager)
        self.assertTrue(Member.objects.get(pk=self.m5.pk).is_manager)
        for key in (self.m4.key, self.far_member.key, "없는회원"):  # 관리자가 아니거나 다른 학교
            with self.assertRaises(CommandError):
                self.run_cmd("7531234", f"--remove-manager={key}")
        self.assertTrue(Member.objects.get(pk=self.m5.pk).is_manager)

    def test_on_with_manager(self):
        out = self.run_cmd("7531234", "--on", "--until", "2027-02-28", f"--manager={self.m4.key}")
        self.school.refresh_from_db()
        self.m4.refresh_from_db()
        self.assertEqual((self.school.plan, self.school.paid_until), ("school", UNTIL))
        self.assertTrue(self.m4.is_manager)
        self.assertFalse(Member.objects.get(pk=self.m5.pk).is_manager)
        self.assertNotIn(self.m4.name, out)  # 이름·토큰은 출력하지 않는다

    def test_off_only_turns_plan_off(self):
        self.run_cmd("7531234", "--on", "--until", "2027-02-28", f"--manager={self.m4.key}")
        self.run_cmd("7531234", "--off")
        self.school.refresh_from_db()
        self.g4.refresh_from_db()
        self.assertEqual(self.school.plan, "free")
        self.assertIsNotNone(self.g4.merged_into_id)

    def test_bad_args(self):
        for args in (["없는코드", "--off"],
                     ["7531234", "--on"],
                     ["7531234", "--on", "--until", "내일"],
                     ["7531234", "--on", "--until", "2027-02-28", "--manager=없는회원"],
                     ["7531234", "--on", "--until", "2027-02-28", f"--manager={self.far_member.key}"]):
            with self.assertRaises(CommandError):
                self.run_cmd(*args)
        self.school.refresh_from_db()
        self.assertEqual(self.school.plan, "free")


class ForwardingTests(MergeBase):
    def setUp(self):
        super().setUp()
        self.hub = open_school(self.school, UNTIL, self.m4)
        self.client.force_login(self.u4)
        self.old = f"/s/{self.g4.token}"
        self.new = f"/s/{self.hub.token}"

    def test_old_address_forwards_with_rest_and_query(self):
        r = self.client.get(f"{self.old}/schedule?w=2026-11-02")
        self.assertRedirects(r, f"{self.new}/schedule?w=2026-11-02", fetch_redirect_response=False)
        self.assertEqual(self.client.get(f"{self.old}/").status_code, 302)
        self.assertEqual(self.client.head(f"{self.old}/").status_code, 302)

    def test_old_address_of_other_grade_forwards_only_to_my_space(self):
        self.assertRedirects(self.client.get(f"/s/{self.g5.token}/today"), f"{self.new}/today",
                             fetch_redirect_response=False)

    def test_anonymous_goes_to_login_same_as_missing_token(self):
        anon = Client()
        real, fake = anon.get(f"{self.old}/today"), anon.get("/s/없는토큰/today")
        self.assertEqual(real.status_code, 302)
        self.assertTrue(real["Location"].startswith("/start"))
        self.assertTrue(fake["Location"].startswith("/start"))
        self.assertNotIn(self.hub.token, real["Location"])

    def test_outsider_404_on_old_and_new(self):
        out = Client()
        out.force_login(self.far_user)
        for path in (f"{self.old}/today", f"{self.new}/today", f"{self.old}/c/{self.c4.key}/"):
            self.assertEqual(out.get(path).status_code, 404, path)
        hx = out.get(f"{self.old}/today", headers={"HX-Request": "true"})
        self.assertEqual(hx.status_code, 404)

    def test_removed_member_404_on_old_address(self):
        Member.objects.filter(pk=self.m4b.pk).delete()
        c = Client()
        c.force_login(self.u4b)
        self.assertEqual(c.get(f"{self.old}/today").status_code, 404)

    def test_other_space_objects_404_after_merge(self):
        self.assertEqual(self.client.get(f"{self.new}/c/{self.far_c.key}/").status_code, 404)

    def test_htmx_reloads_page_instead_of_swapping(self):
        r = self.client.get(f"{self.old}/members", headers={"HX-Request": "true"})
        self.assertEqual((r.status_code, r["HX-Refresh"]), (204, "true"))
        n = Member.objects.in_space(self.hub).get(pk=self.m4.pk).name
        r = self.client.post(f"{self.old}/settings/me", {"name": "바뀜"}, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(Member.objects.get(pk=self.m4.pk).name, n)

    def test_post_is_not_replayed(self):
        r = self.client.post(f"{self.old}/settings/me", {"name": "바뀜"})
        self.assertEqual((r.status_code, r["Location"]), (303, f"{self.new}/today"))
        self.assertEqual(Member.objects.get(pk=self.m4.pk).name, "송민재")

    def test_unmerged_space_not_affected(self):
        far = Client()
        far.force_login(self.far_user)
        self.assertEqual(far.get(f"/s/{self.far.token}/today").status_code, 200)

    def test_submit_link_and_board_work_after_merge(self):
        anon = Client()
        self.assertEqual(anon.get(f"/c/{self.c4.link_token}").status_code, 200)
        self.assertContains(anon.get(f"/c/{self.c4.link_token}/u/{self.r4.key}"), "새솔초등학교")
        r = anon.post(f"/c/{self.c4.link_token}/u/{self.r4.key}", {"f0": "7", "by": "대리"})
        self.assertLess(r.status_code, 400)
        self.assertEqual(Cell.objects.get(row=self.r4).value, "7")
        self.assertContains(self.client.get(f"{self.new}/c/{self.c4.key}/"), "사학년 취합")
        self.assertEqual(self.client.get(f"{self.new}/c/{self.c4.key}/copy").status_code, 200)

    def test_hwp_downloads_keep_working_old_and_new(self):
        old_name = self.sf4.original.name
        self.assertIn(f"hwp/{self.g4.pk}/", old_name)  # 경로의 옛 공간 번호는 그대로, 파일 이름은 DB에 저장돼 있다
        self.sf4.refresh_from_db()
        self.assertEqual(self.sf4.original.name, old_name)
        r = self.client.get(f"{self.new}/hwp/{self.sf4.key}/hwpx")
        self.assertEqual(b"".join(r.streaming_content), "사학년-src".encode())
        r = self.client.get(f"{self.new}/hwp/out/{self.rf4.key}/hwpx")
        self.assertEqual(b"".join(r.streaming_content), "사학년-out".encode())
        self.assertEqual(self.client.get(f"{self.old}/hwp/out/{self.rf4.key}/hwpx").status_code, 302)
        # 합친 뒤 지워도 저장된 경로로 찾아 지운다
        name = self.rf4.hwpx.name
        storage = self.rf4.hwpx.storage
        self.assertTrue(storage.exists(name))
        self.rf4.drop_files()
        self.assertFalse(storage.exists(name))


class InviteAfterMergeTests(MergeBase):
    def setUp(self):
        super().setUp()
        self.hub = open_school(self.school, UNTIL, self.m4)

    def enter(self, c, token, cls, name):
        self.assertEqual(c.get(f"/j/{token}").status_code, 302)
        return c.post("/join", {"cls": cls, "name": name})

    def test_old_invite_only_shows_notice(self):
        c = Client()
        page = c.get(f"/j/{self.g4.invite_token}")
        self.assertContains(page, "학교 공간으로 바뀌었어요. 학교 담당 선생님께 새 링크를 받아 주세요")
        self.assertNotContains(page, "새솔")  # 학교 이름·회원 수·초대 양식 없음
        self.assertNotContains(page, "<form")
        self.assertNotIn(INVITE, c.session)
        self.assertEqual(c.post("/join", {"cls": "4-3", "name": "새선생"}).status_code, 404)
        self.assertFalse(Member.objects.filter(name="새선생").exists())

    def test_invite_opened_before_merge_cannot_join_after(self):
        """합치기 전에 /j/를 열어 세션에 넣어 둔 뒤 합쳐져도 들어오지 못한다."""
        c = Client()
        s = c.session
        s[INVITE] = self.g4.invite_token
        s.save()
        self.assertContains(c.post("/join", {"cls": "4-3", "name": "새선생"}), "학교 공간으로 바뀌었어요")
        self.assertFalse(Member.objects.filter(name="새선생").exists())
        self.assertNotIn(INVITE, c.session)
        self.assertEqual(c.get("/join").status_code, 404)  # 세션에서 지웠으니 다시 시도할 수 없다

    def test_old_invite_for_merged_member_goes_home(self):
        self.client.force_login(self.u5)
        r = self.client.get(f"/j/{self.g5.invite_token}", follow=True)
        self.assertRedirects(r, f"/s/{self.hub.token}/school")

    def test_removed_member_gets_only_notice(self):
        Member.objects.filter(pk=self.m4b.pk).delete()
        c = Client()
        c.force_login(self.u4b)
        self.assertContains(c.get(f"/j/{self.g4.invite_token}"), "학교 공간으로 바뀌었어요")
        self.assertFalse(Member.objects.filter(user=self.u4b).exists())

    def google_client(self, sub):
        c = Client()
        c.force_login(User.objects.create(username=f"google:{sub}", first_name=sub))
        return c

    def test_school_size_is_not_capped_by_one_grade_seats(self):
        for i in range(12):
            Member.objects.create(space=self.hub, name=f"선생{i}", cls="5-2")
        c = self.google_client("thirteen")
        c.get(f"/j/{self.hub.invite_token}")
        r = c.post("/join", {"group": "g4", "cls": "4-4", "name": "열세번째"})
        self.assertEqual(r.status_code, 302)

    def test_school_invite_link_itself_works(self):
        c = self.google_client("five")
        page = c.get(f"/j/{self.hub.invite_token}", follow=True)
        self.assertContains(page, "4학년")
        self.assertContains(page, "5학년")
        self.assertNotContains(page, "0학년")
        c.get(f"/j/{self.hub.invite_token}")
        r = c.post("/join", {"group": "g5", "cls": "5-3", "name": "오학년샘"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Member.objects.in_space(self.hub).get(name="오학년샘").group, "g5")

    def test_reissuing_school_invite_closes_old_school_link(self):
        self.client.force_login(self.u4)  # 관리자
        old = self.hub.invite_token
        self.assertEqual(self.client.post(f"/s/{self.hub.token}/invite").status_code, 302)
        self.assertEqual(Client().get(f"/j/{old}").status_code, 404)

    def seat_is_not_taken(self, seat, cls, name):
        """이름만으로는(익명) 어떤 자리도 얻지 못한다(H2: 구글 필수)."""
        user_id = seat.user_id
        c = Client()
        c.get(f"/j/{self.hub.invite_token}")
        r = c.post("/join", {"group": seat.group, "cls": cls, "name": name})
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("_auth_user_id", c.session)  # 그 자리로 로그인되지 않는다
        seat.refresh_from_db()
        self.assertEqual(seat.user_id, user_id)
        self.assertEqual(Member.objects.in_space(self.hub).filter(cls=cls, name=name).count(), 1)

    def test_same_class_and_name_never_takes_a_school_seat(self):
        guest = User.objects.create(username="guest:park", first_name="박손님")
        self.seat_is_not_taken(Member.objects.create(space=self.hub, user=guest, name="박손님", cls="4-3", group="g4"),
                               "4-3", "박손님")

    def test_seat_without_user_is_not_taken_either(self):
        self.seat_is_not_taken(Member.objects.create(space=self.hub, user=None, name="빈자리", cls="4-4", group="g4"),
                               "4-4", "빈자리")

    def test_manager_seat_is_not_taken(self):
        self.seat_is_not_taken(self.m4, "4-1", "송민재")

    def test_guest_merged_from_grade_keeps_seat_but_cannot_be_reentered(self):
        guest = User.objects.create(username="guest:merged", first_name="옮긴손님")
        seat = Member.objects.create(space=self.g5, user=guest, name="옮긴손님", cls="5-3")
        open_school(self.school, UNTIL, self.m4)
        seat.refresh_from_db()
        self.assertEqual(seat.space_id, self.hub.pk)
        self.seat_is_not_taken(seat, "5-3", "옮긴손님")
        c = Client()
        c.force_login(guest)  # 원래 PC(기존 세션)는 그대로 쓴다
        self.assertEqual(c.get(f"/s/{self.hub.token}/today").status_code, 200)

    def test_first_joined_is_not_manager_by_seniority(self):
        """합치면 가장 먼저 들어온 사람이 아니라 관리자가 학교 공간의 대표다(직위·순서로 권한을 주지 않음)."""
        Member.objects.filter(pk=self.m4.pk).update(is_manager=False)
        Member.objects.filter(pk=self.m5.pk).update(is_manager=True)
        self.client.force_login(self.u4)  # 가장 먼저 들어온 사람
        self.assertEqual(self.client.post(f"/s/{self.hub.token}/invite").status_code, 403)
        self.assertEqual(self.client.post(f"/s/{self.hub.token}/members/{self.m4b.key}/out").status_code, 403)
        self.client.force_login(self.u5)
        self.assertEqual(self.client.post(f"/s/{self.hub.token}/invite").status_code, 302)

    def test_no_manager_nobody_has_lead_rights_and_pages_live(self):
        Member.objects.filter(pk=self.m4.pk).update(is_manager=False)
        self.client.force_login(self.u4)
        self.assertEqual(self.client.get(f"/s/{self.hub.token}/school").status_code, 200)
        self.assertEqual(self.client.post(f"/s/{self.hub.token}/invite").status_code, 403)
        self.assertEqual(Client().get(f"/j/{self.hub.invite_token}", follow=True).status_code, 200)


class LastManagerTests(MergeBase):
    def setUp(self):
        super().setUp()
        self.hub = open_school(self.school, UNTIL, self.m4)
        self.base = f"/s/{self.hub.token}"
        self.client.force_login(self.u4)

    def leave(self, client=None):
        return (client or self.client).post(f"{self.base}/settings/leave", {"confirm": self.hub.name})

    def test_last_manager_cannot_leave(self):
        r = self.leave()
        self.assertEqual(r.status_code, 409)
        self.assertTrue(Member.objects.filter(pk=self.m4.pk).exists())

    def test_last_manager_alone_cannot_delete_school_space(self):
        Member.objects.in_space(self.hub).exclude(pk=self.m4.pk).delete()
        self.assertEqual(self.leave().status_code, 409)
        self.assertTrue(Space.objects.filter(pk=self.hub.pk).exists())

    def test_settings_replaces_leave_button_for_last_manager(self):
        r = self.client.get(f"{self.base}/settings?a=space")
        self.assertContains(r, "마지막 관리자는 나갈 수 없어요")
        self.assertNotContains(r, 'data-open="#leaveDlg"')

    def test_manager_can_leave_when_another_remains(self):
        Member.objects.filter(pk=self.m5.pk).update(is_manager=True)
        self.assertNotContains(self.client.get(f"{self.base}/settings?a=space"), "마지막 관리자는 나갈 수 없어요")
        self.assertEqual(self.leave().status_code, 302)
        self.assertFalse(Member.objects.filter(pk=self.m4.pk).exists())

    def test_every_manager_has_rights_not_only_first(self):
        Member.objects.filter(pk=self.m5.pk).update(is_manager=True)
        c = Client()
        c.force_login(self.u5)  # 두 번째 관리자
        self.assertEqual(c.post(f"{self.base}/invite").status_code, 302)
        Collection.objects.filter(pk=self.c4.pk).update(created_by=None)
        self.assertContains(c.get(f"{self.base}/c/{self.c4.key}/"), f"{self.base}/c/{self.c4.key}/lock")

    def test_remove_refuses_last_manager_even_with_stale_rights(self):
        """관리자 둘이 동시에 서로를 내보내면, 늦게 처리된 쪽은 이미 권한이 없는 상태로 들어온다."""
        Member.objects.filter(pk=self.m4.pk).update(is_manager=True)
        c = Client()
        c.force_login(self.u5)  # 자기 자리가 관리자라고 믿는 옛 요청
        real = views._space_ctx

        def stale(request, **extra):
            return {**real(request, **extra), "is_creator": True}

        with mock.patch("spaces.views._space_ctx", side_effect=stale):
            r = c.post(f"{self.base}/members/{self.m4.key}/out")
        self.assertEqual(r.status_code, 409)
        self.assertTrue(Member.objects.filter(pk=self.m4.pk).exists())

    def test_manager_can_remove_other_manager(self):
        Member.objects.filter(pk=self.m5.pk).update(is_manager=True)
        self.assertEqual(self.client.post(f"{self.base}/members/{self.m5.key}/out").status_code, 302)
        self.assertFalse(Member.objects.filter(pk=self.m5.pk).exists())


class SettingsLabelTests(MergeBase):
    def space_tab(self, user):
        self.client.force_login(user)
        return self.client.get(f"/s/{self.hub.token}/settings?a=space")

    def setUp(self):
        super().setUp()
        self.hub = open_school(self.school, UNTIL, self.m5)

    def test_school_labels_manager_not_grade_lead(self):
        r = self.space_tab(self.u5)
        self.assertNotContains(r, "학년 대표")
        self.assertContains(r, '<span class="pill acc">관리자</span>', count=1)
        html = r.content.decode()
        self.assertIn("pill acc", html[html.index("김도윤"):])
        self.assertNotIn("pill acc", html[html.index("송민재"):html.index("정하은")])

    def test_school_leave_text_says_manager_takes_over(self):
        r = self.space_tab(self.u4)
        self.assertNotContains(r, "먼저 들어온 선생님이 넘겨받아요")
        self.assertNotContains(r, "학년 대표")
        self.assertContains(r, "내가 만든 취합은 관리자가 넘겨받아요")

    def test_grade_space_labels_stay(self):
        self.client.force_login(self.far_user)
        r = self.client.get(f"/s/{self.far.token}/settings?a=space")
        self.assertContains(r, '<span class="pill acc">학년 대표</span>', count=1)
        self.assertContains(r, "학년 대표는 가장 먼저 들어온 선생님이 맡아요")


class ScreensAfterMergeTests(MergeBase):
    def setUp(self):
        super().setUp()
        self.hub = open_school(self.school, UNTIL, self.m4)
        self.client.force_login(self.u5)
        self.base = f"/s/{self.hub.token}"

    def test_main_screens_200_no_grade_zero(self):
        for path in ("/today", "/c/", f"/c/{self.c5.key}/", "/c/new", "/schedule", "/", "/members", "/made", "/welcome",
                     "/settings", "/settings?a=alerts", "/settings?a=space", "/settings?a=device", "/bell"):
            r = self.client.get(self.base + path, follow=True)
            self.assertEqual(r.status_code, 200, path)
            self.assertNotContains(r, "0학년")
        self.assertEqual(self.client.get("/forms").status_code, 200)
        self.assertNotContains(self.client.get("/forms"), "0학년")

    def test_sidebar_shows_my_class_and_school(self):
        r = self.client.get(f"{self.base}/school")
        self.assertContains(r, "5학년 1반")
        self.assertContains(r, "학교 공간")

    def test_recv_header_has_no_grade_zero(self):
        self.assertNotContains(Client().get(f"/c/{self.c5.link_token}/u/{self.r5.key}"), "0학년")

    def test_home_goes_to_school_space(self):
        self.assertRedirects(self.client.get("/"), f"{self.base}/today", fetch_redirect_response=False)

    def test_new_collection_rosters_use_each_members_grade(self):
        from collect.recipients import roster_rows

        rows = roster_rows(self.hub, "people")
        self.assertEqual([r["unit"] for r in rows], ["4학년 1반", "4학년 2반", "5학년 1반"])
        self.assertEqual([r["unit"] for r in roster_rows(self.hub, "class")], ["4-1", "4-2", "5-1"])

    def test_create_collection_and_event_in_school_space(self):
        r = self.client.post(f"{self.base}/c/new", {"src": "preset", "preset": "supplies"})
        self.assertEqual(r.status_code, 302)
        c = Collection.objects.in_space(self.hub).get(source="preset")
        self.assertEqual(list(c.rows.values_list("unit", flat=True)), ["4-1", "4-2", "5-1"])
        self.client.post(f"{self.base}/today/events", {"line": "내일 회의"})
        self.assertTrue(Event.objects.in_space(self.hub).filter(title__contains="회의").exists())

    def test_update_me_keeps_group_and_class(self):
        r = self.client.post(f"{self.base}/settings/me", {"name": "김도윤2"})
        self.assertEqual(r.status_code, 302)
        m = Member.objects.get(pk=self.m5.pk)
        self.assertEqual((m.name, m.cls, m.group), ("김도윤2", "5-1", "g5"))
        self.client.post(f"{self.base}/settings/me", {"cls": "4-3"})
        m.refresh_from_db()
        self.assertEqual((m.cls, m.group), ("4-3", "g4"))
        r = self.client.post(f"{self.base}/settings/me", {"cls": "0-1"})
        m.refresh_from_db()
        self.assertEqual(m.cls, "4-3")

    def test_new_grade_blocked_once_school_space_exists(self):
        """옛 공간이 남아 유일 제약이 막고, 아직 없던 학년도 학교 공간이 있으면 새로 못 만든다."""
        from .views import _grade_taken, _school_open

        pick = {"atpt": "J10", "code": "7531234"}
        self.assertTrue(_grade_taken(pick, 4))
        self.assertTrue(_school_open(pick))
        self.assertFalse(_school_open({"atpt": "B10", "code": "7010001"}))

    def test_leave_in_school_space(self):
        r = self.client.post(f"{self.base}/settings/leave", {"confirm": self.hub.name})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Member.objects.filter(pk=self.m5.pk).exists())
        self.c5.refresh_from_db()
        self.assertIsNone(self.c5.created_by)  # 입장 순서가 아니라 관리자가 넘겨받는다
        owner = Client()
        owner.force_login(self.u4)
        self.assertContains(owner.get(f"{self.base}/c/{self.c5.key}/"), f"{self.base}/c/{self.c5.key}/lock")

    def test_last_member_leaving_school_space_removes_old_shells(self):
        Member.objects.in_space(self.hub).exclude(pk=self.m5.pk).delete()
        self.client.post(f"{self.base}/settings/leave", {"confirm": self.hub.name})
        self.assertFalse(self.school.spaces.exists())


class ExpiryFieldTests(TestCase):
    def test_defaults_for_existing_style_rows(self):
        s = School.objects.create(neis_atpt="A", neis_code="1", name="기본초")
        sp = Space.objects.create(school=s, grade=1, name="1학년")
        m = Member.objects.create(space=sp, name="가", cls="1-1")
        self.assertEqual((s.plan, s.paid_until), ("free", None))
        self.assertEqual((sp.kind, sp.merged_into_id, sp.is_school, sp.label), ("grade", None, False, "1학년"))
        self.assertEqual((m.group, m.is_manager), ("", False))
