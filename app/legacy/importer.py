"""P3 구버전 덤프(M1 표별 JSON Lines) → 고티처. 규칙은 35번 2절, 순수 규칙은 rules.py.

- 계정은 탈퇴·차단을 뺀 전원(D10). 이미 있는 아이디(대소문자 무시)는 덮어쓰지 않고 그 계정만 멈춘다.
- 학교는 학교 하나가 트랜잭션 하나. 이미 공간이 있는 학교(이관 기록 없이)는 통째로 멈춘다.
- LegacyMap에 있는 것은 건너뛰고 새것만 더한다(최종 증분 덤프). 버린 것은 기록하지 않아 다시 돌려도 같은 까닭으로 센다.
- 작성 시각은 원래 값. auto_now(_add) 칸은 넣은 뒤 bulk_update로 덮는다(bulk_update는 pre_save를 부르지 않는다).
- 보고서에는 학교 코드·이름·종류·구 번호·까닭만 쓴다(이름·아이디·내용 없음).
"""
import csv
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.files import File
from django.db import transaction
from django.db.models.functions import Lower
from django.utils import timezone

from accounts.models import ID_RE
from hwpfiles.models import storage_room
from notices.views import MAX_FILE, MAX_FILES

from notices.models import AUDIENCE, FILE_LIST, FILES, ITEMS, TITLE, Notice, NoticeFile, NoticeVersion
from spaces.models import Attendance, Member, School, Space
from today.models import ALL, ME, Event, EventComment, EventFile
from today.more import COMMENT_FILES

from .models import LegacyMap
from .rules import (OLD_CODES, Rejected, body_of, day, duty_name, duty_of, group_of, judge_file, moment,
                    password_of, seat_fields, unique_name)

PAID_UNTIL = date(2027, 2, 28)  # D6'
ACTIVE_SINCE = "2026-03-01"  # D1
BOT = "neis_sync_bot"
BATCH = 2000
NO_TITLE = "(제목 없음)"
ACCOUNT, SCHOOL, SEAT, MANAGER = "계정", "학교", "자리", "관리자"
EVENT, DUTY, DUTY_DAYS, COMMENT, EFILE, CFILE = "일정", "복무", "복무 날짜 행", "댓글", "첨부(일정)", "첨부(댓글)"
NOTICE, NFILE, NCOMMENT = "게시글", "첨부(게시판)", "게시판 댓글"
IN, DONE, MERGED = "넣음", "이미 넣음", "합침"
KEEP = ("wr_id", "wr_parent", "wr_is_comment", "mb_id", "wr_name", "wr_1", "wr_2", "wr_3", "wr_4", "wr_7", "wr_8",
        "wr_10", "wr_subject", "wr_content", "wr_option", "wr_datetime")


def drop(why):
    return f"버림: {why}"


