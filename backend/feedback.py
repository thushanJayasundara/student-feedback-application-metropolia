import base64
import json
import os

import httpx

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"

SYSTEM_PROMPT = """You are grading a photo of a student's handwritten work against the teacher's assignment.
Read every question and the answer given for it. Judge each answer separately.
Respond with ONLY a JSON object, no markdown fences, with this exact shape:
{"questions": [{"question": "...", "answer": "...", "correct_answer": "...", "is_correct": true/false, "feedback": "..."}], "score": 0, "feedback": "..."}
Use null when a question, answer, or correct answer cannot be determined. Score the complete submission from 0 to 100.
If you cannot find an answer, set is_correct to false and explain what is missing in that question's feedback."""


async def grade_image(image_bytes: bytes, content_type: str, assignment_content: str = "") -> dict:
    b64 = base64.b64encode(image_bytes).decode()
    data_url = f"data:{content_type};base64,{b64}"

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Teacher assignment questions/content:\n{assignment_content or 'Not provided.'}"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    choices = data.get("choices")
    if not choices or not isinstance(choices[0], dict):
        error = data.get("error", {})
        if isinstance(error, dict):
            detail = error.get("message", "The grading service returned no choices.")
        else:
            detail = "The grading service returned no choices."
        return {
            "questions": [],
            "score": 0,
            "feedback": f"Grading service error: {detail}",
        }

    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str):
        return {
            "questions": [],
            "score": 0,
            "feedback": "The grading service returned no readable answer.",
        }

    raw = content.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        result = json.loads(raw)
        if "questions" not in result and "question" in result:
            result["questions"] = [{key: result.get(key) for key in ("question", "answer", "correct_answer", "is_correct", "feedback")}]
        return result
    except json.JSONDecodeError:
        return {
            "questions": [],
            "score": 0,
            "feedback": f"Could not parse model response: {raw}",
        }


async def grade_text(text: str, assignment_content: str = "") -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a friendly middle-school teacher. Grade each question in the submitted work. Respond only as JSON: {\"questions\": [{\"question\": string, \"answer\": string, \"correct_answer\": string, \"is_correct\": boolean, \"feedback\": string}], \"score\": number, \"feedback\": string}. Include one item for every teacher question you can identify. Grade the complete work from 0 to 100."},
            {"role": "user", "content": f"Teacher assignment questions/content:\n{assignment_content or 'Not provided.'}\n\nStudent submission:\n{text or 'No readable text was extracted.'}"},
        ],
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(OPENROUTER_URL, headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}, json=payload)
        response.raise_for_status()
        data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    raw = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        result = json.loads(raw)
        return {"questions": result.get("questions", []), "score": max(0, min(100, int(result.get("score", 0)))), "feedback": result.get("feedback", "Keep working and ask your teacher for a next step.")}
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return {"questions": [], "score": 0, "feedback": "Your work was received. Your teacher will add the next step."}
