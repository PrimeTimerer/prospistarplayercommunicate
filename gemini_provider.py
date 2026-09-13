#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded Gemini text adapter for optional user-authorized narration.

The adapter sends only caller-constructed text to Google's fixed API host. It
never reads saves, screenshots, settings, or secrets on its own, and it never
puts a key in a URL or exception string.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from product_version import USER_AGENT
import stat_engine
import prose_format
import narrative_context
import narrative_review


DEFAULT_MODEL = "gemini-3.5-flash-lite"
SUPPORTED_MODELS = (
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)
API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_PROMPT_BYTES = 512 * 1024
MAX_INLINE_IMAGE_BYTES = 12 * 1024 * 1024
MAX_MULTIMODAL_REQUEST_BYTES = 19 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_TRANSIENT_ERROR_RETRIES = 3
_TRANSIENT_RETRY_LIMITS = {
    "REMOTE_UNAVAILABLE": 3,
    "REMOTE_TIMEOUT": 3,
    "QUOTA_EXCEEDED": 2,
}
# A 5xx burst from a shared hosted model is measured in tens of seconds, not
# in single seconds. The previous ladder (0.75/1.5/3.0) gave up after about six
# seconds of waiting, which read as "the service is down" for an outage that
# was still clearing. The ladder below waits 5s, 15s, then 45s.
_TRANSIENT_RETRY_BASE_SECONDS = 5.0
_TRANSIENT_RETRY_GROWTH = 3.0
_TRANSIENT_RETRY_MAX_SECONDS = 45.0
SUPPORTED_IMAGE_MIME_TYPES = (
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/heic",
    "image/heif",
)
_MODEL_PATTERN = re.compile(r"^gemini-[a-z0-9][a-z0-9.-]{1,62}$")
_HANGUL_PATTERN = re.compile(r"[가-힣]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:\d+(?:[.,]\d+)*|\.\d+)")
_REPAIR_INSTRUCTION = (
    "응답은 자연스러운 한국어 문단으로만 작성하세요. 이름·공식 약어 외의 영문 문장을 쓰지 말고, "
    "입력에 없는 점수·통계·날짜·횟수 같은 숫자를 새 사실처럼 만들지 마세요. "
    "장면 제목은 반드시 독립된 한 줄의 '## 짧은 소제목'으로 쓰고 다음 줄을 비운 뒤 본문을 쓰세요. "
    "제목 뒤 같은 줄에 본문이나 다음 제목을 붙이지 마세요. "
    "지정된 출력 한도 안에서 마지막 문장까지 완결하세요."
)


def _repair_instruction(error: Exception) -> str:
    """Name what the previous answer got wrong, not just the general rule."""

    issues = list(getattr(error, "context_issues", ()) or [])
    if issues:
        return (_REPAIR_INSTRUCTION + " 숫자 자체를 지우지 말고 앞뒤 문장과 문단의 의미를 확인하세요. "
                "목표·예상·조건부 표현과 이미 달성한 기록을 구분하고, 나열된 여러 목표의 남은 수량을 각각 확인하세요. "
                "시즌 누적·통산 성적을 오늘 한 경기의 기록으로 쓰지 마세요. 문단의 다른 장면과 말투는 유지하세요. "
                + _context_summary(issues))
    numbers = list(getattr(error, "unsupported_numbers", ()) or [])
    if not numbers:
        return _REPAIR_INSTRUCTION
    listed = ", ".join(str(value) for value in numbers[:8])
    return (
        _REPAIR_INSTRUCTION
        + f" 직전 응답의 숫자 {listed}은(는) 입력에 없는 값이라 저장되지 못했습니다. "
        "그 숫자를 쓰지 말고, 필요하면 입력에 있는 값을 형태 그대로 옮기거나 숫자 없이 서술하세요. "
        "입력의 숫자를 반올림하거나 자리수를 줄여 쓰지 마세요."
    )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep the API key on the one allowlisted Google origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_SECURE_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _secure_urlopen(request, timeout):
    return _SECURE_OPENER.open(request, timeout=timeout)


@dataclass(frozen=True)
class ProviderResult:
    text: str
    model: str
    request_hash: str
    request_bytes: int
    response_bytes: int
    finish_reason: str | None
    request_count: int = 1
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    thoughts_tokens: int = 0
    metered_responses: int = 0
    validation_repair: dict | None = None
    validation_draft: str = field(default="", repr=False, compare=False)
    validation_draft_truncated: bool = False
    validation_review: dict | None = None
    validation_review_diagnostic: dict | None = field(default=None, repr=False, compare=False)


class GeminiProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        usage: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.request_count = 0
        self.request_bytes = 0
        self.response_bytes = 0
        self.prompt_tokens = int((usage or {}).get("prompt_tokens", 0) or 0)
        self.output_tokens = int((usage or {}).get("output_tokens", 0) or 0)
        self.total_tokens = int((usage or {}).get("total_tokens", 0) or 0)
        self.cached_tokens = int((usage or {}).get("cached_tokens", 0) or 0)
        self.thoughts_tokens = int((usage or {}).get("thoughts_tokens", 0) or 0)
        self.metered_responses = int((usage or {}).get("metered_responses", 0) or 0)


def normalize_model(value: object) -> str:
    model = str(value or DEFAULT_MODEL).strip().lower()
    if model not in SUPPORTED_MODELS or not _MODEL_PATTERN.fullmatch(model):
        raise ValueError("지원 목록에 있는 Gemini 모델을 선택해 주세요.")
    return model


def _bounded_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}이 비어 있습니다.")
    return text


