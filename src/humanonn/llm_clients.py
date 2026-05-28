from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from groq import Groq

from humanonn.config import Settings
from humanonn.model_routing import ModelCandidate, ModelTask, route_for


class ModelRouter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def call_json(
        self,
        task: ModelTask,
        system_prompt: str,
        user_payload: dict[str, Any],
        image_paths: list[str] | None = None,
        temperature: float = 0.1,
    ) -> tuple[dict[str, Any], ModelCandidate, list[dict[str, str]]]:
        attempts: list[dict[str, str]] = []
        for candidate in route_for(task):
            api_keys = self.settings.api_keys_for(candidate.provider)
            if not api_keys:
                attempts.append({"bug_tag": candidate.bug_tag, "status": "skipped", "reason": "missing_api_key"})
                continue
            try:
                if candidate.provider == "groq":
                    result = self._call_groq(candidate, system_prompt, user_payload, temperature)
                elif candidate.provider == "gemini":
                    result = self._call_gemini(candidate, system_prompt, user_payload, image_paths or [])
                elif candidate.provider == "openrouter":
                    result = self._call_openrouter(candidate, system_prompt, user_payload, temperature)
                elif candidate.provider == "deepseek":
                    result = self._call_deepseek(candidate, system_prompt, user_payload, temperature)
                else:
                    attempts.append({"bug_tag": candidate.bug_tag, "status": "skipped", "reason": "provider_not_supported"})
                    continue
                attempts.append({"bug_tag": candidate.bug_tag, "status": "ok", "reason": "success"})
                return _coerce_json(result), candidate, attempts
            except Exception as exc:
                attempts.append({"bug_tag": candidate.bug_tag, "status": "failed", "reason": str(exc).splitlines()[0]})
        raise RuntimeError(f"No provider succeeded for task {task}: {attempts}")

    def embed_texts(self, texts: list[str], labels: list[str] | None = None) -> tuple[dict[str, Any], ModelCandidate, list[dict[str, str]]]:
        attempts: list[dict[str, str]] = []
        for candidate in route_for("embeddings"):
            api_keys = self.settings.api_keys_for(candidate.provider)
            if not api_keys:
                attempts.append({"bug_tag": candidate.bug_tag, "status": "skipped", "reason": "missing_api_key"})
                continue
            try:
                if candidate.provider == "jina":
                    vectors = self._call_jina_embeddings(candidate, texts)
                    result = {"mode": "vectors", "vectors": vectors}
                elif candidate.provider == "groq":
                    if not labels:
                        attempts.append({"bug_tag": candidate.bug_tag, "status": "skipped", "reason": "missing_labels"})
                        continue
                    similarities = self._call_groq_similarity(candidate, texts[0], texts[1:], labels)
                    result = {"mode": "similarities", "similarities": similarities}
                else:
                    attempts.append({"bug_tag": candidate.bug_tag, "status": "skipped", "reason": "provider_not_supported"})
                    continue
                attempts.append({"bug_tag": candidate.bug_tag, "status": "ok", "reason": "success"})
                return result, candidate, attempts
            except Exception as exc:
                attempts.append({"bug_tag": candidate.bug_tag, "status": "failed", "reason": str(exc).splitlines()[0]})
        raise RuntimeError(f"No embedding provider succeeded: {attempts}")

    def _call_groq(
        self,
        candidate: ModelCandidate,
        system_prompt: str,
        user_payload: dict[str, Any],
        temperature: float,
    ) -> Any:
        last_error: Exception | None = None
        for api_key in self.settings.api_keys_for("groq"):
            try:
                client = Groq(api_key=api_key)
                response = client.chat.completions.create(
                    model=candidate.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
                    ],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                )
                return response.choices[0].message.content or "{}"
            except Exception as exc:
                last_error = exc
        if last_error is None:
            raise RuntimeError("Missing Groq API key.")
        raise last_error

    def _call_gemini(
        self,
        candidate: ModelCandidate,
        system_prompt: str,
        user_payload: dict[str, Any],
        image_paths: list[str],
    ) -> Any:
        api_key = self.settings.api_key_for("gemini")
        parts: list[dict[str, Any]] = [
            {"text": system_prompt},
            {"text": json.dumps(user_payload, ensure_ascii=True)},
        ]
        for image_path in image_paths[:4]:
            path = Path(image_path)
            if not path.exists():
                continue
            mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            parts.append(
                {
                    "inline_data": {
                        "mime_type": mime,
                        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                    }
                }
            )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{candidate.model}:generateContent"
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-goog-api-key": api_key or ""},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.settings.navigation_timeout_ms / 1000) as response:
            body = json.loads(response.read().decode("utf-8"))
        text = "".join(part.get("text", "") for part in body["candidates"][0]["content"]["parts"])
        return text

    def _call_openrouter(
        self,
        candidate: ModelCandidate,
        system_prompt: str,
        user_payload: dict[str, Any],
        temperature: float,
    ) -> Any:
        api_key = self.settings.api_key_for("openrouter")
        payload = {
            "model": candidate.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.settings.navigation_timeout_ms / 1000) as response:
            body = json.loads(response.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]

    def _call_deepseek(
        self,
        candidate: ModelCandidate,
        system_prompt: str,
        user_payload: dict[str, Any],
        temperature: float,
    ) -> Any:
        api_key = self.settings.api_key_for("deepseek")
        payload = {
            "model": candidate.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.settings.navigation_timeout_ms / 1000) as response:
            body = json.loads(response.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]

    def _call_jina_embeddings(self, candidate: ModelCandidate, texts: list[str]) -> list[list[float]]:
        api_key = self.settings.api_key_for("jina")
        payload = {"model": candidate.model, "input": texts}
        request = urllib.request.Request(
            "https://api.jina.ai/v1/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.settings.navigation_timeout_ms / 1000) as response:
            body = json.loads(response.read().decode("utf-8"))
        data = sorted(body["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in data]

    def _call_groq_similarity(
        self,
        candidate: ModelCandidate,
        site_signature: str,
        archetypes: list[str],
        labels: list[str],
    ) -> list[dict[str, Any]]:
        payload = {
            "site_signature": site_signature,
            "archetypes": [{"label": label, "text": text} for label, text in zip(labels, archetypes)],
        }
        prompt = (
            "You score semantic similarity between one website signature and several archetype descriptions. "
            "Return JSON only in this shape: "
            "{\"similarities\":[{\"label\":\"name\",\"score\":0.0,\"reason\":\"short reason\"}]} . "
            "Score must be between 0 and 1."
        )
        last_error: Exception | None = None
        for api_key in self.settings.api_keys_for("groq"):
            try:
                client = Groq(api_key=api_key)
                response = client.chat.completions.create(
                    model=candidate.model,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )
                content = response.choices[0].message.content or "{}"
                parsed = _coerce_json(content)
                similarities = parsed.get("similarities", [])
                similarities.sort(key=lambda item: item.get("score", 0), reverse=True)
                return similarities
            except Exception as exc:
                last_error = exc
        if last_error is None:
            raise RuntimeError("Missing Groq API key.")
        raise last_error


def _coerce_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value).strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json", "", 1).strip()
    return json.loads(text or "{}")
