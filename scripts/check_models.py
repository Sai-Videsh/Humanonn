from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from groq import Groq

from humanonn.config import load_settings
from humanonn.model_routing import ModelCandidate, route_for


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO8B9x0AAAAASUVORK5CYII="
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_models",
        description="Probe the same Humanonn model routes and report whether each model responds.",
    )
    parser.add_argument(
        "--task",
        choices=["all", "main_orchestrator", "vision", "fast_ambiguity", "fix_generation", "json_classification", "embeddings"],
        default="all",
        help="Limit the check to one model task.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=64,
        help="Upper bound for completion tokens used in text-model probes.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=60,
        help="Request timeout for each provider call.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = load_settings()
    tasks = [args.task] if args.task != "all" else ["main_orchestrator", "vision", "fast_ambiguity", "fix_generation", "json_classification", "embeddings"]
    all_results: list[dict[str, Any]] = []

    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as handle:
        handle.write(PNG_1X1)
        image_path = Path(handle.name)

    try:
        print("Humanonn model checker")
        print(f"Task filter: {args.task}")
        print()
        for task in tasks:
            print(f"[{task}]")
            for candidate in route_for(task):
                result = _probe_candidate(settings, task, candidate, image_path, args.max_output_tokens, args.timeout_seconds)
                all_results.append(result)
                print(_format_result(result))
            print()
        _print_attention_summary(all_results)
    finally:
        try:
            image_path.unlink(missing_ok=True)
        except Exception:
            pass


def _probe_candidate(
    settings,
    task: str,
    candidate: ModelCandidate,
    image_path: Path,
    max_output_tokens: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    api_keys = settings.api_keys_for(candidate.provider)
    if not api_keys:
        return {
            "task": task,
            "bug_tag": candidate.bug_tag,
            "provider": candidate.provider,
            "model": candidate.model,
            "status": "skipped",
            "reason": "missing_api_key",
        }

    try:
        if candidate.provider == "groq":
            response = _call_groq(candidate, task, settings, max_output_tokens)
            return _ok_result(task, candidate, response)
        if candidate.provider == "gemini":
            response = _call_gemini(candidate, task, settings, image_path if task == "vision" else None, timeout_seconds)
            return _ok_result(task, candidate, response)
        if candidate.provider == "openrouter":
            response = _call_openrouter(candidate, task, settings, max_output_tokens, timeout_seconds)
            return _ok_result(task, candidate, response)
        if candidate.provider == "deepseek":
            response = _call_deepseek(candidate, task, settings, max_output_tokens, timeout_seconds)
            return _ok_result(task, candidate, response)
        if candidate.provider == "jina":
            response = _call_jina(candidate, task, settings, timeout_seconds)
            return _ok_result(task, candidate, response)
        return {
            "task": task,
            "bug_tag": candidate.bug_tag,
            "provider": candidate.provider,
            "model": candidate.model,
            "status": "skipped",
            "reason": "provider_not_supported_by_checker",
        }
    except Exception as exc:
        return {
            "task": task,
            "bug_tag": candidate.bug_tag,
            "provider": candidate.provider,
            "model": candidate.model,
            "status": "failed",
            "reason": str(exc).splitlines()[0],
        }


def _call_groq(candidate: ModelCandidate, task: str, settings, max_output_tokens: int) -> dict[str, Any]:
    prompt = _text_prompt(task, candidate)
    last_error: Exception | None = None
    for api_key in settings.api_keys_for("groq"):
        try:
            client = Groq(api_key=api_key)
            response = client.chat.completions.create(
                model=candidate.model,
                messages=[
                    {"role": "system", "content": "Return JSON only."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=max_output_tokens,
            )
            return {
                "content": response.choices[0].message.content or "{}",
                "usage": _usage_to_dict(getattr(response, "usage", None)),
            }
        except Exception as exc:
            last_error = exc
    if last_error is None:
        raise RuntimeError("Missing Groq API key.")
    raise last_error


def _call_gemini(candidate: ModelCandidate, task: str, settings, image_path: Path | None, timeout_seconds: int) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [
        {"text": "Return JSON only."},
        {"text": _text_prompt(task, candidate)},
    ]
    if image_path and image_path.exists():
        parts.append(
            {
                "inline_data": {
                    "mime_type": "image/png",
                    "data": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                }
            }
        )
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0, "maxOutputTokens": 64},
    }
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{candidate.model}:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-goog-api-key": settings.api_key_for("gemini") or ""},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = json.loads(response.read().decode("utf-8"))
    return {
        "content": _gemini_text(body),
        "usage": _usage_to_dict(body.get("usageMetadata")),
    }


def _call_openrouter(candidate: ModelCandidate, task: str, settings, max_output_tokens: int, timeout_seconds: int) -> dict[str, Any]:
    payload = {
        "model": candidate.model,
        "messages": [
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": _text_prompt(task, candidate)},
        ],
        "temperature": 0.0,
        "max_tokens": max_output_tokens,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.api_key_for('openrouter')}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = json.loads(response.read().decode("utf-8"))
    return {
        "content": body["choices"][0]["message"]["content"],
        "usage": _usage_to_dict(body.get("usage")),
    }


def _call_deepseek(candidate: ModelCandidate, task: str, settings, max_output_tokens: int, timeout_seconds: int) -> dict[str, Any]:
    payload = {
        "model": candidate.model,
        "messages": [
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": _text_prompt(task, candidate)},
        ],
        "temperature": 0.0,
        "max_tokens": max_output_tokens,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.api_key_for('deepseek')}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = json.loads(response.read().decode("utf-8"))
    return {
        "content": body["choices"][0]["message"]["content"],
        "usage": _usage_to_dict(body.get("usage")),
    }


def _call_jina(candidate: ModelCandidate, task: str, settings, timeout_seconds: int) -> dict[str, Any]:
    payload = {"model": candidate.model, "input": [_text_prompt(task, candidate), "token-check"]}
    request = urllib.request.Request(
        "https://api.jina.ai/v1/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.api_key_for('jina')}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = json.loads(response.read().decode("utf-8"))
    return {
        "content": f"embeddings={len(body.get('data', []))}",
        "usage": _usage_to_dict(body.get("usage")),
    }


def _ok_result(task: str, candidate: ModelCandidate, response: dict[str, Any]) -> dict[str, Any]:
    content = str(response.get("content", "")).strip()
    parsed = _safe_json(content)
    result = {
        "task": task,
        "bug_tag": candidate.bug_tag,
        "provider": candidate.provider,
        "model": candidate.model,
        "status": "ok",
        "usage": response.get("usage") or {},
    }
    if parsed is not None:
        result["preview"] = parsed
    else:
        result["preview"] = content[:240]
    return result


def _usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    data: dict[str, Any] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens", "cached_tokens", "billable_tokens"):
        value = getattr(usage, key, None)
        if value is not None:
            data[key] = value
    return data


def _gemini_text(body: dict[str, Any]) -> str:
    candidates = body.get("candidates", [])
    if not candidates:
        return "{}"
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts) or "{}"


def _safe_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None


def _text_prompt(task: str, candidate: ModelCandidate) -> str:
    return json.dumps(
        {
            "task": task,
            "model": candidate.model,
            "request": "Confirm the model responds and show a minimal JSON payload.",
            "expected": "Return a compact JSON object with ok:true, task, and model.",
        },
        ensure_ascii=True,
    )


def _format_result(result: dict[str, Any]) -> str:
    base = (
        f"- {result['bug_tag']} | {result['provider']}/{result['model']} | {result['status']}"
    )
    if result.get("status") == "ok":
        usage = result.get("usage") or {}
        usage_text = ", ".join(f"{key}={value}" for key, value in usage.items()) if usage else "usage=unavailable"
        preview = result.get("preview")
        if isinstance(preview, dict):
            preview_text = json.dumps(preview, ensure_ascii=True)
        else:
            preview_text = str(preview)
        return f"{base} | {usage_text} | preview={preview_text[:200]}"
    return f"{base} | reason={result.get('reason', 'unknown')}"


def _print_attention_summary(results: list[dict[str, Any]]) -> None:
    needs_attention = []
    for result in results:
        status = result.get("status")
        reason = str(result.get("reason", ""))
        if status == "ok":
            continue
        provider = result.get("provider", "unknown")
        model = result.get("model", "unknown")
        bug_tag = result.get("bug_tag", "unknown")
        label = f"{bug_tag} ({provider}/{model})"
        if status == "skipped" and reason == "missing_api_key":
            needs_attention.append(f"{label}: add or expose the missing API key")
        elif "403" in reason or "1010" in reason:
            needs_attention.append(f"{label}: access blocked or key not allowed for this endpoint")
        elif "429" in reason or "rate" in reason.lower():
            needs_attention.append(f"{label}: quota or rate limit reached")
        elif status == "failed":
            needs_attention.append(f"{label}: {reason}")

    if not needs_attention:
        print("[attention] No API keys or model endpoints need regeneration right now.")
        return

    print("[attention] APIs to generate or fix next:")
    for item in dict.fromkeys(needs_attention):
        print(f"- {item}")


if __name__ == "__main__":
    main()