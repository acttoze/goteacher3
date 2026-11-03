def space_word(request):
    """문구의 공간 낱말 하나: 동학년 / 학교. 공간 밖 주소(만들기·입장)는 동학년 기준 문구다."""
    space = getattr(request, "space", None)
    return {"space_word": space.word if space else "동학년"}
