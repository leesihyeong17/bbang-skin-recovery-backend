import json
import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)

# RecoveryReport.ai_prompt_version이 max_length=10이다. 넘기면 저장에서 DataError.
PROMPT_VERSION = "polish-v1"

# 들어오면 그 줄을 버리는 어휘. 판정·평가·조언으로 읽히는 표현들.
BANNED = (
    "악화", "호전", "정상", "비정상", "이상", "위험", "심각", "우려", "경고",
    "양호", "불량", "부족", "권장", "권고", "주의가 필요", "진단",
    "悪化", "正常", "異常", "危険", "深刻", "良好", "推奨", "診断",
    "abnormal", "normal", "risk", "severe", "warning", "recommend", "diagnos",
)

SYSTEM_PROMPT = (
    "You polish sentences in a post-surgery recovery record that a patient "
    "submits to their clinic. The sentences are already factually complete.\n"
    "Rules:\n"
    "- Improve readability and flow ONLY. Keep every number, D+N marker, "
    "routine name, and symptom name exactly as given.\n"
    "- Do NOT add judgment, evaluation, encouragement, advice, diagnosis, or "
    "severity wording. These are neutral observations, not assessments.\n"
    "- Do NOT merge, split, add, or remove lines.\n"
    "- Keep the same language as the input.\n"
    # response_format=json_object는 최상위가 객체여야 한다. 배열을 돌려주면
    # payload.get()이 터져서 조용히 원문 폴백으로 떨어진다.
    '- Return a JSON object of the exact form {"lines": ["...", "..."]}, '
    "with the array the same length and in the same order as the input."
)


def _numbers_kept(original, polished):
    """원문에 없던 숫자가 들어왔는지 본다.

    문장에 나오는 숫자는 전부 계산값이다. AI가 문장을 재구성하다 값을 바꾸면
    (87% → "약 90%") 사실이 틀어지는데, weekly_summary처럼 text만 있는 줄은
    옆에 대조할 숫자가 없어서 틀린 채로 저장된다. 병원 제출 문서라 막아야 한다.

    빼는 건 허용한다 — 문장이 짧아지는 것뿐이다. 없던 걸 만드는 것만 거부한다.
    """
    allowed = set(re.findall(r"\d+", original))
    return set(re.findall(r"\d+", polished)) <= allowed


def _sentence_count(text):
    return len(re.findall(r"[.。!?！？]", text))


def _looks_safe(original, polished):
    """다듬은 문장이 원문의 사실을 유지하는지 본다."""
    if not polished or not isinstance(polished, str):
        return False
    low = polished.lower()
    if any(word.lower() in low for word in BANNED):
        return False
    # 길이가 크게 늘면 뭔가 덧붙인 것이다
    if len(polished) > len(original) * 1.6 + 20:
        return False
    # 문장이 늘면 없던 내용이 붙은 것이다. 격려·조언이 이 경로로 들어온다.
    # 금지어 목록으로 잡으려 하면 끝이 없어서 구조로 막는다.
    if _sentence_count(polished) > max(_sentence_count(original), 1):
        return False
    # D+N 표기가 사라지면 근거가 빠진 것이다
    if "D+" in original and "D+" not in polished:
        return False
    if not _numbers_kept(original, polished):
        return False
    return True


def polish_lines(lines, lang):
    """[{kind, text}] → 다듬은 같은 구조. 실패하면 입력을 그대로 돌려준다.

    (결과, ai_model) 튜플. AI를 안 썼으면 ai_model은 빈 문자열.
    """
    texts = [line["text"] for line in lines]
    if not texts:
        return lines, ""

    api_key = getattr(settings, "OPENAI_API_KEY", "")
    if not api_key:
        return lines, ""

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            timeout=getattr(settings, "REPORT_POLISH_TIMEOUT", 8),
            max_retries=0,
        )
        model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

        response = client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(
                    {"lines": texts}, ensure_ascii=False)},
            ],
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        # 규격은 {"lines": [...]}지만 모델이 배열을 그냥 줄 때가 있다.
        polished = payload.get("lines") if isinstance(payload, dict) else payload

        if not isinstance(polished, list) or len(polished) != len(texts):
            logger.warning("polish rejected: 응답 형식 또는 줄 개수 불일치")
            return lines, ""

        # kind와 부속 데이터(curr_avg 등)는 원본을 유지한다. AI는 text만 만진다.
        out = [
            {**line, "text": new if _looks_safe(line["text"], new) else line["text"]}
            for line, new in zip(lines, polished)
        ]
        return out, f"openai:{model}"

    except Exception as exc:
        logger.warning("report polish failed: %s", exc)
        return lines, ""


def polish_body(body, lang):
    """body의 모든 묶음을 한 번에 다듬는다. 호출 1회로 끝낸다.

    키 목록을 고정하지 않는다 — 본문 구성이 바뀌어도 여기는 안 고쳐도 된다.
    """
    def is_lines(value):
        """{kind, text} 목록만 다듬는다. ips_mapping 같은 부속 데이터는 건너뛴다."""
        return isinstance(value, list) and all(
            isinstance(item, dict) and "text" in item for item in value
        )

    # overview는 "라벨: 값" 형식이라 문장으로 풀어쓰면 표를 못 그린다.
    # 사실 나열이라 다듬을 것도 없다.
    SKIP = {"overview"}

    keys = [key for key in body if key not in SKIP and is_lines(body[key])]
    flat = [line for key in keys for line in body[key]]
    polished, model = polish_lines(flat, lang)

    out, cursor = dict(body), 0
    for key in keys:
        size = len(body[key])
        out[key] = polished[cursor:cursor + size]
        cursor += size
    return out, model