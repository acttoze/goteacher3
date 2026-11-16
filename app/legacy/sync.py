"""V2 구 고티처(그누보드 MySQL) → 새 고티처 단방향 동기화. manage.py legacy_sync가 부른다.

- 구 DB에는 SELECT만 한다(MySQL.select가 다른 문장을 막는다). 트리거·표를 만들지 않는다.
- 대상은 이관 학교(LegacyMap kind=school)와 그 학교 회원만. 새 학교는 더하지 않는다.
- 증분: 새 번호(wr_id·mb_no)·새 첨부 시각(bf_datetime). 전체 대조: 이관 학교 행마다 지문(CRC32)을 받아 기억한 것과 견준다
  (그누보드는 고쳐도 번호·시각이 그대로라 고친 글은 전체 대조로만 잡힌다). 기억한 지문은 LegacyPrint.
- 바뀐 행이 있는 학교마다: 고친·지운 행의 새 쪽 객체를 고치거나 지우고 LegacyMap에서 빼고, 그 학교의 지금 구 행으로
  importer를 다시 돌린다(이미 넣은 것은 건너뛰고 빠진 것만 더함). 그래서 같은 상태의 전체 이관과 결과가 같다.
  일정은 혼자인 행을 고친 것이면 그 자리에서 고친다(주소·댓글·첨부 유지). 복사 중복으로 합친 일정·게시글은 지우고 다시 넣는다.
- 한 학교가 실패하면 바뀐 행을 하나씩 다시 해 보고, 실패한 행은 지문을 그대로 둬 다음 대조에서 다시 한다.
- 로그에는 학교 코드·종류·구 번호·까닭(예외 종류)만 쓴다(이름·내용 없음).
- 회원(보안 검토 반영): 비밀번호 해시는 구 mb_password가 실제로 바뀐 때만 덮고(지문 password를 따로 둔다) 그 사람의 기기 토큰을
  지운다(세션은 해시가 바뀌어 끊긴다). 새 쪽에서 바꾼·발급한 비밀번호는 구 비밀번호가 바뀌기 전까지 그대로.
  자리는 탈퇴·차단·전출 그룹·학교 변경(전근)일 때만 지운다(학교 코드를 바꾸면 구가 전원 mb_5=0으로 돌리므로 mb_5만으로는 안 지운다).
  구에서 회원 행이 아예 사라지면 탈퇴와 같게. is_active는 내리기만 한다. 관리자는 go_admin의 이전→현재 차이만 반영한다.
"""
import re
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone

from accounts.models import DeviceToken
from notices.models import Notice, NoticeFile
from spaces.models import Attendance, Member
from today.models import Event, EventComment, EventFile

from .importer import KEEP, Dump, Importer, Report, _gone
from .models import LegacyMap, LegacyPrint
from .rules import password_of, unique_name

KINDS = ("member", "password", "admin", "goteacher", "free_school", "file")
WRITES = {"goteacher", "free_school", "file"}  # 한 행씩 다시 할 때 다른 바뀐 행을 빼는 종류(회원은 작성자 자리라 빼지 않는다)
BOARD = ("wr_id", "wr_parent", "wr_is_comment", "mb_id", "wr_name", "wr_3", "wr_subject", "wr_content", "wr_option", "wr_datetime")
FILE = ("bo_table", "wr_id", "bf_no", "bf_source", "bf_file", "bf_filesize", "bf_datetime")
MEMBER = ("mb_no", "mb_id", "mb_password", "mb_name", "mb_datetime", "mb_today_login", "mb_leave_date", "mb_intercept_date",
          "mb_level", "mb_1", "mb_3", "mb_4", "mb_5")
