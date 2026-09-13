#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed template packs, FTS5 retrieval, deterministic scoring, diversity.

Master plan 8.4 (planner weights), 8.5 (typed slots, locale renderers),
8.6 (retrieval and diversity), 8.10 (anti-template-collapse memory), 13.3
(provenance per emitted line). Packs are JSON files under
``data/packs/<pack>/*.json`` bundled with the application; every template is
authored (license_class ``authored``) or explicitly rights-cleared.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import threading
from pathlib import Path

import personas
import realism_gate

STORE_VERSION = "1.0.0"
PACK_ROOT = Path(__file__).resolve().parent / "data" / "packs"
ALLOWED_LICENSE_CLASSES = ("authored", "public_domain", "cc_by", "permission")

# Planner weights (8.4). They sum to 1.0 before penalties.
WEIGHTS = {
    "fact": 0.24,
    "act": 0.18,
    "persona": 0.14,
    "relationship": 0.12,
    "thread": 0.10,
    "emotion": 0.08,
    "locale": 0.06,
    "novelty": 0.04,
    "preference": 0.04,
}


class TemplateError(ValueError):
    pass


# --------------------------------------------------------------------------
# Korean particles (8.5 locale renderer)
# --------------------------------------------------------------------------

_PARTICLES = {
    "은는": ("은", "는"),
    "이가": ("이", "가"),
    "을를": ("을", "를"),
    "과와": ("과", "와"),
    "아야": ("아", "야"),
    "으로로": ("으로", "로"),
    "이라라": ("이라", "라"),
    "이나나": ("이나", "나"),
}


def _final_consonant(word: str) -> int | None:
    """Return the jongseong index of the last syllable (0 = none), or None."""
    text = str(word or "").strip()
    if not text:
        return None
    last = text[-1]
    if "가" <= last <= "힣":
        return (ord(last) - 0xAC00) % 28
    if last.isdigit():
        # Korean readings: 0 영, 1 일, 2 이, 3 삼, 4 사, 5 오, 6 육, 7 칠, 8 팔, 9 구.
        return {"0": 21, "1": 8, "2": 0, "3": 16, "4": 0, "5": 0, "6": 1, "7": 8, "8": 8, "9": 0}[last]
    if last.isascii() and last.isalpha():
        lower = last.lower()
        if lower in "lmn":
            return 8 if lower == "l" else 1
        return 0
    return None


def particle(word: str, pair: str) -> str:
    consonant, vowel = _PARTICLES[pair]
    jong = _final_consonant(word)
    if jong is None:
        return f"{consonant}({vowel})" if pair != "으로로" else "(으)로"
    if pair == "으로로" and jong == 8:
        return "로"
    return consonant if jong else vowel


def attach(word: str, pair: str) -> str:
    return f"{word}{particle(word, pair)}"


# --------------------------------------------------------------------------
# Condition evaluator ("requires")
# --------------------------------------------------------------------------

_CONDITION = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*(==|!=|>=|<=|>|<|in|not_in|has|missing)\s*(.*?)\s*$")


def _lookup(context: dict, path: str):
    value = context
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
        if value is None:
            return None
    return value


def _coerce(text: str):
    text = text.strip()
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return text.strip("'\"")


def evaluate_condition(condition: str, context: dict) -> bool:
    match = _CONDITION.match(str(condition or ""))
    if not match:
        raise TemplateError(f"unparseable condition: {condition!r}")
    path, operator, raw = match.groups()
    actual = _lookup(context, path)
    if operator == "has":
        return actual is not None and actual != "" and actual != [] and actual != 0
    if operator == "missing":
        return actual is None or actual == "" or actual == []
    if operator in ("in", "not_in"):
        options = [item.strip() for item in raw.split(",") if item.strip()]
        found = str(actual) in options if actual is not None else False
        return found if operator == "in" else not found
    expected = _coerce(raw)
    if actual is None:
        return operator == "!="
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            actual_number = float(actual)
        except (TypeError, ValueError):
            return False
        return {
            "==": actual_number == expected,
            "!=": actual_number != expected,
            ">=": actual_number >= expected,
            "<=": actual_number <= expected,
            ">": actual_number > expected,
            "<": actual_number < expected,
        }[operator]
    if operator == "==":
        return str(actual) == str(expected)
    if operator == "!=":
        return str(actual) != str(expected)
    return False


# --------------------------------------------------------------------------
# Slot rendering
# --------------------------------------------------------------------------

_SLOT = re.compile(r"\{([A-Za-z_][\w.]*)(?::([^}]+))?\}")


class MissingSlot(TemplateError):
    pass


def _format_value(value) -> str:
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, float):
        if 0 < value < 1:
            return f"{value:.3f}".lstrip("0")
        return f"{value:.1f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return str(value)
    return str(value)


def render_text(text: str, context: dict) -> str:
    """Fill typed slots; raise MissingSlot when a required value is absent."""

    def replace(match: re.Match) -> str:
        path, modifier = match.group(1), match.group(2)
        value = _lookup(context.get("slots") or {}, path)
        if value is None:
            value = _lookup(context.get("facts") or {}, path)
        if value is None or value == "":
            raise MissingSlot(path)
        rendered = _format_value(value)
        if modifier:
            if modifier in _PARTICLES:
                return attach(rendered, modifier)
            if modifier.startswith("suffix="):
                return rendered + modifier[len("suffix=") :]
            raise TemplateError(f"unknown slot modifier {modifier!r}")
        return rendered

    return _SLOT.sub(replace, text)


def slot_names(text: str) -> list[str]:
    return [match.group(1) for match in _SLOT.finditer(text)]


# --------------------------------------------------------------------------
# Pack loading and validation
# --------------------------------------------------------------------------

REQUIRED_TEMPLATE_KEYS = ("id", "responds_to", "persona", "variants")


def validate_template(row: dict, pack_id: str) -> list[str]:
    problems = []
    for key in REQUIRED_TEMPLATE_KEYS:
        if key not in row:
            problems.append(f"{pack_id}:{row.get('id', '?')}: missing {key}")
    if row.get("persona") not in personas.PERSONAS and row.get("persona") != "narrator":
        problems.append(f"{pack_id}:{row.get('id')}: unknown persona {row.get('persona')!r}")
    variants = row.get("variants") or []
    if not variants:
        problems.append(f"{pack_id}:{row.get('id')}: no variants")
    for index, variant in enumerate(variants):
        if not isinstance(variant, dict) or not str(variant.get("text") or "").strip():
            problems.append(f"{pack_id}:{row.get('id')}: variant {index} has no text")
            continue
        for condition in row.get("requires") or []:
            try:
                _CONDITION.match(condition).groups()  # type: ignore[union-attr]
            except AttributeError:
                problems.append(f"{pack_id}:{row.get('id')}: bad condition {condition!r}")
        if variant.get("translation_ko") is None and str(row.get("locale") or "ko") != "ko":
            problems.append(f"{pack_id}:{row.get('id')}: foreign variant {index} lacks translation_ko")
    return problems


class TemplateStore:
    def __init__(self, pack_root: str | Path | None = None):
        self.pack_root = Path(pack_root) if pack_root else PACK_ROOT
        self.packs: dict[str, dict] = {}
        self.templates: dict[str, dict] = {}
        self.problems: list[str] = []
        self.manifest: dict[str, str] = {}
        self._lock = threading.RLock()
        self._db: sqlite3.Connection | None = None
        self.load()

    # ---------------------------------------------------------------- load
    def load(self) -> None:
        with self._lock:
            self.packs.clear()
            self.templates.clear()
            self.problems.clear()
            self.manifest.clear()
            if self.pack_root.is_dir():
                for pack_dir in sorted(path for path in self.pack_root.iterdir() if path.is_dir()):
                    for file in sorted(pack_dir.glob("*.json")):
                        self._load_file(file)
            self._build_index()

    def _load_file(self, path: Path) -> None:
        try:
            raw = path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as exc:
            self.problems.append(f"{path.name}: unreadable ({exc})")
            return
        if not isinstance(payload, dict):
            self.problems.append(f"{path.name}: pack must be an object")
            return
        pack_id = str(payload.get("pack_id") or path.parent.name)
        license_class = str(payload.get("license_class") or "")
        if license_class not in ALLOWED_LICENSE_CLASSES:
            self.problems.append(f"{pack_id}: license_class {license_class!r} is not allowed at runtime")
            return
        self.manifest[f"{pack_id}/{path.name}"] = hashlib.sha256(raw).hexdigest()
        pack = self.packs.setdefault(
            pack_id,
            {
                "pack_id": pack_id,
                "version": str(payload.get("version") or "0"),
                "locale": str(payload.get("locale") or "ko"),
                "license_class": license_class,
                "attribution": str(payload.get("attribution") or ""),
                "files": [],
                "template_count": 0,
            },
        )
        pack["files"].append(path.name)
        locale = str(payload.get("locale") or pack["locale"]).split("-")[0]
        for row in payload.get("templates") or []:
            if not isinstance(row, dict):
                continue
            row = dict(row)
            row.setdefault("locale", locale)
            row["pack_id"] = pack_id
            row["license_class"] = license_class
            row.setdefault("target", ["*"])
            row.setdefault("visibility", ["*"])
            row.setdefault("requires", [])
            row.setdefault("facts", [])
            row.setdefault("effects", {})
            row.setdefault("signature", {})
            row.setdefault("emotion_range", [-1.0, 1.0])
            row.setdefault("domain", None)
            row.setdefault("kind", "response")
            problems = validate_template(row, pack_id)
            if problems:
                self.problems.extend(problems)
                continue
            if row["id"] in self.templates:
                self.problems.append(f"{pack_id}: duplicate template id {row['id']}")
                continue
            self.templates[row["id"]] = row
            pack["template_count"] += 1

    def _build_index(self) -> None:
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.execute(
            "CREATE VIRTUAL TABLE templates USING fts5(id UNINDEXED, pack UNINDEXED, locale UNINDEXED, kind UNINDEXED, acts, targets, visibility, persona, domain, tags, text)"
        )
        rows = []
        for row in self.templates.values():
            text = " ".join(str(variant.get("text") or "") for variant in row["variants"])
            rows.append(
                (
                    row["id"],
                    row["pack_id"],
                    row["locale"],
                    row["kind"],
                    " ".join(row.get("responds_to") or []),
                    " ".join(row.get("target") or []),
                    " ".join(row.get("visibility") or []),
                    str(row.get("persona") or ""),
                    str(row.get("domain") or ""),
                    " ".join(row.get("tags") or []),
                    text,
                )
            )
        db.executemany("INSERT INTO templates VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
        self._db = db

    # ------------------------------------------------------------- reports
    def report(self) -> dict:
        counts: dict[str, int] = {}
        for row in self.templates.values():
            key = f"{row['locale']}:{row['kind']}"
            counts[key] = counts.get(key, 0) + 1
        return {
            "store_version": STORE_VERSION,
            "packs": {pack_id: dict(pack) for pack_id, pack in self.packs.items()},
            "template_count": len(self.templates),
            "by_locale_kind": counts,
            "problems": list(self.problems),
            "manifest": dict(self.manifest),
        }

    def manifest_hash(self) -> str:
        payload = json.dumps(self.manifest, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    # ----------------------------------------------------------- retrieval
    @staticmethod
    def _matches(values: list[str], wanted: str | None) -> bool:
        if not values or "*" in values or wanted == "*":
            return True
        return wanted is not None and str(wanted) in [str(value) for value in values]

    def candidates(
        self,
        *,
        responds_to: str | None = None,
        target: str | None = None,
        visibility: str | None = None,
        persona: str | None = None,
        personas_allowed=None,
        domain: str | None = None,
        locale: str = "ko",
        kind: str = "response",
        context: dict | None = None,
        query: str | None = None,
        limit: int = 60,
    ) -> list[dict]:
        """Structured filter first, then FTS for keyword affinity."""
        context = context or {}
        allowed = set(personas_allowed or [])
        rows = []
        for row in self.templates.values():
            if row["kind"] != kind or row["locale"] != locale:
                continue
            if responds_to and responds_to not in (row.get("responds_to") or []) and "*" not in (row.get("responds_to") or []):
                continue
            if not self._matches(row.get("target") or [], target):
                continue
            if not self._matches(row.get("visibility") or [], visibility):
                continue
            if persona and row.get("persona") != persona:
                continue
            if allowed and row.get("persona") not in allowed and row.get("persona") != "narrator":
                continue
            if domain and row.get("domain") and not str(domain).startswith(str(row["domain"])):
                continue
            try:
                if not all(evaluate_condition(condition, context) for condition in row.get("requires") or []):
                    continue
            except TemplateError:
                continue
            # Every fact slot must be renderable; otherwise the template is
            # ineligible rather than filled with an invented value.
            renderable = []
            for variant in row["variants"]:
                try:
                    render_text(str(variant.get("text") or ""), context)
                    renderable.append(variant)
                except MissingSlot:
                    continue
                except TemplateError:
                    continue
            if not renderable:
                continue
            rows.append(dict(row, _variants=renderable))
        if query and rows and self._db is not None:
            hits = self.fts_ids(query, limit=200)
            order = {template_id: index for index, template_id in enumerate(hits)}
            rows.sort(key=lambda row: order.get(row["id"], len(order)))
        return rows[:limit]

    def fts_ids(self, query: str, *, limit: int = 50, locale: str | None = None, kind: str | None = None) -> list[str]:
        if self._db is None:
            return []
        terms = [token for token in re.findall(r"[가-힣]+|[A-Za-z]+|[ぁ-んァ-ン一-鿿]+", str(query or "")) if len(token) >= 2]
        if not terms:
            return []
        match = " OR ".join(f'"{term}"' for term in terms[:12])
        sql = "SELECT id FROM templates WHERE templates MATCH ?"
        params: list = [match]
        if locale:
            sql += " AND locale = ?"
            params.append(locale)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY rank LIMIT ?"
        params.append(int(limit))
        try:
            return [row[0] for row in self._db.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return []

    # ------------------------------------------------------------- scoring
    @staticmethod
    def _answers(row: dict, wanted: set) -> bool:
        """True when the template declares that it answers one asked fact."""
        for pattern in row.get("answers") or []:
            pattern = str(pattern)
            if pattern.endswith("*"):
                prefix = pattern[:-1]
                if any(str(item).startswith(prefix) for item in wanted):
                    return True
            elif pattern in wanted:
                return True
        return False

    @staticmethod
    def _fact_fit(row: dict, context: dict) -> float:
        needed = list(row.get("facts") or [])
        wanted = set(context.get("fact_requirements") or [])
        facts = context.get("facts") or {}
        available = sum(1 for path in needed if _lookup(facts, path) is not None)
        availability = (available / len(needed)) if needed else 1.0
        if not wanted:
            if not needed:
                return 0.7
            return 0.8 * availability
        if TemplateStore._answers(row, wanted):
            return availability
        if not needed:
            return 0.3
        if wanted & set(needed):
            return availability
        # Fact-bearing template that answers a different question than asked.
        return 0.2

    @staticmethod
    def _act_fit(row: dict, context: dict) -> float:
        acts = row.get("responds_to") or []
        primary = context.get("primary_act")
        secondary = set(context.get("secondary_acts") or [])
        if primary in acts:
            return 1.0
        if secondary & set(acts):
            return 0.6
        if "*" in acts:
            return 0.3
        return 0.0

    @staticmethod
    def _persona_fit(row: dict, context: dict) -> float:
        persona = row.get("persona")
        preferred = list(context.get("preferred_personas") or [])
        if persona == "narrator":
            return 0.6
        if not preferred:
            return 0.5
        if persona == preferred[0]:
            return 1.0
        if persona in preferred:
            return 0.8
        return 0.2

    @staticmethod
    def _relationship_fit(row: dict, context: dict) -> float:
        relation = context.get("relation") or {}
        needs = row.get("relationship") or {}
        if not needs:
            return 0.6
        score = 0.0
        for key, minimum in needs.items():
            try:
                if float(relation.get(key) or 0.0) >= float(minimum):
                    score += 1.0
            except (TypeError, ValueError):
                continue
        return score / max(1, len(needs))

    @staticmethod
    def _thread_fit(row: dict, context: dict) -> float:
        threads = [str(row_type) for row_type in context.get("active_thread_types") or []]
        wanted = row.get("thread_type")
        if not wanted:
            return 0.5
        return 1.0 if wanted in threads else 0.2

    @staticmethod
    def _emotion_fit(row: dict, context: dict) -> float:
        low, high = row.get("emotion_range") or [-1.0, 1.0]
        valence = float((context.get("emotion") or {}).get("valence") or 0.0)
        if low <= valence <= high:
            return 1.0
        distance = min(abs(valence - low), abs(valence - high))
        return max(0.0, 1.0 - distance)

    @staticmethod
    def _locale_fit(row: dict, context: dict) -> float:
        return 1.0 if row.get("locale") == str(context.get("locale") or "ko").split("-")[0] else 0.3

    @staticmethod
    def _novelty(row: dict, context: dict) -> float:
        recent = context.get("recent_template_ids") or []
        if row["id"] not in recent:
            return 1.0
        position = list(reversed(recent)).index(row["id"])
        return min(1.0, position / 20.0)

    @staticmethod
    def _preference(row: dict, context: dict) -> float:
        tone = context.get("tone")
        tones = row.get("tones") or []
        if not tone or not tones:
            return 0.5
        return 1.0 if tone in tones else 0.2

    def score(self, row: dict, context: dict) -> dict:
        parts = {
            "fact": self._fact_fit(row, context),
            "act": self._act_fit(row, context),
            "persona": self._persona_fit(row, context),
            "relationship": self._relationship_fit(row, context),
            "thread": self._thread_fit(row, context),
            "emotion": self._emotion_fit(row, context),
            "locale": self._locale_fit(row, context),
            "novelty": self._novelty(row, context),
            "preference": self._preference(row, context),
        }
        total = sum(WEIGHTS[key] * value for key, value in parts.items())
        penalties = {}
        signature = row.get("signature") or {}
        recent_signatures = context.get("recent_signatures") or []
        if signature and any(
            sig.get("opening") == signature.get("opening") and sig.get("persona") == row.get("persona")
            for sig in recent_signatures[-12:]
        ):
            penalties["repetition"] = 0.15
        if row.get("license_class") != "authored":
            penalties["provenance_risk"] = 0.05
        total -= sum(penalties.values())
        return {"total": round(total, 4), "parts": parts, "penalties": penalties}

    # ------------------------------------------------------------ planning
    def choose(self, candidates: list[dict], context: dict, *, seed: str, top_k: int = 3) -> dict | None:
        """Deterministic choice: best score, seeded tie-break inside the top band."""
        if not candidates:
            return None
        scored = []
        for row in candidates:
            score = self.score(row, context)
            scored.append((score["total"], row["id"], row, score))
        scored.sort(key=lambda item: (-item[0], item[1]))
        best_total = scored[0][0]
        band = [item for item in scored if best_total - item[0] <= 0.02][:top_k]
        rng = random.Random(int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16))
        total, template_id, row, score = band[rng.randrange(len(band))] if len(band) > 1 else band[0]
        variants = row.get("_variants") or row["variants"]
        weights = [max(1, int(variant.get("weight") or 1)) for variant in variants]
        variant = rng.choices(variants, weights=weights, k=1)[0]
        return {"template": row, "variant": variant, "score": score}

    def diversity_filter(self, candidates: list[dict], recent_signatures: list[dict], *, persona: str | None = None) -> list[dict]:
        """Drop candidates whose opening/closing/metaphor recently appeared for the same persona."""
        if not recent_signatures:
            return candidates
        blocked_openings = {
            (sig.get("persona"), sig.get("opening")) for sig in recent_signatures[-20:] if sig.get("opening")
        }
        blocked_heads = {sig.get("head") for sig in recent_signatures[-20:] if sig.get("head")}
        blocked_metaphors = {sig.get("metaphor") for sig in recent_signatures[-20:] if sig.get("metaphor")}
        kept = []
        for row in candidates:
            signature = row.get("signature") or {}
            if (row.get("persona"), signature.get("opening")) in blocked_openings and signature.get("opening"):
                continue
            if signature.get("metaphor") and signature.get("metaphor") in blocked_metaphors:
                continue
            head = realism_gate.normalize_text(str((row.get("_variants") or row["variants"])[0].get("text") or ""))[:8]
            if head in blocked_heads:
                continue
            kept.append(row)
        return kept or candidates

    # --------------------------------------------------------- realisation
    def realize(self, chosen: dict, context: dict) -> dict:
        row = chosen["template"]
        variant = chosen["variant"]
        text = render_text(str(variant.get("text") or ""), context)
        translation = variant.get("translation_ko")
        if translation:
            translation = render_text(str(translation), context)
        note = variant.get("note_ko")
        signature = dict(row.get("signature") or {})
        signature.update(
            {
                "template_id": row["id"],
                "persona": row.get("persona"),
                "head": realism_gate.normalize_text(text)[:8],
                "tail": realism_gate.normalize_text(text)[-8:],
                "skeleton": realism_gate.headline_skeleton(text, context.get("names") or []),
            }
        )
        return {
            "text": text,
            "translation_ko": translation,
            "note_ko": note,
            "persona": row.get("persona"),
            "locale": row.get("locale"),
            "template_id": row["id"],
            "pack_id": row["pack_id"],
            "license_class": row["license_class"],
            "effects": dict(row.get("effects") or {}),
            "signature": signature,
            "score": chosen.get("score"),
        }


_default_store: TemplateStore | None = None
_default_lock = threading.Lock()


def default_store() -> TemplateStore:
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = TemplateStore()
        return _default_store
