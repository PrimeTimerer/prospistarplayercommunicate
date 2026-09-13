#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-LLM understanding pipeline (master plan 8.2, 8.3, 8.7, 8.11).

Rules, weighted lexical features, and conversation state only. The engine
returns an inspectable ``understanding`` dict: language, tokens, dialogue
acts with evidence, resolved entities, visibility hint, emotion, urgency,
risk tags, fact requirements, per-clause analysis for utterances that carry
several acts, and the fallback-ladder level. It never invents an event.
"""

from __future__ import annotations

import re
import unicodedata

ENGINE_VERSION = "1.0.0"
MAX_INPUT = 1200

# --------------------------------------------------------------------------
# Normalisation and language identification
# --------------------------------------------------------------------------

_ZERO_WIDTH = re.compile(r"[​‌‍﻿]")


def normalize(text: object) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = _ZERO_WIDTH.sub("", value)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r"\s*\n\s*", "\n", value).strip()
    return value[:MAX_INPUT]


_SCRIPTS = {
    "hangul": re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]"),
    "kana": re.compile(r"[ぁ-んァ-ンー]"),
    "han": re.compile(r"[一-鿿]"),
    "latin": re.compile(r"[A-Za-zÀ-ÿ]"),
}
_ES_HINTS = re.compile(r"\b(?:el|la|los|las|de|que|y|es|un|una|con|para|por|no|muy|hoy|jugador|equipo|partido|lanzador|bateador|gracias|hola|quiero|puede)\b", re.I)
_EN_HINTS = re.compile(r"\b(?:the|and|is|are|to|of|in|that|with|for|it|today|manager|team|game|pitcher|batter|want|please|thanks|hello|record)\b", re.I)
_ZH_TRAD = re.compile(r"[這們個為說時間會來對於這樣經體讓還進與應該們]")


def detect_language(text: object) -> str:
    value = normalize(text)
    if not value:
        return "unknown"
    counts = {name: len(pattern.findall(value)) for name, pattern in _SCRIPTS.items()}
    total = sum(counts.values()) or 1
    if counts["hangul"] / total >= 0.3:
        return "ko"
    if counts["kana"] > 0 and counts["kana"] / total >= 0.15:
        return "ja"
    if counts["han"] / total >= 0.4:
        return "zh-Hant" if _ZH_TRAD.search(value) or counts["kana"] == 0 else "ja"
    if counts["latin"] / total >= 0.5:
        es = len(_ES_HINTS.findall(value))
        en = len(_EN_HINTS.findall(value))
        if es > en:
            return "es"
        if en or es == 0:
            return "en"
        return "es"
    if counts["hangul"]:
        return "ko"
    return "unknown"


# --------------------------------------------------------------------------
# Tokenisation (light Korean particle stripping)
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"[가-힣]+|[A-Za-zÀ-ÿ]+|[ぁ-んァ-ン一-鿿ー]+|\d+(?:[.,]\d+)?|[?!.…]")
_JOSA = (
    "에게서", "한테서", "으로는", "으로도", "에서는", "에서도", "처럼", "까지", "부터", "에게", "한테", "께서",
    "에서", "으로", "이라고", "라고", "이라도", "라도", "이든", "든", "은", "는", "이", "가", "을", "를",
    "과", "와", "도", "만", "의", "에", "로", "께", "요",
)


def strip_particle(token: str) -> str:
    if not re.fullmatch(r"[가-힣]+", token) or len(token) < 2:
        return token
    for josa in _JOSA:
        if token.endswith(josa) and len(token) > len(josa):
            return token[: -len(josa)]
    return token


def tokenize(text: object) -> list[str]:
    return _TOKEN.findall(normalize(text))


def stems(text: object) -> list[str]:
    return [strip_particle(token) for token in tokenize(text)]


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------

ENTITY_LEXICON: dict[str, dict] = {
    "manager": {"label": "감독", "forms": ["감독", "감독님", "監督", "manager", "skipper", "mánager", "entrenador", "教練"]},
    "coach": {"label": "코치", "forms": ["코치", "코치님", "투수코치", "타격코치", "コーチ", "coach", "코칭스태프"]},
    "teammate": {"label": "동료", "forms": ["동료", "팀원", "선배", "형", "팀 동료", "チームメイト", "teammate", "compañero", "포수", "捕手"]},
    "rookie": {"label": "후배", "forms": ["후배", "신인", "루키", "新人", "rookie", "novato", "젊은 선수"]},
    "reporter": {"label": "기자단", "forms": ["기자", "기자들", "기자단", "언론", "취재진", "인터뷰", "기자회견", "미디어", "記者", "press", "media", "reporter", "prensa", "periodista"]},
    "fans": {"label": "팬들", "forms": ["팬", "팬들", "관중", "응원", "ファン", "fans", "aficionados", "球迷"]},
    "rival": {"label": "라이벌", "forms": ["라이벌", "상대", "상대팀", "상대 투수", "상대 타자", "ライバル", "rival", "opponent"]},
    "agent": {"label": "에이전트", "forms": ["에이전트", "대리인", "エージェント", "agent", "agente"]},
    "family": {"label": "가족", "forms": ["가족", "부모님", "어머니", "아버지", "엄마", "아빠", "동생", "家族", "family", "familia"]},
    "partner": {"label": "연인", "forms": ["여자친구", "남자친구", "연인", "애인", "파트너", "彼女", "partner", "novia", "novio"]},
    "front_office": {"label": "구단", "forms": ["구단", "프런트", "단장", "球団", "front office", "club"]},
    "medical": {"label": "의료진", "forms": ["트레이너", "의료진", "팀닥터", "의사", "トレーナー", "trainer", "doctor"]},
    "self": {"label": "자기 자신", "forms": ["나 자신", "스스로", "혼자", "내 자신", "自分", "myself"]},
}

_ENTITY_PATTERNS = {
    entity_id: re.compile(
        "|".join(re.escape(form) for form in sorted(row["forms"], key=len, reverse=True)),
        re.I,
    )
    for entity_id, row in ENTITY_LEXICON.items()
}

_VISIBILITY_CUES = [
    ("private", re.compile(r"혼자|속으로|비공개|몰래|아무한테도|아무에게도|조용히|개인적으로|따로|둘이서|1대1|일대일|사적으로|独り|privately|en privado")),
    ("clubhouse", re.compile(r"팀\s*안|라커룸|클럽하우스|동료들\s*앞|팀\s*내부|더그아웃|팀\s*안에서만|チーム内|clubhouse|vestuario")),
    ("national", re.compile(r"기자들?\s*앞|인터뷰|기자회견|공개적으로|공개\s*석상|방송|카메라\s*앞|언론에|記者会見|publicly|press conference|en público")),
    ("international", re.compile(r"sns|소셜|트위터|인스타|해외\s*팬|전\s*세계|SNS|ソーシャル", re.I)),
]

_RISK_CUES = [
    ("injury", re.compile(r"부상|아프|통증|다쳤|다친|재활|수술|怪我|injur|lesi[oó]n")),
    ("rumor", re.compile(r"소문|루머|찌라시|噂|rumor")),
    ("romance", re.compile(r"여자친구|남자친구|연인|데이트|고백|사귀|彼女|novia|novio|romance")),
    ("family", re.compile(r"가족|부모|어머니|아버지|家族|family|familia")),
    ("contract", re.compile(r"계약|연봉|협상|FA|이적|트레이드|契約|contract|salary|contrato")),
    ("conflict", re.compile(r"싸움|갈등|충돌|화났|화가\s*나|대립|따지|맞서|喧嘩|conflict|pelea")),
    ("mental_health", re.compile(r"우울|불안|공황|무섭|두렵|잠이\s*안|번아웃|鬱|anxious|depress")),
    ("privacy", re.compile(r"사생활|프라이버시|집\s*주소|사진\s*찍|プライバシー|privacy")),
]

_URGENCY = re.compile(r"지금\s*당장|당장|오늘\s*안에|바로|긴급|急いで|right now|ahora mismo")
_QUESTION = re.compile(r"\?|까\s*$|[을를이가은는]\s*(?:알려|말해)\s*줘|궁금|얼마나|몇\s*[개번승회명]|어떻게|언제|누구|무엇|뭐야|뭐지|왜\b|였나|였지|였어\s*$|기억나|어때")
_PROPOSAL_QUESTION = re.compile(r"(?:할까|볼까|갈까|말할까|만들까|해볼까|어때|어떨까|괜찮을까)\s*\??\s*$")

# --------------------------------------------------------------------------
# Dialogue acts
# --------------------------------------------------------------------------

# act -> list of (regex, weight, evidence label)
ACT_LEXICON: dict[str, list[tuple[str, float, str]]] = {
    "vent": [
        (r"서운|섭섭", 1.0, "서운함"),
        (r"속상|답답|억울|서럽|분하|열받|짜증|화가\s*나|화나", 0.9, "감정 토로"),
        (r"힘들|지쳤|지친다|버겁|외롭|괴롭|무너질\s*것", 0.8, "피로·괴로움"),
        (r"불만|이해가\s*안|납득이\s*안|왜\s*나만", 0.7, "불만"),
        (r"위로가\s*필요|위로받고|들어줘|털어놓", 0.7, "털어놓기"),
        (r"悔しい|辛い|frustrated|upset|hurt", 0.7, "감정(외국어)"),
    ],
    "console": [
        (r"위로해|위로하|다독|달래|토닥|격려하|격려해|응원해\s*주|힘내라고|괜찮다고\s*말", 1.0, "위로하기"),
        (r"(?:후배|동료|신인|친구|선배|형|포수)[을를이가]?\s*(?:위로|다독|격려|챙기|안아)", 1.0, "상대를 위로"),
        (r"실망하지\s*말라고|고개\s*들라고|다음이\s*있다고", 0.8, "위로 문구"),
        (r"慰め|comfort|console|consolar|animar", 0.7, "위로(외국어)"),
    ],
    "praise": [
        (r"칭찬|치켜|인정한다|인정하고\s*싶|존경|훌륭|대단하다고|고맙다고\s*말|고마움을\s*전|박수", 1.0, "칭찬"),
        (r"(?:잘했다|최고다|멋졌다|고맙다)고\s*(?:말|전|해)", 0.9, "칭찬 발언"),
        (r"褒め|praise|compliment|elogiar|felicitar", 0.7, "칭찬(외국어)"),
    ],
    "criticize_self": [
        (r"내\s*잘못|내\s*탓|나\s*때문|반성|자책|내가\s*못했|내가\s*부족|부족했다고|실수를\s*인정|책임은\s*나", 1.0, "자책"),
        (r"제\s*책임|스스로를\s*탓|내가\s*더\s*잘했어야", 0.9, "책임 인정"),
        (r"反省|my fault|blame myself|mi culpa", 0.7, "자책(외국어)"),
    ],
    "declare": [
        (r"선언|공언|노린다고|노리겠다|해내겠|반드시\s*(?:이기|잡|넘|달성)|도전하겠|목표를\s*(?:밝히|말하|공개)|약속하겠|기록을\s*노", 1.0, "선언"),
        (r"(?:우승|기록|타이틀|MVP|20승|300탈삼진|퍼펙트)[을를]?\s*(?:노린|잡겠|해내|이루)", 0.9, "목표 선언"),
        (r"宣言|declare|announce|prometo|voy a", 0.7, "선언(외국어)"),
    ],
    "apologize": [
        (r"사과|미안|죄송|용서를\s*구|사죄|잘못했다고\s*말", 1.0, "사과"),
        (r"謝る|謝罪|apolog|sorry|perd[oó]n|disculpa", 0.8, "사과(외국어)"),
    ],
    "request_private_meeting": [
        (r"면담|독대|따로\s*만나|따로\s*이야기|직접\s*묻|직접\s*물어|직접\s*만나|만나서\s*(?:이야기|얘기|말)|찾아가서|감독실|1대1로|일대일로|개인적으로\s*(?:이야기|얘기|만나|묻)", 1.0, "비공개 면담"),
        (r"(?:감독|코치|단장|에이전트)[과와에게한테]?\s*(?:따로|직접|조용히)\s*(?:이야기|얘기|말|만나)", 0.9, "직접 대화 요청"),
        (r"話し合い|meeting|talk privately|hablar en privado", 0.7, "면담(외국어)"),
    ],
    "give_public_quote": [
        (r"기자들?\s*앞에서|인터뷰에서|기자회견에서|공개적으로\s*(?:말|밝히|답)|코멘트|한마디\s*(?:하|남기)|발언|강하게\s*말|팀을\s*감싸|공개\s*답변", 1.0, "공개 발언"),
        (r"(?:언론|기자|미디어)에\s*(?:말|답|밝히)", 0.9, "언론 대응"),
        (r"会見|コメント|quote|statement|declaraci[oó]n", 0.7, "공개 발언(외국어)"),
    ],
    "ask": [
        (r"몇\s*[개번승회명점]|얼마나|어떻게\s*됐|언제였|누구였|기억나|알려\s*줘|알려줘|말해\s*줘|궁금|어디쯤|몇\s*위|순위가|기록이\s*어|성적이\s*어|통산|시즌\s*(?:성적|기록)|지난\s*(?:경기|시즌|번)", 1.0, "질문"),
        (r"\?$", 0.4, "물음표"),
        (r"教えて|何個|how many|what was|cu[aá]nt", 0.7, "질문(외국어)"),
    ],
    "celebrate": [
        (r"기쁘|신난|최고의\s*날|축하|짜릿|행복|やった|so happy|celebrat", 0.8, "기쁨")],
    "worry": [
        (r"걱정|불안|두렵|무섭|염려|초조|心配|worried|nervous|preocup", 0.8, "걱정")],
    "thank": [
        (r"감사|고마워|고맙|ありがとう|thank|gracias", 0.6, "감사")],
    "recall": [
        (r"그때|그날|예전에|지난번|기억\s*나|떠올|다시\s*꺼내|回想|remember", 0.8, "회상")],
    "predict": [
        (r"예상|전망|될\s*것\s*같|될까|예측|forecast|predict", 0.6, "전망")],
    # narrative prop control acts (8.11)
    "prop_create": [(r"(?:내부|우리만의|팀\s*안)?\s*농담으로\s*만들|소재로\s*(?:만들|삼)|별명으로\s*(?:하|만들|부르)|앞으로\s*(?:계속|자주)\s*(?:꺼내|쓰|언급)|이걸\s*(?:기억|소재)", 1.0, "소품 생성")],
    "prop_set_visibility": [(r"안에서만\s*(?:돌|알|쓰)|밖으로\s*(?:내보내지|새지)|우리끼리만|팬들까지\s*(?:쓰게|알게)|공개해도|공개하지\s*마", 1.0, "소품 공개 범위")],
    "prop_attach_relationship": [(r"긴장하게\s*만들|사이가\s*(?:틀어|가까워)|(?:코치|트레이너|동료)[과와]\s*(?:식단|문제|일)로\s*(?:갈등|긴장|다투)|관계를\s*(?:엮|얽)", 1.0, "관계 연결")],
    "prop_schedule_callback": [(r"다음\s*(?:홈런|경기|등판|승리|우승|안타)\s*(?:때|에)\s*(?:이|그)?\s*(?:농담|얘기|이야기|소재|별명)?[을를]?\s*(?:다시|또)\s*(?:꺼내|하|부르)|다시\s*꺼내자|나중에\s*다시", 1.0, "콜백 예약")],
    "prop_change_role": [(r"웃기지\s*말고|진지한\s*(?:장면|화해|이야기)|이번에는\s*(?:진지|무겁|가볍|웃기)게|분위기를\s*바꿔", 1.0, "감정 역할 변경")],
    "prop_escalate": [(r"팬들까지|더\s*키우|크게\s*만들|전국적으로|스폰서|광고|언론까지|키워\s*보자", 0.9, "확대")],
    "prop_retire": [(r"이제\s*그만|그만하고|좋은\s*기억으로만|여기까지만|더는\s*안\s*꺼내|은퇴시키|끝내자", 1.0, "소재 정리")],
}

_ACT_PATTERNS = {
    act: [(re.compile(pattern, re.I), weight, label) for pattern, weight, label in rows]
    for act, rows in ACT_LEXICON.items()
}

PROP_ACTS = tuple(act for act in ACT_LEXICON if act.startswith("prop_"))
EVENT_ACTS = (
    "vent",
    "console",
    "praise",
    "criticize_self",
    "declare",
    "apologize",
    "request_private_meeting",
    "give_public_quote",
)
INFORMATION_ACTS = ("ask", "recall", "predict")

# Acts that imply a default visibility when the text has no cue.
ACT_DEFAULT_VISIBILITY = {
    "vent": "private",
    "console": "clubhouse",
    "praise": "clubhouse",
    "criticize_self": "national",
    "declare": "national",
    "apologize": "clubhouse",
    "request_private_meeting": "private",
    "give_public_quote": "national",
}

# Acts that imply a default target when none is named.
ACT_DEFAULT_TARGET = {
    "give_public_quote": "reporter",
    "criticize_self": "reporter",
    "declare": "reporter",
    "request_private_meeting": "manager",
    "console": "teammate",
    "praise": "teammate",
    "apologize": "teammate",
    "vent": "self",
}

# --------------------------------------------------------------------------
# Emotion
# --------------------------------------------------------------------------

_EMOTION = [
    ("hurt", -0.6, 0.5, re.compile(r"서운|섭섭|속상|억울|서럽|상처")),
    ("angry", -0.7, 0.8, re.compile(r"화가\s*나|화나|열받|분하|짜증|분노")),
    ("sad", -0.6, 0.3, re.compile(r"슬프|우울|눈물|허전|외롭")),
    ("anxious", -0.4, 0.7, re.compile(r"불안|걱정|초조|긴장돼|무섭|두렵")),
    ("tired", -0.4, 0.2, re.compile(r"지쳤|피곤|힘들|지친|버겁")),
    ("determined", 0.4, 0.8, re.compile(r"반드시|해내|노린|도전|각오|이겨내")),
    ("grateful", 0.6, 0.4, re.compile(r"고마|감사|덕분")),
    ("happy", 0.7, 0.6, re.compile(r"기쁘|신난|행복|짜릿|최고")),
    ("calm", 0.2, 0.1, re.compile(r"차분|담담|조용히|괜찮")),
    ("guilty", -0.5, 0.4, re.compile(r"미안|죄송|내\s*탓|자책|반성")),
]

_CLAUSE_SPLIT = re.compile(r"\s*(?:,|，|;|\.|。|하지만|지만\s|그렇지만|그래도|반면에?|그런데|그리고\s|근데|그러나)\s*")

# --------------------------------------------------------------------------
# Fact requirements (8.2 step 9)
# --------------------------------------------------------------------------

_FACT_CUES = [
    ("game.result", re.compile(r"경기\s*(?:결과|점수|스코어)|몇\s*대\s*몇|점수|스코어|오늘.*(?:이겼|졌)|game\s*(?:result|score)", re.I)),
    ("stats.pit_K", re.compile(r"탈삼진|삼진\s*(?:몇|개수|기록)|奪三振|strikeout|ponche")),
    ("stats.pit_W", re.compile(r"[몇\d]\s*승|승수|승리\s*(?:몇|기록)|勝|wins?\b|victorias")),
    ("stats.pit_IP", re.compile(r"이닝|イニング|innings")),
    ("stats.pit_H", re.compile(r"피안타|被安打|hits allowed")),
    ("stats.bat_HR", re.compile(r"홈런|本塁打|home\s*run|jonr[oó]n")),
    ("stats.bat_AVG", re.compile(r"타율|打率|batting average|promedio")),
    ("stats.bat_H", re.compile(r"안타|安打|\bhits\b")),
    ("stats.bat_RBI", re.compile(r"타점|打点|RBI")),
    ("stats.bat_SB", re.compile(r"도루|盗塁|stolen|robo")),
    ("career.honors", re.compile(r"수상|MVP|타이틀|상을\s*받|表彰|award|premio")),
    ("career.records", re.compile(r"기록\s*(?:경신|동률|근접)|신기록|통산\s*기록|記録|record")),
    ("career.seasons", re.compile(r"지난\s*시즌|작년|통산|커리어|昨シーズン|last season|career")),
    ("context.standings", re.compile(r"순위|몇\s*위|승차|매직넘버|順位|standings|posici[oó]n")),
    ("context.salary", re.compile(r"연봉|계약금|年俸|salary|sueldo")),
    ("snapshot.date", re.compile(r"오늘\s*(?:날짜|며칠)|무슨\s*요일|몇\s*월|날짜|日付|what day|fecha")),
    ("story.history", re.compile(r"지난번|그때\s*(?:내가|우리가)|예전에\s*(?:말|했)|기억나|이전\s*대화|前回|last time")),
    ("props", re.compile(r"농담|별명|햄버거|소재|약속했던|내기|ジョーク|joke|nickname")),
]

# --------------------------------------------------------------------------
# Core analysis
# --------------------------------------------------------------------------


def _score_acts(text: str) -> list[dict]:
    rows = []
    for act, patterns in _ACT_PATTERNS.items():
        score = 0.0
        evidence = []
        for pattern, weight, label in patterns:
            matches = pattern.findall(text)
            if matches:
                score += weight * min(2, len(matches))
                evidence.append(label)
        if score > 0:
            rows.append({"act": act, "score": round(min(1.0, score / 1.6), 3), "evidence": evidence})
    rows.sort(key=lambda row: (-row["score"], row["act"]))
    return rows


def _entities(text: str) -> list[dict]:
    found = []
    for entity_id, pattern in _ENTITY_PATTERNS.items():
        match = pattern.search(text)
        if match:
            found.append({"id": entity_id, "label": ENTITY_LEXICON[entity_id]["label"], "surface": match.group(0), "position": match.start()})
    found.sort(key=lambda row: row["position"])
    return found


def _visibility(text: str) -> str | None:
    hits = [(pattern.search(text).start(), level) for level, pattern in _VISIBILITY_CUES if pattern.search(text)]
    if not hits:
        return None
    hits.sort()
    return hits[0][1]


def _emotion(text: str) -> dict:
    labels = []
    valence = 0.0
    arousal = 0.0
    for label, value, energy, pattern in _EMOTION:
        if pattern.search(text):
            labels.append(label)
            valence += value
            arousal += energy
    count = len(labels) or 1
    return {
        "labels": labels,
        "valence": round(max(-1.0, min(1.0, valence / count)), 3) if labels else 0.0,
        "arousal": round(max(0.0, min(1.0, arousal / count)), 3) if labels else 0.2,
    }


def _risks(text: str) -> list[str]:
    return [tag for tag, pattern in _RISK_CUES if pattern.search(text)]


def _fact_requirements(text: str) -> list[str]:
    return [key for key, pattern in _FACT_CUES if pattern.search(text)]


def _analyze_clause(text: str) -> dict:
    acts = _score_acts(text)
    entities = _entities(text)
    primary_act = acts[0]["act"] if acts else None
    target = None
    for row in entities:
        if row["id"] != "self" or primary_act == "vent":
            target = row["id"]
            break
    if target is None and primary_act in ACT_DEFAULT_TARGET:
        target = ACT_DEFAULT_TARGET[primary_act]
        target_source = "act_default"
    elif target is not None:
        target_source = "text"
    else:
        target_source = None
    visibility = _visibility(text)
    visibility_source = "text" if visibility else None
    if visibility is None and primary_act in ACT_DEFAULT_VISIBILITY:
        visibility = ACT_DEFAULT_VISIBILITY[primary_act]
        visibility_source = "act_default"
    return {
        "text": text,
        "acts": acts,
        "primary_act": primary_act,
        "entities": entities,
        "target": target,
        "target_source": target_source,
        "visibility": visibility,
        "visibility_source": visibility_source,
        "emotion": _emotion(text),
    }


def _split_clauses(text: str) -> list[str]:
    parts = [part.strip() for part in _CLAUSE_SPLIT.split(text) if part and part.strip()]
    if len(parts) <= 1:
        return [text]
    # Keep very short fragments attached to their neighbour.
    merged: list[str] = []
    for part in parts:
        if merged and len(part) < 4:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


def understand(text: object, state: dict | None = None) -> dict:
    """Analyse one user utterance against the active conversation state.

    ``state`` may carry ``last_target``, ``last_act``, ``active_threads``
    (list of dicts with ``thread_id``/``label``/``participants``), and
    ``active_persona``. The result is deterministic for the same inputs.
    """
    state = state or {}
    normalized = normalize(text)
    language = detect_language(normalized)
    clauses = [_analyze_clause(clause) for clause in _split_clauses(normalized)] if normalized else []
    whole = _analyze_clause(normalized) if normalized else _analyze_clause("")

    # Combine clause acts: keep the strongest instance of each act.
    combined: dict[str, dict] = {}
    for clause in clauses or [whole]:
        for row in clause["acts"]:
            existing = combined.get(row["act"])
            if existing is None or row["score"] > existing["score"]:
                combined[row["act"]] = dict(row)
    acts = sorted(combined.values(), key=lambda row: (-row["score"], row["act"]))
    primary_act = acts[0]["act"] if acts else None
    secondary = [row["act"] for row in acts[1:] if row["score"] >= 0.4]

    entities = whole["entities"]
    target = whole["target"]
    target_source = whole["target_source"]
    resolved_from_context = False
    if (target is None or target_source == "act_default") and state.get("last_target") and primary_act not in INFORMATION_ACTS:
        # An act-default target is only a guess; the conversation's last
        # named counterpart wins (ellipsis resolution, 8.2 step 8).
        target = state["last_target"]
        target_source = "context"
        resolved_from_context = True
    visibility = whole["visibility"]
    visibility_source = whole["visibility_source"]

    question = bool(_QUESTION.search(normalized)) if normalized else False
    proposal_question = bool(_PROPOSAL_QUESTION.search(normalized)) if normalized else False
    fact_requirements = _fact_requirements(normalized)
    if primary_act is None and fact_requirements and question:
        primary_act = "ask"
        acts.insert(0, {"act": "ask", "score": 0.5, "evidence": ["사실 질문"]})

    # Confidence: strongest act, bonus for a named target, penalty for very short input.
    confidence = acts[0]["score"] if acts else 0.0
    if target_source == "text":
        confidence = min(1.0, confidence + 0.1)
    if len(normalized) < 4:
        confidence = min(confidence, 0.2)
    if not normalized:
        confidence = 0.0

    active_threads = list(state.get("active_threads") or [])
    active_persona = state.get("active_persona") or state.get("last_target")
    emotion = whole["emotion"]

    target_known = bool(target) and target_source in ("text", "act_default", "context")
    if primary_act and confidence >= 0.55 and (
        target_known or primary_act in INFORMATION_ACTS or primary_act in PROP_ACTS
    ):
        fallback_level = 1
    elif primary_act and confidence >= 0.4 and active_threads:
        fallback_level = 2
    elif emotion["labels"] and active_persona:
        fallback_level = 3
    elif normalized and (emotion["labels"] or acts):
        fallback_level = 4
    elif normalized:
        fallback_level = 5
    else:
        fallback_level = 6

    # Multi-act clauses with distinct targets/visibility (8.3 example).
    dual = []
    for clause in clauses:
        if clause["primary_act"] and clause["primary_act"] in EVENT_ACTS:
            dual.append(
                {
                    "text": clause["text"],
                    "act": clause["primary_act"],
                    "target": clause["target"] or target,
                    "visibility": clause["visibility"],
                    "score": clause["acts"][0]["score"],
                }
            )
    if len(dual) > 1:
        seen = set()
        distinct = []
        for row in dual:
            key = (row["act"], row["target"], row["visibility"])
            if key not in seen:
                seen.add(key)
                distinct.append(row)
        dual = distinct if len(distinct) > 1 else []
    else:
        dual = []

    return {
        "engine_version": ENGINE_VERSION,
        "language": language,
        "normalized_text": normalized,
        "tokens": tokenize(normalized),
        "stems": stems(normalized),
        "acts": acts,
        "primary_act": primary_act,
        "secondary_acts": secondary,
        "entities": entities,
        "target": target,
        "target_source": target_source,
        "resolved_from_context": resolved_from_context,
        "visibility_hint": visibility,
        "visibility_source": visibility_source,
        "emotion": emotion,
        "urgency": bool(_URGENCY.search(normalized)) if normalized else False,
        "risk_tags": _risks(normalized) if normalized else [],
        "fact_requirements": fact_requirements,
        "question": question,
        "proposal_question": proposal_question,
        "confidence": round(confidence, 3),
        "fallback_level": fallback_level,
        "clauses": [
            {
                "text": clause["text"],
                "primary_act": clause["primary_act"],
                "target": clause["target"],
                "visibility": clause["visibility"],
                "emotion": clause["emotion"],
            }
            for clause in clauses
        ],
        "dual_acts": dual,
        "prop_acts": [row["act"] for row in acts if row["act"] in PROP_ACTS],
        "creates_event": primary_act in EVENT_ACTS,
    }


def clarification_question(understanding: dict) -> str:
    """One concise clarification for fallback level 5 (8.7 example)."""
    acts = [row["act"] for row in understanding.get("acts") or []]
    if "vent" in acts and "give_public_quote" in acts:
        return "그 말이 감독에게 직접 전할 불만인지, 기자들 앞에서 할 발언인지가 중요해. `감독과 비공개 면담` 또는 `경기 후 공개 발언` 중 어느 쪽으로 이어갈까?"
    if understanding.get("emotion", {}).get("labels") and not understanding.get("target"):
        return "누구에게 향한 마음인지 먼저 정하자. 감독, 동료, 팬, 아니면 나 자신에게 하는 말일까?"
    if understanding.get("question") and not understanding.get("fact_requirements"):
        return "무엇을 확인하고 싶은지 조금만 더 알려줘. 오늘 성적, 통산 기록, 순위, 아니면 지난 대화 중 어느 쪽일까?"
    return "어떤 장면으로 이어갈지 정하자. 비공개 대화, 팀 안의 장면, 공개 발언 중 어느 쪽이 가까울까?"


def help_suggestions() -> list[str]:
    """Fallback level 6: offline help for available categories."""
    return [
        "오늘 감독한테 서운했어 — 비공개 감정을 정리하고 면담을 제안합니다.",
        "기자들 앞에서는 강하게 말할래 — 경기 후 공개 발언을 만듭니다.",
        "후배를 위로해 주고 싶다 — 라커룸 장면을 만듭니다.",
        "다음 등판에서 기록을 노린다고 선언할까? — 선언 이벤트를 제안합니다.",
        "내 시즌 탈삼진 몇 개야? — 검증된 세이브 기록으로 답합니다.",
        "이걸 앞으로 내부 농담으로 만들자 — 반복 소재를 등록합니다.",
    ]
