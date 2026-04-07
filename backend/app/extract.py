"""
Structured PCR field extraction from free-form transcripts.

Uses Claude (Anthropic API) with tool-use style structured output. Given a
paramedic's narration like "patient is a 54 year old male with chest pain,
BP 148 over 92", returns a PCRExtraction object with the individual fields
populated.

Falls back to a regex-based extractor if no ANTHROPIC_API_KEY is set, so
the system can run end-to-end in development without burning tokens.
"""

import json
import logging
import re
from typing import Any

from .config import settings
from .schemas import PCRExtraction

logger = logging.getLogger(__name__)


EXTRACTION_SYSTEM_PROMPT = """You are a clinical data extraction assistant for an EMS (emergency medical services) system.

You will be given a transcript of a paramedic narrating a patient encounter. Your job is to extract
structured Patient Care Report (PCR) fields from the transcript.

Rules:
- Only extract values that are explicitly stated or can be directly inferred from the transcript.
- For any field not mentioned, return null (not a guess).
- Normalize units: heart_rate and respiratory_rate as integers (BPM / breaths per minute),
  spo2 as integer percent, temperature as Fahrenheit float, blood_pressure as "SYS/DIA" string.
- patient_sex must be one of "F", "M", or "O".
- Return a confidence score between 0 and 1 reflecting how complete and unambiguous the transcript was.
- Be conservative. Missing fields are better than hallucinated ones.

Return your answer by calling the extract_pcr_fields tool. Do not return prose."""


EXTRACTION_TOOL = {
    "name": "extract_pcr_fields",
    "description": "Return the structured PCR fields extracted from the transcript.",
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_name": {"type": ["string", "null"]},
            "patient_age": {"type": ["integer", "null"]},
            "patient_sex": {"type": ["string", "null"], "enum": ["F", "M", "O", None]},
            "blood_pressure": {"type": ["string", "null"]},
            "heart_rate": {"type": ["integer", "null"]},
            "respiratory_rate": {"type": ["integer", "null"]},
            "spo2": {"type": ["integer", "null"]},
            "temperature": {"type": ["number", "null"]},
            "gcs": {"type": ["integer", "null"]},
            "chief_complaint": {"type": ["string", "null"]},
            "hpi": {"type": ["string", "null"]},
            "assessment": {"type": ["string", "null"]},
            "treatment": {"type": ["string", "null"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["confidence"],
    },
}


async def extract_with_claude(transcript: str) -> PCRExtraction:
    """Call Claude with tool-use to get a structured extraction."""
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    message = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1024,
        system=EXTRACTION_SYSTEM_PROMPT,
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "extract_pcr_fields"},
        messages=[{"role": "user", "content": f"Transcript:\n\n{transcript}"}],
    )

    # Find the tool_use block in the response.
    for block in message.content:
        if block.type == "tool_use" and block.name == "extract_pcr_fields":
            payload: dict[str, Any] = dict(block.input)
            return PCRExtraction(**payload)

    # Shouldn't happen if tool_choice forced the tool call, but fall back safely.
    raise RuntimeError("Claude did not return a tool_use block for extract_pcr_fields")


def extract_with_regex(transcript: str) -> PCRExtraction:
    """
    Fallback extractor using regular expressions.

    Handles the common patterns a paramedic would use in narration. Much worse
    than the LLM at handling paraphrases, but runs with zero dependencies and
    zero API cost so it keeps the pipeline testable.
    """
    t = transcript

    def _search(pattern: str, group: int = 1, flags: int = re.IGNORECASE) -> str | None:
        m = re.search(pattern, t, flags)
        return m.group(group).strip() if m else None

    def _int(val: str | None) -> int | None:
        try:
            return int(val) if val is not None else None
        except ValueError:
            return None

    def _float(val: str | None) -> float | None:
        try:
            return float(val) if val is not None else None
        except ValueError:
            return None

    name = _search(r"patient(?:'s)? name (?:is |:)?([A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+)*)")
    age = _int(_search(r"(\d{1,3})[- ]?year[- ]?old"))
    sex_raw = _search(r"\b(male|female)\b")
    sex = {"male": "M", "female": "F"}.get(sex_raw.lower()) if sex_raw else None

    bp = _search(r"(?:blood pressure|bp)(?: is|:)?\s*(\d{2,3})\s*(?:over|/)\s*(\d{2,3})", group=0)
    if bp:
        nums = re.findall(r"\d{2,3}", bp)
        bp = f"{nums[0]}/{nums[1]}" if len(nums) >= 2 else None

    hr = _int(_search(r"(?:heart rate|hr|pulse)(?: is|:)?\s*(\d{2,3})"))
    rr = _int(_search(r"(?:respiratory rate|rr|resp)(?: is|:)?\s*(\d{1,3})"))
    spo2 = _int(_search(r"(?:spo2|sat(?:uration)?|o2 sat)(?: is|:)?\s*(\d{2,3})"))
    temp = _float(_search(r"temp(?:erature)?(?: is|:)?\s*(\d{2,3}(?:\.\d)?)"))
    gcs = _int(_search(r"gcs(?: is|:)?\s*(\d{1,2})"))

    chief = _search(r"chief complaint (?:is|:)?\s*([^.]+)")
    hpi = _search(r"(?:started |began )([^.]+)")
    assessment = _search(r"assessment (?:is|:)?\s*([^.]+)")
    treatment = _search(r"(?:gave|administered|on)\s+([^.]+)")

    filled = sum(1 for v in [name, age, sex, bp, hr, rr, spo2, chief] if v is not None)
    confidence = round(min(filled / 8.0, 1.0), 2)

    return PCRExtraction(
        patient_name=name,
        patient_age=age,
        patient_sex=sex,  # type: ignore[arg-type]
        blood_pressure=bp,
        heart_rate=hr,
        respiratory_rate=rr,
        spo2=spo2,
        temperature=temp,
        gcs=gcs,
        chief_complaint=chief,
        hpi=hpi,
        assessment=assessment,
        treatment=treatment,
        confidence=confidence,
    )


async def extract(transcript: str) -> PCRExtraction:
    """
    Top-level extraction entrypoint.

    Uses Claude if an API key is configured, falls back to regex otherwise.
    """
    if settings.anthropic_api_key:
        try:
            logger.info("Extracting PCR fields with Claude (%s)", settings.anthropic_model)
            return await extract_with_claude(transcript)
        except Exception as e:
            logger.exception("Claude extraction failed, falling back to regex: %s", e)
            return extract_with_regex(transcript)
    else:
        logger.info("No ANTHROPIC_API_KEY set; using regex fallback extractor")
        return extract_with_regex(transcript)