DATES = {"wr_datetime", "bf_datetime", "mb_datetime", "mb_today_login"}  # 덤프와 같게 글자로(0000-00-00도 그대로)
TABLES = (("g5_write_goteacher", "goteacher"), ("g5_write_free_school", "free_school"))
SELECT_ONLY = re.compile(r"\s*SELECT\s(?![^;]*\b(?:INTO|FOR\s+UPDATE|FOR\s+SHARE|LOCK)\b)[^;]*\Z", re.IGNORECASE | re.DOTALL)


def _cols(alias, cols):
    return ", ".join(f"CAST({alias}.{c} AS CHAR) AS {c}" if c in DATES else f"{alias}.{c}" for c in cols)


def _crc(alias, cols):
    return f"CRC32(CONCAT_WS(0x1f, {', '.join(f'{alias}.{c}' for c in cols)}))"


def _write_prints(table, cols):
    """글 표의 지문: 댓글은 학교 칸(wr_3)이 비어 있어 원글(wr_parent)의 학교로 묶는다."""
    return (f"SELECT w.wr_id AS id, p.wr_3 AS school, {_crc('w', cols)} AS crc, w.wr_id AS cur FROM {table} w "
            f"JOIN {table} p ON p.wr_id = w.wr_parent WHERE p.wr_3 IN ({{codes}})", "w.wr_id >")


# 종류별 지문 질의: [(SQL, 증분 조건)]. 결과 칸: id·school·crc·cur(증분 기준 값)
PRINTS = {
    "goteacher": [_write_prints("g5_write_goteacher", KEEP[1:])],
    "free_school": [_write_prints("g5_write_free_school", BOARD[1:])],
    "file": [(f"SELECT CONCAT(f.bo_table, ':', f.wr_id, ':', f.bf_no) AS id, p.wr_3 AS school, {_crc('f', FILE[3:])} AS crc, "
              f"CAST(f.bf_datetime AS CHAR) AS cur FROM g5_board_file f JOIN {table} w ON w.wr_id = f.wr_id "
              f"JOIN {table} p ON p.wr_id = w.wr_parent WHERE f.bo_table = '{bo}' AND p.wr_3 IN ({{codes}})", "f.bf_datetime >=")
             for table, bo in TABLES],
    "member": [(f"SELECT m.mb_no AS id, m.mb_1 AS school, "
                f"{_crc('m', [c for c in MEMBER if c not in ('mb_no', 'mb_today_login', 'mb_password')])} "
                "AS crc, m.mb_no AS cur FROM g5_member m WHERE m.mb_1 IN ({codes})", "m.mb_no >")],
    # 비밀번호는 따로: 다른 칸이 바뀌어도 해시를 덮지 않게
    "password": [("SELECT m.mb_no AS id, m.mb_1 AS school, CRC32(m.mb_password) AS crc, m.mb_no AS cur FROM g5_member m "
                  "WHERE m.mb_1 IN ({codes})", "m.mb_no >")],
    # 관리자는 (학교:아이디)가 번호: 사라진 번호로 '구에서 빠진 사람'을 안다
    "admin": [("SELECT CONCAT(a.wr_3, ':', a.mb_id) AS id, a.wr_3 AS school, CRC32(a.mb_id) AS crc, a.wr_id AS cur "
               "FROM g5_write_go_admin a WHERE a.wr_is_comment = 0 AND a.wr_3 IN ({codes})", "a.wr_id >")],
}
SCHOOL_ROWS = {
    "g5_member": f"SELECT {_cols('m', MEMBER)} FROM g5_member m WHERE m.mb_1 = %s",
    "contact_group": "SELECT g.group_id, g.group_name, g.school_id FROM contact_group g WHERE g.school_id = %s",
    "school": "SELECT s.id, s.code_sido, s.name, s.grade FROM school s WHERE s.id = %s",
    "g5_write_channel": "SELECT c.wr_id, c.wr_3, c.wr_subject FROM g5_write_channel c",  # 일정 제목 접두([채널])는 학교와 무관하게 찾는다
    "g5_write_go_admin": "SELECT a.wr_id, a.wr_3, a.mb_id, a.wr_is_comment FROM g5_write_go_admin a WHERE a.wr_3 = %s",
    **{table: f"SELECT {_cols('w', cols)} FROM {table} w JOIN {table} p ON p.wr_id = w.wr_parent WHERE p.wr_3 = %s"
       for (table, _), cols in zip(TABLES, (KEEP, BOARD))},
    "g5_board_file": " UNION ALL ".join(
        f"SELECT {_cols('f', FILE)} FROM g5_board_file f JOIN {table} w ON w.wr_id = f.wr_id "
        f"JOIN {table} p ON p.wr_id = w.wr_parent WHERE f.bo_table = '{bo}' AND p.wr_3 = %s" for table, bo in TABLES),
}
ORDER = {"g5_write_goteacher": ("wr_id",), "g5_write_free_school": ("wr_id",), "g5_board_file": ("bo_table", "wr_id", "bf_no"),
         "g5_member": ("mb_no",)}  # 덤프와 같은 순서(복사 중복 댓글·첨부는 앞의 것이 남는다)