def _body(system_text: object, user_text: object, max_tokens: int, *, response_schema=None) -> tuple[bytes, str]:
    system = _bounded_text(system_text, "시스템 지시")
    user = _bounded_text(user_text, "사용자 입력")
    try:
        output_tokens = max(256, min(65536, int(max_tokens)))
    except (TypeError, ValueError):
        output_tokens = 2400
    value = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"maxOutputTokens": output_tokens},
    }
    if response_schema is not None:
        value["generationConfig"].update(responseMimeType="application/json", responseJsonSchema=response_schema)
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_PROMPT_BYTES:
        raise ValueError("Gemini로 보낼 검증 요약이 너무 큽니다. 첨부나 과거 문맥을 줄여 주세요.")
    return raw, hashlib.sha256(raw).hexdigest()


def _vision_body(
    system_text: object,
    user_text: object,
    image_bytes: object,
    mime_type: object,
    max_tokens: int,
) -> tuple[bytes, str]:
    """Build one bounded inline-image request without exposing a local path."""

    system = _bounded_text(system_text, "시스템 지시")
    user = _bounded_text(user_text, "사용자 입력")
    if not isinstance(image_bytes, (bytes, bytearray, memoryview)):
        raise ValueError("Gemini 이미지 데이터 형식이 올바르지 않습니다.")
    image = bytes(image_bytes)
    if not image:
        raise ValueError("Gemini로 보낼 이미지가 비어 있습니다.")
    if len(image) > MAX_INLINE_IMAGE_BYTES:
        raise ValueError("Gemini로 보낼 이미지가 12MB 제한을 넘었습니다.")
    mime = str(mime_type or "").strip().lower()
    if mime not in SUPPORTED_IMAGE_MIME_TYPES:
        raise ValueError("Gemini 이미지 해석은 PNG, JPEG, WebP, HEIC, HEIF만 지원합니다.")
    try:
        output_tokens = max(128, min(8192, int(max_tokens)))
    except (TypeError, ValueError):
        output_tokens = 600
    value = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": base64.b64encode(image).decode("ascii"),
                        }
                    },
                    {"text": user},
                ],
            }
        ],
        "generationConfig": {"maxOutputTokens": output_tokens},
    }
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_MULTIMODAL_REQUEST_BYTES:
        raise ValueError("Gemini 이미지 요청이 20MB 안전 한도에 너무 가깝습니다. 더 작은 이미지를 사용해 주세요.")
    return raw, hashlib.sha256(raw).hexdigest()


def _request(url: str, *, api_key: str, data: bytes | None, timeout: float, urlopen=None) -> bytes:
    target = urllib.parse.urlsplit(url)
    if (
        target.scheme != "https"
        or target.hostname != "generativelanguage.googleapis.com"
        or target.port is not None
        or target.username is not None
        or target.password is not None
    ):
        raise ValueError("Gemini 요청 대상이 허용된 Google API 주소가 아닙니다.")
    key = str(api_key or "").strip()
    if not key or len(key) > 4096 or any(character.isspace() for character in key):
        raise ValueError("Gemini API 키가 설정되지 않았거나 형식이 올바르지 않습니다.")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "x-goog-api-key": key,
    }
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    opener = urlopen or _secure_urlopen
    try:
        with opener(request, timeout=max(3.0, min(900.0, float(timeout)))) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0)
        # The response body may echo request details. Never read it, and close
        # the handle before returning a bounded local error category.
        try:
            exc.close()
        except OSError:
            pass
        if status in (401, 403):
            raise GeminiProviderError("AUTH_REJECTED", "Gemini API 키 또는 프로젝트 권한이 거부됐습니다.") from None
        if status == 404:
            raise GeminiProviderError("MODEL_NOT_FOUND", "선택한 Gemini 모델을 현재 프로젝트에서 사용할 수 없습니다.") from None
        if status == 429:
            raise GeminiProviderError("QUOTA_EXCEEDED", "Gemini API 사용 한도에 도달했습니다. 원본 기록은 그대로 유지됩니다.", retryable=True) from None
        if status == 408:
            raise GeminiProviderError("REMOTE_TIMEOUT", "Gemini 서비스가 요청 처리 시간을 초과했습니다.", retryable=True) from None
        if 500 <= status <= 599:
            raise GeminiProviderError("REMOTE_UNAVAILABLE", "Gemini 서비스가 일시적으로 응답하지 않습니다.", retryable=True) from None
        raise GeminiProviderError("REMOTE_REJECTED", f"Gemini 요청이 거부됐습니다 (HTTP {status or '오류'}).") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise GeminiProviderError("NETWORK_FAILED", "Gemini에 연결하지 못했습니다. 네트워크와 API 설정을 확인해 주세요.", retryable=True) from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise GeminiProviderError("RESPONSE_TOO_LARGE", "Gemini 응답이 안전한 크기 제한을 넘었습니다.")
    return raw


