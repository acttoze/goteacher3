"""W7 명단으로 한 번에 등록(/s/<token>/school/bulk): 붙여넣기 → 미리보기 → 새 자리 + 입장 코드 → 쪽지 인쇄."""
import json
import logging
import re
import shutil
import subprocess
import threading
from datetime import date, timedelta
from pathlib import Path
from unittest import mock, skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.cache import caches
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings

from legacy.models import LegacyMap

from . import entry
from .merge import open_free
from .models import INVITE, EntryCode, Member, MemberLog, School, Space

User = get_user_model()
SITE = "https://t.example"
JS = Path(settings.BASE_DIR).parent / "web" / "static" / "js" / "bulk_paste.js"
CODE = re.compile(r"\b[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}\b")


def account(name):
    return User.objects.create(username=f"google:{name}", first_name=name)


def seat(space, name, cls, user=None, **extra):
    return Member.objects.create(space=space, user=user or account(f"{space.pk}-{name}"), name=name, cls=cls, **extra)


def logged(user):
    c = Client()
    c.force_login(user)
    return c


def tsv(*rows):
    return "\n".join("\t".join(r) for r in rows)


def form(rows, **extra):
    """미리보기 화면이 보내는 줄 값: r{i}-name·group·cls·role(·off)."""
    data = {"n": str(len(rows)), **extra}
    for i, r in enumerate(rows):
        for k, v in r.items():
            data[f"r{i}-{k}"] = v
    return data


