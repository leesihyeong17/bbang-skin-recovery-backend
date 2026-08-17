"""번역 어댑터.

저장 시 1회 번역하고 결과를 보관합니다(ConsultMessage docstring).
조회할 때마다 부르지 않습니다.

[실패해도 상담은 살아야 한다]
API가 죽거나 느려도 메시지는 저장돼야 합니다. 모든 예외를 삼키고 ("", "")를
돌려주면 body_translated가 빈 문자열로 나가고, 프론트는 원문만 표시합니다.
계약 형식은 그대로 유지됩니다.

[번역만 한다]
의학적 조언·해석·요약을 붙이지 않습니다. 앱이 판정하는 것처럼 읽히면
의료행위가 됩니다(CLAUDE.md 규칙 2). 프롬프트에서 명시적으로 금지합니다.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

LANG_NAME = {"ko": "Korean", "ja": "Japanese", "en": "English"}

SYSTEM_PROMPT = (
    "You translate messages between a patient and a clinic in a post-surgery "
    "aftercare app. Translate the user's message from {source} to {target}.\n"
    "Rules:\n"
    "- Output ONLY the translation. No preamble, no quotes, no notes.\n"
    "- Do NOT add medical advice, interpretation, reassurance, or severity "
    "judgial wording that is not in the source.\n"
    "- Keep the speaker's tone and politeness level.\n"
    "- Keep symptom wording plain and literal. Do not upgrade or downgrade "
    "how severe something sounds.\n"
    "- If the source is already in {target}, return it unchanged."
)


def translate(text, source, target):
    """(번역문, 엔진명)을 돌려준다. 못 하면 ("", "").

    호출부는 반환값이 빈 문자열인 경우를 반드시 처리해야 한다.
    """
    if not text or source == target:
        return "", ""
    if source not in LANG_NAME or target not in LANG_NAME:
        return "", ""

    api_key = getattr(settings, "OPENAI_API_KEY", "")
    if not api_key:
        return "", ""

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            timeout=getattr(settings, "TRANSLATION_TIMEOUT", 6),
            max_retries=1,          # 상담 전송을 오래 붙잡지 않는다
        )
        model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

        response = client.chat.completions.create(
            model=model,
            temperature=0,          # 같은 문장은 같은 번역이 나와야 분쟁 시 재현된다
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT.format(
                        source=LANG_NAME[source], target=LANG_NAME[target]
                    ),
                },
                {"role": "user", "content": text},
            ],
        )
        translated = (response.choices[0].message.content or "").strip()
        if not translated:
            return "", ""
        return translated, f"openai:{model}"

    except Exception as exc:
        # 번역 실패가 메시지 저장을 막으면 안 된다. 원문만 남는다.
        logger.warning("translation failed: %s", exc, exc_info=True)
        return "", ""