def _usage_metadata(value: object) -> dict:
    usage = value.get("usageMetadata") if isinstance(value, dict) else None
    if not isinstance(usage, dict):
        return {
            "prompt_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "thoughts_tokens": 0,
            "metered_responses": 0,
        }

    def count(key: str) -> int:
        try:
            return max(0, int(usage.get(key, 0) or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    return {
        "prompt_tokens": count("promptTokenCount"),
        "output_tokens": count("candidatesTokenCount"),
        "total_tokens": count("totalTokenCount"),
        "cached_tokens": count("cachedContentTokenCount"),
        "thoughts_tokens": count("thoughtsTokenCount"),
        "metered_responses": 1,
    }


def _response_text(raw: bytes, *, structured: bool = False) -> tuple[str, str | None, dict]:
    usage = _usage_metadata(None)
    try:
        value = json.loads(raw.decode("utf-8"))
        usage = _usage_metadata(value)
        candidates = value.get("candidates") if isinstance(value, dict) else None
        first = candidates[0]
        parts = ((first.get("content") or {}).get("parts") or [])
        final_parts = [str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict) and not part.get("thought")
            and (structured or str(part.get("text") or "").strip())]
        # JSON text fragments must not acquire newlines inside quoted values.
        # Preserve existing prose separators; thought parts are never output.
        text = ("".join(final_parts) if structured else "\n".join(part.strip() for part in final_parts)).strip()
        finish_reason = str(first.get("finishReason") or "").strip() or None
    except (UnicodeError, json.JSONDecodeError, AttributeError, IndexError, KeyError, TypeError):
        raise GeminiProviderError("INVALID_RESPONSE", "Gemini 응답 형식을 확인하지 못했습니다. 원본 기록은 그대로 유지됩니다.", retryable=True, usage=usage) from None
    if not text:
        raise GeminiProviderError("EMPTY_RESPONSE", "Gemini가 빈 응답을 반환했습니다. 원본 기록은 그대로 유지됩니다.", retryable=True, usage=usage)
    if finish_reason == "MAX_TOKENS":
        raise GeminiProviderError(
            "TRUNCATED_RESPONSE",
            "Gemini 응답이 출력 한도에서 잘려 저장하지 않았습니다.",
            retryable=True,
            usage=usage,
        )
    if finish_reason and finish_reason != "STOP":
        raise GeminiProviderError(
            "GENERATION_BLOCKED",
            "Gemini가 응답 생성을 완료하지 않아 저장하지 않았습니다.",
            usage=usage,
        )
    return text, finish_reason, usage


def _add_usage(target: dict, source: object) -> None:
    for key in (
        "prompt_tokens", "output_tokens", "total_tokens", "cached_tokens",
        "thoughts_tokens", "metered_responses",
    ):
        try:
            target[key] += max(0, int(getattr(source, key, 0) if not isinstance(source, dict) else source.get(key, 0)) or 0)
        except (TypeError, ValueError, OverflowError):
            continue


def _annotate_error(error: GeminiProviderError, accounting: dict) -> GeminiProviderError:
    error.request_count = accounting["request_count"]
    error.request_bytes = accounting["request_bytes"]
    error.response_bytes = accounting["response_bytes"]
    for key in (
        "prompt_tokens", "output_tokens", "total_tokens", "cached_tokens",
        "thoughts_tokens", "metered_responses",
    ):
        setattr(error, key, accounting[key])
    return error


def _new_accounting() -> dict:
    return {
        "request_count": 0,
        "request_bytes": 0,
        "response_bytes": 0,
        "prompt_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "thoughts_tokens": 0,
        "metered_responses": 0,
    }


def _transient_retry_delay(retry_number: int, random_fn=None) -> float:
    base = min(
        _TRANSIENT_RETRY_MAX_SECONDS,
        _TRANSIENT_RETRY_BASE_SECONDS * (_TRANSIENT_RETRY_GROWTH ** max(0, int(retry_number) - 1)),
    )
    source = random_fn or random.random
    try:
        jitter = max(0.0, min(1.0, float(source())))
    except (TypeError, ValueError, OverflowError):
        jitter = 0.0
    return base * (1.0 + (0.25 * jitter))


def _retry_exhausted_error(error: GeminiProviderError, retry_count: int) -> GeminiProviderError:
    if error.code == "QUOTA_EXCEEDED":
        message = (
            f"Gemini API 사용 한도가 계속 응답해 {retry_count}회 자동 재시도했지만 복구되지 않았습니다. "
            "잠시 후 다시 시도해 주세요. 원본 기록은 그대로 유지됩니다."
        )
    elif error.code == "REMOTE_TIMEOUT":
        message = (
            f"Gemini 요청이 지연되어 {retry_count}회 자동 재시도했지만 완료되지 않았습니다. "
            "잠시 후 다시 시도하거나 설정에서 다른 모델을 선택해 주세요. 원본 기록은 그대로 유지됩니다."
        )
    else:
        message = (
            f"Gemini 서비스가 혼잡해 {retry_count}회 자동 재시도했지만 복구되지 않았습니다. "
            "잠시 후 다시 시도하거나 설정에서 다른 모델을 선택해 주세요. 원본 기록은 그대로 유지됩니다."
        )
    return GeminiProviderError(error.code, message, retryable=True)


def _generation_request(
    url: str,
    *,
    api_key: str,
    data: bytes,
    timeout: float,
    accounting: dict,
    retry_state: dict,
    transient_error_retries: int,
    on_transient_retry=None,
    check_cancelled=None,
    before_request=None,
    sleep_fn=None,
    random_fn=None,
    urlopen=None,
) -> bytes:
    """Send one logical generation request with bounded, observable retries.

    Only explicit server replies that are safe to replay are retried. A generic
    network failure is deliberately returned immediately because the caller
    cannot know whether an unobserved response was already metered.
    """

    sleeper = sleep_fn or time.sleep
    try:
        configured_limit = max(0, min(DEFAULT_TRANSIENT_ERROR_RETRIES, int(transient_error_retries)))
    except (TypeError, ValueError, OverflowError):
        configured_limit = DEFAULT_TRANSIENT_ERROR_RETRIES
    while True:
        if check_cancelled:
            check_cancelled()
        if before_request:
            before_request()
        accounting["request_count"] += 1
        accounting["request_bytes"] += len(data)
        try:
            return _request(url, api_key=api_key, data=data, timeout=timeout, urlopen=urlopen)
        except GeminiProviderError as exc:
            code_limit = _TRANSIENT_RETRY_LIMITS.get(exc.code)
            effective_limit = min(configured_limit, code_limit) if code_limit is not None else 0
            used = int(retry_state.get("used", 0) or 0)
            if not exc.retryable or not effective_limit or used >= effective_limit:
                if code_limit is not None and used:
                    exc = _retry_exhausted_error(exc, used)
                raise _annotate_error(exc, accounting) from None
            retry_number = used + 1
            retry_state["used"] = retry_number
            delay = _transient_retry_delay(retry_number, random_fn=random_fn)
            if on_transient_retry:
                on_transient_retry(
                    {
                        "retry": retry_number,
                        "max_retries": effective_limit,
                        "code": exc.code,
                        "delay_seconds": round(delay, 3),
                    }
                )
            # Keep cancellation responsive during backoff. An in-flight HTTP
            # request is not retried or published after ownership is lost.
            remaining = delay
            while remaining > 0:
                if check_cancelled:
                    check_cancelled()
                step = min(0.2, remaining) if check_cancelled else remaining
                sleeper(step)
                remaining -= step


class BatchRequestPacer:
    """Share a transient-error cooldown within one batch wave, never globally.

    Healthy requests remain concurrent. After congestion, retries and queued
    requests share a cooldown and staggered admission instead of another burst.
    No credential, quota guess, or persistent account state is kept here.
    """

    def __init__(self, *, clock=None, sleep=None):
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._lock = threading.Lock()
        self._next = 0.0
        self._spacing = 0.0

    def defer(self, retry: dict) -> None:
        with self._lock:
            self._next = max(self._next, self._clock() + max(0.0, float(retry["delay_seconds"])))
            self._spacing = max(self._spacing, 2.0 if retry.get("code") == "QUOTA_EXCEEDED" else 1.0)

    def wait(self, check_cancelled=None) -> None:
        while True:
            if check_cancelled:
                check_cancelled()
            with self._lock:
                now = self._clock()
                remaining = self._next - now
                if remaining <= 0:
                    self._next = now + self._spacing
                    return
            self._sleep(min(0.2, remaining))


def _canonical_number(value: str) -> str:
    compact = value.replace(",", "")
    if compact.startswith("."):
        compact = "0" + compact
    try:
        number = Decimal(compact)
    except InvalidOperation:
        return compact
    normalized = format(number.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _validate_korean_text(text: str) -> None:
    hangul = len(_HANGUL_PATTERN.findall(text))
    latin = len(_LATIN_PATTERN.findall(text))
    if hangul == 0 or (latin >= 12 and hangul * 2 < latin):
        raise GeminiProviderError(
            "LANGUAGE_REJECTED",
            "Gemini 응답이 한국어 출력 기준을 통과하지 못했습니다. 원본 기록은 그대로 유지됩니다.",
            retryable=True,
        )


def _target_mentions(text: str, row: dict):
    """Bind a proved target to its metric in either Korean quantity order.

    Validation and bounded recovery must use the same binding. The expression
    only locates a quantity; it does not prove a future/completed predicate.
    """
    return narrative_context.target_mentions(text, row)


_FORUM_RECORD_LABELS = frozenset((
    "기록", "성적", "평균", "시즌", "통산", "투수", "타자", "타율", "출루율", "장타율",
    "평균자책점", "자책점", "실점", "이닝", "탈삼진", "삼진", "승", "승수", "홈런", "피홈런",
    "도루", "안타", "피안타", "타점", "득점", "타수", "타석", "볼넷", "사구", "구속", "비거리",
    "era", "avg", "obp", "slg", "ops", "whip", "war", "ip", "k", "hr", "rbi", "bb", "h", "ab", "pa",
))


def _mask_narrative_forum_headers(text: str, source_text: str) -> str:
    """Ignore a display-only two-octet tag, not an IP/number elsewhere.

    Cinematic callers must opt in, and their source must request a forum.
    Require an entire short nickname line followed by comment text. Common
    stat labels, full addresses, invalid octets and inline prose are not tags.
    This projection never alters returned prose or the factual number pool.
    """
    if not re.search(r"DCInside|FMKorea|MLBPARK|디시인사이드|디씨|펨코|엠엘비파크", source_text, re.I):
        return text
    pattern = re.compile(
        r"^(?P<nick>[A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ][A-Za-z0-9가-힣ㄱ-ㅎㅏ-ㅣ_-]{0,23})[ \t]+"
        r"\((?P<tag>(?P<first>\d{1,3})\.(?P<second>\d{1,3}))\)[ \t]*(?=\r?\n[^\r\n])", re.M)
    def mask(match):
        if (match["nick"].casefold() in _FORUM_RECORD_LABELS
                or not 1 <= int(match["first"]) <= 255 or not 0 <= int(match["second"]) <= 255):
            return match[0]
        start, end = match.start("tag") - match.start(), match.end("tag") - match.start()
        return match[0][:start] + " " * (end - start) + match[0][end:]
    return pattern.sub(mask, text)


def _grounded_prospective_text(text: str, prospective_targets=(), *, source_text="") -> str:
    """Keep the existing adapter entry point; use shared discourse analysis."""
    value, issues = narrative_context.prospective_projection(text, prospective_targets, _prose_numbers(source_text))
    if issues:
        raise _context_error(issues)
    return value


def _context_summary(issues) -> str:
    labels = {"achieved_future_target": "목표를 달성 기록으로 표현함",
              "unresolved_target_context": "목표의 앞뒤 문맥이 불명확함",
              "remaining_mismatch": "나열한 목표와 남은 수량이 맞지 않음",
              "season_as_single_game": "시즌 누적을 한 경기 성적으로 표현함",
              "current_metric_mismatch": "현재 기록의 항목과 수량이 맞지 않음",
              "contradictory_hit_inference": "피안타 기록과 무안타 해석이 충돌함"}
    return " / ".join(f"문단 {row['paragraph_index'] + 1}: {labels.get(row['reason'], '기록 문맥 확인 필요')}"
                      for row in issues[:4])


def _context_error(issues):
    error = GeminiProviderError("UNSUPPORTED_NUMERIC_CLAIM", _context_summary(issues), retryable=True)
    error.context_issues = issues[:16]
    error.unsupported_numbers = sorted({number for row in issues for number in row["numbers"]})
    return error


def _validate_generated_text(text: str, source_text: str, *, prospective_targets=(), narrative_metadata=False, narrative_facts=None) -> None:
    _validate_korean_text(text)
    _validate_generated_numbers(text, source_text, prospective_targets=prospective_targets,
                                narrative_metadata=narrative_metadata, narrative_facts=narrative_facts)


def _validate_generated_numbers(text: str, source_text: str, *, prospective_targets=(), narrative_metadata=False, narrative_facts=None) -> None:
    allowed_numbers = _prose_numbers(source_text)
    projected = _mask_narrative_forum_headers(text, source_text) if narrative_metadata else text
    if narrative_metadata:
        issues = narrative_context.record_context_issues(text, narrative_facts)
        if issues:
            raise _context_error(issues)
        projected = narrative_context.mask_scene_metadata(projected)
    output_numbers = _prose_numbers(_grounded_prospective_text(projected, prospective_targets, source_text=source_text))
    unsupported = sorted(output_numbers - allowed_numbers)
    if unsupported:
        error = GeminiProviderError(
            "UNSUPPORTED_NUMERIC_CLAIM",
            "Gemini 응답에 입력 근거가 없는 숫자가 있어 저장하지 않았습니다.",
            retryable=True,
        )
        # Carry the offending values so the retry can name them instead of
        # repeating a generic "do not invent numbers".
        error.unsupported_numbers = unsupported
        raise error


def recover_narrative_passages(text: str, source_text: str, prospective_targets=(), *, narrative_facts=None):
    """Recover a small numeric refusal with exact current-count paragraphs.

    Only a newly generated cinematic draft is eligible. Do not edit a user's
    saved prose or grant its rejected numbers factual status. Preserve every
    unchanged normalized paragraph, validate the entire assembled result, and
    refuse recovery when most of the story would need replacement.
    """
    if not text or len(text) > 32768:
        return None
    targets = stat_engine.validate_narrative_targets(prospective_targets)
    if not targets and not narrative_facts:
        return None
    normalized = prose_format.normalize_generated_markdown(text)
    parts = re.split(r"(\n\s*\n)", normalized)
    changed = []
    safe_body_chars = 0
    safe_body_count = 0
    total_chars = sum(len(part.strip()) for part in parts)
    for index in range(0, len(parts), 2):
        paragraph = parts[index]
        try:
            _validate_generated_numbers(paragraph, source_text, prospective_targets=targets, narrative_metadata=True,
                                        narrative_facts=narrative_facts)
        except GeminiProviderError as exc:
            if exc.code != "UNSUPPORTED_NUMERIC_CLAIM":
                return None
            numbers = set(getattr(exc, "unsupported_numbers", ()))
            # The same number with an unrelated unit (money, distance, etc.) is
            # not permission to insert baseball facts into that paragraph.
            linked = []
            for row in targets:
                if str(row["target"]) in numbers and any(_target_mentions(re.sub(r"[*_`]", "", paragraph), row)):
                    linked.append(row)
            contextual_counts = [row for issue in getattr(exc, "context_issues", [])
                                 for row in issue.get("verified_counts", [])]
            if not linked and not contextual_counts:
                return None
            heading = re.match(r"^(#{1,3})\s+", paragraph)
            if heading:
                replacement = heading[1] + " " + "·".join(row["label"] for row in linked) + " 기록의 다음 장"
            else:
                counts = " · ".join(dict.fromkeys(f"{row['current']}{row['label']}" for row in linked + contextual_counts))
                replacement = f"현재 시즌 기록은 {counts}이다. 다음 기록을 향한 이야기는 아직 진행 중이다."
            changed.append({"paragraph_index": index // 2, "original_chars": len(paragraph.strip()),
                            "unsupported_numbers": sorted(numbers)[:8],
                            "verified_counts": [{"stat": row["stat"], "current": row["current"]} for row in linked + contextual_counts],
                            "context_reasons": sorted({row["reason"] for row in getattr(exc, "context_issues", [])})})
            parts[index] = replacement
        else:
            if paragraph.strip() and not paragraph.lstrip().startswith("#"):
                safe_body_count += 1
                safe_body_chars += len(paragraph.strip())
    replaced_chars = sum(row["original_chars"] for row in changed)
    if (not changed or len(changed) > 4 or safe_body_count < 2 or safe_body_chars < 160
            or replaced_chars > total_chars * .40):
        return None
    candidate = "".join(parts)
    try:
        _validate_generated_text(candidate, source_text, prospective_targets=targets, narrative_metadata=True,
                                 narrative_facts=narrative_facts)
    except GeminiProviderError:
        return None
    return candidate, {"strategy": "verified_count_paragraphs", "changed_paragraphs": len(changed),
                       "retained_fraction": round(1 - replaced_chars / max(1, total_chars), 3),
                       "changes": changed}


def _prose_numbers(text: str) -> set[str]:
    """Normalize calendar typography and list markers, not factual quantities."""
    # A dotted YYYY.M.D date is not one decimal quantity. Keep every component
    # subject to the same numeric check as the Korean YYYY년 M월 D일 form.
    value = re.sub(r"(?<!\d)(\d{4})\.(\d{1,2})\.(\d{1,2})(?!\d)", r"\1년 \2월 \3일", text)
    # Only sequential Markdown list ordinals starting at one are typography.
    # Inline numbers, decimal statistics and nonsequential numbered facts remain.
    expected = 1
    lines = []
    for line in value.splitlines():
        match = re.match(r"^(\s*)(\d{1,3})[.)]\s+(.+)$", line)
        if match and int(match[2]) == expected:
            line = match[1] + match[3]
            expected += 1
        elif line.strip():
            expected = 1
        lines.append(line)
    return {_canonical_number(number) for number in _NUMBER_PATTERN.findall("\n".join(lines))}


def public_failure_detail(error: Exception) -> dict:
    """Expose allowlisted diagnostics, never an upstream body or credentials."""
    code = str(getattr(error, "code", "INVALID_RESPONSE"))
    labels = {
        "UNSUPPORTED_NUMERIC_CLAIM": "응답은 받았지만 입력 근거가 없는 숫자가 있어 검증에서 보류했습니다.",
        "LANGUAGE_REJECTED": "응답은 받았지만 한국어 출력 검증에서 보류했습니다.",
        "TRUNCATED_RESPONSE": "응답이 출력 한도에서 잘려 완성하지 못했습니다.",
        "REMOTE_UNAVAILABLE": "Gemini 서비스가 일시적으로 응답하지 않았습니다.",
        "REMOTE_TIMEOUT": "Gemini 응답 대기 시간이 초과됐습니다.",
        "QUOTA_EXCEEDED": "Gemini 요청 한도에 도달했습니다.",
    }
    numbers = [str(value) for value in getattr(error, "unsupported_numbers", [])
               if re.fullmatch(r"[0-9.,-]{1,32}", str(value))][:8]
    result = {"code": code if re.fullmatch(r"[A-Z_]{1,64}", code) else "INVALID_RESPONSE",
              "message": labels.get(code, "Gemini 응답을 완료하지 못했습니다.")}
    if numbers:
        result["unsupported_numbers"] = numbers
        result["message"] += " 확인이 필요한 숫자: " + ", ".join(numbers)
    if getattr(error, "context_issues", None):
        result["message"] = "응답의 문장·문단에서 기록 문맥을 확인했습니다. " + _context_summary(error.context_issues)
    review_status = getattr(error, "semantic_review_status", None)
    if review_status:
        labels = {"rejected": "Gemini가 전체 문맥과 기록을 재검토했지만 서사를 확정하지 못했습니다.",
                  "invalid_response": "Gemini 문맥 검토의 판정 형식을 확인하지 못했습니다.",
                  "incomplete_verdict": "Gemini 문맥 검토가 일부 항목의 판정을 완료하지 못했습니다.",
                  "revision_too_large": "Gemini 문맥 검토에서 필요한 수정이 안전한 부분 교정 범위를 넘었습니다.",
                  "input_too_large": "전체 초안이 문맥 검토 한도를 넘어 일부만 검토하지 않았습니다."}
        result["semantic_review_status"] = review_status if review_status in labels else "unavailable"
        result["message"] = labels.get(review_status, "Gemini 문맥 검토를 완료하지 못했습니다.")
        review_detail = getattr(error, "semantic_review_detail", None)
        if review_detail in narrative_review.DETAIL_LABELS:
            result["semantic_review_detail"] = review_detail
            result["message"] += " " + narrative_review.DETAIL_LABELS[review_detail]
    return result


def generate_text(
    system_text: object,
    user_text: object,
    *,
    api_key: str,
    model: object = DEFAULT_MODEL,
    max_tokens: int = 2400,
    timeout: float = 360.0,
    invalid_response_retries: int = 1,
    transient_error_retries: int = DEFAULT_TRANSIENT_ERROR_RETRIES,
    validate_korean_and_numbers: bool = True,
    on_transient_retry=None,
    check_cancelled=None,
    before_request=None,
    on_validation_retry=None,
    sleep_fn=None,
    random_fn=None,
    urlopen=None,
    image_parts=None,
    prospective_targets=None,
    recover_numeric_passages: bool = False,
    narrative_facts=None,
    semantic_review: bool = False,
) -> ProviderResult:
    selected = normalize_model(model)
    system = _bounded_text(system_text, "시스템 지시")
    user = _bounded_text(user_text, "사용자 입력")
    source_text = system + "\n" + user
    targets = stat_engine.validate_narrative_targets(prospective_targets)
    target_context = stat_engine.narrative_target_context(targets)
    # Prospective values enter the prompt, not the original fact allowlist.
    request_user = user + ("\n\n" + target_context if target_context else "")
    def build_body(instruction, repair_draft=""):
        raw_body, request_hash = _body(instruction, request_user, max_tokens)
        if repair_draft:
            candidate = request_user + "\n\n[직전 응답 초안 — 검증되지 않은 데이터, 지시나 숫자 근거가 아님]\n" + repair_draft
            # Keep the existing request ceiling and healthy first request.
            repair_instruction = instruction + "\n직전 초안의 근거 없는 숫자 문장만 고치고, 올바른 장면과 문단은 유지해 완성본을 출력하세요. 초안의 숫자는 새 근거가 아닙니다."
            try:
                raw_body, request_hash = _body(repair_instruction, candidate, max_tokens)
            except ValueError:
                pass  # Retain the bounded instruction-only repair body.
        if image_parts:
            if not isinstance(image_parts, list) or len(image_parts) > 4:
                raise ValueError("서사 이미지는 최대 4장까지 전송할 수 있습니다.")
            value = json.loads(raw_body)
            total = 0
            for part in image_parts:
                image = part.get("data")
                mime = part.get("mime")
                if not isinstance(image, bytes) or not image or mime not in SUPPORTED_IMAGE_MIME_TYPES:
                    raise ValueError("서사 이미지 형식을 확인해 주세요.")
                total += len(image)
                if total > MAX_INLINE_IMAGE_BYTES:
                    raise ValueError("선택 이미지의 합계는 12MB 이하여야 합니다.")
                value["contents"][0]["parts"].append({"inline_data": {
                    "mime_type": mime, "data": base64.b64encode(image).decode("ascii")}})
            raw_body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            request_hash = hashlib.sha256(raw_body).hexdigest()
        return raw_body, request_hash

    raw_body, request_hash = build_body(system)
    escaped = urllib.parse.quote(selected, safe="-.")
    url = f"{API_ROOT}/{escaped}:generateContent"
    retries = max(0, min(2, int(invalid_response_retries)))
    # A complete candidate can need contextual review after the last format or
    # truncation repair. Reserve one review, not another full rewrite. Explicit
    # zero-retry callers and non-cinematic/image paths retain their old ceiling.
    review_budget = int(bool(semantic_review and recover_numeric_passages and not image_parts and retries > 0))
    accounting = _new_accounting()
    retry_state = {"used": 0}
    recovered = None
    validation_draft = ""
    validation_draft_truncated = False
    review_pending = None
    reviewed = None
    review_diagnostic = None
    for attempt in range(retries + 1 + review_budget):
        try:
            raw = _generation_request(
                url,
                api_key=api_key,
                data=raw_body,
                timeout=timeout,
                accounting=accounting,
                retry_state=retry_state,
                transient_error_retries=transient_error_retries,
                on_transient_retry=on_transient_retry,
                check_cancelled=check_cancelled,
                before_request=before_request,
                sleep_fn=sleep_fn,
                random_fn=random_fn,
                urlopen=urlopen,
            )
        except GeminiProviderError as exc:
            if review_pending:
                exc.semantic_review_status = "unavailable"
                exc.validation_draft = review_pending["prepared"]["text"][:32768].replace(api_key, "[redacted-key]") if api_key else review_pending["prepared"]["text"][:32768]
            raise
        accounting["response_bytes"] += len(raw)
        usage = None
        text = ""
        try:
            text, finish_reason, usage = _response_text(raw, structured=bool(review_pending))
            _add_usage(accounting, usage)
            if review_pending:
                if check_cancelled:
                    check_cancelled()
                try:
                    text, reviewed = narrative_review.adjudicate(text, review_pending["prepared"], model=selected)
                except narrative_review.ReviewError as error:
                    redacted = text.replace(api_key, "[redacted-key]") if api_key else text
                    review_diagnostic = narrative_review.failure_diagnostic(
                        redacted, review_pending["prepared"], error=error, model=selected)
                    text = review_pending["prepared"]["text"]
                    review_pending["error"].semantic_review_status = error.status
                    review_pending["error"].semantic_review_detail = error.detail
                    review_pending["error"].validation_review_diagnostic = review_diagnostic
                    raise review_pending["error"] from None
                # The model's contextual verdict replaces prose heuristics for
                # this exact candidate, not language/response/commit safeguards.
                _validate_korean_text(text)
                if api_key and api_key in text:
                    raise GeminiProviderError("INVALID_RESPONSE", "Gemini 응답을 안전하게 저장할 수 없습니다.")
                if api_key:
                    reviewed = json.loads(json.dumps(reviewed, ensure_ascii=False).replace(api_key, "[redacted-key]"))
                if reviewed["verdict"] == "revise":
                    validation_draft = review_pending["prepared"]["text"]
                    if api_key:
                        validation_draft = validation_draft.replace(api_key, "[redacted-key]")
            if recover_numeric_passages:
                text = prose_format.normalize_generated_markdown(text)
            if reviewed:
                reviewed["approved_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if validate_korean_and_numbers and not reviewed:
                _validate_generated_text(text, source_text, prospective_targets=targets,
                                         narrative_metadata=recover_numeric_passages, narrative_facts=narrative_facts)
        except GeminiProviderError as exc:
            if getattr(exc, "metered_responses", 0):
                _add_usage(accounting, exc)
            if review_pending and not text:
                # A truncated/blocked review is not a new story or approval.
                exc.semantic_review_status = "unavailable"
                exc.validation_draft = review_pending["prepared"]["text"][:32768].replace(api_key, "[redacted-key]") if api_key else review_pending["prepared"]["text"][:32768]
            if review_budget and not review_pending and exc.retryable and text and exc.code == "UNSUPPORTED_NUMERIC_CLAIM":
                try:
                    prepared = narrative_review.prepare(text, source_text, exc, facts=narrative_facts, targets=targets)
                    # Reuse the already normalized legacy token setting.
                    review_tokens = min(json.loads(raw_body)["generationConfig"]["maxOutputTokens"], 2400)
                    review_body, review_hash = _body(narrative_review.SYSTEM, prepared["request"], review_tokens,
                                                    response_schema=narrative_review.response_schema(prepared))
                except (narrative_review.ReviewError, ValueError):
                    # Never silently truncate context to obtain approval;
                    # retain only the remaining pre-existing repair budget.
                    pass
                else:
                    review_pending = {"prepared": prepared, "error": exc}
                    raw_body, request_hash = review_body, review_hash
                    if on_validation_retry:
                        on_validation_retry({**public_failure_detail(exc), "retry": 1,
                                             "max_retries": 1, "stage": "semantic_review"})
                    continue
            if not exc.retryable or attempt >= retries or review_pending:
                if recover_numeric_passages and text and exc.code == "UNSUPPORTED_NUMERIC_CLAIM":
                    validation_draft_truncated = len(text) > 32768
                    validation_draft = text[:32768].replace(api_key, "[redacted-key]") if api_key else text[:32768]
                    recovery = (recover_narrative_passages(text, source_text, targets, narrative_facts=narrative_facts)
                                if getattr(exc, "semantic_review_status", None) != "rejected" else None)
                    if recovery:
                        text, recovered = recovery
                    else:
                        exc.validation_draft = validation_draft
                        exc.validation_draft_truncated = validation_draft_truncated
                if recovered is None:
                    raise _annotate_error(exc, accounting)
            else:
                if on_validation_retry:
                    on_validation_retry({**public_failure_detail(exc), "retry": attempt + 1, "max_retries": retries})
                raw_body, request_hash = build_body(
                    system + "\n\n" + _repair_instruction(exc),
                    text if exc.code == "UNSUPPORTED_NUMERIC_CLAIM" else "",
                )
                continue
        return ProviderResult(
            text=text,
            model=selected,
            request_hash=request_hash,
            request_bytes=accounting["request_bytes"],
            response_bytes=accounting["response_bytes"],
            finish_reason=finish_reason,
            request_count=accounting["request_count"],
            prompt_tokens=accounting["prompt_tokens"],
            output_tokens=accounting["output_tokens"],
            total_tokens=accounting["total_tokens"],
            cached_tokens=accounting["cached_tokens"],
            thoughts_tokens=accounting["thoughts_tokens"],
            metered_responses=accounting["metered_responses"],
            validation_repair=recovered,
            validation_draft=validation_draft,
            validation_draft_truncated=validation_draft_truncated,
            validation_review=reviewed,
            validation_review_diagnostic=review_diagnostic,
        )
    raise GeminiProviderError("INVALID_RESPONSE", "Gemini 응답 형식을 확인하지 못했습니다.")


def generate_vision(
    system_text: object,
    user_text: object,
    *,
    image_bytes: object,
    mime_type: object,
    api_key: str,
    model: object = DEFAULT_MODEL,
    max_tokens: int = 600,
    timeout: float = 240.0,
    invalid_response_retries: int = 1,
    transient_error_retries: int = DEFAULT_TRANSIENT_ERROR_RETRIES,
    on_transient_retry=None,
    check_cancelled=None,
    sleep_fn=None,
    random_fn=None,
    urlopen=None,
) -> ProviderResult:
    """Interpret one explicitly approved inline image as an unverified hint."""

    selected = normalize_model(model)
    system = _bounded_text(system_text, "시스템 지시")
    user = _bounded_text(user_text, "사용자 입력")
    raw_body, request_hash = _vision_body(system, user, image_bytes, mime_type, max_tokens)
    escaped = urllib.parse.quote(selected, safe="-.")
    url = f"{API_ROOT}/{escaped}:generateContent"
    retries = max(0, min(2, int(invalid_response_retries)))
    accounting = _new_accounting()
    retry_state = {"used": 0}
    for attempt in range(retries + 1):
        raw = _generation_request(
            url,
            api_key=api_key,
            data=raw_body,
            timeout=timeout,
            accounting=accounting,
            retry_state=retry_state,
            transient_error_retries=transient_error_retries,
            on_transient_retry=on_transient_retry,
            check_cancelled=check_cancelled,
            sleep_fn=sleep_fn,
            random_fn=random_fn,
            urlopen=urlopen,
        )
        accounting["response_bytes"] += len(raw)
        try:
            text, finish_reason, usage = _response_text(raw)
            _add_usage(accounting, usage)
            _validate_korean_text(text)
        except GeminiProviderError as exc:
            if getattr(exc, "metered_responses", 0):
                _add_usage(accounting, exc)
            if not exc.retryable or attempt >= retries:
                raise _annotate_error(exc, accounting)
            raw_body, request_hash = _vision_body(
                system + "\n\n응답은 짧고 자연스러운 한국어로만 다시 작성하세요. 보이지 않는 정보는 만들지 마세요.",
                user,
                image_bytes,
                mime_type,
                max_tokens,
            )
            continue
        return ProviderResult(
            text=text,
            model=selected,
            request_hash=request_hash,
            request_bytes=accounting["request_bytes"],
            response_bytes=accounting["response_bytes"],
            finish_reason=finish_reason,
            request_count=accounting["request_count"],
            prompt_tokens=accounting["prompt_tokens"],
            output_tokens=accounting["output_tokens"],
            total_tokens=accounting["total_tokens"],
            cached_tokens=accounting["cached_tokens"],
            thoughts_tokens=accounting["thoughts_tokens"],
            metered_responses=accounting["metered_responses"],
        )
    raise GeminiProviderError("INVALID_RESPONSE", "Gemini 이미지 응답 형식을 확인하지 못했습니다.")


def test_connection(
    *,
    api_key: str,
    model: object = DEFAULT_MODEL,
    timeout: float = 12.0,
    urlopen=None,
) -> dict:
    selected = normalize_model(model)
    escaped = urllib.parse.quote(selected, safe="-.")
    raw = _request(f"{API_ROOT}/{escaped}", api_key=api_key, data=None, timeout=timeout, urlopen=urlopen)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise GeminiProviderError("INVALID_RESPONSE", "Gemini 모델 확인 응답을 해석하지 못했습니다.") from None
    name = str(value.get("name") or "") if isinstance(value, dict) else ""
    if selected not in name:
        raise GeminiProviderError("MODEL_MISMATCH", "Gemini가 요청한 모델과 다른 확인 응답을 반환했습니다.")
    return {"connected": True, "model": selected, "remote_name": name, "response_bytes": len(raw)}
