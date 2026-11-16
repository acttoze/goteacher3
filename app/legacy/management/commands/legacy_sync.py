"""V2 구 → 새 동기화: manage.py legacy_sync [--once]

상시 실행: 5초마다 증분, 30초마다 이관 학교 행 전체 대조. --once는 전체 대조 한 번만.
LEGACY_DB_URL(읽기 전용 계정)이 비면 아무것도 하지 않는다. LEGACY_FILES_DIR(구 첨부 폴더)가 없으면 멈춘다
(첨부가 '디스크에 없음'으로 버려지지 않게). 접속이 끊기면 다음 주기에 다시 붙는다.
"""
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from legacy.sync import MySQL, Sync

STEP, FULL = 5, 30


class Command(BaseCommand):
    help = "구 고티처 → 새 고티처 동기화(V2, 단방향)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="전체 대조 한 번만")

    def log(self, line):
        self.stdout.write(line)
        self.stdout.flush()

    def handle(self, *args, **o):
        if not settings.LEGACY_DB_URL:
            self.log("legacy_sync: LEGACY_DB_URL이 비어 동기화 꺼짐")
            return
        if not os.path.isdir(settings.LEGACY_FILES_DIR):
            raise CommandError("LEGACY_FILES_DIR(구 첨부 폴더)를 찾지 못했습니다.")
        if o["once"]:
            source = MySQL.connect(settings.LEGACY_DB_URL, settings.LEGACY_FILES_DIR)
            try:
                Sync(source, self.log).full()
            finally:
                source.close()
            return
        sync, last_full = None, 0.0
        while True:
            close_old_connections()
            try:
                if sync is None:
                    sync = Sync(MySQL.connect(settings.LEGACY_DB_URL, settings.LEGACY_FILES_DIR), self.log)
                    last_full = 0.0
                if time.monotonic() - last_full >= FULL:
                    last_full = time.monotonic()
                    sync.full()
                else:
                    sync.step()
            except Exception as e:  # 구 DB 접속·질의 실패: 다음 주기에 다시 붙는다
                self.log(f"legacy_sync: 구 DB 대조 실패({type(e).__name__}), {STEP}초 뒤 다시")
                if sync is not None:
                    try:
                        sync.source.close()
                    except Exception:
                        pass
                sync = None
            time.sleep(STEP)