class MySQL:
    """구 DB(읽기 전용). conn: PyMySQL 연결(DictCursor), files: 구 첨부 폴더."""

    def __init__(self, conn, files):
        self.conn, self.files = conn, Path(files)

    @classmethod
    def connect(cls, url, files):
        import pymysql  # 동기화를 켤 때만 필요하다

        u = urlsplit(url)
        # TLS 없이 붙는다: 같은 호스트의 docker 내부 네트워크(mysql:3306)만 쓴다는 전제
        # autocommit=None: 접속 때 SET 문장을 보내지 않고 서버 기본값(자동 커밋)을 쓴다 → 매 SELECT가 최신 상태를 본다
        conn = pymysql.connect(host=u.hostname, port=u.port or 3306, user=unquote(u.username or ""),
                               password=unquote(u.password or ""), database=u.path.lstrip("/"), charset="utf8mb4",
                               autocommit=None, connect_timeout=10, read_timeout=120,
                               cursorclass=pymysql.cursors.DictCursor)
        return cls(conn, files)

    def close(self):
        if self.conn is not None:
            self.conn.close()

    def select(self, sql, args=()):
        if not SELECT_ONLY.match(sql):
            raise ValueError("구 DB에는 SELECT 한 문장만 보낸다")
        return self._run(sql, args)

    def _run(self, sql, args):
        with self.conn.cursor() as c:
            c.execute(sql, args)
            return list(c.fetchall())

    def prints(self, kind, codes, after=None):
        marks = ", ".join(["%s"] * len(codes))
        parts, args = [], []
        for sql, cond in PRINTS[kind]:
            sql, a = sql.format(codes=marks), list(codes)
            if after is not None:
                sql, a = f"{sql} AND {cond} %s", a + [after]
            parts.append(sql)
            args += a
        return self.select(" UNION ALL ".join(parts), args)

    def existing_members(self, mb_nos):
        """지금 구 DB에 있는 회원 번호(대상 학교 밖으로 옮긴 것과 아예 지운 것을 가른다)."""
        if not mb_nos:
            return set()
        rows = self.select(f"SELECT m.mb_no FROM g5_member m WHERE m.mb_no IN ({', '.join(['%s'] * len(mb_nos))})",
                           [int(x) for x in mb_nos])
        return {str(r["mb_no"]) for r in rows}

    def school(self, code, skip=()):
        """한 학교의 구 행(Dump가 읽는 표 이름별). skip: 빼고 읽을 (종류, 구 번호)."""
        tables = {}
        for table, sql in SCHOOL_ROWS.items():
            rows = self.select(sql, [code] * sql.count("%s"))
            if table in ORDER:
                rows.sort(key=lambda r: tuple(r[k] for k in ORDER[table]))
            tables[table] = rows
        if skip:
            for table, kind in TABLES:
                tables[table] = [r for r in tables[table] if (kind, str(r["wr_id"])) not in skip]
            tables["g5_board_file"] = [f for f in tables["g5_board_file"]
                                       if ("file", f"{f['bo_table']}:{f['wr_id']}:{f['bf_no']}") not in skip]
        return tables.__getitem__