def _rows(folder, name):
    with open(Path(folder) / f"{name}.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _gone(m):
    return bool(m["mb_leave_date"].strip() or m["mb_intercept_date"].strip())


def _system(m):
    """NEIS 봇·최고관리자(레벨 10): 계정·자리를 만들지 않는다."""
    return m["mb_id"] == BOT or m["mb_level"] >= 10


class Dump:
    """덤프 폴더를 읽은 것. 일정은 필요한 칸만 남긴다(본문이 커서)."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.members = list(_rows(folder, "g5_member"))
        self.groups = {g["group_id"]: g for g in _rows(folder, "contact_group")}
        self.schools = {s["id"]: s for s in _rows(folder, "school")}
        self.channels = {c["wr_id"]: c["wr_subject"].strip() for c in _rows(folder, "g5_write_channel")}
        self.admins = defaultdict(set)
        for a in _rows(folder, "g5_write_go_admin"):
            if not a["wr_is_comment"]:
                self.admins[a["wr_3"]].add(a["mb_id"])
        self.events, self.comments = defaultdict(list), defaultdict(list)
        for r in _rows(folder, "g5_write_goteacher"):
            r = {k: r[k] for k in KEEP}
            (self.comments[r["wr_parent"]] if r["wr_is_comment"] else self.events[r["wr_3"]]).append(r)
        self.board, self.board_comments = defaultdict(list), defaultdict(list)
        for r in _rows(folder, "g5_write_free_school"):
            (self.board_comments[r["wr_parent"]] if r["wr_is_comment"] else self.board[r["wr_3"]]).append(r)
        self.files = defaultdict(list)
        for f in _rows(folder, "g5_board_file"):
            self.files[(f["bo_table"], f["wr_id"])].append(f)
        for rows in (*self.events.values(), *self.board.values()):
            rows.sort(key=lambda r: r["wr_id"])

    def targets(self):
        """D1: 2026-03-01 이후 로그인한 가입 회원(탈퇴·차단 제외)이 있는 학교. school 표에 있는 코드만."""
        return sorted({m["mb_1"] for m in self.members if m["mb_5"] == "1" and not _gone(m)
                       and m["mb_today_login"] >= ACTIVE_SINCE and m["mb_1"] in self.schools})

    def path(self, f):
        """덤프의 files/<bo_table>/ 밖을 가리키면(../ 등) None."""
        root = (self.folder / "files" / f["bo_table"]).resolve()
        p = (root / f["bf_file"].strip()).resolve()
        return p if p.is_relative_to(root) else None


class Report:
    """counts[(학교 코드, 종류, 결과)]. items: 넣음·이미 넣음이 아닌 것 하나하나(학교, 종류, 구 번호, 결과)."""

    def __init__(self):
        self.counts, self.items, self.names = Counter(), [], {"": "(학교 밖)"}

    def add(self, school, kind, result, legacy_id="", n=1):
        self.counts[(school, kind, result)] += n
        if result not in (IN, DONE):
            self.items.append((school, kind, legacy_id, result))

    def write(self, path):
        path = Path(path)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["학교 코드", "학교", "종류", "결과", "건수"])
            totals = Counter()
            for (school, kind, result), n in self.counts.items():
                totals[(school, kind)] += n
            for (school, kind), n in sorted(totals.items()):
                w.writerow([school, self.names.get(school, ""), kind, "원본", n])
                for (s, k, result), m in sorted(self.counts.items()):
                    if (s, k) == (school, kind):
                        w.writerow([school, self.names.get(school, ""), kind, result, m])
        items = path.with_name(path.stem + "_items" + path.suffix)
        with open(items, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["학교 코드", "종류", "구 번호", "결과"])
            w.writerows(self.items)
        return items

    def summary(self):
        by_kind = defaultdict(Counter)
        for (_, kind, result), n in self.counts.items():
            by_kind[kind][result] += n
        lines = []
        for kind in (ACCOUNT, SCHOOL, SEAT, MANAGER, EVENT, DUTY, DUTY_DAYS, COMMENT, EFILE, CFILE, NOTICE, NFILE, NCOMMENT):
            c = by_kind.get(kind)
            if not c:
                continue
            put = sum(n for r, n in c.items() if r.startswith(IN))
            dropped = sum(n for r, n in c.items() if r.startswith("버림"))
            lines.append(f"{kind}: 원본 {sum(c.values())} · 넣음 {put} · 이미 넣음 {c[DONE]} · 합침 {c[MERGED]} · 버림 {dropped}")
            for r, n in sorted(c.items(), key=lambda x: -x[1]):
                if r not in (IN, DONE, MERGED):
                    lines.append(f"    {r} {n}")
        return lines


class Importer:
    def __init__(self, dump, report, dry_run=False):
        self.dump, self.report, self.dry = dump, report, dry_run
        self.done = defaultdict(dict)
        for kind, lid, oid in LegacyMap.objects.values_list("kind", "legacy_id", "obj_id"):
            self.done[kind][lid] = oid
        self.new_maps = []
        self.saved = []  # 이번 실행에서 MEDIA에 쓴 파일(실패하면 run이 지운다)
        self.counts = {}  # 일정·댓글별 첨부 수
        self.dry_bytes = Counter()  # 마른 실행에서 공간별로 넣었을 크기(저장 한도 계산용)

    def _map(self, kind, lid, oid):
        self.done[kind][lid] = oid
        self.new_maps.append(LegacyMap(kind=kind, legacy_id=lid, obj_id=oid))

    def _flush_maps(self):
        LegacyMap.objects.bulk_create(self.new_maps, batch_size=BATCH)
        self.new_maps = []

    # 계정 -----------------------------------------------------------------------------------------
    def users(self):
        User = get_user_model()
        taken = set(User.objects.annotate(u=Lower("username")).values_list("u", flat=True))
        new, now = [], timezone.now()
        for m in self.dump.members:
            lid = str(m["mb_no"])
            if _gone(m):
                self.report.add("", ACCOUNT, drop("탈퇴·차단"), lid)
            elif _system(m):
                self.report.add("", ACCOUNT, drop("시스템·운영 계정"), lid)
            elif lid in self.done["user"]:
                self.report.add("", ACCOUNT, DONE)
            elif not ID_RE.fullmatch(m["mb_id"]):  # 구글·손님 접두사(:)를 흉내 낸 아이디 차단(P1 규칙)
                self.report.add("", ACCOUNT, drop("아이디 규칙 밖(멈춤)"), lid)
            elif m["mb_id"].lower() in taken:
                self.report.add("", ACCOUNT, drop("같은 아이디가 이미 있음(멈춤)"), lid)
            else:
                taken.add(m["mb_id"].lower())
                pw = password_of(m["mb_password"])
                u = User(username=m["mb_id"], password=pw or make_password(None), first_name=m["mb_name"].strip()[:150],
                         email="", date_joined=moment(m["mb_datetime"]) or now, last_login=moment(m["mb_today_login"]))
                new.append((lid, u))
                self.report.add("", ACCOUNT, IN if pw else f"{IN}·구 해시 형식 아님(비밀번호 쓸 수 없게)", lid)
        with transaction.atomic():
            User.objects.bulk_create([u for _, u in new], batch_size=BATCH)
            for lid, u in new:
                self._map("user", lid, u.pk)
            self._flush_maps()

    # 학교 -----------------------------------------------------------------------------------------
    def school(self, code):
        row = self.dump.schools.get(code)
        if row is None:
            self.report.add(code, SCHOOL, drop("덤프의 school 표에 없음"), code)
            return
        self.report.names[code] = row["name"].strip()
        with transaction.atomic():
            space = self._space(code, row)
            if space is None:
                return
            seats = self._seats(code, space)
            events = self._events(code, space, seats)
            comments = self._comments(code, events, seats)
            self._event_files(code, space, events)
            self._comment_files(code, space, comments)
            self._notices(code, space, seats)
            self._flush_maps()

    def _space(self, code, row):
        if code in self.done["school"]:
            self.report.add(code, SCHOOL, DONE)
            return Space.objects.get(school_id=self.done["school"][code], grade=0)
        school = School.objects.filter(neis_atpt=row["code_sido"], neis_code=code).first()
        if school is not None and school.spaces.exists():
            self.report.add(code, SCHOOL, drop("이미 공간이 있는 학교(멈춤)"), code)
            return None
        school = school or School(neis_atpt=row["code_sido"], neis_code=code, name=row["name"].strip()[:100])
        school.plan, school.paid_until = School.Plan.SCHOOL, PAID_UNTIL
        school.save()
        space = Space.objects.create(school=school, kind=Space.Kind.SCHOOL, grade=0, name=school.name)
        self._map("school", code, school.pk)
        self.report.add(code, SCHOOL, IN, code)
        return space

    def _seats(self, code, space):
        """이관 학교의 가입 회원(D2) → 자리. 돌려주는 값: {구 아이디: Member}(이전 실행에서 넣은 자리 포함)."""
        have = {m.pk: m for m in Member.objects.in_space(space)}
        taken = {(m.cls, m.name) for m in have.values()}
        seats, new = {}, []
        rows = sorted((m for m in self.dump.members if m["mb_1"] == code and m["mb_5"] == "1"),
                      key=lambda m: (m["mb_datetime"], m["mb_no"]))
        for m in rows:
            lid = str(m["mb_no"])
            if _gone(m):
                self.report.add(code, SEAT, drop("탈퇴·차단"), lid)
                continue
            if _system(m):
                self.report.add(code, SEAT, drop("시스템·운영 계정"), lid)
                continue
            if lid in self.done["member"]:
                if self.done["member"][lid] in have:
                    seats[m["mb_id"]] = have[self.done["member"][lid]]
                self.report.add(code, SEAT, DONE)
                continue
            if lid not in self.done["user"]:
                self.report.add(code, SEAT, drop("계정을 만들지 못함(아이디 겹침·규칙 밖)"), lid)
                continue
            g = self.dump.groups.get(int(m["mb_3"])) if m["mb_3"].strip().isdigit() else None
            gname = g["group_name"] if g and g["school_id"] == code else None
            fields = seat_fields(gname, m["mb_4"])
            if fields is None:
                self.report.add(code, SEAT, drop("전출·휴직 그룹"), lid)
                continue
            name = unique_name(m["mb_name"], fields["cls"], taken)
            note = "·".join(x for x in ("그룹 없음(기타/지원)" if gname is None else "",
                                        "같은 반·이름이라 번호 붙임" if name != (m["mb_name"].strip()[:40] or "이름 없음") else "") if x)
            seat = Member(space=space, user_id=self.done["user"][lid], name=name,
                          is_manager=m["mb_id"] in self.dump.admins.get(code, ()), **fields)
            new.append((lid, m, seat))
            seats[m["mb_id"]] = seat
            self.report.add(code, SEAT, f"{IN}·{note}" if note else IN, lid)
        Member.objects.bulk_create([s for _, _, s in new], batch_size=BATCH)
        for lid, m, seat in new:
            seat.joined_at = moment(m["mb_datetime"]) or seat.joined_at
            self._map("member", lid, seat.pk)
        Member.objects.bulk_update([s for _, _, s in new], ["joined_at"], batch_size=BATCH)
        managers = Member.objects.in_space(space).filter(is_manager=True)
        if not managers.exists():
            first = Member.objects.in_space(space).exclude(user=None).order_by("joined_at", "pk").first()
            if first is not None:
                Member.objects.filter(pk=first.pk).update(is_manager=True)
                self.report.add(code, MANAGER, f"{IN}·관리자 없어 첫 가입자")
            else:
                self.report.add(code, MANAGER, drop("자리가 없어 관리자 없음"), code)
        elif any(s.is_manager for _, _, s in new):
            self.report.add(code, MANAGER, IN, n=sum(1 for _, _, s in new if s.is_manager))
        return seats

    # 일정·복무 --------------------------------------------------------------------------------------
    def _group_scope(self, wr_7, code):
        """wr_4=4의 wr_7: 같은 학교 그룹이면 그 소속, 아니면 옛 학년 코드. 둘 다 아니면 None."""
        if not wr_7.strip().isdigit():
            return None
        n = int(wr_7)
        g = self.dump.groups.get(n)
        if g and g["school_id"] == code:
            return group_of(g["group_name"])
        return OLD_CODES.get(n)

    def _plan(self, r, code, seats):
        """일정 한 행 → ("drop", 까닭) | ("duty", (이름, 유형, 시간), 날짜들) | ("event", 칸들, 중복 키)."""
        author = seats.get(r["mb_id"])
        if r["mb_id"] == BOT:
            return ("drop", "NEIS 봇")
        if r["wr_10"].strip() == "hidden":
            return ("drop", "숨긴 NEIS 행")
        start = day(r["wr_1"])
        if start is None:
            return ("drop", "날짜 없음")
        end = day(r["wr_2"])
        end = end if end and end > start else None
        ch = r["wr_4"].strip()
        if ch == "2":
            parsed, why = duty_of(r["wr_subject"])
            if parsed is None:
                return ("drop", why)
            return ("duty", parsed, [start + timedelta(days=i) for i in range(((end or start) - start).days + 1)])
        if not ch.isdigit():
            return ("drop", "채널 모름")
        ch, prefix = int(ch), ""
        mine = author.group if author else None
        if r["wr_8"].strip():
            scope = ME
        elif ch in (0, 1):
            scope = ALL
        elif ch == 3:
            scope = ME
        elif ch == 4:
            scope = self._group_scope(r["wr_7"], code) or mine or ME
        else:
            prefix = f"[{self.dump.channels.get(ch) or '채널'}] "
            scope = ALL  # D5(리드 결정): 구독자가 대부분이라 채널은 사실상 학교 공용 달력
        if scope == ME and author is None:
            return ("drop", "개인 일정인데 작성자 자리 없음")
        title = (prefix + (r["wr_subject"].strip() or NO_TITLE))[:200]
        fields = {"date": start, "end": end, "title": title, "scope": scope, "by": author, "body": body_of(r)}
        return ("event", fields, (r["mb_id"], start, end, title, scope))

    def _events(self, code, space, seats):
        """돌려주는 값: {wr_id: ("event", pk) | ("duty", pk)} — 이 학교에서 옮긴(또는 이미 옮긴) 일정 행."""
        rows = self.dump.events.get(code, [])
        plans = [(r, self._plan(r, code, seats)) for r in rows]
        out, owner = {}, {}
        for r, p in plans:  # 이전 실행에서 넣은 것부터: 중복 묶음의 새 행이 거기로 합쳐지게
            lid = str(r["wr_id"])
            if lid in self.done["event"]:
                out[r["wr_id"]] = ("event", self.done["event"][lid])
                if p[0] == "event":
                    owner.setdefault(p[2], self.done["event"][lid])
            elif lid in self.done["attendance"]:
                out[r["wr_id"]] = ("duty", self.done["attendance"][lid])
        names = Counter(m.name for m in Member.objects.in_space(space))
        by_name = {m.name: m for m in Member.objects.in_space(space) if names[m.name] == 1}
        roster = list(names)
        events, merges, duties = [], [], []
        for r, p in plans:
            lid = str(r["wr_id"])
            if r["wr_id"] in out:
                self.report.add(code, DUTY if out[r["wr_id"]][0] == "duty" else EVENT, DONE)
                continue
            if p[0] == "drop":
                self.report.add(code, DUTY if r["wr_4"].strip() == "2" else EVENT, drop(p[1]), lid)
            elif p[0] == "duty":
                (raw, kind, time), days = p[1], p[2]
                name = duty_name(raw, roster)
                if name is None:
                    self.report.add(code, DUTY, drop("복무 이름 판별 못 함"), lid)
                    continue
                at = moment(r["wr_datetime"])
                rows_ = [Attendance(space=space, date=d, name=name, member=by_name.get(name), kind=kind, time_text=time,
                                    by=seats.get(r["mb_id"])) for d in days]
                duties.append((lid, r["wr_id"], at, rows_))
                self.report.add(code, DUTY, IN, lid)
                self.report.add(code, DUTY_DAYS, IN, n=len(rows_))
            elif p[2] in owner:
                merges.append((lid, r["wr_id"], p[2]))
                self.report.add(code, EVENT, MERGED, lid)
            else:
                e = Event(space=space, **p[1])
                e.legacy_at = moment(r["wr_datetime"])
                owner[p[2]] = e
                events.append((lid, r["wr_id"], e))
                body = p[1]["body"]
                self.report.add(code, EVENT, f"{IN}·본문 1만 자 넘음" if len(body) > 10000 else IN, lid)
        Event.objects.bulk_create([e for _, _, e in events], batch_size=BATCH)
        for _, _, e in events:
            e.created_at = e.updated_at = e.legacy_at or e.created_at  # 바뀐 것 목록에 옛 일정이 뜨지 않게 둘 다
        Event.objects.bulk_update([e for _, _, e in events], ["created_at", "updated_at"], batch_size=BATCH)
        for lid, wr_id, e in events:
            self._map("event", lid, e.pk)
            out[wr_id] = ("event", e.pk)
        for lid, wr_id, key in merges:
            pk = owner[key] if isinstance(owner[key], int) else owner[key].pk
            self._map("event", lid, pk)
            out[wr_id] = ("event", pk)
        all_rows = [a for *_, rs in duties for a in rs]
        Attendance.objects.bulk_create(all_rows, batch_size=BATCH)
        for lid, wr_id, at, rs in duties:
            for a in rs:
                a.created_at = at or a.created_at
            self._map("attendance", lid, rs[0].pk)
            out[wr_id] = ("duty", rs[0].pk)
        Attendance.objects.bulk_update(all_rows, ["created_at"], batch_size=BATCH)
        return out

    def _comments(self, code, events, seats):
        """돌려주는 값: {댓글 wr_id: (일정 pk, 댓글 pk)} 또는 {댓글 wr_id: 결과 글자}(합침·버림) — 댓글 첨부가 따른다."""
        seen, new, out = set(), [], {}
        for wr_id, (what, pk) in events.items():
            for c in self.dump.comments.get(wr_id, []):
                lid = str(c["wr_id"])
                key = (pk, c["mb_id"], c["wr_datetime"], c["wr_content"])
                if lid in self.done["comment"]:
                    seen.add(key)
                    out[c["wr_id"]] = (pk, self.done["comment"][lid])
                    self.report.add(code, COMMENT, DONE)
                elif what == "duty":
                    out[c["wr_id"]] = drop("복무 댓글")
                    self.report.add(code, COMMENT, out[c["wr_id"]], lid)
                elif key in seen:  # 복사로 생긴 중복 일정에 같이 복사된 댓글
                    out[c["wr_id"]] = MERGED
                    self.report.add(code, COMMENT, MERGED, lid)
                else:
                    seen.add(key)
                    seat = seats.get(c["mb_id"])
                    name = seat.name if seat else (c["wr_name"].strip()[:40] or "이름 없음")
                    new.append((lid, c["wr_id"], EventComment(event_id=pk, by=seat, by_name=name, body=body_of(c),
                                                              at=moment(c["wr_datetime"]) or timezone.now())))
                    self.report.add(code, COMMENT, IN, lid)
        EventComment.objects.bulk_create([x for *_, x in new], batch_size=BATCH)
        for lid, wr_id, x in new:
            self._map("comment", lid, x.pk)
            out[wr_id] = (x.event_id, x.pk)
        # 옮기지 않은 일정 행의 댓글
        for r in self.dump.events.get(code, []):
            if r["wr_id"] not in events:
                for c in self.dump.comments.get(r["wr_id"], []):
                    out[c["wr_id"]] = drop("원글을 옮기지 않음")
                    self.report.add(code, COMMENT, out[c["wr_id"]], str(c["wr_id"]))
        return out

    def _comment_files(self, code, space, comments):
        """P5 댓글 첨부: 댓글을 따라간다(댓글이 버려졌으면 같은 까닭). 댓글당 COMMENT_FILES개까지."""
        for wr_id, target in comments.items():
            files = sorted(self.dump.files.get(("goteacher", wr_id), []), key=lambda f: f["bf_no"])
            for f in files:
                lid = f"goteacher:{f['wr_id']}:{f['bf_no']}"
                if isinstance(target, str):
                    self.report.add(code, CFILE, target, lid)
                elif lid in self.done["file"]:
                    self.report.add(code, CFILE, DONE)
                elif self._count(("c", target[1]), EventFile.objects.filter(comment_id=target[1])) >= COMMENT_FILES:
                    self.report.add(code, CFILE, drop(f"댓글당 첨부 {COMMENT_FILES}개 넘음"), lid)
                else:
                    event_id, comment_id = target
                    if self._attach(code, CFILE, f, space, lambda k, name, size, at: EventFile(
                            event_id=event_id, comment_id=comment_id, name=name, kind=k, stored=size, at=at)) is not None:
                        self.counts[("c", comment_id)] += 1

    def _count(self, key, qs):
        """이미 있는 첨부 수(이전 실행 포함). 이번 실행에서 넣은 것은 부르는 쪽이 더한다(마른 실행도 같게)."""
        if key not in self.counts:
            self.counts[key] = qs.count()
        return self.counts[key]

    def _attach(self, code, kind, f, space, make):
        """첨부 한 개. make(형식, 이름, 크기, 시각) → 저장한 행. 돌려주는 값: 행 pk(마른 실행이면 0) 또는 None(버림).
        업로드와 같은 개당 크기·학교 저장 한도를 지킨다(이관 첨부도 한도에 센다, D3-a)."""
        lid = f"{f['bo_table']}:{f['wr_id']}:{f['bf_no']}"
        if not f["bf_file"].strip():
            self.report.add(code, kind, drop("파일 칸 빈 값"), lid)
            return None
        path = self.dump.path(f)
        if path is None:
            self.report.add(code, kind, drop("첨부 경로가 덤프 밖"), lid)
            return None
        if not path.is_file():
            self.report.add(code, kind, drop("디스크에 없음"), lid)
            return None
        try:
            fkind, name = judge_file(path, f["bf_source"])
        except Rejected as e:
            self.report.add(code, kind, drop(str(e)), lid)
            return None
        size = path.stat().st_size
        if size > MAX_FILE:
            self.report.add(code, kind, drop(f"{MAX_FILE // 1024 // 1024}MB 넘음"), lid)
            return None
        if size > storage_room(space)[0] - self.dry_bytes[space.pk]:
            self.report.add(code, kind, drop("저장 한도 넘음"), lid)
            return None
        self.report.add(code, kind, IN, lid)
        if self.dry:
            self.dry_bytes[space.pk] += size
            return 0
        row = make(fkind, name, size, moment(f["bf_datetime"]) or timezone.now())
        with open(path, "rb") as fh:
            row.file.save(fkind, File(fh), save=False)
            self.saved.append((row.file.storage, row.file.name))
            row.save()
        self._map("file", lid, row.pk)
        return row.pk

    def _event_files(self, code, space, events):
        seen = {}
        for r in self.dump.events.get(code, []):
            files = self.dump.files.get(("goteacher", r["wr_id"]), [])
            target = events.get(r["wr_id"])
            for f in files:
                lid = f"goteacher:{f['wr_id']}:{f['bf_no']}"
                if lid in self.done["file"]:
                    if target:
                        seen[(target[1], f["bf_source"], f["bf_filesize"])] = True
                    self.report.add(code, EFILE, DONE)
                elif target is None:
                    self.report.add(code, EFILE, drop("원글을 옮기지 않음"), lid)
                elif target[0] == "duty":
                    self.report.add(code, EFILE, drop("복무 첨부"), lid)
                elif (target[1], f["bf_source"], f["bf_filesize"]) in seen:
                    self.report.add(code, EFILE, MERGED, lid)
                elif self._count(("e", target[1]), EventFile.objects.filter(event_id=target[1], comment=None)) >= MAX_FILES:
                    self.report.add(code, EFILE, drop(f"일정당 첨부 {MAX_FILES}개 넘음"), lid)
                else:
                    seen[(target[1], f["bf_source"], f["bf_filesize"])] = True
                    if self._attach(code, EFILE, f, space, lambda k, n, size, at, pk=target[1]: EventFile(
                            event_id=pk, name=n, kind=k, stored=size, at=at)) is not None:
                        self.counts[("e", target[1])] += 1

    # 게시판 ---------------------------------------------------------------------------------------
    def _notices(self, code, space, seats):
        for r in self.dump.board.get(code, []):
            lid = str(r["wr_id"])
            for c in self.dump.board_comments.get(r["wr_id"], []):
                self.report.add(code, NCOMMENT, drop("게시판 댓글"), str(c["wr_id"]))
                for f in self.dump.files.get(("free_school", c["wr_id"]), []):
                    self.report.add(code, NFILE, drop("댓글 첨부"), f"free_school:{f['wr_id']}:{f['bf_no']}")
            files = self.dump.files.get(("free_school", r["wr_id"]), [])
            if lid in self.done["notice"]:
                self.report.add(code, NOTICE, DONE)
                for f in files:
                    flid = f"free_school:{f['wr_id']}:{f['bf_no']}"
                    self.report.add(code, NFILE, DONE if flid in self.done["file"] else drop("글을 이미 옮김(새 첨부는 안 더함)"), flid)
                continue
            seat = seats.get(r["mb_id"])
            by_name = seat.name if seat else (r["wr_name"].strip()[:40] or "이름 없음")
            at = moment(r["wr_datetime"]) or timezone.now()
            n = Notice.objects.create(space=space, title=r["wr_subject"].strip()[:200] or NO_TITLE, by=seat, by_name=by_name,
                                      group=seat.group if seat else "", audience=ALL)
            self.report.add(code, NOTICE, IN, lid)
            kept = []
            for f in files:
                pk = self._attach(code, NFILE, f, space, lambda k, name, size, _at: NoticeFile(notice=n, name=name, kind=k, stored=size))
                if pk:
                    kept.append(pk)
            NoticeFile.objects.filter(pk__in=kept).update(at=at)
            listed = list(NoticeFile.objects.filter(pk__in=kept).order_by("pk").values("key", "name"))
            fields = {TITLE: n.title, **{k: "" for k in ITEMS}, AUDIENCE: "전체",
                      FILES: ", ".join(x["name"] for x in listed), FILE_LIST: listed}
            v = NoticeVersion.objects.create(notice=n, v=1, fields=fields, body=body_of(r), by=by_name)
            NoticeVersion.objects.filter(pk=v.pk).update(at=at)
            Notice.objects.filter(pk=n.pk).update(created_at=at, updated_at=at)
            self._map("notice", lid, n.pk)


def run(folder, schools=None, dry_run=False, report_path=None):
    """돌려주는 값: (Report, 학교 코드들, 보고서 항목 파일 경로)."""
    dump = Dump(folder)
    report = Report()
    codes = schools or dump.targets()
    for r in dump.events.get("", []):
        report.add("", DUTY if r["wr_4"].strip() == "2" else EVENT, drop("학교 코드 없음"), str(r["wr_id"]))
    for r in dump.board.get("", []):
        report.add("", NOTICE, drop("학교 코드 없음"), str(r["wr_id"]))
    imp = None
    try:
        with transaction.atomic():
            imp = Importer(dump, report, dry_run)
            imp.users()
            for code in codes:
                imp.school(code)
            if dry_run:
                transaction.set_rollback(True)
    except BaseException:
        for storage, name in imp.saved if imp else []:  # 되돌린 행의 파일이 MEDIA에 고아로 남지 않게
            storage.delete(name)
        raise
    items = report.write(report_path) if report_path else None
    return report, codes, items

