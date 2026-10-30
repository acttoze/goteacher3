"""E2 올린 한글 양식(SourceFile). 원본과 변환한 HWPX는 default storage(MEDIA_ROOT 볼륨)에 둔다.

- 공간 소속: space FK, 조회는 SpaceQuerySet.in_space()로만. 주소에는 key만 쓴다(순번 ID 안 씀).
- 저장 경로는 파일 이름을 쓰지 않고 hwp/<공간 번호>/<key>.<형식> 로 만든다(경로 조작 차단).
- 상한: 회원당 진행 중 변환 HWP_IN_FLIGHT건, 공간당 저장 HWP_SPACE_QUOTA·서비스 전체 HWP_TOTAL_QUOTA(원본+변환).
- MEDIA_URL로 서빙하지 않는다. 내려받기는 회원 확인 뷰로만(E6).
- 표 위치(E5): 교사가 미리보기에서 눌러 확정한 표 = (table_sec, table_index). report["tables"] 안에 있는 것만 저장한다.
  table_sec 은 구역 순번(hwp.tables 의 sec), table_index 는 구역 안 문서 순서(중첩 포함, hwp.schema 의 index).
  칸 매핑(E6): 칸 확인으로 만든 모으기(collection, 파일 하나에 하나)와 cellmap(collect/hwpform.cellmap — 줄마다
  [Field.pos 또는 unit·who, 행, 열, 단위]). F1 채우기가 이것만으로 hwp.fill 값을 만든다.
- 보관 기간 자동 삭제(파일 포함)는 E3.
- report["tables"]: hwp.tables 결과(표 구역·순서·행·열·칸 수·후보 순위, 글자 없음). 미리보기(E4)가 쓴다.
"""
import io
import re
import zipfile
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

import hwp
from hwp.engine import section_names

from spaces.models import Member, Space, SpaceQuerySet, new_token

# 변환 보고의 요소 종류(python-hwpx 이름) → 교사에게 보일 말. 모르는 이름은 '기타 요소'.
DROPPED_LABELS = {
    "range-tag": "형광펜·교정 부호 범위",
    "label-item": "라벨 문서 항목",
}

ERROR_TEXT = {
    "ConvertError": "이 한글 파일을 변환하지 못했어요. 암호·배포용 문서인지 확인하거나 한글에서 HWPX로 저장해 올려 주세요.",
    "UnsafeFile": "파일 구조가 안전하지 않아 열지 않았어요. 한글에서 다시 저장해 올려 주세요.",
    "Timeout": "변환이 너무 오래 걸려 멈췄어요. 한글에서 HWPX로 저장해 올려 주세요.",
}
ERROR_DEFAULT = "변환하지 못했어요. 한글에서 HWPX로 저장해 다시 올려 주세요."


def _original_path(instance, filename):
    """올린 파일 이름은 경로에 쓰지 않는다."""
    return f"hwp/{instance.space_id}/{instance.key}.{instance.kind}"


def _hwpx_path(instance, filename):
    return f"hwp/{instance.space_id}/{instance.key}.conv.hwpx"


