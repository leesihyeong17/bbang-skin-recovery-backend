"""번역 어댑터.

저장 시 1회 번역하고 결과를 보관한다(모델 docstring). 조회마다 부르지 않는다.
지금은 스텁 — API 키가 붙으면 이 함수 하나만 갈아끼우면 된다.
"""


def translate(text, source, target):
    """(번역문, 엔진명)을 돌려준다. 못 하면 ("", "")."""
    if not text or source == target:
        return "", ""
    return "", ""        # TODO: 번역 API 연결