"""양식 자료실 일괄 등록: import_forms <forms.js 또는 .json> <hwpx 폴더>.

- 목록: prototype/assets/forms.js(TR.seedForms = [...]) 또는 같은 배열의 JSON 파일.
  쓰는 값은 id·ca·t·d·tips·file. dl·hit·at은 시연용 임의값이라 쓰지 않는다(수는 0, 날짜는 등록 시각).
- 파일은 폴더 아래(하위 폴더 포함)에서 file 이름으로 하나만 찾는다. hwp.sniff를 통과해야 저장한다.
- 같은 id는 고친다(중복 생성 없음, slug 유지). 파일이 같으면 다시 쓰지 않는다.
- 문제가 있는 줄은 건너뛰고 끝에 id·사유를 보여 준 뒤 오류로 끝난다(나머지는 들어간다).
"""
import json
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

import hwp
from library.models import CATEGORIES, FormDoc, make_slug


def _load(path):
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith(".js"):  # TR.seedForms = [ ... ];
        text = text[text.index("["): text.rindex("]") + 1]
    rows = json.loads(text)
    if not isinstance(rows, list):
        raise CommandError("목록은 배열이어야 해요.")
    return rows


def _check(r):
    ok = (isinstance(r, dict) and isinstance(r.get("id"), str) and 0 < len(r["id"]) <= 20
          and r.get("ca") in CATEGORIES and isinstance(r.get("t"), str) and 0 < len(r["t"].strip()) <= 200
          and isinstance(r.get("d"), str) and isinstance(r.get("tips", []), list)
          and all(isinstance(t, str) for t in r.get("tips", []))
          and isinstance(r.get("file"), str) and r["file"] == Path(r["file"]).name and r["file"])
    if not ok:
        raise ValueError("값 형식(id·분류·제목·설명·tips·file)")


class Command(BaseCommand):
    help = "양식 자료실 일괄 등록(같은 id는 고침). 예: import_forms /import/forms.js /import/files"

    def add_arguments(self, parser):
        parser.add_argument("list")
        parser.add_argument("folder")

    def handle(self, *args, **opts):
        rows = _load(opts["list"])
        folder = Path(opts["folder"])
        if not folder.is_dir():
            raise CommandError(f"폴더가 없어요: {folder}")
        files = {}
        for p in folder.rglob("*"):
            if p.is_file():
                files.setdefault(p.name, []).append(p)
        n = {"새로": 0, "고침": 0, "그대로": 0}
        bad = []
        for r in rows:
            rid = r.get("id") if isinstance(r, dict) else None
            try:
                _check(r)
                found = files.get(r["file"], [])
                if len(found) != 1:
                    raise ValueError("파일 없음" if not found else "같은 이름 파일이 여럿")
                data = found[0].read_bytes()
                if len(data) > hwp.MAX_UPLOAD:
                    raise ValueError("파일이 너무 큼")
                try:
                    kind = hwp.sniff(data)
                except hwp.HwpError as e:
                    raise ValueError(f"한글 파일이 아님({type(e).__name__})") from None
                n[self._save(r, data, kind)] += 1
            except ValueError as e:
                bad.append(f"{rid}: {e}")
        self.stdout.write(" · ".join(f"{k} {v}건" for k, v in n.items()) + f" · 거부 {len(bad)}건")
        if bad:
            raise CommandError("거부한 줄\n" + "\n".join(bad))

    @transaction.atomic
    def _save(self, r, data, kind):
        f = FormDoc.objects.select_for_update().filter(src=r["id"]).first()
        name = f"{Path(r['file']).stem}.{kind}"
        fields = {"category": r["ca"], "title": r["t"].strip(), "desc": r["d"].strip(),
                  "tips": [t.strip() for t in r.get("tips", []) if t.strip()], "filename": name}
        if f is None:
            taken = set(FormDoc.objects.values_list("slug", flat=True))
            f = FormDoc(src=r["id"], slug=make_slug(fields["title"], taken), size=len(data), **fields)
            f.file.save(name, ContentFile(data), save=False)
            f.save()
            return "새로"
        try:
            with f.file.open("rb") as fh:
                same_file = fh.read() == data and f.filename == name
        except FileNotFoundError:
            same_file = False
        changed = not same_file or any(getattr(f, k) != v for k, v in fields.items())
        for k, v in fields.items():
            setattr(f, k, v)
        if not same_file:
            f.file.delete(save=False)
            f.size = len(data)
            f.file.save(name, ContentFile(data), save=False)
        f.save()
        return "고침" if changed else "그대로"
