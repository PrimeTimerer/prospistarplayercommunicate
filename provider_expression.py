#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider-neutral expression adapter for a frozen Star Player interaction plan.

The remote provider may improve prose, but it never owns an interaction ID,
proposal decision, fact callout, explicit user quotation, relationship change,
or commit transition.  The deterministic plan and its locked blocks are
fingerprinted before inference and revalidated again before a proposal is
committed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

import narrative_contracts as nc
import prose_format


VERSION = "1.1.0"
READABLE_VERSIONS = {"1.0.0", VERSION}
MAX_EXPRESSION_CHARS = 12_000
MAX_EXPRESSION_BLOCKS = 24


def _canonical_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def response_plan_hash(response: dict) -> str:
    """Fingerprint the complete deterministic response before any rewrite."""

    return _canonical_hash(response)


def _without_override(proposal: dict) -> dict:
    value = copy.deepcopy(proposal)
    value.pop("provider_expression", None)
    return value


def proposal_plan_hash(proposal: dict) -> str:
    return _canonical_hash(_without_override(proposal))


def blocks_hash(blocks: list[dict]) -> str:
    return _canonical_hash(blocks)


def _locked(block: dict) -> bool:
    return block.get("type") in ("fact_callout", "source_note") or block.get("origin") == "user_explicit"


def locked_blocks_hash(blocks: list[dict]) -> str:
    return _canonical_hash([block for block in blocks if _locked(block)])


def _preview_only(block: dict) -> bool:
    return block.get("type") == "fact_callout" and block.get("label") == "확정 대기"


def _scene_blocks(blocks: list[dict]) -> list[dict]:
    return [copy.deepcopy(block) for block in blocks if not _preview_only(block)]


def _split_long_paragraph(text: str, limit: int = nc.PARAGRAPH_HARD_MAX) -> list[str]:
    remaining = str(text or "").strip()
    parts: list[str] = []
    while len(remaining) > limit:
        window = remaining[: limit + 1]
        candidates = [match.end() for match in re.finditer(r"[.!?。！？](?:\s+|$)", window)]
        cut = candidates[-1] if candidates and candidates[-1] >= limit // 3 else window.rfind(" ")
        if cut < limit // 3:
            cut = limit
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def generated_blocks(text: object, *, provider: str = "gemini") -> list[dict]:
    """Convert provider Markdown into bounded typed prose blocks."""

    normalized = prose_format.normalize_generated_markdown(text)
    if not normalized:
        raise ValueError("선택한 모델이 비어 있는 대화 표현을 반환했습니다.")
    if len(normalized) > MAX_EXPRESSION_CHARS:
        raise ValueError("선택한 모델의 대화 표현이 저장 한도를 넘었습니다.")
    repaired = nc.paragraphize_legacy(normalized)["blocks"]
    result: list[dict] = []
    for row in repaired:
        value = str(row.get("text") or "").strip()
        heading = re.fullmatch(r"#{1,3}\s+(.+)", value)
        if heading:
            result.append(
                nc.block(
                    "section_heading",
                    heading.group(1).strip(),
                    origin=f"{provider}_expression",
                    evidence_class="generated_fiction",
                )
            )
            continue
        if row.get("type") == "section_heading":
            result.append(
                nc.block(
                    "section_heading",
                    value,
                    origin=f"{provider}_expression",
                    evidence_class="generated_fiction",
                )
            )
            continue
        for part in _split_long_paragraph(value):
            result.append(
                nc.block(
                    "paragraph",
                    part,
                    origin=f"{provider}_expression",
                    evidence_class="generated_fiction",
                )
            )
    if not result or len(result) > MAX_EXPRESSION_BLOCKS:
        raise ValueError("선택한 모델의 대화 표현 문단 수가 저장 한도를 벗어났습니다.")
    problems = nc.validate_blocks(result)
    if problems:
        raise ValueError("선택한 모델의 대화 표현 문단 구조가 올바르지 않습니다: " + "; ".join(problems))
    return result


def _merge_expression(source: list[dict], generated: list[dict]) -> list[dict]:
    leading: list[dict] = []
    remaining_locked: list[dict] = []
    seen_mutable = False
    for block in source:
        if _locked(block):
            (remaining_locked if seen_mutable else leading).append(copy.deepcopy(block))
        else:
            seen_mutable = True
    merged = leading + copy.deepcopy(generated) + remaining_locked
    problems = nc.validate_blocks(merged)
    if problems:
        raise ValueError("모델 표현과 고정 사실을 합칠 수 없습니다: " + "; ".join(problems))
    return merged