class Sync:
    def __init__(self, source, log):
        self.source, self.log = source, log
        self.codes = sorted(LegacyMap.objects.filter(kind="school").values_list("legacy_id", flat=True))
        self.prints = {k: {} for k in KINDS}  # 종류 → {구 번호: (학교, 지문)}
        for kind, lid, school, crc in LegacyPrint.objects.values_list("kind", "legacy_id", "school", "crc"):
            self.prints[kind][lid] = (school, crc)
        self.cursor = {}

    def full(self):
        return self._cycle("대조", full=True)

    def step(self):
        return self._cycle("증분", full=False) if self.cursor else self.full()

    def _cycle(self, label, full):
        started = time.monotonic()
        if not self.codes:
            self.log(f"legacy_sync {label}: 이관 학교 없음")
            return {"changed": 0, "applied": 0, "failed": 0}
        first = full and not any(self.prints.values())
        changes, seen = defaultdict(dict), {}  # 학교 → {(종류, 구 번호): 지문 또는 None(지움)}
        for kind in KINDS:
            rows = self.source.prints(kind, self.codes, None if full else self.cursor[kind])
            seen[kind] = set()
            for r in rows:
                lid = str(r["id"])
                seen[kind].add(lid)
                if self.prints[kind].get(lid, (None, None))[1] != r["crc"]:  # 학교만 달라진 것은 바뀜이 아님(지문에 학교 칸이 든다)
                    changes[r["school"]][(kind, lid)] = r["crc"]
            self.cursor[kind] = max((r["cur"] for r in rows), default=self.cursor.get(kind, "" if kind == "file" else 0))
            if full:
                for lid, (school, _) in self.prints[kind].items():
                    if lid not in seen[kind]:
                        changes[school][(kind, lid)] = None
        if first:
            self._first(changes, seen)
        applied = failed = 0
        for code in sorted(changes):
            a, f = self._apply(code, changes[code])
            applied, failed = applied + a, failed + f
        n = sum(len(c) for c in changes.values())
        self.log(f"legacy_sync {label}: 바뀐 행 {n} · 반영 {applied} · 실패 {failed} · {time.monotonic() - started:.1f}초")
        return {"changed": n, "applied": applied, "failed": failed}

    def _first(self, changes, seen):
        """첫 실행(기억한 지문 없음): 이관 기록에 있는 행은 덤프 때와 같다고 보고 지문만 기억한다(덤프와 첫 실행 사이에 고친
        행은 다시 고쳐질 때 잡힌다). 이관 기록에 없는 행은 '바뀜'으로 둬 importer가 넣어 보게 하고(버린 행은 다시 버림),
        이관 기록에 있는데 구에서 사라진 행은 '지움'으로 더한다. 학교마다 한 번은 importer가 돈다."""
        maps = defaultdict(dict)
        for kind, lid, oid in LegacyMap.objects.values_list("kind", "legacy_id", "obj_id"):
            maps[kind][lid] = oid
        mapped = {"goteacher": {*maps["event"], *(x for x in maps["attendance"] if ":" not in x), *maps["comment"]},
                  "free_school": set(maps["notice"]), "file": set(maps["file"]), "member": set(maps["member"])}
        keep = []
        for code in self.codes:
            ch = changes.setdefault(code, {})
            for (kind, lid), crc in list(ch.items()):
                if crc is not None and (kind in ("admin", "password") or lid in mapped[kind]):
                    keep.append(LegacyPrint(kind=kind, legacy_id=lid, school=code, crc=crc))
                    del ch[(kind, lid)]
        LegacyPrint.objects.bulk_create(keep, batch_size=2000)
        for p in keep:
            self.prints[p.kind][p.legacy_id] = (p.school, p.crc)
        schools = {str(pk): code for code, pk in maps["school"].items()}
        owners = {"event": (Event, "space__school_id"), "attendance": (Attendance, "space__school_id"),
                  "comment": (EventComment, "event__space__school_id"), "notice": (Notice, "space__school_id"),
                  "member": (Member, "space__school_id"), "goteacher": (EventFile, "event__space__school_id"),
                  "free_school": (NoticeFile, "notice__space__school_id")}
        for kind, map_kinds in (("goteacher", ("event", "attendance", "comment")), ("free_school", ("notice",)),
                                ("file", ("file",)), ("member", ("member",))):
            for mk in map_kinds:
                for lid, oid in maps[mk].items():
                    base = lid.split(":")[0] if mk == "attendance" else lid
                    if base in seen[kind]:
                        continue
                    model, path = owners[lid.split(":")[0] if mk == "file" else mk]
                    school = model.objects.filter(pk=oid).values_list(path, flat=True).first()
                    if str(school) in schools:
                        changes[schools[str(school)]][(kind, base)] = None
                    else:  # 새 쪽 객체도 이미 없음
                        LegacyMap.objects.filter(kind=mk, legacy_id=lid).delete()

    def _apply(self, code, ch):
        """돌려주는 값: (반영한 행 수, 실패한 행 수)."""
        try:
            self._run(code, ch)
            return len(ch), 0
        except Exception as e:
            if len(ch) == 1:
                (kind, lid), = ch
                self.log(f"legacy_sync {code} {kind} {lid} 실패: {type(e).__name__}")
                return 0, 1
            self.log(f"legacy_sync {code} 학교 한꺼번에 실패({type(e).__name__}), 한 행씩 다시")
        ok = failed = 0
        for key, crc in ch.items():
            skip = {k for k, c in ch.items() if k != key and k[0] in WRITES and c is not None}
            try:
                self._run(code, {key: crc}, skip)
                ok += 1
            except Exception as e:
                failed += 1
                self.log(f"legacy_sync {code} {key[0]} {key[1]} 실패: {type(e).__name__}")
        return ok, failed

    def _run(self, code, ch, skip=()):
        imp = None
        try:
            with transaction.atomic():
                gone = [lid for (kind, lid), crc in ch.items() if kind == "member" and crc is None]
                imp = _Refresh(Dump(self.source.school(code, skip), self.source.files), ch,
                               self.source.existing_members(gone))
                imp.users()
                imp.school(code)
                gone = defaultdict(list)
                for (kind, lid), crc in ch.items():
                    if crc is None:
                        gone[kind].append(lid)
                for kind, lids in gone.items():
                    LegacyPrint.objects.filter(kind=kind, legacy_id__in=lids).delete()
                LegacyPrint.objects.bulk_create(
                    [LegacyPrint(kind=k, legacy_id=lid, school=code, crc=c) for (k, lid), c in ch.items() if c is not None],
                    update_conflicts=True, unique_fields=["kind", "legacy_id"], update_fields=["school", "crc"], batch_size=2000)
        except BaseException:
            for storage, name in imp.saved if imp else []:  # 되돌린 행의 첨부가 MEDIA에 고아로 남지 않게
                storage.delete(name)
            raise
        for (kind, lid), crc in ch.items():
            if crc is None:
                self.prints[kind].pop(lid, None)
            else:
                self.prints[kind][lid] = (code, crc)


