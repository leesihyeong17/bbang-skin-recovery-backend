"""처방전 사진 OCR — 처방 항목 추출.

[의료행위 아님] 텍스트를 그대로 읽어내는 것이지 판정하지 않는다. 증상·중증도
관련 필드는 만들지 않는다(CLAUDE.md 규칙 2). 못 읽은 4항목(요양기관기호 등)은
프롬프트에서 명시적으로 제외한다 — Prescription 모델에 애초에 그 컬럼이 없다.

[실패해도 확정을 막지 않는다]
OCR이 실패하면 draft 없이 pending으로 남는다. 환자는 계약이 정한 폴백대로
수동 입력 화면으로 넘어간다(API-CONTRACT 처방전 OCR 우선순위 표).
"""
import base64
import json
import logging
import re
from collections import Counter

from django.conf import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You read a Korean prescription (처방전) photo and transcribe drug items exactly as printed.
Rules:
- Output ONLY what is printed. Do not infer, guess, or fill in anything not visible.
- Do NOT extract 요양기관기호, 질병분류기호, 면허번호, 교부번호 — omit them entirely.
- Do NOT add any severity, risk, or medical judgment. You are transcribing, not interpreting.
- category: only fill this from an explicit category/classification column printed
  in the image. Most Korean prescriptions have no such column — in that case
  output "" for every item. NEVER infer a drug class (정제/주사제/캡슐 etc.) from
  the drug name or dosage form yourself.
- is_prn is true only if the item is explicitly marked "필요 시" / "돈복" / PRN dosing.
- per_day and total_days (top-level) are a best-effort guess only — the server
  recomputes them from the items, so just transcribe each item's row faithfully.
- Transcribe day/count digits exactly as printed. Do not merge digits from
  adjacent columns or invent extra digits.
- Each field is the cell VALUE only. Never repeat the column header text
  (e.g. "1회 투약량") in front of the value.
- drug_name is exactly the printed name, character by character. If a
  character is hard to read, give your best literal reading of THAT image —
  never substitute a different, more familiar/common drug name you recognize
  from training. Do not add a dosage-form suffix (정/캡슐/주사 등) that isn't
  already part of the printed characters.
- Column headers sometimes have a parenthetical hint, e.g. "약품명 (성분명)".
  That hint is a header label, not part of any cell's value — never copy it
  into drug_name or any other field.
- Return strict JSON:
{
  "issued_date": "YYYY-MM-DD" or null,
  "timing": string,
  "per_day": integer,
  "total_days": integer,
  "items": [
    {"seq": integer, "drug_name": string, "category": string, "dose": string,
     "times_per_day": string, "days": string, "usage": string, "is_prn": boolean}
  ]
}
If the image is not a legible prescription, return {"items": []}."""


def _majority_int(items, field):
    """돈복이 아닌 항목들의 field에서 첫 숫자를 뽑아 최빈값을 돌려준다.

    AI가 top-level per_day/total_days를 직접 합산·평균해 틀리는 걸 실측했다
    (per_day 6 · total_days 49로 응답, 실제는 3·6). 이 값은 신뢰하지 않고
    정기약 항목들의 텍스트에서 서버가 직접 다시 계산한다.
    """
    counted = Counter()
    for item in items:
        if item.get("is_prn"):
            continue
        match = re.search(r"\d+", str(item.get(field) or ""))
        if match:
            counted[int(match.group())] += 1
    return counted.most_common(1)[0][0] if counted else None


def extract_prescription(image_bytes, content_type):
    """(draft_dict, provider) 또는 (None, "")."""
    api_key = getattr(settings, "OPENAI_API_KEY", "")
    if not api_key:
        return None, ""

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            timeout=getattr(settings, "OCR_TIMEOUT", 25),
            max_retries=1,
        )
        # 번역(mini, 비용·속도 우선)과 다른 모델을 쓴다. 표 안 작은 글자를
        # 정밀 판독해야 해서 mini는 약 이름을 비슷한 다른 약으로 잘못 대체하는
        # 환각이 실측됐다 — OCR_MODEL이 없으면 상위 모델(gpt-4o)로 기본 폴백한다.
        model = getattr(settings, "OCR_MODEL", None) or "gpt-4o"
        b64 = base64.b64encode(image_bytes).decode("ascii")

        response = client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "이 처방전 사진에서 약 정보를 추출해줘."},
                        {"type": "image_url",
                         "image_url": {"url": f"data:{content_type};base64,{b64}"}},
                    ],
                },
            ],
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        if not isinstance(payload, dict) or not payload.get("items"):
            return None, ""

        # top-level 집계는 모델을 믿지 않고 항목 텍스트에서 서버가 다시 계산한다.
        items = payload["items"]
        per_day = _majority_int(items, "times_per_day")
        total_days = _majority_int(items, "days")
        if per_day is not None:
            payload["per_day"] = per_day
        if total_days is not None:
            payload["total_days"] = total_days

        return payload, f"openai:{model}"

    except Exception as exc:
        logger.warning("prescription ocr failed: %s", exc, exc_info=True)
        return None, ""