def build_prompt(
    response: dict,
    payload: dict,
    *,
    verified_fact: dict,
    approved_player_context: str = "",
    approved_memory_context: str = "",
) -> tuple[str, str]:
    """Build a text-only prompt from the currently approved frozen preview."""

    reply_blocks = list((response.get("reply") or {}).get("blocks") or [])
    expression = [
        {
            "type": row.get("type"),
            "speaker": row.get("speaker"),
            "text": row.get("text"),
        }
        for row in reply_blocks
        if not _locked(row) and not _preview_only(row)
    ]
    locked = [
        {"label": row.get("label"), "text": row.get("text")}
        for row in reply_blocks
        if _locked(row)
    ]
    system = (
        "너는 스타플레이어 세계선의 '표현 전용' 한국어 대화 작가다. 입력에는 이미 내장 엔진이 확정한 "
        "의도, 공개 범위, 제안 여부와 고정 사실이 있다. 그것들을 바꾸거나 새 사건·관계 변화·약속·갈등 해결·공개 결정을 "
        "추가하지 말고, 오직 현재 장면의 분위기·동작·대사·감정의 결을 자연스럽고 풍부하게 다시 쓴다. "
        "사용자의 직접 인용문과 고정 사실은 앱이 별도로 원문 보존하므로 되풀이하거나 고쳐 쓰지 않는다. "
        "입력에 없는 인명, 부상, 계약, 연애, 범죄, 실제 보도, 경기 결과, 날짜, 점수, 통계 숫자를 만들지 않는다. "
        "제안 상태인 장면을 이미 일어난 일처럼 확정하지 않는다. JSON·목록·명령문·메타 설명 없이 한국어 본문만 쓴다. "
        "짧은 문단 2~6개를 빈 줄로 구분하고 한 문단에 번호를 몰아넣지 않는다."
    )
    packet = {
        "verified_save_fact": verified_fact,
        "current_user_message": payload.get("user_text"),
        "selected_context": {
            "category": payload.get("category"),
            "situation": payload.get("situation"),
            "target_role": payload.get("target"),
            "tone": payload.get("tone"),
            "visibility": payload.get("visibility"),
        },
        "deterministic_decision": {
            "primary_act": (response.get("understanding") or {}).get("primary_act"),
            "mode": response.get("mode"),
            "creates_event": bool(response.get("creates_event")),
        },
        "locked_notes_do_not_rewrite": locked,
        "expression_draft_to_enrich": expression,
        "approved_player_context": approved_player_context or None,
        "approved_layered_memory": approved_memory_context or None,
    }
    return system, json.dumps(packet, ensure_ascii=False, indent=2)


def apply_to_response(
    response: dict,
    text: object,
    *,
    model: str,
    provider_audit: dict,
    provider: str = "gemini",
) -> dict:
    """Return a provider-rendered copy while keeping the state plan intact."""

    original = copy.deepcopy(response)
    original_hash = response_plan_hash(original)
    provider = "local_llm" if provider == "local_llm" else "gemini"
    renderer = f"{provider}_expression"
    generated = generated_blocks(text, provider=provider)
    source_blocks = list((original.get("reply") or {}).get("blocks") or [])
    scene_source = _scene_blocks(source_blocks)
    scene_override = _merge_expression(scene_source, generated)
    preview_prefix = [copy.deepcopy(row) for row in source_blocks if _preview_only(row)]
    result = copy.deepcopy(original)
    result["renderer"] = renderer
    result["reply"]["blocks"] = preview_prefix + scene_override
    result["reply"]["text"] = nc.project_blocks(result["reply"]["blocks"])
    for proposal in result.get("proposed_events") or []:
        proposal["provider_expression"] = {
            "version": VERSION,
            "provider": provider,
            "model": str(model),
            "proposal_hash": proposal_plan_hash(proposal),
            "source_blocks_hash": blocks_hash(scene_source),
            "locked_blocks_hash": locked_blocks_hash(scene_source),
            "blocks": copy.deepcopy(scene_override),
        }
    provenance = result.setdefault("provenance", {})
    provenance.update(
        {
            "renderer": renderer,
            "state_renderer": original.get("renderer") or "deterministic",
            "expression_adapter_version": VERSION,
            "deterministic_plan_hash": original_hash,
            "provider": provider,
            "provider_model": str(model),
            "provider_audit": copy.deepcopy(provider_audit),
        }
    )
    return result


def apply_to_realized(realized: dict, proposal: dict) -> dict:
    """Reapply a stored expression only when its frozen source still matches."""

    override = proposal.get("provider_expression")
    if not isinstance(override, dict):
        return realized
    provider = str(override.get("provider") or "")
    if override.get("version") not in READABLE_VERSIONS or provider not in ("gemini", "local_llm"):
        raise ValueError("이 제안의 모델 표현 버전을 확인할 수 없습니다. 다시 대화해 주세요.")
    if override.get("proposal_hash") != proposal_plan_hash(proposal):
        raise ValueError("모델이 표현한 뒤 사건 제안의 구조가 달라졌습니다. 다시 대화해 주세요.")

    source = list(realized.get("blocks") or [])
    extra_user_block = None
    if blocks_hash(source) != override.get("source_blocks_hash"):
        for index, block in enumerate(source):
            if block.get("origin") != "user_explicit":
                continue
            candidate = source[:index] + source[index + 1 :]
            if blocks_hash(candidate) == override.get("source_blocks_hash"):
                source = candidate
                extra_user_block = copy.deepcopy(block)
                break
    if blocks_hash(source) != override.get("source_blocks_hash"):
        raise ValueError("확정 시점의 장면 표현이 달라졌습니다. 현재 내용으로 다시 대화해 주세요.")
    blocks = copy.deepcopy(override.get("blocks") or [])
    if locked_blocks_hash(source) != override.get("locked_blocks_hash") or locked_blocks_hash(blocks) != override.get("locked_blocks_hash"):
        raise ValueError("모델 표현에서 고정 사실 또는 직접 인용문이 달라졌습니다.")
    if extra_user_block:
        blocks.append(extra_user_block)
    problems = nc.validate_blocks(blocks)
    if problems:
        raise ValueError("저장할 모델 장면 표현이 올바르지 않습니다: " + "; ".join(problems))

    result = copy.deepcopy(realized)
    renderer = f"{provider}_expression"
    result["renderer"] = renderer
    result["blocks"] = blocks
    result["text"] = nc.project_blocks(blocks)
    result.setdefault("provenance", {}).update(
        {
            "renderer": renderer,
            "state_renderer": realized.get("renderer") or "deterministic",
            "expression_adapter_version": VERSION,
            "provider": provider,
            "provider_model": override.get("model"),
        }
    )
    return result
