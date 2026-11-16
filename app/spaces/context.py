from django.conf import settings


def space_word(request):
    """문구의 공간 낱말 하나: 동학년 / 학교. 공간 밖 주소(만들기·입장)는 동학년 기준 문구다."""
    space = getattr(request, "space", None)
    return {"space_word": space.word if space else "동학년"}


def legacy_site(request):
    """V3 이관 학교 보기 전용 안내(spaces/_legacy_ro.html)의 구 사이트 주소."""
    return {"legacy_site_url": settings.LEGACY_SITE_URL}