class _Refresh(Importer):
    """importer + 고친·지운 구 행의 새 쪽 객체 정리. ch: {(종류, 구 번호): 지문 또는 None(지움)}."""

    def __init__(self, dump, ch, existing=()):
        super().__init__(dump, Report())
        self.existing = set(existing)  # 지금 학교에서 사라진 회원 중 구 DB에 아직 있는 번호(다른 학교로 옮김)
        self.ch = defaultdict(dict)
        for (kind, lid), crc in ch.items():
            self.ch[kind][lid] = crc
        self.plans = {}

    def _plan(self, r, code, seats):  # 한 번 돌 때 자리가 그대로라 행마다 한 번만(본문 HTML 풀기가 무겁다)
        if r["wr_id"] not in self.plans:
            self.plans[r["wr_id"]] = super()._plan(r, code, seats)
        return self.plans[r["wr_id"]]

    def _seats(self, code, space):
        self._members(code, space)
        seats = super()._seats(code, space)
        self._writes(code, space, seats)
        return seats

    def _unmap(self, kind, lids):
        LegacyMap.objects.filter(kind=kind, legacy_id__in=lids).delete()
        for lid in lids:
            self.done[kind].pop(lid, None)

    def _unmap_objs(self, kind, pks, prefix=""):
        q = LegacyMap.objects.filter(kind=kind, obj_id__in=list(pks))
        if prefix:
            q = q.filter(legacy_id__startswith=prefix)
        self._unmap(kind, list(q.values_list("legacy_id", flat=True)))

    # 회원 -----------------------------------------------------------------------------------------
    def _members(self, code, space):
        """바뀐 회원. 비밀번호는 지문 password가 바뀐 때만(+기기 토큰 지움). 계정 이름은 따라가고, 탈퇴·차단·삭제면 로그인을 막는다
        (다시 풀어 주지는 않는다). 자리는 탈퇴·차단·전출 그룹·학교 변경·구에서 삭제일 때만 지우고, 그 밖에는 소속·반·직책·이름만
        고친다. 같은 학교에서 mb_5만 풀린 회원의 자리는 그대로 둔다. 새 자리는 importer가 만든다."""
        rows = {str(m["mb_no"]): m for m in self.dump.members}
        users = get_user_model().objects
        for lid, crc in self.ch["password"].items():
            m, uid = rows.get(lid), self.done["user"].get(lid)
            if crc is None or m is None or uid is None:
                continue
            u = users.filter(pk=uid).first()
            pw = password_of(m["mb_password"])
            if u is None or (u.password == pw if pw else not u.has_usable_password()):
                continue  # 이번에 importer가 만든 계정이거나 이미 같다
            users.filter(pk=uid).update(password=pw or make_password(None))  # 세션 인증 해시가 바뀌어 다른 세션도 끊긴다
            DeviceToken.objects.filter(user_id=uid).delete()
        for lid in self.ch["member"]:
            m, uid = rows.get(lid), self.done["user"].get(lid)
            deleted = m is None and lid not in self.existing
            if uid and (deleted or (m is not None and _gone(m))):
                users.filter(pk=uid).update(is_active=False)
                DeviceToken.objects.filter(user_id=uid).delete()
            elif uid and m is not None:
                users.filter(pk=uid).update(first_name=m["mb_name"].strip()[:150])
            pk = self.done["member"].get(lid)
            seat = Member.objects.filter(pk=pk).first() if pk else None
            if seat is None:
                if pk:
                    self._unmap("member", [lid])  # 새 쪽에서 지운 자리: importer가 조건이 맞으면 다시 만든다
                continue
            # 학교가 바뀜(전근, 리드 결정): 옛 학교 자리를 지운다. 새 학교가 이관 대상이고 mb_5='1'이면 importer가 새 자리를 만든다
            moved = m is None or m["mb_1"] != code or seat.space_id != space.pk
            want = None if moved else self._seat_of(code, m)[0]
            if moved or _gone(m) or want is None:
                seat.delete()
                self._unmap("member", [lid])
                continue
            taken = {(x.cls, x.name) for x in Member.objects.in_space(space).exclude(pk=seat.pk)}
            Member.objects.filter(pk=seat.pk).update(name=unique_name(m["mb_name"], want["cls"], taken), **want)
        seats = {m["mb_id"]: self.done["member"][str(m["mb_no"])] for m in self.dump.members if str(m["mb_no"]) in self.done["member"]}
        admins = self.dump.admins.get(code, set())
        for lid, crc in self.ch["admin"].items():
            mb_id = lid.split(":", 1)[1]
            if mb_id in seats and (crc is not None or mb_id not in admins):
                Member.objects.in_space(space).filter(pk=seats[mb_id]).update(is_manager=crc is not None)

    # 일정·댓글·첨부·게시글 ---------------------------------------------------------------------------
    def _writes(self, code, space, seats):
        gt, fs, fl = self.ch["goteacher"], self.ch["free_school"], self.ch["file"]
        rows = {r["wr_id"]: r for r in self.dump.events.get(code, [])}
        rebuild, notices = set(), set()
        for lid in gt:
            if lid in self.done["comment"]:
                self._drop_comment(lid)
            elif lid in self.done["attendance"]:
                self._drop_duty(lid)
            elif lid in self.done["event"]:
                r = rows.get(int(lid))
                if r is None or not self._update_event(code, space, seats, r):
                    rebuild.add(self.done["event"][lid])
        for pk in rebuild:
            self._drop_event(pk)
        for lid in fl:
            bo, wr_id, _ = lid.split(":")
            if bo == "free_school":
                if wr_id in self.done["notice"]:  # 이미 옮긴 글에는 첨부를 더하지 않으므로 글째 다시 넣는다
                    notices.add(wr_id)
            elif lid in self.done["file"]:
                EventFile.objects.filter(pk=self.done["file"][lid]).delete()
                self._unmap("file", [lid])
        notices.update(lid for lid in fs if lid in self.done["notice"])
        for lid in notices:
            self._drop_notice(lid)

    def _update_event(self, code, space, seats, r):
        """혼자인 일정 행을 고친 것이면 그 자리에서 고치고 True. 합친 묶음이거나, 일정이 아니게 됐거나(복무·버림),
        고친 뒤 다른 일정과 복사 중복이 되면 False(지우고 다시 넣어 importer가 합치게)."""
        lid, pk = str(r["wr_id"]), self.done["event"][str(r["wr_id"])]
        if [x for x, obj in self.done["event"].items() if obj == pk] != [lid]:
            return False
        p = self._plan(r, code, seats)
        if p[0] != "event":
            return False
        for o in self.dump.events.get(code, []):
            ol = str(o["wr_id"])
            if ol != lid and ol in self.done["event"] and self._plan(o, code, seats)[2:] == p[2:]:
                return False
        return bool(Event.objects.filter(pk=pk, space=space).update(**p[1], updated_at=timezone.now()))

    def _drop_event(self, pk):
        self._unmap_objs("event", [pk])
        self._unmap_objs("comment", EventComment.objects.filter(event_id=pk).values_list("pk", flat=True))
        self._unmap_objs("file", EventFile.objects.filter(event_id=pk).values_list("pk", flat=True), "goteacher:")
        Event.objects.filter(pk=pk).delete()  # 댓글·첨부 CASCADE, 디스크 파일은 커밋 뒤 post_delete

    def _drop_comment(self, lid):
        pk = self.done["comment"][lid]
        self._unmap_objs("file", EventFile.objects.filter(comment_id=pk).values_list("pk", flat=True), "goteacher:")
        self._unmap("comment", [lid])
        EventComment.objects.filter(pk=pk).delete()

    def _drop_duty(self, lid):
        lids = [lid, *(x for x in self.done["attendance"] if x.startswith(f"{lid}:"))]
        Attendance.objects.filter(pk__in=[self.done["attendance"][x] for x in lids]).delete()
        self._unmap("attendance", lids)

    def _drop_notice(self, lid):
        pk = self.done["notice"][lid]
        self._unmap_objs("file", NoticeFile.objects.filter(notice_id=pk).values_list("pk", flat=True), "free_school:")
        self._unmap("notice", [lid])
        Notice.objects.filter(pk=pk).delete()
