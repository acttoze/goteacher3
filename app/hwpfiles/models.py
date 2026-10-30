"""E2 올린 한글 양식(SourceFile). 원본과 변환한 HWPX는 default storage(MEDIA_ROOT 볼륨)에 둔다.

- 공간 소속: space FK, 조회는 SpaceQuerySet.in_space()로만. 주소에는 key만 쓴다(순번 ID 안 씀).
- 저장 경로는 파일 이름을 쓰지 않고 hwp/<공간 번호>/<key>.<형식> 로 만든다(경로 조작 차단).
- 상한: 회원당 진행 중 변환 HWP_IN_FLIGHT건, 공간당 저장 HWP_SPACE_QUOTA·서비스 전체 HWP_TOTAL_QUOTA(원본+변환).
- MEDIA_URL로 서빙하지 않는다. 내려받기는 회원 확인 뷰로만(E6).
- 표 위치(E5): 교사가 미리보기에서 눌러 확정한 표 = (table_sec, table_index). report["tables"] 안에 있는 것만 저장한다.
  table_sec 은 구역 순번(hwp.tables 의 sec), table_index 는 구역 안 문서 순서(중첩 포함, hwp.schema 의 index).
  칸 매핑은 E6.
- 보관 기간 자동 삭제(파일 포함)는 E3.
- report["tables"]: hwp.tables 결과(표 구역·순서·행·열·칸 수·후보 순위, 글자 없음). 미리보기(E4)가 쓴다.
"""
import io
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
    created_at = models.DateTimeField(auto_now_add=True)
    converted_at = models.DateTimeField(null=True, blank=True)

    objects = SpaceQuerySet.as_manager()

    def __str__(self):
        return self.key

    @classmethod
    def used_bytes(cls, space=None):
        """공간 하나의 저장 합계. space=None 이면 서비스 전체(서버 디스크 보호)."""
        qs = cls.objects.in_space(space) if space is not None else cls.objects.all()
        return qs.aggregate(n=models.Sum("stored"))["n"] or 0

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
