"""V2 QA: 구에서 글의 학교 코드(wr_3)를 바꿨을 때 공간이 섞이지 않는지(구현자 테스트에 없는 경계).

- 다른 이관 학교로 옮긴 일정·댓글·첨부는 옛 학교 공간에서 사라지고, 결과는 같은 상태의 전체 이관과 같다.
- 이관 밖 학교로 옮긴 글은 옛 공간에서 사라지고 어디에도 들어가지 않는다.
"""
from django.contrib.auth import get_user_model

from spaces.models import School, Space
from today.models import Event

from .models import LegacyMap
from .test_sync import SameAsFullImportTests, SyncBase
from .tests import A, B, C


class MoveSchoolTests(SyncBase):
    snapshot = SameAsFullImportTests.snapshot

    def test_event_moved_to_other_target_school_leaves_old_space(self):
        for wr_id in (100, 101):  # 합친 복사 묶음째(댓글·첨부 딸림) 나상초로
            self.row("g5_write_goteacher", wr_id=wr_id).update(wr_3=B)
        self.row("g5_write_goteacher", wr_id=107).update(wr_3=C)  # 이관 밖 학교로
        self.sync()
        self.assertEqual(self.sync()["changed"], 0)
        self.assertFalse(Event.objects.in_space(self.space(A)).filter(title__in=["운동회", "과학실 예약"]).exists())
        self.assertFalse(LegacyMap.objects.filter(kind="event", legacy_id="107").exists())
        self.assertFalse(Space.objects.filter(school__neis_code=C).exists())
        synced = self.snapshot()
        Space.objects.all().delete()
        School.objects.all().delete()
        get_user_model().objects.all().delete()
        LegacyMap.objects.all().delete()
        self.run_import()
        self.assertEqual(synced, self.snapshot())