@override_settings(SITE_URL=SITE)
class Base(TestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        patcher = mock.patch("spaces.neis.class_names", return_value=[])  # 반은 1~4반 + 전담
        patcher.start()
        self.addCleanup(patcher.stop)
        self.school = School.objects.create(neis_atpt="J10", neis_code="7530101", name="새솔초등학교")
        g4 = Space.objects.create(school=self.school, grade=4, name="4학년")
        self.head = seat(g4, "김관리", "4-1")
        self.hub = open_free(g4, self.head)
        self.school.refresh_from_db()
        self.head.refresh_from_db()
        self.admin = logged(self.head.user)
        self.url = f"/s/{self.hub.token}/school/bulk"

    def preview(self, text, c=None):
        return (c or self.admin).post(self.url + "/preview", {"paste": text})

    def make(self, rows, c=None):
        return (c or self.admin).post(self.url, form(rows))

    def new_seats(self):
        return Member.objects.in_space(self.hub).filter(user__isnull=True)


class AccessTests(Base):
    def test_manager_sees_page(self):
        r = self.admin.get(self.url)
        self.assertContains(r, "명단으로 한 번에 등록")
        self.assertContains(r, 'name="paste"')

    def test_plain_member_is_refused(self):
        plain = seat(self.hub, "박교사", "4-2", group="g4")
        c = logged(plain.user)
        self.assertEqual(c.get(self.url).status_code, 403)
        self.assertEqual(c.post(self.url, form([{"name": "가", "group": "g4", "cls": "4-3"}])).status_code, 403)
        self.assertEqual(c.post(self.url + "/preview", {"paste": "가\t4학년\t3반"}).status_code, 403)
        self.assertFalse(self.new_seats().exists())

    def test_other_school_manager_gets_404(self):
        other = School.objects.create(neis_atpt="J10", neis_code="7530102", name="다른초등학교")
        g = Space.objects.create(school=other, grade=4, name="4학년")
        boss = seat(g, "남관리", "4-1")
        open_free(g, boss)
        c = logged(boss.user)
        self.assertEqual(c.get(self.url).status_code, 404)
        self.assertEqual(c.post(self.url, form([{"name": "가", "group": "g4", "cls": "4-3"}])).status_code, 404)
        self.assertFalse(self.new_seats().exists())

    def test_grade_and_org_spaces_have_no_page(self):
        g5 = Space.objects.create(school=self.school, grade=5, name="5학년")
        lead = seat(g5, "오대표", "5-1")
        self.assertEqual(logged(lead.user).get(f"/s/{g5.token}/school/bulk").status_code, 404)
        org = School.objects.create(neis_atpt="J10", neis_code="", name="교육청 과", plan="school", paid_until=date(2030, 1, 1))
        os = Space.objects.create(school=org, kind=Space.Kind.ORG, grade=0, name="과")
        boss = seat(os, "기관장", "미배정", is_manager=True)
        self.assertEqual(logged(boss.user).get(f"/s/{os.token}/school/bulk").status_code, 404)

    def test_expired_plan_refused(self):
        School.objects.filter(pk=self.school.pk).update(paid_until=date.today() - timedelta(days=1))
        self.assertEqual(self.admin.get(self.url).status_code, 403)
        self.assertEqual(self.make([{"name": "가", "group": "g4", "cls": "4-3"}]).status_code, 403)
        self.assertFalse(self.new_seats().exists())

    @override_settings(LEGACY_READONLY=True)
    def test_legacy_readonly_refused(self):
        LegacyMap.objects.create(kind="school", legacy_id="1", obj_id=self.school.pk)
        self.assertEqual(self.admin.get(self.url).status_code, 403)
        self.assertEqual(self.make([{"name": "가", "group": "g4", "cls": "4-3"}]).status_code, 403)
        self.assertFalse(self.new_seats().exists())

    def test_legacy_school_without_readonly_and_paid_school_can_use(self):
        LegacyMap.objects.create(kind="school", legacy_id="1", obj_id=self.school.pk)
        self.assertEqual(self.admin.get(self.url).status_code, 200)
        paid = School.objects.create(neis_atpt="J10", neis_code="7530103", name="유료초등학교", plan="school", paid_until=date(2030, 1, 1))
        hub = Space.objects.create(school=paid, kind=Space.Kind.SCHOOL, grade=0, name=paid.name)
        boss = seat(hub, "유관리", "교무실", group="office", is_manager=True)
        c = logged(boss.user)
        r = c.post(f"/s/{hub.token}/school/bulk", form([{"name": "가나다", "group": "g2", "cls": "2-1"}]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(EntryCode.objects.in_space(hub).count(), 1)

    def test_csrf_required(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.head.user)
        self.assertEqual(c.post(self.url, form([{"name": "가", "group": "g4", "cls": "4-3"}])).status_code, 403)
        self.assertEqual(c.post(self.url + "/preview", {"paste": "가\t4학년\t3반"}).status_code, 403)
        self.assertFalse(self.new_seats().exists())

    def test_entry_buttons(self):
        page = self.admin.get(f"/s/{self.hub.token}/school")
        self.assertContains(page, f'href="{self.url}"')
        self.assertContains(page, "링크는 각자, 명단은 관리자가 한 번에")
        plain = seat(self.hub, "박교사", "4-2", group="g4")
        self.assertNotContains(logged(plain.user).get(f"/s/{self.hub.token}/school"), self.url)

    def test_paid_school_title_row_has_button(self):
        paid = School.objects.create(neis_atpt="J10", neis_code="7530104", name="유료초등학교", plan="school", paid_until=date(2030, 1, 1))
        hub = Space.objects.create(school=paid, kind=Space.Kind.SCHOOL, grade=0, name=paid.name)
        boss = seat(hub, "유관리", "교무실", group="office", is_manager=True)
        self.assertContains(logged(boss.user).get(f"/s/{hub.token}/school"), f'href="/s/{hub.token}/school/bulk"')


class ReadTests(Base):
    def rows(self, text):
        r = self.preview(text)
        self.assertEqual(r.status_code, 200, r.content.decode()[:500])
        return r.context["lines"]

    def test_group_and_class_words(self):
        rows = self.rows(tsv(
            ["이름", "소속", "반/직책"],
            ["가일", "1학년", "3반"], ["나이", "2학년부", "1"], ["다삼", "사학년", "4-2"], ["라사", "3학년", "전담"],
            ["마오", "5학년", "교과전담"], ["바육", "6학년 2반", ""], ["사칠", "교무실", "교무부장"], ["아팔", "행정실", "주무관"],
            ["자구", "급식소", "영양교사"], ["차십", "기타", "보건"], ["카", "기타/지원", ""],
        ))
        got = {r["name"]: (r["group"], r["cls"], r["role"], r["status"]) for r in rows}
        self.assertEqual(got["가일"][:2], ("g1", "1-3"))
        self.assertEqual(got["나이"][:2], ("g2", "2-1"))
        self.assertEqual(got["다삼"][:2], ("g4", "4-2"))
        self.assertEqual(got["라사"][:2], ("g3", "3-전담"))
        self.assertEqual(got["마오"][:2], ("g5", "5-전담"))
        self.assertEqual(got["바육"][:2], ("g6", "6-2"))
        self.assertEqual(got["사칠"], ("office", "교무실", "교무부장", "ok"))
        self.assertEqual(got["아팔"][:3], ("admin", "행정실", "주무관"))
        self.assertEqual(got["자구"][:3], ("meal", "급식소", "영양교사"))
        self.assertEqual(got["차십"][:3], ("support", "기타/지원", "보건"))
        self.assertEqual(got["카"][:3], ("support", "기타/지원", ""))
        self.assertTrue(all(r["status"] == "ok" for r in rows))

    def test_two_separate_columns_and_title_line(self):
        rows = self.rows(tsv(["2026학년도 교직원 명단"], ["번호", "성명", "부서", "학급", "직위", "비고"],
                             ["1", "홍길동", "4학년", "2", "", "휴직 예정"], ["2", "이교감", "교무실", "", "교감", ""]))
        self.assertEqual([(r["name"], r["group"], r["cls"], r["role"]) for r in rows],
                         [("홍길동", "g4", "4-2", ""), ("이교감", "office", "교무실", "교감")])

    def test_needs_fixing_first(self):
        rows = self.rows(tsv(["이름", "소속", "반"], ["정상", "4학년", "2반"], ["모름", "체육부", ""], ["반없음", "3학년", ""],
                             ["반틀림", "2학년", "9반"]))
        self.assertEqual([(r["name"], r["status"]) for r in rows],
                         [("모름", "fix"), ("반없음", "fix"), ("반틀림", "fix"), ("정상", "ok")])

    def test_existing_and_same_name(self):
        seat(self.hub, "이미", "4-2", group="g4")
        seat(self.hub, "동명", "교무실", group="office", position="교무")
        rows = self.rows(tsv(["이름", "소속", "반"], ["이미", "4학년", "2반"], ["동명", "1학년", "1반"], ["새분", "2학년", "1반"],
                             ["새분", "2학년", "1반"]))
        self.assertEqual([(r["name"], r["status"]) for r in rows],
                         [("이미", "exists"), ("동명", "dup"), ("새분", "ok"), ("새분", "exists")])
        self.assertContains(self.preview(tsv(["이름", "소속", "반"], ["이미", "4학년", "2반"], ["새분", "2학년", "1반"])), "1명 만들기")

    def test_other_columns_are_ignored(self):
        r = self.preview(tsv(["이름", "소속", "반", "비고", "연락처"], ["홍길동", "4학년", "2", "알레르기 있음", "010-1234-5678"]))
        body = r.content.decode()
        self.assertNotIn("알레르기", body)
        self.assertNotIn("1234", body)
        self.assertNotIn("비고", body)

    def test_student_list_refused(self):
        r = self.preview(tsv(["이름", "학번", "반"], ["홍길동", "10203", "2"]))
        self.assertContains(r, "학생")
        self.assertNotIn("lines", r.context)

    def test_limit_150(self):
        ok = self.preview(tsv(["이름", "소속", "반"], *[[f"사람{i}", "4학년", "1반"] for i in range(150)]))
        self.assertEqual(len(ok.context["lines"]), 150)
        r = self.preview(tsv(["이름", "소속", "반"], *[[f"사람{i}", "4학년", "1반"] for i in range(151)]))
        self.assertContains(r, "150명")
        self.assertNotIn("lines", r.context)
        too = self.make([{"name": f"사람{i}", "group": "g4", "cls": "4-1"} for i in range(151)])
        self.assertEqual(too.status_code, 400)
        self.assertFalse(self.new_seats().exists())

    def test_recheck_from_form_after_choosing(self):
        r = self.admin.post(self.url + "/preview", form([{"name": "모름", "group": "체육부", "cls": "3반"}]))
        self.assertEqual(r.context["lines"][0]["status"], "fix")
        self.assertContains(r, "체육부")  # 적힌 글자를 보여 주고 고르게 한다
        r = self.admin.post(self.url + "/preview", form([{"name": "모름", "group": "g3", "cls": "3반"}]))
        self.assertEqual((r.context["lines"][0]["cls"], r.context["lines"][0]["status"]), ("3-3", "ok"))


class MakeTests(Base):
    ROWS = [{"name": "홍길동", "group": "g4", "cls": "4-2"}, {"name": "이교감", "group": "office", "role": "교감"},
            {"name": "김조리", "group": "meal", "role": "조리사"}]

    def test_creates_seats_and_codes_once(self):
        r = self.make(self.ROWS)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "3명을 만들었어요. 입장 코드는 지금만 볼 수 있어요.")
        seats = {m.name: m for m in self.new_seats()}
        self.assertEqual((seats["홍길동"].group, seats["홍길동"].cls, seats["홍길동"].position), ("g4", "4-2", "2반"))
        self.assertEqual((seats["이교감"].group, seats["이교감"].cls, seats["이교감"].position), ("office", "교무실", "교감"))
        self.assertFalse(any(m.is_manager for m in seats.values()))
        codes = CODE.findall(r.content.decode())
        self.assertEqual(len(set(codes)), 3)
        for code in set(codes):
            live = EntryCode.objects.get(code_hash=entry.code_hash(code))
            self.assertIn(live.member.name, seats)
            self.assertEqual(live.created_by, self.head.user)

    def test_extra_fields_are_dropped(self):
        rows = [{**self.ROWS[0], "phone": "010-1234-5678", "birth": "1980-01-01"}]
        r = self.admin.post(self.url, {**form(rows), "paste": "홍길동\t010-1234-5678"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("1234", r.content.decode())
        m = self.new_seats().get()
        self.assertEqual([m.name, m.cls, m.position, m.group], ["홍길동", "4-2", "2반", "g4"])

    def test_skips_existing_and_removed_rows(self):
        seat(self.hub, "홍길동", "4-2", group="g4")
        rows = [*self.ROWS, {"name": "뺄분", "group": "g1", "cls": "1-1", "off": "1"}]
        r = self.make(rows)
        self.assertContains(r, "2명을 만들었어요")
        self.assertEqual(set(self.new_seats().values_list("name", flat=True)), {"이교감", "김조리"})

    def test_fix_rows_must_be_fixed_or_removed(self):
        r = self.make([*self.ROWS, {"name": "모름", "group": "체육부", "cls": ""}])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(self.new_seats().exists())
        r = self.make([*self.ROWS, {"name": "모름", "group": "체육부", "cls": "", "off": "1"}])
        self.assertContains(r, "3명을 만들었어요")

    def test_only_new_seats_get_codes(self):
        empty = Member.objects.create(space=self.hub, name="옛빈자리", cls="교무실", group="office")
        self.make(self.ROWS)
        self.assertFalse(EntryCode.objects.filter(member=empty).exists())
        self.assertEqual(EntryCode.objects.in_space(self.hub).count(), 3)

    def test_same_list_twice_is_safe(self):
        self.make(self.ROWS)
        r = self.make(self.ROWS)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "새로 만든 자리가 없어요")
        self.assertEqual(self.new_seats().count(), 3)
        self.assertEqual(EntryCode.objects.in_space(self.hub).count(), 3)
        self.assertEqual(MemberLog.objects.in_space(self.hub).filter(name__startswith="일괄 등록").count(), 1)

    def test_seat_cap(self):
        with mock.patch("spaces.bulk.SCHOOL_SEATS", 3):
            r = self.make(self.ROWS)  # 관리자 1 + 3 > 3
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "3명", status_code=400)
        self.assertFalse(self.new_seats().exists())

    def test_failure_midway_leaves_nothing(self):
        real, calls = entry.issue, []

        def flaky(m, by):
            calls.append(m)
            if len(calls) == 2:
                raise RuntimeError("boom")
            return real(m, by)
        with mock.patch("spaces.entry.issue", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                self.make(self.ROWS)
        self.assertFalse(self.new_seats().exists())
        self.assertFalse(EntryCode.objects.exists())
        self.assertFalse(MemberLog.objects.in_space(self.hub).filter(name__startswith="일괄 등록").exists())

    def test_refused_issue_rolls_back_with_message(self):
        with mock.patch("spaces.entry.issue", side_effect=ValueError("학교 관리자만 입장 코드를 줄 수 있어요.")):
            r = self.make(self.ROWS)
        self.assertContains(r, "학교 관리자만", status_code=400)
        self.assertFalse(self.new_seats().exists())

    def test_one_log_line(self):
        before = MemberLog.objects.in_space(self.hub).count()
        self.make(self.ROWS)
        logs = MemberLog.objects.in_space(self.hub)
        self.assertEqual(logs.count(), before + 1)
        log = logs.latest("pk")
        self.assertEqual((log.by, log.name), ("김관리", "일괄 등록 3명"))

    def test_plain_codes_are_not_kept(self):
        with self.assertLogs(level="DEBUG") as logs:
            r = self.make(self.ROWS)
            logging.getLogger("w7").debug("끝")
        self.assertIn("no-store", r["Cache-Control"])
        codes = [c.replace("-", "") for c in set(CODE.findall(r.content.decode()))]
        self.assertEqual(len(codes), 3)
        stored = json.dumps([list(EntryCode.objects.values_list("code_hash", flat=True)),
                             list(MemberLog.objects.values_list("by", "name", "before", "after")),
                             [s.get_decoded() for s in Session.objects.all()]], ensure_ascii=False)
        joined = "\n".join(logs.output).replace("-", "")
        for code in codes:
            self.assertNotIn(code, stored.replace("-", ""))
            self.assertNotIn(code, joined)  # 로그에 원문 없음

    def test_print_sheet(self):
        rows = [{"name": f"교사{i:02d}", "group": f"g{i % 6 + 1}", "cls": f"{i % 6 + 1}-{i % 4 + 1}"} for i in range(10)]
        rows[3] = {"name": "교사03", "group": "office", "role": "교무"}
        r = self.make(rows)
        body = r.content.decode()
        self.assertIn("size: A4 portrait", body)
        self.assertEqual(body.count('class="sheet"'), 2)
        self.assertEqual(body.count('class="slip"'), 10)
        first = body.index('class="sheet"')
        self.assertEqual(body[first:body.index('class="sheet"', first + 1)].count('class="slip"'), 8)
        self.assertIn(f"{SITE}/enter", body)
        self.assertIn("새솔초등학교", body)
        for step in ("주소를 열어요", "입장 코드를 넣어요", "구글이나 아이디로 계속"):
            self.assertIn(step, body)
        names = re.findall(r'<b class="s-name">([^<]+)</b>', body)
        self.assertEqual(names[0], "교사00")  # 1학년부터
        self.assertEqual(names[-1], "교사03")  # 교무실은 학년 뒤
        self.assertIn("안내 쪽지 인쇄", body)
        msg = re.search(r'data-text="([^"]*)"', body).group(1)
        self.assertIn("/enter", msg)
        self.assertIsNone(CODE.search(msg))

    def test_code_from_slip_completes_enter(self):
        r = self.make(self.ROWS[:1])
        code = CODE.search(r.content.decode()).group(0)
        c = Client()
        self.assertEqual(c.post("/enter", {"code": code}).status_code, 303)
        u = User.objects.create(username="hong_gd", first_name="홍길동")
        u.set_password("long-enough-pass-77")
        u.save()
        c.force_login(u)
        self.assertEqual(c.post("/enter", {"claim": "1"}).status_code, 303)
        m = Member.objects.in_space(self.hub).get(name="홍길동")
        self.assertEqual(m.user, u)


class JoinHintTests(Base):
    def join(self, user, name, group, cls):
        c = logged(user)
        c.get(f"/j/{self.hub.invite_token}")
        self.assertIn(INVITE, c.session)
        return c.post("/join", {"group": group, "cls": cls, "name": name})

    def test_empty_seat_points_to_enter(self):
        self.make([{"name": "홍길동", "group": "g4", "cls": "4-2"}])
        r = self.join(account("hong"), "홍길동", "g4", "4-2")
        self.assertContains(r, "관리자에게 받은 입장 코드를", status_code=400)
        self.assertContains(r, f"{SITE}/enter", status_code=400)
        self.assertNotContains(r, "처음 들어온 PC에서", status_code=400)

    def test_guest_seat_keeps_old_hint(self):
        from accounts.models import new_guest
        Member.objects.create(space=self.hub, user=new_guest("손님"), name="손님", cls="4-3", group="g4", position="3반")
        r = self.join(account("guest-again"), "손님", "g4", "4-3")
        self.assertContains(r, "처음 들어온 PC에서", status_code=400)


class ServerGuardTests(Base):
    """브라우저를 거치지 않고 보낸 값도 서버가 막는다(보안 권고)."""

    def rows(self, text):
        r = self.preview(text)
        self.assertEqual(r.status_code, 200)
        return r, r.context["lines"]

    def test_private_values_are_blanked_and_flagged(self):
        r, rows = self.rows(tsv(["이름", "소속", "반/직책"], ["홍길동", "교무실", "010 - 1234 - 5678"], ["김행정", "행정실", "010-1234-5678(개인)"],
                               ["메일씨", "hong@school.kr", "2반"], ["생일씨", "4학년", "1980.03.04"], ["번호씨", "교무실", "교무 1234567"],
                               ["800304-1234567", "4학년", "3반"], ["정상", "교무실", "교무부장"]))
        body = r.content.decode()
        for leaked in ("5678", "hong@", "1980", "1234567", "800304"):
            self.assertNotIn(leaked, body)
        got = {x["name"]: x for x in rows}
        for name in ("홍길동", "김행정", "메일씨", "생일씨", "번호씨", ""):
            self.assertEqual(got[name]["status"], "fix", name)
        self.assertEqual((got["홍길동"]["role"], got["정상"]["status"], got["정상"]["role"]), ("", "ok", "교무부장"))

    def test_make_refuses_private_values(self):
        r = self.make([{"name": "홍길동", "group": "office", "role": "010-1234-5678"}])
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("5678", r.content.decode())
        self.assertFalse(self.new_seats().exists())

    def test_three_homeroom_lines_in_one_class_look_like_students(self):
        r, rows = self.rows(tsv(["이름", "소속", "반"], ["가", "3학년", "2반"], ["나", "3학년", "2반"], ["다", "3학년", "2반"],
                               ["라", "3학년", "1반"], ["마", "3학년", "전담"], ["바", "3학년", "전담"], ["사", "3학년", "전담"]))
        self.assertContains(r, "학생 명단은 받지 않아요. 교직원 명단인지 확인해 주세요")
        self.assertEqual({x["name"]: x["status"] for x in rows},
                         {"가": "fix", "나": "fix", "다": "fix", "라": "ok", "마": "ok", "바": "ok", "사": "ok"})
        made = self.make([{"name": n, "group": "g3", "cls": "3-2"} for n in "가나다"])
        self.assertEqual(made.status_code, 400)
        self.assertFalse(self.new_seats().exists())

    def test_lines_with_their_own_role_are_not_counted(self):
        r, rows = self.rows(tsv(["이름", "소속", "반", "직책"], ["가", "3학년", "2반", ""], ["나", "3학년", "2반", "부장"],
                               ["다", "3학년", "2반", "협력교사"]))
        self.assertTrue(all(x["status"] == "ok" for x in rows))
        self.assertNotContains(r, "학생 명단은 받지 않아요")

    def test_done_page_clears_on_back_cache(self):
        r = self.make([{"name": "홍길동", "group": "g4", "cls": "4-2"}])
        self.assertContains(r, f'data-back="/s/{self.hub.token}/school"')
        self.assertIn("e.persisted", JS.read_text())


@skipUnless(shutil.which("node"), "node가 있어야 브라우저 쪽 정리 함수를 돌린다")
class BrowserCleanTests(TestCase):
    """bulk_paste.js의 clean(): 전화·생년월일·주민번호·메일로 보이는 열은 서버로 보내기 전에 지운다."""

    def run_js(self, grid):
        script = f"var B = require({json.dumps(str(JS))}); process.stdout.write(JSON.stringify(B.clean({json.dumps(grid)})));"
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=20)
        return json.loads(out.stdout)

    def test_sensitive_columns_by_head(self):
        got = self.run_js([["이름", "소속", "반", "휴대전화", "생년월일", "E-mail", "주민등록번호", "비고"],
                           ["홍길동", "4학년", "2", "", "", "", "", "메모"]])
        self.assertEqual(got["rows"], [["이름", "소속", "반", "비고"], ["홍길동", "4학년", "2", "메모"]])
        self.assertEqual(got["dropped"], ["휴대전화", "생년월일", "E-mail", "주민등록번호"])

    def test_sensitive_columns_by_value_without_head(self):
        got = self.run_js([["홍길동", "4학년", "2반", "010-1234-5678", "1980.3.4", "hong@school.kr", "800304-1234567"],
                           ["이교감", "교무실", "교감", "01098765432", "19750102", "lee@x.kr", "750102-2******"]])
        self.assertEqual(got["rows"], [["홍길동", "4학년", "2반"], ["이교감", "교무실", "교감"]])
        self.assertEqual(len(got["dropped"]), 4)

    def test_stray_values_in_kept_columns_are_blanked(self):
        got = self.run_js([["이름", "소속", "반/직책"], ["홍길동", "4학년", "2반"], ["이교감", "교무실", "010-2222-3333"]])
        self.assertEqual(got["rows"], [["이름", "소속", "반/직책"], ["홍길동", "4학년", "2반"], ["이교감", "교무실", ""]])

    def test_text_lines(self):
        script = (f"var B = require({json.dumps(str(JS))});"
                  "process.stdout.write(JSON.stringify(B.fromText('이름\\t소속\\t전화\\r\\n홍길동\\t4학년\\t010-1111-2222\\n\\n')));")
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=20)
        self.assertEqual(json.loads(out.stdout), [["이름", "소속", "전화"], ["홍길동", "4학년", "010-1111-2222"]])


@skipUnless(connection.vendor == "postgresql", "동시 요청 직렬화(select_for_update)는 PostgreSQL에서만 확인된다")
@override_settings(SITE_URL=SITE)
class ConcurrentTests(TransactionTestCase):
    def setUp(self):
        caches["ratelimit"].clear()
        patcher = mock.patch("spaces.neis.class_names", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        school = School.objects.create(neis_atpt="J10", neis_code="7530109", name="동시초등학교")
        g4 = Space.objects.create(school=school, grade=4, name="4학년")
        self.head = seat(g4, "관리", "4-1")
        self.hub = open_free(g4, self.head)

    def test_double_submit_makes_one_set(self):
        rows = [{"name": f"교사{i}", "group": f"g{i % 6 + 1}", "cls": f"{i % 6 + 1}-{i // 6 + 1}"} for i in range(20)]
        data, url, out = form(rows), f"/s/{self.hub.token}/school/bulk", []
        gate = threading.Barrier(2)

        def go():
            try:
                c = logged(self.head.user)
                gate.wait()
                out.append(c.post(url, data).status_code)
            finally:
                connection.close()
        threads = [threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(out, [200, 200])
        self.assertEqual(Member.objects.in_space(self.hub).filter(user__isnull=True).count(), 20)
        self.assertEqual(EntryCode.objects.in_space(self.hub).count(), 20)
        self.assertEqual(MemberLog.objects.in_space(self.hub).filter(name__startswith="일괄 등록").count(), 1)

    def test_cap_holds_under_race(self):
        rows_a = [{"name": f"가{i}", "group": "g2", "cls": f"2-{i + 1}"} for i in range(3)]
        rows_b = [{"name": f"나{i}", "group": "g3", "cls": f"3-{i + 1}"} for i in range(3)]
        out = []
        gate = threading.Barrier(2)

        def go(rows):
            try:
                c = logged(self.head.user)
                gate.wait()
                out.append(c.post(f"/s/{self.hub.token}/school/bulk", form(rows)).status_code)
            finally:
                connection.close()
        with mock.patch("spaces.bulk.SCHOOL_SEATS", 5):
            threads = [threading.Thread(target=go, args=(r,)) for r in (rows_a, rows_b)]
            [t.start() for t in threads]
            [t.join() for t in threads]
        self.assertEqual(sorted(out), [200, 400])
        self.assertEqual(Member.objects.in_space(self.hub).count(), 4)