class SourceFile(models.Model):
    class Kind(models.TextChoices):
        HWP = "hwp", "HWP"
        HWPX = "hwpx", "HWPX"

    class Status(models.TextChoices):
        PENDING = "pending", "대기"
        CONVERTING = "converting", "변환 중"
        DONE = "done", "완료"
        FAILED = "failed", "실패"

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="hwp_files")
    created_by = models.ForeignKey(
        Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="hwp_files"
    )
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    name = models.CharField("올린 파일 이름", max_length=200, blank=True)
    kind = models.CharField("판별 형식(매직 바이트)", max_length=4, choices=Kind.choices)
    size = models.PositiveIntegerField("원본 크기(바이트)")
    stored = models.PositiveBigIntegerField("디스크에 둔 크기(원본+변환, 바이트)", default=0)
    original = models.FileField("원본(변환 실패하면 바로 지움)", upload_to=_original_path, blank=True)
    hwpx = models.FileField("변환한 HWPX(원본이 hwp일 때만)", upload_to=_hwpx_path, blank=True)
    status = models.CharField("변환 상태", max_length=10, choices=Status.choices, default=Status.PENDING)
    report = models.JSONField(
        "변환 보고", default=dict, blank=True, help_text='{"unconverted": [...], "dropped": [...]} 요소 종류 이름만'
    )
    error = models.CharField("실패 종류(예외 이름)", max_length=40, blank=True)
    table_sec = models.PositiveSmallIntegerField("고른 표 구역 순번", null=True, blank=True)
    table_index = models.PositiveIntegerField("고른 표 순서(구역 안, 중첩 포함)", null=True, blank=True)
    collection = models.OneToOneField("collect.Collection", on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="source_file", verbose_name="이 파일로 만든 모으기")
    cellmap = models.JSONField("칸 매핑(E6)", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    converted_at = models.DateTimeField(null=True, blank=True)

    objects = SpaceQuerySet.as_manager()

    def __str__(self):
        return self.key

    @classmethod
    def used_bytes(cls, space=None):
        """공간 하나의 저장 합계(올린 파일 + F1 채운 결과). space=None 이면 서비스 전체(서버 디스크 보호)."""
        total = 0
        for model in (SourceFile, ResultFile):
            qs = model.objects.in_space(space) if space is not None else model.objects.all()
            total += qs.aggregate(n=models.Sum("stored"))["n"] or 0
        return total

    @property
    def busy(self):
        return self.status in (self.Status.PENDING, self.Status.CONVERTING)

    def discard(self, error):
        """실패로 바꾸고 원본·결과 파일을 바로 지운다(stored 0). 변환 실패·멈춘 작업 정리가 같이 쓴다."""
        for f in (self.original, self.hwpx):
            if f:
                f.delete(save=False)
        SourceFile.objects.filter(pk=self.pk).update(
            status=self.Status.FAILED, error=error[:40], original="", hwpx="", stored=0
        )
        self.status, self.error, self.stored = self.Status.FAILED, error[:40], 0

    def expire_if_stuck(self):
        """작업이 시간 상한을 넘겼는데 아직 대기·변환 중이면(worker 메모리 초과로 끊김 등) 실패로 바꾼다."""
        limit = timedelta(seconds=settings.RQ_JOB_TIMEOUT * 2)
        if self.busy and self.created_at < timezone.now() - limit:
            self.discard("Timeout")

    def hwpx_file(self):
        """채우기에 쓸 HWPX. 원본이 hwpx면 원본 그대로."""
        return self.original if self.kind == self.Kind.HWPX else self.hwpx

    def table_at(self, sec, index):
        """변환 보고의 표 요약 하나(없으면 None). no 는 미리보기에 보이는 '표 N'(문서 전체 순서, 1부터)."""
        for no, t in enumerate(self.report.get("tables", []), 1):
            if t.get("sec") == sec and t.get("index") == index:
                return {**t, "no": no}
        return None

    @property
    def picked(self):
        """교사가 확정한 표의 요약(안 골랐으면 None)."""
        if self.table_index is None or self.table_sec is None:
            return None
        return self.table_at(self.table_sec, self.table_index)

    def picked_schema(self):
        """고른 표의 입력 칸(hwp.schema). 칸 확인(E6)이 쓴다. 고른 표가 없으면 None."""
        if self.picked is None:
            return None
        with self.hwpx_file().open("rb") as fh:
            data = fh.read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            section = section_names(z.namelist())[self.table_sec]
        return hwp.schema(data, section, self.table_index)

    def error_text(self):
        return ERROR_TEXT.get(self.error, ERROR_DEFAULT)

    def report_labels(self):
        """변환하며 빠진 요소를 교사에게 보일 말로(중복 없이)."""
        names = list(self.report.get("unconverted", [])) + list(self.report.get("dropped", []))
        out = []
        for n in names:
            label = next((v for k, v in DROPPED_LABELS.items() if n.startswith(k)), "기타 요소")
            if label not in out:
                out.append(label)
        return out


def lock_member(member):
    """회원 줄을 잠근다(transaction.atomic 안에서). 진행 중 작업 수를 세고 새 작업을 만드는 사이에
    같은 회원의 동시 요청이 끼어 상한을 넘지 못하게 한다."""
    Member.objects.select_for_update().filter(pk=member.pk).first()


def in_flight(space, member):
    """회원 한 명이 맡겨 둔 한글 작업 수(변환 + 채우기, 대기·진행 중). 상한은 HWP_IN_FLIGHT(E2와 같은 2건)."""
    S, R = SourceFile.Status, ResultFile.Status
    return (SourceFile.objects.in_space(space).filter(created_by=member, status__in=[S.PENDING, S.CONVERTING]).count()
            + ResultFile.objects.in_space(space).filter(created_by=member, status__in=[R.PENDING, R.FILLING]).count())


RESULT_ERROR_TEXT = {
    "FillError": "원본 표에 값을 넣지 못했어요. 병합 칸이 많은 표는 CSV나 합본 표 복사로 받아 주세요.",
    "NoValues": "아직 낸 선생님이 없어요. 한 분이라도 내면 다시 눌러 주세요.",
    "Quota": "동학년 저장 공간이 가득 찼어요. 보관 기간이 지난 파일이 지워진 뒤 다시 받아 주세요.",
    "Timeout": "한글 파일을 만드는 데 너무 오래 걸려 멈췄어요. CSV나 합본 표 복사로 받아 주세요.",
    "TooManyCopies": "신청서형 양식은 한 번에 {n}부까지 만들 수 있어요. CSV로 받아 주세요.",
}
RESULT_ERROR_DEFAULT = "한글 파일을 만들지 못했어요. 잠시 뒤 다시 눌러 주세요."
MAX_PROBLEMS = 10


def _result_path(instance, filename):
    """결과 파일 경로도 이름을 쓰지 않는다. filename 은 'file'·'hwpx' 둘 중 하나(작업이 넘김)."""
    ext = instance.fmt if filename == "file" else "hwpx"
    return f"hwp/{instance.space_id}/{instance.key}.out.{ext}{'.zip' if instance.bundle else ''}"


class ResultFile(models.Model):
    """F1 채운 결과(27번 4-1 SourceFile → ResultFile). 모으기의 제출 값을 원본 표에 넣은 파일.

    - file: 올린 형식(hwp.output_format — 기본은 올린 형식, GT_HWP_OUTPUT=hwpx 면 hwpx). hwpx면 비우고 hwpx 하나만.
    - hwpx: 늘 만든다. bundle(신청서형: 머리글 없는 표)이면 둘 다 선생님마다 한 부씩 담은 zip.
    - 검증(hwp.fill 의 재파싱·zip_ok, hwp.to_hwp 되읽기, 작업의 값 대조)을 통과한 것만 저장한다. 실패하면 파일 없음.
    - conflicts·problems 에는 표 주소 [행, 열](0부터)만. 값·이름은 넣지 않는다.
    - 모으기마다 마지막으로 끝난 결과 하나만 남긴다(새로 끝나면 앞의 파일을 지움).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "대기"
        FILLING = "filling", "만드는 중"
        DONE = "done", "완료"
        FAILED = "failed", "실패"

    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name="hwp_results")
    source = models.ForeignKey(SourceFile, on_delete=models.CASCADE, related_name="results")
    collection = models.ForeignKey("collect.Collection", on_delete=models.CASCADE, related_name="hwp_results")
    created_by = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="hwp_results")
    key = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    status = models.CharField("상태", max_length=10, choices=Status.choices, default=Status.PENDING)
    error = models.CharField("실패 종류(예외 이름)", max_length=40, blank=True)
    problems = models.JSONField("문제 칸 [[행, 열], …](값 없음)", default=list, blank=True)
    fmt = models.CharField("올린 형식으로 낸 파일 형식", max_length=4, choices=SourceFile.Kind.choices)
    bundle = models.BooleanField("선생님마다 한 부(zip)", default=False)
    file = models.FileField("올린 형식 결과(hwp일 때만)", upload_to=_result_path, blank=True)
    hwpx = models.FileField("HWPX 결과", upload_to=_result_path, blank=True)
    stored = models.PositiveBigIntegerField("디스크에 둔 크기(바이트)", default=0)
    conflicts = models.JSONField("같은 칸에 다른 값 [[행, 열], …]", default=list, blank=True)
    added = models.PositiveIntegerField("더한 표 줄 수", default=0)
    left_out = models.PositiveIntegerField("줄을 더하지 못해 못 넣은 줄 수", default=0)
    dropped = models.PositiveIntegerField("표에 칸이 없어 못 넣은 값 수", default=0)
    copies = models.PositiveIntegerField("만든 부수(신청서형)", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    done_at = models.DateTimeField(null=True, blank=True)

    objects = SpaceQuerySet.as_manager()

    def __str__(self):
        return self.key

    @property
    def busy(self):
        return self.status in (self.Status.PENDING, self.Status.FILLING)

    def drop_files(self):
        for f in (self.file, self.hwpx):
            if f:
                f.delete(save=False)
        self.stored = 0

    def discard(self, error, problems=()):
        """실패로 바꾸고 만든 파일을 지운다(검증 못 한 결과를 남기지 않음)."""
        self.drop_files()
        self.status, self.error = self.Status.FAILED, error[:40]
        self.problems = [list(p) for p in problems][:MAX_PROBLEMS]
        ResultFile.objects.filter(pk=self.pk).update(status=self.status, error=self.error, problems=self.problems,
                                                     file="", hwpx="", stored=0)

    def expire_if_stuck(self):
        limit = timedelta(seconds=settings.RQ_JOB_TIMEOUT * 2)
        if self.busy and self.created_at < timezone.now() - limit:
            self.discard("Timeout")

    def error_text(self):
        from .tasks import MAX_COPIES
        return RESULT_ERROR_TEXT.get(self.error, RESULT_ERROR_DEFAULT).replace("{n}", str(MAX_COPIES))

    def problem_text(self):
        """문제 칸 주소를 사람이 읽는 말로(1부터). 값은 없다."""
        return ", ".join(f"{r + 1}행 {c + 1}열" for r, c in self.problems if r is not None and c is not None)

    def names(self):
        """내려받을 파일 이름 {which: 이름}. 원본 이름_합본.hwp / .hwpx, 신청서형은 _선생님별_형식.zip."""
        stem = re.sub(r"\.(hwpx?)$", "", self.source.name, flags=re.I) or "한글양식"
        if self.bundle:
            out = {"hwpx": f"{stem}_선생님별_hwpx.zip"}
            if self.file:
                out["file"] = f"{stem}_선생님별_{self.fmt}.zip"
        else:
            out = {"hwpx": f"{stem}_합본.hwpx"}
            if self.file:
                out["file"] = f"{stem}_합본.{self.fmt}"
        return out
