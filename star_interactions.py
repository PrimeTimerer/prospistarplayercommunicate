"""Offline Star Player activity scenes with revisioned, world-local continuity.

Game-action observations and authored scenes have separate provenance. This
module never accesses a save, process, network, clock, or model. Planning is
pure; the service commits a validated plan under its existing state lock.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

import narrative_contracts as nc
import interaction_intent
import interaction_voice
import personal_context
import realism_gate
import template_store as ts
import world_identity

VERSION = "1.1.0"
STORE = "star_interactions"
EVENTS = "star_interaction_events"
ROLES = {
    "teammate": "동료", "rookie": "후배 선수", "rival": "라이벌",
    "coach": "코치", "manager": "감독", "family": "가족", "self": "혼자",
}
PROGRESS = {"discussion": "배울 내용 상의", "registered": "러닝 등록", "practice": "학습 중", "acquired": "습득 확인"}
STATUS = {"open": "대화 중", "paused": "잠시 멈춤", "closed": "마무리"}
BEATS = {
    "start": "첫 장면", "continue": "대화 계속", "agree": "동의",
    "refuse": "거절", "joke": "농담", "confide": "속마음",
    "disagree": "의견 충돌", "reconcile": "화해", "promise": "약속",
    "change": "약속 변경", "fulfill": "약속 이행", "close": "대화 마무리",
    "pause": "잠시 멈춤", "resume": "다시 이어가기", "share": "공개 발언",
    "callback": "약속을 떠올릴 계기",
}
_RESUME = re.compile(r"어제|지난|그때|다시|이어|기억")
_FACT_QUESTION = re.compile(r"(?:성적|기록|승패|점수|경기\s*결과|통산|홈런\s*수|친밀도|능력치|스탯).*(?:알려|몇|얼마|어떻게|보여|확인)")
_PATTERNS = interaction_intent.PATTERNS


@lru_cache(maxsize=1)
def pack() -> dict:
    path = Path(__file__).resolve().parent / "data" / "interactions" / "core-ko.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("license_class") != "authored" or value.get("version") != VERSION:
        raise ValueError("The interaction pack has an unsupported contract.")
    return value


@lru_cache(maxsize=1)
def manifest_hash() -> str:
    return hashlib.sha256(json.dumps(pack(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _authored_block(kind: str, text: str, source: str, **extra) -> dict:
    return nc.block(kind, text, template_id=f"{pack()['pack_id']}:{source}", origin="authored_template", **extra)


def catalog() -> dict:
    return {
        "version": VERSION, "id": "starplayer", "label": "스타플레이어 행동",
        "description": "선수 교류·산책·외출·학습을 대화와 기억으로 이어갑니다.",
        "actions": [{key: copy.deepcopy(row[key]) for key in ("id", "label", "event_type", "rule_note", "rule_sources")} for row in pack()["actions"].values()],
        "progress": [{"id": key, "label": label} for key, label in PROGRESS.items()],
        "availability": "unknown_in_current_save",
        "game_writes": False,
    }


def _text(value: object, *, limit: int, label: str, required: bool = False) -> str:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{label}은(는) 텍스트로 입력해 주세요.")
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) > limit or (required and not text):
        raise ValueError(f"{label}을(를) 1~{limit}자로 입력해 주세요." if required else f"{label}은(는) {limit}자 이하여야 합니다.")
    return text


def check_origin(payload: dict, *, universe_id: str, protagonist_id: object, game_date: str) -> None:
    origin = payload.get("interaction_origin")
    if origin is None:
        return  # Existing API callers bind to the freshly prepared live world.
    expected = {"universe_id": str(universe_id), "protagonist_id": str(protagonist_id), "game_date": game_date}
    if not isinstance(origin, dict) or any(str(origin.get(key) or "") != value for key, value in expected.items()):
        raise ValueError("행동을 고른 뒤 선수·세계선·날짜가 바뀌었습니다. 현재 화면에서 다시 선택해 주세요.")


def owned_rows(state: dict, *, universe_id: str, protagonist_id: object) -> list[dict]:
    return [
        row for row in (state.get(STORE) or {}).values()
        if isinstance(row, dict) and row.get("universe_id") == str(universe_id)
        and row.get("protagonist_id") == str(protagonist_id)
    ]


def check_chat_binding(state: dict, payload: dict, *, universe_id: str, protagonist_id: object, game_date: str) -> dict | None:
    check_origin(payload, universe_id=universe_id, protagonist_id=protagonist_id, game_date=game_date)
    ident = payload.get("interaction_id")
    if not ident:
        return None
    row = next((row for row in owned_rows(state, universe_id=universe_id, protagonist_id=protagonist_id)
                if row["interaction_id"] == ident and row["last_date"] <= game_date), None)
    if row is None:
        raise ValueError("현재 세계선에서 이어갈 만남을 찾지 못했습니다.")
    return row


def summaries(state: dict, *, universe_id: str, protagonist_id: object, game_date: str) -> list[dict]:
    rows = [row for row in owned_rows(state, universe_id=universe_id, protagonist_id=protagonist_id) if row["last_date"] <= game_date]
    rows.sort(key=lambda row: (row["last_date"], int(row.get("last_order", 0))), reverse=True)
    return [{
        "interaction_id": row["interaction_id"], "action_id": row["action_id"],
        "label": f"{pack()['actions'][row['action_id']]['label']} · {row['participant_label']} · {row['topic']}",
        "participant_label": row["participant_label"], "participant_key": row.get("participant_key"),
        "participant_source": row.get("participant_source"), "status": row["status"],
        "status_label": STATUS[row["status"]], "origin_date": row["origin_date"], "last_date": row["last_date"],
        "revision": row["revision"], "visibility": row["visibility"],
        "activity_evidence_class": row["activity_evidence_class"],
        "learning_progress": row["learning_progress"], "game_progress": row.get("game_progress"),
        "promise": copy.deepcopy(row.get("promise")), "last_beat": row.get("last_beat"),
    } for row in rows]


def start_plan(payload: dict, *, snapshot: dict, game_date: str, universe_id: str, state: dict, spotlight: dict | None = None) -> dict:
    player_id = str(snapshot["player"]["id"])
    check_origin(payload, universe_id=universe_id, protagonist_id=player_id, game_date=game_date)
    action_id = str(payload.get("situation") or "player_exchange")
    action = pack()["actions"].get(action_id)
    if action is None:
        raise ValueError("지원하는 스타플레이어 행동을 선택해 주세요.")
    role = str(payload.get("target") or ("self" if action_id == "outing_walk" else "teammate"))
    if role not in ROLES or (action_id in ("player_exchange", "learning") and role not in ("teammate", "rookie", "rival")):
        raise ValueError("선수 교류·학습의 상대는 동료·후배 선수·라이벌 중에서 선택해 주세요.")
    visibility = nc.visibility_of(payload.get("visibility") or "private")
    if visibility not in ("private", "clubhouse"):
        raise ValueError("이 행동은 비공개 또는 팀 내부로 시작합니다. 공개하려면 대화에서 별도로 제안하고 확정해 주세요.")
    evidence = str(payload.get("interaction_mode") or "fictional_intervention")
    if evidence not in ("fictional_intervention", "user_confirmed"):
        raise ValueError("게임 행동 출처는 창작 또는 사용자 확인만 선택할 수 있습니다.")
    if evidence == "user_confirmed" and payload.get("action_confirmed") is not True:
        raise ValueError("게임에서 실제로 한 행동인지 직접 확인해 주세요.")
    progress = str(payload.get("learning_progress") or "discussion") if action_id == "learning" else None
    if progress is not None and progress not in PROGRESS:
        raise ValueError("학습 진행 상태를 선택해 주세요.")
    name = _text(payload.get("participant_name"), limit=60, label="상대 이름")
    if role == "self" and name:
        raise ValueError("혼자 하는 행동에는 상대 이름을 입력하지 않습니다.")
    place = _text(payload.get("interaction_place"), limit=100, label="장소")
    topic = _text(payload.get("interaction_topic"), limit=160, label="이야깃거리·배울 내용", required=action_id == "learning")
    note = _text(payload.get("user_text"), limit=1200, label="하고 싶은 말")
    tone = str(payload.get("tone") or "honest")
    if tone not in pack()["tones"]:
        raise ValueError("지원하는 말투를 선택해 주세요.")
    if role == "self":
        participant = {
            "participant_key": None,
            "participant_label": ROLES[role],
            "participant_source": "fictional_role",
            "participant_identity_revision": 0,
        }
    else:
        participant = world_identity.prepare_participant(
            state,
            universe_id=universe_id,
            protagonist_id=player_id,
            role=role,
            supplied_name=name,
            entity_id=payload.get("participant_entity_id"),
        )
    order = len(state.get(EVENTS) or []) + 1
    ident = nc.stable_id(
        universe_id,
        player_id,
        game_date,
        order,
        action_id,
        participant["participant_key"] or role,
        topic,
        length=20,
    )
    participant_key = participant["participant_key"]
    interaction = {
        "schema_version": 1, "interaction_id": ident, "universe_id": str(universe_id),
        "protagonist_id": player_id, "action_id": action_id, "participant_role": role,
        "participant_label": participant["participant_label"], "participant_key": participant_key,
        "participant_source": participant["participant_source"],
        "participant_identity_revision": participant["participant_identity_revision"],
        "place": place or action["default_place"], "topic": topic or action["default_topic"],
        "origin_date": game_date, "last_date": game_date, "status": "open",
        "revision": 0, "visibility": visibility, "activity_evidence_class": evidence,
        "learning_progress": progress,
        "game_progress": progress if evidence == "user_confirmed" else None,
        "rule_sources": list(action["rule_sources"]), "rule_note": action["rule_note"],
        "promise": None, "last_line": "", "last_beat": None, "last_order": order, "tone": tone,
    }
    if action_id == "learning" and participant_key and evidence == "user_confirmed":
        previous = [row for row in owned_rows(state, universe_id=universe_id, protagonist_id=player_id)
                    if row["action_id"] == "learning" and row.get("participant_key") == participant_key
                    and row.get("topic") == topic and row.get("game_progress")
                    and row["last_date"] <= game_date]
        if previous:
            prior = max(previous, key=lambda row: (row["last_date"], row.get("last_order", 0)))
            interaction["previous_learning"] = {
                "interaction_id": prior["interaction_id"], "game_date": prior["origin_date"],
                "progress": prior["game_progress"], "fact_id": prior.get("action_fact_id"),
            }
    proposal = _proposal(interaction, "start", game_date, line=note, initial=interaction, state=state)
    proposal["expression_plan"]["stature_coda"] = int((spotlight or {}).get("tier_index") or 0) >= 4
    return proposal


def _proposal(row: dict, beat: str, game_date: str, *, line: str = "", initial: dict | None = None, state: dict | None = None) -> dict:
    action = pack()["actions"][row["action_id"]]
    event_type = "SP.MEDIA.QUOTE" if beat == "share" else "SP.USER.PROMISE" if beat == "promise" else action["event_type"]
    result = {
        "source": "star_interaction", "interaction_id": row["interaction_id"], "expected_revision": row["revision"],
        "proposal_id": nc.stable_id(row["interaction_id"], row["revision"], game_date, beat, line),
        "type": event_type, "act": f"interaction_{beat}", "beat": beat, "line": line,
        "label": f"{action['label']} · {BEATS[beat]}", "target": row["participant_key"],
        "target_label": row["participant_label"], "visibility": "national" if beat == "share" else row["visibility"],
        "game_date": game_date, "universe_id": row["universe_id"], "protagonist_id": row["protagonist_id"],
        "thread_type": "performance_pursuit" if row["action_id"] == "learning" else "private_life",
        "requires_confirmation": True, "consequential": True,
    }
    if initial is not None:
        result["initial"] = copy.deepcopy(initial)
    if state is not None:
        result["context_refs"] = personal_context.scene_references(
            state,
            universe_id=row["universe_id"],
            protagonist_id=row["protagonist_id"],
            game_date=game_date,
            query=" ".join(
                value for value in (row.get("topic"), row.get("place"), line) if value
            ),
            audience=result["visibility"],
        )
        result["expression_plan"] = interaction_voice.plan(state, row, result, pack())
    return result


def prepare_expression(state: dict, proposal: dict) -> dict:
    """Prepare a scheduled callback immediately before its serialized commit."""
    result = copy.deepcopy(proposal)
    row = result.get("initial") or state[STORE][result["interaction_id"]]
    result["expression_plan"] = interaction_voice.plan(state, row, result, pack())
    return result


def validate_plan(state: dict, proposal: dict, *, universe_id: str, protagonist_id: object, game_date: str) -> dict:
    if (proposal.get("source") != "star_interaction" or proposal.get("universe_id") != str(universe_id)
            or proposal.get("protagonist_id") != str(protagonist_id) or proposal.get("game_date") != game_date):
        raise ValueError("이 만남의 제안은 현재 선수·세계선·날짜에 속하지 않습니다.")
    ident = proposal.get("interaction_id")
    current = (state.get(STORE) or {}).get(ident)
    if proposal.get("beat") == "start":
        row = proposal.get("initial") or {}
        if current is not None or row.get("interaction_id") != ident or row.get("revision") != 0:
            raise ValueError("이미 시작했거나 올바르지 않은 만남입니다.")
        if row.get("participant_source") == "world_entity":
            identity = world_identity.prepare_participant(
                state,
                universe_id=str(universe_id),
                protagonist_id=protagonist_id,
                role=row.get("participant_role"),
                supplied_name=row.get("participant_label"),
                entity_id=row.get("participant_key"),
            )
            if identity["participant_identity_revision"] != row.get("participant_identity_revision"):
                raise ValueError("주변 인물 정보가 바뀌었습니다. 현재 목록에서 다시 선택해 주세요.")
    else:
        row = current or {}
        if row.get("revision") != proposal.get("expected_revision"):
            raise ValueError("이후 대화로 만남이 달라졌습니다. 현재 내용으로 다시 제안해 주세요.")
    if row.get("universe_id") != str(universe_id) or row.get("protagonist_id") != str(protagonist_id):
        raise ValueError("다른 선수의 만남에는 기록할 수 없습니다.")
    if str(row.get("last_date") or "") > game_date:
        raise ValueError("현재 날짜보다 뒤의 만남에는 기록할 수 없습니다.")
    if proposal.get("beat") not in BEATS or row.get("action_id") not in pack()["actions"]:
        raise ValueError("지원하지 않는 대화 단계입니다.")
    personal_context.validate_references(
        state,
        proposal.get("context_refs"),
        universe_id=str(universe_id),
        protagonist_id=protagonist_id,
        game_date=game_date,
        audience=str(proposal.get("visibility") or row.get("visibility") or "private"),
    )
    interpretation = proposal.get("intent_analysis")
    if interpretation is not None:
        if (not isinstance(interpretation, dict) or not isinstance(interpretation.get("text"), str)
                or len(interpretation["text"]) > 1200 or interpretation != interaction_intent.analyze(interpretation["text"])):
            raise ValueError("대화 해석이 달라졌습니다. 현재 내용으로 다시 제안해 주세요.")
        if proposal["beat"] != "start" and (interpretation["status"] != "resolved" or interpretation["beat"] != proposal["beat"] or interpretation["selected_text"] != proposal.get("line")):
            raise ValueError("제안한 행동과 대화 해석이 일치하지 않습니다.")
        if proposal["beat"] == "share" and interpretation["keep_private"]:
            raise ValueError("비공개 요청이 있는 대화를 공개할 수 없습니다.")
    interaction_voice.validate(state, row, proposal)
    return copy.deepcopy(row)


def _choose(rows: list, seed: str):
    return rows[int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16) % len(rows)]


def _memory(row: dict) -> str:
    promise = row.get("promise")
    if promise:
        labels = {"pending": "아직 남아 있는 약속", "reminded": "다시 떠오른 약속", "cancelled": "바꾸기로 한 약속", "fulfilled": "지킨 약속"}
        return f"{promise['created_on']} · {labels[promise['status']]}: {promise['text']}"
    return f"{row['last_date']} · {BEATS.get(row.get('last_beat'), '첫 만남')} · {row['topic']}"


def _context_block(reference: dict) -> dict:
    kind = reference.get("kind")
    label = str(reference.get("label") or "기억")
    detail = str(reference.get("detail") or "")
    if kind == "background":
        text = f"{label}. {detail} 그 오래된 배경은 오늘의 말과 침묵에도 자연스럽게 이어져 있었다."
    elif kind == "hobby":
        text = f"평소 이어 온 {label} 이야기가 잠깐 대화에 스며들었다. {detail}"
    elif kind == "preference":
        text = f"{label}에 관한 취향이 두 사람 사이의 작은 화제가 되었다. {detail}"
    else:
        text = f"이 세계선에 남은 {label}의 기억이 오늘 장면과 맞닿았다. {detail}"
    return nc.block(
        "paragraph",
        text,
        origin="world_context_projection",
        evidence_class=reference.get("evidence_class") or "fictional_intervention",
        memory_event_ids=[reference["context_id"]],
        context_revision=reference.get("revision"),
    )


def _blocks(row: dict, proposal: dict, *, snapshot: dict, spotlight: dict | None = None, statement: str | None = None) -> list[dict]:
    beat = proposal["beat"]
    action = pack()["actions"][row["action_id"]]
    seed = f"{proposal['proposal_id']}:{snapshot.get('content_hash')}"
    player = snapshot["player"].get("name") or "선수"
    context = {"slots": {"player": player, "partner": row["participant_label"], "place": row["place"], "topic": row["topic"]}}
    if beat == "share":
        # Publish the authorized, authored abstraction, never the private
        # transcript, participant name, location, topic, or promise.
        blocks = [_authored_block("paragraph", ts.render_text(action["public_scene"], {"slots": {"player": player}}), f"actions/{row['action_id']}/public_scene")]
        if statement:
            blocks.append(nc.block("quote", statement, speaker=player, origin="user_explicit"))
        return blocks
    expression = proposal.get("expression_plan") or {}
    selections = expression.get("selections") or {}
    if selections:
        source = selections["main"]
        paragraphs = interaction_voice.node(pack(), source)
    else:
        candidates = action["openings"] if beat == "start" else pack()["solo_beats"][beat] if not row["participant_key"] else pack()["beats"][beat]
        paragraphs = _choose(candidates, seed)
        branch = f"actions/{row['action_id']}/openings" if beat == "start" else f"{'solo_beats' if not row['participant_key'] else 'beats'}/{beat}"
        source = f"{branch}/{candidates.index(paragraphs)}"
    blocks = [_authored_block("paragraph", ts.render_text(text, context), f"{source}/paragraphs/{index}") for index, text in enumerate(paragraphs["paragraphs"])]
    for group in ("setting", "bridge", "recall"):
        if group not in selections:
            continue
        path = selections[group]
        slots = {**context["slots"], "previous_date": expression.get("previous_date"), "previous_topic": expression.get("previous_topic")}
        block = _authored_block("paragraph", ts.render_text(interaction_voice.node(pack(), path), {"slots": slots}), path,
                                memory_event_ids=list(expression.get("source_event_ids") or []), evidence_class="fictional_intervention")
        if group == "setting":
            blocks.insert(0, block)
        else:
            blocks.append(block)
    for reference in proposal.get("context_refs") or row.get("context_refs") or []:
        blocks.append(_context_block(reference))
    if beat == "start":
        blocks.append(_authored_block("paragraph", pack()["tones"][row.get("tone") or "honest"], f"tones/{row.get('tone') or 'honest'}"))
    if beat == "start" and row["action_id"] == "learning":
        blocks.append(_authored_block("paragraph", ts.render_text(pack()["learning"][row["learning_progress"]], context), f"learning/{row['learning_progress']}"))
    # Instructions such as "make a joke" are not literal player dialogue.
    # Only an explicit statement or a user-quoted line becomes direct speech.
    quoted = re.search(r'["“「]([^"”」\n]{1,500})["”」]', proposal.get("line") or "")
    interpretation = proposal.get("intent_analysis")
    if interpretation and not interpretation["allow_literal_quote"] and proposal["beat"] != "start":
        quoted = None
    spoken = statement or (quoted.group(1) if quoted else None)
    if spoken:
        blocks.append(nc.block("quote", spoken, speaker=player, origin="user_explicit"))
    if row["participant_key"]:
        if selections.get("reply"):
            voice_source = selections["reply"]
            voice = interaction_voice.node(pack(), voice_source)
        else:
            voices = pack()["voices"].get(row["participant_role"], pack()["voices"]["teammate"])
            voice = paragraphs.get("reply") or _choose(voices, seed + ":voice")
            voice_source = f"{source}/reply" if paragraphs.get("reply") else f"voices/{row['participant_role']}/{voices.index(voice)}"
        blocks.append(_authored_block("quote", ts.render_text(voice, context), voice_source, speaker=row["participant_label"]))
    elif paragraphs.get("reply"):
        blocks.append(nc.block("paragraph", "대답을 서두르지 않고, 떠오른 생각을 잠시 그대로 두었다."))
    if row.get("promise") and beat in ("resume", "callback", "fulfill", "change"):
        blocks.append(nc.block("fact_callout", _memory(row), label="이 세계선에서 나눈 말", evidence_class="fictional_intervention", fact_ids=[row["promise"]["event_id"]]))
    if beat == "promise":
        blocks.append(nc.block("fact_callout", proposal["line"], label="남길 약속 · 창작", evidence_class="fictional_intervention", fact_ids=[f"interaction-proposal:{proposal['proposal_id']}"]))
    stature = expression.get("stature_coda", False) if expression else int((spotlight or {}).get("tier_index") or 0) >= 4
    if beat == "start" and stature:
        blocks.append(_authored_block("paragraph", ts.render_text(pack()["stature_coda"], context), "stature_coda"))
    if beat == "start":
        prior = row.get("previous_learning")
        if prior:
            blocks.append(nc.block("fact_callout", f"{prior['game_date']} · {PROGRESS[prior['progress']]} → 오늘 {PROGRESS[row['learning_progress']]}",
                                   label="같은 상대·학습 내용의 이전 기록", evidence_class="user_confirmed", fact_ids=[prior["fact_id"]]))
        if row["activity_evidence_class"] == "user_confirmed":
            detail = PROGRESS[row["learning_progress"]] if row["action_id"] == "learning" else action["label"]
            blocks.append(nc.block("fact_callout", f"{detail} · {row['participant_label']} · {row['topic']}. 행동만 사용자 확인이며 대사·감정·관계 변화는 창작입니다.", label="게임 행동 · 사용자 확인", evidence_class="user_confirmed", fact_ids=[f"interaction-action:{row['interaction_id']}"]))
        else:
            blocks.append(nc.block("fact_callout", "창작 장면입니다. 게임의 친밀도·능력·목적지 개방·습득 기록은 바뀌지 않습니다.", label="장면 출처", evidence_class="fictional_intervention", fact_ids=[f"interaction:{row['interaction_id']}"]))
    return blocks


def realized_event(state: dict, proposal: dict, *, snapshot: dict, game_date: str, universe_id: str, spotlight: dict, statement: str | None = None) -> dict:
    row = validate_plan(state, proposal, universe_id=universe_id, protagonist_id=snapshot["player"]["id"], game_date=game_date)
    blocks = _blocks(row, proposal, snapshot=snapshot, spotlight=spotlight, statement=statement)
    action = pack()["actions"][row["action_id"]]
    shared = proposal["beat"] == "share"
    headline = f"{snapshot['player'].get('name') or '선수'} · {action['public_subject']}" if shared else f"{row['participant_label']} · {row['topic']} — {BEATS[proposal['beat']]}"
    reactions = {"boards": [], "media": [], "foreign": [], "waves": [], "budget": {"threads": 0, "press": 0, "foreign": 0, "waves": 0}}
    if shared:
        import editorial_engine
        import spotlight_engine

        budget = spotlight_engine.story_budget(spotlight, "public")
        reactions = editorial_engine.build(
            {"snapshot": snapshot, "kind": "STORY", "role": "no_appearance", "delta": {}},
            budget={"boards": budget["boards"], "media": budget["media"], "comments": budget["boards"] * 4},
            memory=state.get("community_memory"), universe_id=universe_id,
            story={"situation": f"share_{row['action_id']}", "visibility": "public"},
            story_id=proposal["proposal_id"], spotlight=spotlight,
        )
    signature = {"template_id": blocks[0].get("template_id"), "persona": row["participant_key"] or "narrator", "opening": blocks[0]["text"], "outline": proposal["beat"]}
    return {
        "event_type": proposal["type"], "label": proposal["label"], "headline": headline,
        "visibility": proposal["visibility"], "renderer": "deterministic",
        "blocks": blocks, "text": nc.project_blocks(blocks), "reactions": reactions,
        "audit": realism_gate.audit_public_prose(" ".join(block["text"] for block in blocks if block["type"] != "fact_callout")).as_dict(),
        "signature": signature, "thread_type": proposal["thread_type"],
        "interaction": {"interaction_id": row["interaction_id"], "action_id": row["action_id"],
                        "action_label": action["label"], "participant_role": row["participant_role"],
                        "participant_label": row["participant_label"], "participant_key": row.get("participant_key"),
                        "beat_label": BEATS[proposal["beat"]]},
        "followups": [{"id": "continue", "label": "다시 이어가자", "interaction_id": row["interaction_id"]},
                      {"id": "recall", "label": "지난 약속이 뭐였지?", "interaction_id": row["interaction_id"]}],
        "provenance": {"renderer": "deterministic", "renderer_version": VERSION, "pack_ids": [pack()["pack_id"]],
                       "corpus_manifest": manifest_hash(), "template_ids": [block["template_id"] for block in blocks if block.get("template_id")],
                       "license_classes": ["authored"], "interaction_id": row["interaction_id"],
                       "beat": proposal["beat"], "activity_evidence_class": row["activity_evidence_class"],
                       "expression": copy.deepcopy(proposal.get("expression_plan")),
                       "context_refs": [
                           {"context_id": ref.get("context_id"), "revision": ref.get("revision"), "kind": ref.get("kind")}
                           for ref in proposal.get("context_refs") or []
                       ],
                       "scene_evidence_class": "fictional_intervention", "rule_sources": list(row["rule_sources"])},
    }


def chat_plan(state: dict, payload: dict, *, snapshot: dict, game_date: str, universe_id: str, sequence: int, spotlight: dict | None = None) -> dict | None:
    text = _text(payload.get("user_text"), limit=1200, label="대화", required=True)
    player_id = snapshot["player"]["id"]
    bound_row = check_chat_binding(state, payload, universe_id=universe_id, protagonist_id=player_id, game_date=game_date)
    explicit = str(payload.get("interaction_id") or "")
    if _FACT_QUESTION.search(text):
        return None
    rows = [row for row in owned_rows(state, universe_id=universe_id, protagonist_id=player_id) if row["last_date"] <= game_date]
    row = bound_row
    interpretation = interaction_intent.analyze(payload["user_text"])
    keep_private = interpretation["keep_private"]
    beat = interpretation["beat"]
    recall = interpretation["status"] == "recall"
    if not explicit and payload.get("category") != "starplayer":
        # Do not hijack a generic exchange about a newly named person.
        named_roles = [key for key, label in ROLES.items() if key != "self" and label.split()[0] in text]
        candidates = [r for r in rows if r["status"] != "closed" and (r["last_date"] == game_date or _RESUME.search(text))
                      and (not named_roles or r["participant_role"] in named_roles)]
        if not (beat or recall or keep_private or interpretation["excluded"] or interpretation["reasons"]) or len(candidates) != 1:
            return None
        row = candidates[0]
    proposal = None
    if row is None and payload.get("category") == "starplayer":
        proposal = start_plan(payload, snapshot=snapshot, game_date=game_date, universe_id=universe_id, state=state, spotlight=spotlight)
        row = proposal["initial"]
    if row is None:
        return None
    message = None
    quoted_start = bool(proposal and not interpretation["reasons"] and not interpretation["excluded"]
                        and interaction_intent._QUOTES.fullmatch(interpretation["text"]))
    if interpretation["status"] in ("clarify", "private", "keep") and not quoted_start:
        message = interaction_intent.clarification(interpretation, BEATS)
        proposal = None
    elif recall:
        message = _memory(row)
        proposal = None
    elif proposal is None:
        if beat == "prop":
            message = "소재 관리와 만남의 행동은 따로 다룹니다. 소재에 적용할 한 가지 명령을 분명하게 알려 주세요. 아직 소재나 만남을 바꾸지 않았습니다."
        elif beat is None:
            message = "이 만남에서 농담을 건네거나, 속마음을 말하거나, 약속을 정할 수 있어요. 어떤 쪽으로 이어갈까요?"
        elif row["status"] in ("closed", "paused") and beat not in ("resume", "share"):
            message = "이 만남은 잠시 멈췄거나 마무리됐습니다. ‘다시 이어가자’로 오늘의 새 장면을 열어 주세요."
        elif beat in ("fulfill", "change") and (not row.get("promise") or row["promise"]["status"] in ("cancelled", "fulfilled")):
            message = "아직 이 만남에서 지키거나 바꿀 약속을 확정하지 않았습니다. 먼저 약속할 내용을 알려 주세요."
        else:
            proposal = _proposal(row, beat, game_date, line=interpretation["selected_text"], state=state)
    if proposal:
        proposal["intent_analysis"] = copy.deepcopy(interpretation)
    known_id = row["interaction_id"] if row["interaction_id"] in (state.get(STORE) or {}) or proposal else None
    preview = _blocks(row, proposal, snapshot=snapshot, spotlight=spotlight) if proposal else [nc.block("paragraph", message or "어떤 이야기를 이어갈까요?")]
    if proposal:
        preview.insert(0, nc.block("fact_callout", "아래는 다음 장면의 제안입니다. 확정 전에는 만남·관계·약속·공개 범위가 바뀌지 않습니다.", label="확정 대기", fact_ids=[f"interaction:{row['interaction_id']}"], evidence_class="fictional_intervention"))
    return {
        "response_id": nc.stable_id(universe_id, player_id, game_date, sequence, text, row["interaction_id"]),
        "engine_version": VERSION, "renderer": "deterministic",
        "understanding": {"primary_act": f"interaction_{'recall' if recall else (proposal or {}).get('beat', 'clarify')}",
                          "target": row["participant_key"], "target_source": "bound_interaction", "prop_acts": [],
                          "visibility": row["visibility"], "interaction_id": known_id,
                          "intent_analysis": copy.deepcopy(interpretation)},
        "reply": {"language": "ko", "text": nc.project_blocks(preview), "blocks": preview, "persona": row["participant_label"] if row["participant_key"] else None},
        "proposed_events": [proposal] if proposal else [],
        "choices": [
            {"id": key, "label": label, **({"interaction_id": known_id} if known_id else {})}
            for key, label in (("joke", "농담을 건네고 싶어"), ("confide", "솔직한 속마음을 말할래"),
                               ("promise", "다음 만남을 약속하자"), ("recall", "지난 약속이 뭐였지?"),
                               ("resume", "다시 이어가자"), ("close", "오늘 대화는 여기서 마무리하자"))],
        "fact_ids": list(dict.fromkeys(fact_id for block in preview for fact_id in block.get("fact_ids", []))),
        "thread_updates": [], "audit": {**realism_gate.audit_public_prose(" ".join(block["text"] for block in preview if block["type"] != "fact_callout")).as_dict(), "scope": "interaction_preview"},
        "provenance": {"renderer": "deterministic", "renderer_version": VERSION, "pack_ids": [pack()["pack_id"]], "license_classes": ["authored"], "interaction_id": row["interaction_id"],
                       "expression": copy.deepcopy((proposal or {}).get("expression_plan")),
                       "intent_parser_version": interaction_intent.VERSION,
                       "corpus_manifest": manifest_hash(), "template_ids": [block["template_id"] for block in preview if block.get("template_id")]},
        "conversation_state": {"last_target": row["participant_role"], "interaction_id": known_id, "last_act": beat},
        "creates_event": bool(proposal), "mode": "사건 제안" if proposal else "대화만 함",
    }


def commit(state: dict, proposal: dict, *, snapshot: dict, game_date: str, universe_id: str, event_id: str, trigger_event: dict | None = None) -> dict:
    row = validate_plan(state, proposal, universe_id=universe_id, protagonist_id=snapshot["player"]["id"], game_date=game_date)
    beat, before = proposal["beat"], row["status"]
    if beat == "start" and row["activity_evidence_class"] == "user_confirmed":
        fact = nc.new_fact(kind="starplayer.action", label=pack()["actions"][row["action_id"]]["label"],
                           value={"action_id": row["action_id"], "participant": row["participant_label"], "place": row["place"], "topic": row["topic"], "learning_progress": row["game_progress"]},
                           evidence_class="user_confirmed", game_date=game_date, scope="game",
                           source="user:starplayer-action-form", fact_id=f"interaction-action:{row['interaction_id']}")
        fact.update(universe_id=str(universe_id), protagonist_id=str(snapshot["player"]["id"]), visibility=row["visibility"])
        state.setdefault("fact_registry", {})[fact["fact_id"]] = fact
        row["action_fact_id"] = fact["fact_id"]
    if beat == "promise":
        previous = row.get("promise")
        if previous:
            row.setdefault("promise_history", []).append(copy.deepcopy(previous))
        positive_homer = re.search(r"다음(?:에)?[^.!?]{0,20}홈런(?:을)?\s*(?:때|치면|쳤을|치게|나오면)", proposal["line"])
        trigger = "SP.GAME.BAT.HOME_RUN" if positive_homer else None
        row["promise"] = {"text": proposal["line"], "created_on": game_date, "status": "pending",
                          "event_id": event_id, "trigger_type": trigger, "after_snapshot_hash": snapshot.get("content_hash"),
                          "fired_by": None}
    elif beat == "change":
        row["promise"].update(status="cancelled", changed_on=game_date, change=proposal["line"], changed_by=event_id)
    elif beat == "fulfill":
        row["promise"].update(status="fulfilled", fulfilled_on=game_date, fulfilled_by=event_id)
    elif beat == "callback":
        if not trigger_event or not row.get("promise"):
            raise ValueError("약속 재등장에는 검증된 계기와 기존 약속이 필요합니다.")
        row["promise"].update(status="reminded", fired_by=trigger_event["event_id"], reminded_on=game_date)
    if beat in ("close", "pause", "resume"):
        row["status"] = {"close": "closed", "pause": "paused", "resume": "open"}[beat]
    # A public statement does not widen the private scene's future audience.
    row.update(revision=row["revision"] + 1, last_date=game_date, last_beat=beat, last_line=proposal.get("line") or "",
               last_event_id=event_id, last_order=len(state.get(EVENTS) or []) + 1)
    state.setdefault(STORE, {})[row["interaction_id"]] = row
    event = {
        "interaction_id": row["interaction_id"], "event_id": event_id, "game_date": game_date,
        "universe_id": row["universe_id"], "protagonist_id": row["protagonist_id"],
        "revision": row["revision"], "beat": beat, "from_status": before, "to_status": row["status"],
        "line": proposal.get("line") or "", "visibility": proposal["visibility"],
        "evidence_class": "fictional_intervention", "activity_fact_id": row.get("action_fact_id"),
        "trigger_event_id": (trigger_event or {}).get("event_id"), "promise": copy.deepcopy(row.get("promise")),
        "participant_key": row.get("participant_key"),
        "context_refs": [
            {"context_id": ref.get("context_id"), "revision": ref.get("revision"), "kind": ref.get("kind")}
            for ref in proposal.get("context_refs") or []
        ],
    }
    interaction_voice.record(row, proposal, event)
    if proposal.get("intent_analysis") is not None:
        event["intent_analysis"] = copy.deepcopy(proposal["intent_analysis"])
    state.setdefault(EVENTS, []).append(event)
    return row


def relationship_deltas(beat: str) -> dict:
    return copy.deepcopy({
        "start": {"familiarity": 0.03}, "agree": {"warmth": 0.03}, "joke": {"warmth": 0.04},
        "confide": {"trust": 0.06}, "disagree": {"tension": 0.06}, "reconcile": {"tension": -0.06},
        "promise": {"unresolved_debt": 0.06}, "fulfill": {"trust": 0.06, "unresolved_debt": -0.06},
    }.get(beat, {}))


def due_callbacks(state: dict, event: dict, *, universe_id: str, protagonist_id: object, game_date: str) -> list[dict]:
    source_hash = (event.get("payload") or {}).get("source_hash")
    if (not source_hash or event.get("universe_id") != str(universe_id) or event.get("protagonist_id") != str(protagonist_id)
            or event.get("game_date") != game_date):
        return []
    plans = []
    for row in owned_rows(state, universe_id=universe_id, protagonist_id=protagonist_id):
        promise = row.get("promise") or {}
        if (row["status"] != "open" or row["last_date"] > game_date or promise.get("status") != "pending" or promise.get("fired_by")
                or not promise.get("trigger_type") or event.get("event_type") != promise["trigger_type"]
                or source_hash == promise.get("after_snapshot_hash") or str(promise.get("created_on") or "") > game_date
                or event.get("evidence_class") not in ("save_verified", "derived_analysis")):
            continue
        plans.append(_proposal(row, "callback", game_date))
    return plans
