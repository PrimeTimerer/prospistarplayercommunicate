import {renderStoryDesk} from "./story-desk.js";
import {renderConnectedStory} from "./connected-story.js";
import {renderWorkspace} from "./workspace.js";
import {setActivityContext, updateJobConsole} from "./activity-console.js";

const EVENT_LABELS = {
  WORLD_INIT: "세계선 기준 생성",
  NEW_GAME: "새 경기 반영",
  SEASON_UPDATE: "시즌 기록 갱신",
  NO_CHANGE: "변화 없음",
  REST_DAY: "휴식일",
  CORRECTION: "기록 정정",
  SEASON_ROLLOVER: "시즌 누적 전환",
};

const ROLE_LABELS = {
  batting: "타격 출전",
  pitching: "투구 출전",
  two_way: "투타 동시 출전",
  no_appearance: "출전 판정 없음",
};

const STAT_LABELS = {
  bat_AB: "AB", bat_H: "H", bat_HR: "HR", bat_RBI: "RBI", bat_R: "R", bat_SO: "SO", bat_SB: "SB",
  pit_IP: "IP", pit_TBF: "TBF", pit_H: "H", pit_K: "K", pit_W: "WINS",
};

let currentDashboard = null;
let readingWorldKey = null;

function providerView(dashboard = currentDashboard) {
  const config = dashboard?.config || {};
  const provider = config.ai_provider || "local_auto";
  if (provider === "gemini") {
    const connection = config.gemini_usage?.connection || {};
    const ready = Boolean(
      config.gemini_consent
      && config.gemini_key_present
      && connection.status === "verified"
      && connection.model === config.gemini_model
    );
    return {
      provider,
      ready,
      badge: "GEMINI API",
      status: ready ? "Gemini 연결 확인됨" : "Gemini 연결 확인 필요",
      button: ready ? "Gemini와 이어가기" : "대화로 이어가기",
      hint: ready
        ? "Gemini 선택됨 · 승인된 대화 문맥만 전송하고 같은 타임라인에 저장합니다."
        : "Gemini가 선택됐지만 동의 또는 API 키가 없습니다. 설정을 확인해 주세요.",
    };
  }
  if (provider === "deterministic") {
    return {
      provider,
      ready: true,
      badge: "내장 서사 엔진",
      status: "내장 서사 엔진",
      button: "대화로 이어가기",
      hint: "완전 오프라인 · 자유 대화는 내장 서사 엔진이 답하고, 사건은 확정 버튼으로 기록됩니다.",
    };
  }
  const ready = Boolean(dashboard?.app?.llm_reachable);
  return {
    provider,
    ready,
    badge: "로컬 LLM",
    status: ready ? "로컬 LLM 연결됨" : "로컬 LLM 꺼짐",
    button: ready ? "로컬 LLM과 이어가기" : "대화로 이어가기",
    hint: ready
      ? "로컬 LLM 연결됨 · 버튼과 자유 대화가 같은 타임라인에 저장됩니다."
      : "로컬 LLM 꺼짐 · 자유 대화는 내장 서사 엔진이 답하고, 사건은 확정 버튼으로 기록됩니다.",
  };
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function number(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function average(value) {
  if (value === null || value === undefined) return "—";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(3).replace(/^0/, "") : "—";
}

function dateText(date, long = false) {
  if (!date?.year) return "—";
  return long
    ? `${date.year}년 ${date.month}월 ${date.day}일 · ${date.career_year || "—"}년차`
    : `${date.year}.${String(date.month).padStart(2, "0")}.${String(date.day).padStart(2, "0")}`;
}

function compactPath(value) {
  if (!value) return "미지정";
  const parts = String(value).replaceAll("\\", "/").split("/").filter(Boolean);
  return parts.length > 4 ? `…/${parts.slice(-4).join("/")}` : parts.join("/");
}

function statCells(entries) {
  return entries.map(([label, value, tone = ""]) =>
    `<div class="stat-cell"><strong class="${tone}">${escapeHTML(value)}</strong><span>${escapeHTML(label)}</span></div>`
  ).join("");
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value ?? "—";
}

function eventCopy(event) {
  if (!event) return "판정 정보가 없습니다.";
  if (event.baseline_only) return "이 스냅샷은 비교 기준으로만 저장됐으며, 누락된 경기를 새로 만들어내지 않습니다.";
  if (event.kind === "NO_CHANGE") return "직전 검증 스냅샷과 같은 기록입니다. 기존 피드는 다시 만들지 않았습니다.";
  if (event.kind === "REST_DAY") return "날짜는 진행됐지만 검증된 누적 기록 변화가 없습니다.";
  if (event.kind === "CORRECTION") return "누적 기록이 감소해 경기로 단정하지 않고 정정으로 분리했습니다.";
  return "두 개의 검증된 세이브 스냅샷 차이만 이번 변화로 표시합니다.";
}

function renderEvent(event) {
  const card = document.getElementById("eventCard");
  if (!event) {
    card.innerHTML = `<div class="event-card__mark">—</div><div class="event-card__body"><strong>판정 없음</strong></div>`;
    return;
  }
  const label = EVENT_LABELS[event.kind] || event.kind || "판정 없음";
  const role = ROLE_LABELS[event.role] || "출전 판정 없음";
  const lines = (event.game_lines || []).map(line => `<div class="event-line">${escapeHTML(line)}</div>`).join("");
  const milestones = (event.milestones || []).map(([, text]) => `<div class="event-line">🏆 ${escapeHTML(text)}</div>`).join("");
  card.innerHTML = `
    <div class="event-card__mark">${escapeHTML((event.kind || "—").slice(0, 2))}</div>
    <div class="event-card__body">
      <div class="event-card__top"><strong>${escapeHTML(label)}</strong><span class="source-pill source-pill--verified">스냅샷 차이</span><span class="badge">${escapeHTML(role)}</span></div>
      <p>${escapeHTML(eventCopy(event))}</p>
      ${(lines || milestones) ? `<div class="event-lines">${lines}${milestones}</div>` : ""}
    </div>`;
}

function renderSpotlight(dashboard) {
  const spotlight = dashboard.spotlight || dashboard.feed?.spotlight || {};
  const score = Math.max(0, Math.min(100, Number(spotlight.score || 0)));
  const budget = spotlight.reaction_budget || {};
  const componentLabels = {
    season_performance: "시즌 성적",
    honors: "수상",
    npb_record_comparisons: "NPB 기록 대조",
    career_milestones: "통산 마일스톤",
    career_continuity: "커리어 축적",
    career_stature: "커리어 등급",
    current_event: "오늘 사건",
  };
  const components = Object.entries(spotlight.components || {}).filter(([, value]) => Number(value) > 0);
  const drivers = spotlight.drivers || [];
  const card = document.getElementById("spotlightCard");
  card.innerHTML = `<div class="spotlight-card__meter">
      <div class="spotlight-card__score"><span>${escapeHTML(spotlight.label || "개인 세계선")}</span><strong>${escapeHTML(score)}</strong><em>/100</em></div>
      <div class="spotlight-card__track"><i style="width:${score}%"></i></div>
      <small>${escapeHTML(spotlight.disclaimer || "공식 인기 지표가 아닌 오프라인 서사 반응 규모입니다.")}</small>
    </div>
    <div class="spotlight-card__body">
      <p>${escapeHTML(spotlight.description || "현재 기록을 기준으로 주변 반응 범위를 계산합니다.")}</p>
      <div class="spotlight-budget"><span><b>${escapeHTML(budget.boards || 0)}</b> 게시판</span><span><b>${escapeHTML(budget.media || 0)}</b> 기사</span><span><b>${escapeHTML(budget.waves || 0)}</b> 반응층</span><span><b>${escapeHTML(budget.comments || 0)}</b> 댓글</span></div>
      <div class="spotlight-components">${components.map(([key, value]) => `<span>${escapeHTML(componentLabels[key] || key)} <b>+${escapeHTML(value)}</b></span>`).join("") || `<span>아직 누적 근거가 적습니다.</span>`}</div>
      <div class="spotlight-drivers">${drivers.slice(0, 5).map(row => `<span title="${escapeHTML(row.provenance || "")}">${escapeHTML(row.label)} <b>+${escapeHTML(row.points)}</b></span>`).join("")}</div>
    </div>`;

  const quiet = spotlight.same_day_echo_active
    ? "오늘 마지막 검증 사건의 파장이 같은 게임 날짜 동안 계속됩니다."
    : spotlight.quiet_current_event && spotlight.ambient_active
      ? "오늘 새 사건이 작아도 누적 위상만으로 주변 반응이 계속됩니다."
      : "오늘 사건과 누적 위상을 함께 반영해 반응 범위를 정합니다.";
  const community = document.getElementById("communitySpotlight");
  community.innerHTML = `<div><span class="kicker">AMBIENT WORLD ENGINE</span><strong>${escapeHTML(spotlight.label || "개인 세계선")} · ${escapeHTML(score)}/100</strong><p>${escapeHTML(quiet)}</p></div><div class="spotlight-budget spotlight-budget--compact"><span>${escapeHTML(budget.boards || 0)} 게시판</span><span>${escapeHTML(budget.media || 0)} 기사</span><span>${escapeHTML(budget.waves || 0)}개 파동</span></div>`;
  const waves = spotlight.waves || dashboard.feed?.ambient_waves || [];
  document.getElementById("ambientWaveList").innerHTML = waves.map(wave => `<article class="ambient-wave"><span>${escapeHTML(wave.sequence || "—")}</span><div><strong>${escapeHTML(wave.circle || "주변 세계")}</strong><em>${escapeHTML(wave.tone || "반응")}</em><p>${escapeHTML(wave.detail || "")}</p></div></article>`).join("");
  setText("storySpotlightHint", `${spotlight.label || "개인 세계선"} ${score}/100 · 작은 선택도 위상에 비례해 최대 ${budget.waves || 0}개 주변 층위로 번집니다. 비공개 행동은 유출 사실로 만들지 않습니다.`);
}

function historySummary(item) {
  const lines = item.game_lines || [];
  if (lines.length) return lines.join(" · ");
  if (item.baseline_only) return "비교 기준 스냅샷";
  if (item.kind === "REST_DAY") return "검증 기록 변화 없음";
  return "기록 변화 상세";
}

function historyRows(history, limit = null) {
  const rows = limit ? history.slice(0, limit) : history;
  if (!rows.length) return `<div class="empty-inline">아직 기록된 변화가 없습니다.</div>`;
  return rows.map(item => `
    <details class="history-item card" data-history-id="${escapeHTML(item.source_hash || "")}">
      <summary>
        <span class="history-date">${escapeHTML(dateText(item.date))}</span>
        <span class="history-title"><strong>${escapeHTML(EVENT_LABELS[item.kind] || item.kind)}</strong><span>${escapeHTML(historySummary(item))}</span></span>
      </summary>
      <div class="history-body">
        <div class="delta-grid">${Object.entries(item.delta || {}).map(([key, value]) => `<span class="delta-chip">${escapeHTML(STAT_LABELS[key] || key)} ${value > 0 ? "+" : ""}${escapeHTML(value)}</span>`).join("") || `<span class="muted">이 항목은 기준선이며 경기 증가분이 없습니다.</span>`}</div>
        ${(item.milestones || []).map(([, text]) => `<div class="event-line">🏆 ${escapeHTML(text)}</div>`).join("")}
      </div>
    </details>`).join("");
}

function renderHistory(history) {
  const openIds = new Set([...document.querySelectorAll(".history-item[open]")].map(node => node.dataset.historyId));
  document.getElementById("historyList").innerHTML = historyRows(history);
  document.querySelectorAll(".history-item").forEach(node => { if (openIds.has(node.dataset.historyId)) node.open = true; });
  setText("historyCount", `${history.length}개 기록`);
  const preview = document.getElementById("timelinePreview");
  if (!history.length) {
    preview.innerHTML = `<div class="timeline-row"><span class="muted">—</span><span class="timeline-dot"></span><span class="timeline-row__copy"><strong>아직 세계선 기록이 없습니다.</strong></span></div>`;
  } else {
    preview.innerHTML = history.slice(0, 4).map(item => `
      <div class="timeline-row"><time>${escapeHTML(dateText(item.date))}</time><span class="timeline-dot"></span><span class="timeline-row__copy"><strong>${escapeHTML(EVENT_LABELS[item.kind] || item.kind)}</strong><span>${escapeHTML(historySummary(item))}</span></span></div>`).join("");
  }
}

function provenanceMeta(value) {
  if ([
    "save_verified",
    "save_verified_completed_season",
    "save_verified_historical_season",
    "save_verified_season_summary",
    "save_verified_historical_import",
    "save_verified_historical_milestone",
    "save_verified_current_season",
    "derived_save_delta",
  ].includes(value)) {
    return ["세이브 검증", "source-pill--verified"];
  }
  if (value === "manual_confirmed" || value === "manual_profile_import") {
    return ["사용자 확인", "source-pill--manual"];
  }
  return ["근거 분리", "source-pill--hint"];
}

function recordRows(rows, careerScope = false) {
  if (!rows?.length) return `<div class="empty-inline">대조 가능한 기록이 없습니다.</div>`;
  return rows.map(row => {
    const state = row.status === "broken" ? "기준 초과" : row.status === "tied" ? "동률" : `${row.remaining_display} 남음`;
    const tone = row.status === "broken" ? "is-broken" : row.status === "tied" ? "is-tied" : "";
    const incomplete = careerScope && row.incomplete_history ? `<span class="record-row__caution">이전 시즌 일부 미입력</span>` : "";
    return `<div class="record-row ${tone}">
      <div class="record-row__top"><strong>${escapeHTML(row.label)}</strong><span>${escapeHTML(state)}</span></div>
      <div class="record-row__values"><b>${escapeHTML(row.current_display)}</b><span>NPB ${escapeHTML(row.record_display)} · ${escapeHTML(row.holder_ko || row.holder)}${row.season_year ? ` · ${escapeHTML(row.season_year)}` : ""}</span></div>
      <progress max="100" value="${escapeHTML(row.progress || 0)}">${escapeHTML(row.progress || 0)}%</progress>
      ${incomplete}
    </div>`;
  }).join("");
}

function seasonSummary(stats) {
  const batting = stats?.bat_AB
    ? `${number(stats.bat_H)}H · ${number(stats.bat_HR)}HR · ${number(stats.bat_RBI)}RBI · AVG ${average(stats.bat_AVG)} · OPS ${stats.bat_OPS == null ? "—" : Number(stats.bat_OPS).toFixed(3)}`
    : "타격 기록 없음";
  const pitching = stats?.pit_IP_outs
    ? `${number(stats.pit_IP)}IP · ${number(stats.pit_W)}W · ${number(stats.pit_K)}K · ERA ${stats.pit_ERA == null ? "—" : Number(stats.pit_ERA).toFixed(2)}`
    : "투구 기록 없음";
  return `${batting} / ${pitching}`;
}

function renderCareer(dashboard) {
  const career = dashboard.career || {};
  const totals = career.totals || {};
  const tracking = career.tracking || {};
  const grade = career.player_grade || {};
  const completeness = document.getElementById("careerCompleteness");
  if (tracking.history_incomplete) {
    completeness.className = "record-notice is-warn";
    completeness.innerHTML = `<strong>통산 누계가 아직 하한선입니다.</strong><span>${escapeHTML(tracking.previous_seasons_expected || 0)}개 이전 시즌 중 ${escapeHTML(tracking.previous_seasons_save_verified || 0)}개는 세이브 자동 복원, ${escapeHTML(tracking.previous_seasons_manual || 0)}개는 사용자 확인입니다. 누락 시즌을 추가하면 통산·NPB 대조·등급이 즉시 다시 계산됩니다.</span>`;
  } else {
    completeness.className = "record-notice is-good";
    completeness.innerHTML = `<strong>현재 세이브가 가리키는 커리어 범위를 모두 합산했습니다.</strong><span>과거 ${escapeHTML(tracking.previous_seasons_save_verified || 0)}개 시즌은 세이브에서 자동 복원했고, 사용자 확인 시즌 ${escapeHTML(tracking.previous_seasons_manual || 0)}개와 현재 시즌을 중복 없이 합산합니다.</span>`;
  }

  const gradeComponentLabels = {
    best_season_peak: "최고 시즌",
    career_volume: "통산 누적",
    confirmed_honors: "확인 수상",
    npb_record_comparisons: "NPB 기록",
    career_continuity: "시즌 축적",
  };
  const gradeComponents = Object.entries(grade.components || {});
  document.getElementById("careerGradeCard").innerHTML = `
    <div class="career-grade__mark"><span>CAREER GRADE</span><strong>${escapeHTML(grade.code || "—")}</strong><em>${escapeHTML(grade.score ?? 0)}/100</em></div>
    <div class="career-grade__body">
      <div class="career-grade__title"><div><span>${escapeHTML(grade.label || "등급 계산 대기")}</span><strong>${escapeHTML(grade.description || "커리어 기록을 불러오면 등급을 계산합니다.")}</strong></div><span class="source-pill source-pill--hint">오프라인 서사 등급</span></div>
      <div class="career-grade__components">${gradeComponents.map(([key, value]) => `<span>${escapeHTML(gradeComponentLabels[key] || key)} <b>+${escapeHTML(value)}</b></span>`).join("")}</div>
      <small>${escapeHTML(grade.disclaimer || "게임 내 공식 능력 등급이 아닙니다.")} ${grade.history_incomplete ? "누락 시즌 때문에 현재 결과는 하한선입니다." : "현재 확인 가능한 커리어 범위를 모두 반영했습니다."}</small>
    </div>`;
  document.getElementById("careerBattingStats").innerHTML = statCells([
    ["AVG", average(totals.bat_AVG), "is-gold"], ["HR", number(totals.bat_HR)], ["RBI", number(totals.bat_RBI)],
    ["H", number(totals.bat_H)], ["AB", number(totals.bat_AB)], ["SB", number(totals.bat_SB)],
  ]);
  document.getElementById("careerPitchingStats").innerHTML = statCells([
    ["ERA", totals.pit_ERA == null ? "—" : Number(totals.pit_ERA).toFixed(2), "is-accent"], ["WINS", number(totals.pit_W)], ["IP", number(totals.pit_IP)],
    ["K", number(totals.pit_K)], ["H ALLOWED", number(totals.pit_H)], ["SEASONS", number((tracking.previous_seasons_entered || 0) + 1)],
  ]);

  const honorSummary = career.honor_summary || [];
  document.getElementById("honorSummary").innerHTML = honorSummary.length
    ? honorSummary.map(row => { const [source] = provenanceMeta(row.provenance); return `<span class="honor-chip" title="${escapeHTML(source)}"><b>${escapeHTML(row.count)}</b>${escapeHTML(row.label)}</span>`; }).join("")
    : `<span class="muted">세이브의 수상 열거형은 아직 구조 검증 중입니다. 일본시리즈 우승, 올스타, 월간 MVP처럼 화면에서 확인한 기억은 근거와 함께 추가할 수 있습니다.</span>`;

  const next = career.next_milestones || [];
  document.getElementById("nextMilestones").innerHTML = next.length
    ? next.slice(0, 8).map(row => `<article class="milestone-card card"><div><strong>${escapeHTML(row.target_display)} ${escapeHTML(row.label)}</strong><span>현재 ${escapeHTML(row.current_display)} · ${escapeHTML(row.remaining_display)} 남음</span></div><progress max="100" value="${escapeHTML(row.progress || 0)}">${escapeHTML(row.progress || 0)}%</progress></article>`).join("")
    : `<div class="empty-inline card">등록된 NPB 달성 기록 기준을 모두 통과했습니다.</div>`;

  document.getElementById("seasonRecordList").innerHTML = recordRows(career.records?.season || []);
  document.getElementById("careerRecordList").innerHTML = recordRows(career.records?.career || [], true);
  setText("recordCatalogVersion", `오프라인 기준표 · ${career.catalog_version || "—"}`);

  const seasons = career.seasons || [];
  setText("seasonLedgerCount", `${seasons.length}개 시즌`);
  document.getElementById("seasonLedger").innerHTML = seasons.length ? seasons.map(row => {
    const [source, sourceClass] = provenanceMeta(row.provenance);
    const records = (row.npb_record_matches || []).map(record => `<span class="record-badge">NPB ${escapeHTML(record.label)} ${record.status === "tied" ? "동률" : "기준 초과"}</span>`).join("");
    const editable = row.provenance === "manual_confirmed";
    return `<article class="season-row">
      <div class="season-row__year"><strong>${escapeHTML(row.season_year)}</strong><span>${escapeHTML(row.team || (row.team_provenance === "unavailable_in_season_summary" ? "팀 정보 없음" : "소속팀 미입력"))}</span></div>
      <div class="season-row__body"><div><span class="source-pill ${sourceClass}">${source}</span>${records}</div><p>${escapeHTML(seasonSummary(row.stats))}</p>${row.note ? `<small>${escapeHTML(row.note)}</small>` : ""}</div>
      <div class="row-actions">${editable ? `<button class="icon-text-button" type="button" data-edit-season="${escapeHTML(row.id)}">수정</button><button class="icon-text-button is-danger" type="button" data-remove-career="season" data-item-id="${escapeHTML(row.id)}">삭제</button>` : `<span class="locked-label">자동 보존</span>`}</div>
    </article>`;
  }).join("") : `<div class="empty-inline">추적 시작 전 시즌이 아직 없습니다. 현재 시즌은 중복 합산하지 않습니다.</div>`;

  const timeline = career.timeline || [];
  document.getElementById("achievementTimeline").innerHTML = timeline.length ? timeline.map(row => {
    const [source, sourceClass] = provenanceMeta(row.provenance);
    const when = row.occurred_on || (row.season_year ? `${row.season_year} 시즌` : "날짜 미입력");
    const manualHonor = row.entry_type === "honor" && row.provenance === "manual_confirmed";
    return `<article class="achievement card"><span class="achievement__date">${escapeHTML(when)}</span><div><strong>${escapeHTML(row.label || row.title)}</strong><span class="source-pill ${sourceClass}">${source}</span>${row.note ? `<p>${escapeHTML(row.note)}</p>` : ""}</div><div class="row-actions">${manualHonor ? `<button class="icon-text-button" type="button" data-edit-honor="${escapeHTML(row.id)}">수정</button><button class="icon-text-button is-danger" type="button" data-remove-career="honor" data-item-id="${escapeHTML(row.id)}">삭제</button>` : ""}</div></article>`;
  }).join("") : `<div class="empty-inline card">아직 달성일·수상 기록이 없습니다.</div>`;

  const archives = dashboard.daily_archive || [];
  document.getElementById("dailyArchiveList").innerHTML = archives.length ? archives.map(row => `<button class="archive-row card" type="button" data-open-archive="${escapeHTML(row.id)}"><span><b>${escapeHTML(row.game_date)}</b><small>${escapeHTML(EVENT_LABELS[row.event_kind] || row.event_kind)}</small></span><strong>${escapeHTML(row.headline)}</strong><em>${escapeHTML(row.board_count || 0)}개 게시판 · ${escapeHTML(row.article_count || 0)}개 기사</em></button>`).join("") : `<div class="empty-inline card">최신 세이브 확인을 실행하면 그날의 반응이 원문 그대로 보관됩니다.</div>`;

  const sources = career.sources || [];
  const awardDecoder = career.award_decoder || {};
  document.getElementById("recordSources").innerHTML = `<p><strong>시즌 기록</strong>은 현재 시즌과 검증 가능한 과거 시즌 요약을 세이브에서 자동 복원합니다. 과거 시즌의 정확한 달성일과 당시 팀은 요약 블록에 없어 연도 단위·정보 없음으로 분리합니다.</p><p><strong>수상 기록</strong>은 열거형 구조를 검증하기 전까지 자동으로 이름을 붙이지 않습니다. 현재 상태: ${escapeHTML(awardDecoder.status || "unavailable")}. 화면에서 확인된 수상은 사용자 확인 근거로 별도 보존합니다.</p><p><strong>판정 표현</strong>은 ‘공식 신기록’이 아니라 ‘NPB 공식 기록 수치와 동률/초과’로 제한합니다.</p>${sources.map(source => `<p><strong>NPB</strong> ${escapeHTML(source.label)} · 기준일 ${escapeHTML(source.as_of)}</p>`).join("")}`;
}

function setOptions(target, rows, selected) {
  if (!target) return;
  const previous = selected ?? target.value;
  target.innerHTML = (rows || []).map(row => `<option value="${escapeHTML(row.id)}">${escapeHTML(row.label)}</option>`).join("");
  if ([...target.options].some(option => option.value === previous)) target.value = previous;
}

function storyCategories(story) {
  const categories = [...(story.catalog?.categories || [])];
  const interactions = story.catalog?.interactions;
  if (interactions?.actions?.length) categories.push({ id: "starplayer", label: interactions.label, description: interactions.description, situations: interactions.actions });
  return categories;
}

function renderWorldbook(story) {
  const root = document.getElementById("storyWorldbook");
  if (!root) return;
  const origin = JSON.stringify({
    universe_id: currentDashboard?.world?.world_id,
    protagonist_id: String(currentDashboard?.player?.id ?? ""),
    game_date: story.game_date,
  });
  const originChanged = Boolean(root.dataset.origin && root.dataset.origin !== origin);
  if (originChanged) {
    for (const id of ["worldContextId", "worldContextRevision", "worldContextLabel", "worldContextDetail", "worldCounterpartId", "worldCounterpartRevision", "worldCounterpartName", "worldCounterpartAliases", "worldCounterpartNote"]) {
      const field = document.getElementById(id);
      if (field) field.value = "";
    }
    document.getElementById("worldContextRemoteAllowed").checked = false;
    document.getElementById("cancelWorldContextButton").classList.add("is-hidden");
    document.getElementById("cancelWorldCounterpartButton").classList.add("is-hidden");
    document.getElementById("saveWorldContextButton").textContent = "설정 저장";
    document.getElementById("saveWorldCounterpartButton").textContent = "인물 저장";
    document.getElementById("storyLifetimeGames").value = "";
  }
  root.dataset.origin = origin;

  const contextCatalog = story.worldbook_catalog?.personal_context || story.personal_context?.catalog || {};
  setOptions(document.getElementById("worldContextBasis"), contextCatalog.basis || [], originChanged ? "authored_background" : undefined);
  setOptions(document.getElementById("worldContextProficiency"), contextCatalog.proficiency || [], originChanged ? "familiar" : undefined);
  setOptions(document.getElementById("worldContextVisibility"), contextCatalog.visibility || [], originChanged ? "private" : undefined);
  const hobby = document.getElementById("worldContextBasis").value === "fictional_experience";
  document.getElementById("worldContextProficiencyField").classList.toggle("is-hidden", !hobby);
  const publicContext = document.getElementById("worldContextVisibility").value === "public";
  document.getElementById("worldContextRemoteAllowed").disabled = !publicContext;
  if (!publicContext) document.getElementById("worldContextRemoteAllowed").checked = false;
  const contexts = story.personal_context?.items || [];
  document.getElementById("worldContextList").innerHTML = contexts.length ? contexts.map(row => {
    const retired = row.status === "retired";
    const flags = [row.basis_label, row.proficiency_label, row.visibility_label, `개정 ${row.revision}`];
    if (row.remote_allowed) flags.push("외부 AI 전달 허용");
    if (retired) flags.push("보관됨");
    return `<article class="worldbook-row ${retired ? "is-retired" : ""}">
      <div><strong>${escapeHTML(row.label)}</strong><p>${escapeHTML(row.detail)}</p><small>${flags.filter(Boolean).map(escapeHTML).join(" · ")}</small></div>
      <div class="worldbook-row__actions">${retired ? "" : `<button type="button" data-edit-world-context="${escapeHTML(row.context_id)}">수정</button><button type="button" data-retire-world-context="${escapeHTML(row.context_id)}">보관</button>`}</div>
    </article>`;
  }).join("") : `<div class="worldbook-empty">아직 기록한 배경·취향·취미가 없습니다.</div>`;

  const counterpartCatalog = story.worldbook_catalog?.counterparts || {};
  setOptions(document.getElementById("worldCounterpartRole"), counterpartCatalog.roles || [], originChanged ? "teammate" : undefined);
  const counterparts = story.counterparts || [];
  document.getElementById("worldCounterpartList").innerHTML = counterparts.length ? counterparts.map(row => {
    const retired = row.status === "retired";
    const aliases = row.aliases?.length ? ` · 별명 ${row.aliases.join(", ")}` : "";
    const ambiguous = row.needs_explicit_selection ? " · 동명이인 선택 필요" : "";
    return `<article class="worldbook-row ${retired ? "is-retired" : ""}">
      <div><strong>${escapeHTML(row.label)}</strong><p>${escapeHTML(row.note || "아직 인물 메모가 없습니다.")}</p><small>${escapeHTML((row.role_labels || []).join("·") || "역할 미기록")}${escapeHTML(aliases)} · 개정 ${escapeHTML(row.identity_revision)}${escapeHTML(ambiguous)}${retired ? " · 보관됨" : ""}</small></div>
      <div class="worldbook-row__actions">${retired ? "" : `<button type="button" data-edit-world-counterpart="${escapeHTML(row.entity_id)}">수정</button><button type="button" data-retire-world-counterpart="${escapeHTML(row.entity_id)}">보관</button>`}</div>
    </article>`;
  }).join("") : `<div class="worldbook-empty">아직 반복 등장 인물을 등록하지 않았습니다.</div>`;
}

export function refreshStoryInteraction() {
  const story = currentDashboard?.story || {};
  const selected = document.getElementById("storyCategoryGrid")?.dataset.category === "starplayer";
  const panel = document.getElementById("storyInteractionPanel");
  if (!panel) return;
  panel.classList.toggle("is-hidden", !selected);
  const origin = JSON.stringify({ universe_id: currentDashboard?.world?.world_id, protagonist_id: String(currentDashboard?.player?.id ?? ""), game_date: story.game_date });
  if (panel.dataset.origin && panel.dataset.origin !== origin) {
    for (const id of ["storyParticipantName", "storyInteractionPlace", "storyInteractionTopic", "storyInteraction", "storyCounterpart"]) document.getElementById(id).value = "";
    document.getElementById("storyActionConfirmed").checked = false;
    document.getElementById("storyInteractionMode").value = "fictional_intervention";
    if (selected) document.getElementById("storyText").value = "";
  }
  panel.dataset.origin = origin;
  const catalog = story.catalog?.interactions || {};
  const action = (catalog.actions || []).find(row => row.id === document.getElementById("storySituation")?.value);
  setOptions(document.getElementById("storyVisibility"), (story.catalog?.visibility || []).filter(row => !selected || ["private", "clubhouse"].includes(row.id)));
  if (!selected) {
    const provider = providerView();
    setText("storyModeHint", provider.hint);
    document.getElementById("storyParticipantName").disabled = false;
    return;
  }
  const roles = ["player_exchange", "learning"].includes(action?.id) ? ["teammate", "rookie", "rival"] : ["teammate", "rookie", "rival", "coach", "manager", "family", "self"];
  setOptions(document.getElementById("storyTarget"), (story.catalog?.targets || []).filter(row => roles.includes(row.id)));
  const counterpartRows = (story.counterparts || []).filter(row => row.status === "active" && (row.roles || []).some(role => roles.includes(role)));
  const counterpartSelect = document.getElementById("storyCounterpart");
  const previousCounterpart = counterpartSelect.value;
  setOptions(counterpartSelect, [
    { id: "", label: "이름 직접 입력(기존 방식)" },
    ...counterpartRows.map(row => ({ id: row.entity_id, label: `${row.label} · ${(row.role_labels || []).join("·") || "등록 인물"}` })),
  ]);
  setOptions(document.getElementById("storyLearningProgress"), catalog.progress || []);
  setOptions(document.getElementById("storyInteraction"), [{ id: "", label: "새 만남" }, ...(story.interactions || []).map(row => ({ id: row.interaction_id, label: row.last_date + " · " + row.label + " · " + row.status_label }))]);
  document.getElementById("storyLearningField").classList.toggle("is-hidden", action?.id !== "learning");
  document.getElementById("storyActionConfirmField").classList.toggle("is-hidden", document.getElementById("storyInteractionMode").value !== "user_confirmed");
  setText("storyInteractionRule", action?.rule_note || "친밀도·능력·목적지 개방은 자동으로 바뀌지 않습니다.");
  const active = (story.interactions || []).find(row => row.interaction_id === document.getElementById("storyInteraction").value);
  if (active?.participant_key && counterpartRows.some(row => row.entity_id === active.participant_key)) counterpartSelect.value = active.participant_key;
  const counterpart = counterpartRows.find(row => row.entity_id === counterpartSelect.value);
  if (active) {
    document.getElementById("storyParticipantName").value = active.participant_label || "";
  } else if (counterpart) {
    document.getElementById("storyParticipantName").value = counterpart.label;
    const matchingRole = (counterpart.roles || []).find(role => roles.includes(role));
    if (matchingRole) document.getElementById("storyTarget").value = matchingRole;
  } else if (previousCounterpart) {
    document.getElementById("storyParticipantName").value = "";
  }
  const solo = document.getElementById("storyTarget").value === "self";
  counterpartSelect.disabled = Boolean(active) || solo;
  document.getElementById("storyParticipantName").disabled = Boolean(active) || solo || Boolean(counterpart);
  if (solo) {
    counterpartSelect.value = "";
    document.getElementById("storyParticipantName").value = "";
  }
  setText("storyInteractionStatus", active
    ? "이어갈 만남: " + active.label + " · " + active.origin_date + " 시작 · " + active.status_label + ". 아래 대화 버튼으로 이어갑니다. ‘새 행동 기록’은 별도의 만남을 만듭니다."
    : "‘새 행동 기록’으로 만남을 시작합니다. 대화로 먼저 제안한 뒤 확정할 수도 있습니다. 게임 날짜는 넘어가지 않습니다.");
  document.getElementById("storyEventButton").textContent = "새 행동 기록";
  const provider = providerView();
  document.getElementById("storyChatButton").textContent = provider.provider === "gemini" && provider.ready
    ? "Gemini와 이 만남 이어가기"
    : provider.provider === "local_auto" && provider.ready
      ? "로컬 LLM과 이 만남 이어가기"
      : "LLM 없이 이 만남 이어가기";
  setText("storyModeHint", provider.provider === "gemini" && provider.ready
    ? "농담·속마음·긴장감은 Gemini가 풍부하게 표현하고, 사건·관계·약속의 확정은 내장 엔진과 확인 버튼이 맡습니다."
    : "농담·속마음·거절·약속을 이어갈 수 있습니다. 공개 발언은 따로 제안·확정합니다.");
}

export function selectStoryCategory(categoryId) {
  const story = currentDashboard?.story || {};
  const categories = storyCategories(story);
  const selected = categories.find(row => row.id === categoryId) || categories[0];
  if (!selected) return;
  const grid = document.getElementById("storyCategoryGrid");
  grid.dataset.category = selected.id;
  grid.querySelectorAll("[data-story-category]").forEach(button => {
    const active = button.dataset.storyCategory === selected.id;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  setOptions(document.getElementById("storySituation"), selected.situations || []);
  setOptions(document.getElementById("storyTarget"), story.catalog?.targets || []);
  document.getElementById("storyEventButton").textContent = "버튼으로 이벤트 만들기";
  document.getElementById("storyChatButton").textContent = providerView().button;
  refreshStoryInteraction();
}

const MEMORY_KIND_LABELS = {
  story: "장면", conversation: "대화", world_event: "사건", verified_game: "경기",
  publication: "기사·커뮤니티", narrative_prop: "소재", prop_beat: "소재 변화",
  honor: "수상", milestone: "마일스톤",
};

function memoryEmpty(message) {
  return `<div class="story-memory-empty">${escapeHTML(message)}</div>`;
}

function memoryRow(row, extraClass = "") {
  const kind = MEMORY_KIND_LABELS[row.kind] || row.kind || "기억";
  const detail = row.detail ? `<p>${escapeHTML(row.detail)}</p>` : "";
  return `<article class="story-memory-row ${extraClass}"><time>${escapeHTML(row.game_date || "—")}</time><div><strong>${escapeHTML(row.label || "기억")}</strong>${detail}</div><span>${escapeHTML(kind)}</span></article>`;
}

function renderMemoryViews(story) {
  const memory = story.memory_views || {};
  const issues = memory.issues || [];
  const activeIssues = issues.filter(row => row.status === "active").length;
  setText("storyIssueCount", `${activeIssues}개 진행 · ${issues.length - activeIssues}개 종료`);
  const issueList = document.getElementById("storyIssueList");
  issueList.innerHTML = issues.length ? issues.map(row => {
    const active = row.status === "active";
    const status = active ? `${row.remaining_games}/${row.budget_games}경기 남음` : "자동 재등장 종료 · 기록 보존";
    return `<article class="story-issue-row ${active ? "" : "is-expired"}"><div><strong>${escapeHTML(row.label || "세계선 이슈")}</strong><span>${escapeHTML(row.created_on || "—")} 시작 · ${escapeHTML(row.visibility || "private")}</span></div><em>${escapeHTML(status)}</em></article>`;
  }).join("") : memoryEmpty("수명을 지정한 이슈가 없습니다. 비워 두면 기존처럼 별도 만료 없이 보관합니다.");

  setText("storyMemoryGameCount", `검증 경기 ${memory.eligible_game_count || 0}`);
  const windows = memory.windows || [];
  const windowRoot = document.getElementById("storyMemoryWindows");
  windowRoot.innerHTML = windows.length ? windows.map(window => {
    const items = window.items || [];
    const rows = items.length ? items.map(row => memoryRow(row)).join("") : memoryEmpty("이 구간에 남은 기억이 없습니다.");
    const more = window.truncated ? `<small>일부만 표시 · 전체 ${escapeHTML(window.total)}개</small>` : "";
    return `<section class="story-memory-window"><header><strong>${escapeHTML(window.label)}</strong><span>${escapeHTML(window.total || 0)}개</span></header><div>${rows}</div>${more}</section>`;
  }).join("") : memoryEmpty("아직 경기 간격을 계산할 기록이 없습니다.");

  const anchors = memory.anchors || [];
  setText("storyMemoryAnchorCount", `${anchors.length}개`);
  const anchorRoot = document.getElementById("storyMemoryAnchors");
  anchorRoot.innerHTML = anchors.length ? anchors.map(row => memoryRow(row, "is-anchor")).join("") : memoryEmpty("날짜가 확인된 수상·마일스톤이나 고정 기억이 아직 없습니다.");
}

function renderStory(dashboard) {
  const story = dashboard.story || {};
  const current = story.current || {};
  const catalog = story.catalog || {};
  const grid = document.getElementById("storyCategoryGrid");
  const previousCategory = grid.dataset.category;
  const categories = storyCategories(story);
  renderWorldbook(story);
  renderMemoryViews(story);
  const selected = categories.some(row => row.id === previousCategory) ? previousCategory : categories[0]?.id;
  grid.innerHTML = categories.map(row => `<button class="story-category" type="button" data-story-category="${escapeHTML(row.id)}" aria-pressed="${row.id === selected ? "true" : "false"}"><strong>${escapeHTML(row.label)}</strong><span>${escapeHTML(row.description)}</span></button>`).join("");
  selectStoryCategory(selected);
  setOptions(document.getElementById("storyTarget"), catalog.targets || [], document.getElementById("storyTarget")?.value || "teammate");
  setOptions(document.getElementById("storyTone"), catalog.tones || [], document.getElementById("storyTone")?.value || "honest");
  setOptions(document.getElementById("storyVisibility"), catalog.visibility || [], document.getElementById("storyVisibility")?.value || "clubhouse");

  const open = current.status !== "sealed";
  setText("storyDayStatus", open ? `${current.game_date || story.game_date || "오늘"} · 열림` : `${current.game_date || "—"} · 마감`);
  document.getElementById("storyDayStatus").className = `badge ${open ? "badge--verified" : ""}`;
  const turns = current.turns || [];
  setText("storyTurnCount", `${turns.length}개 개입 · 날짜 진행 없음`);
  const timeline = document.getElementById("storyTimeline");
  timeline.innerHTML = turns.length ? [...turns].reverse().map(turn => {
    const input = turn.input || {};
    const scene = turn.scene || {};
    const llm = turn.provenance === "llm_generated_fiction";
    const chat = turn.source === "chat";
    const effects = (turn.effects || []).map(effect => `<span>${escapeHTML(effect.label)}</span>`).join("");
    const attention = scene.attention || {};
    const attentionBadge = attention.label
      ? `<span class="attention-pill">${escapeHTML(attention.label)} ${escapeHTML(attention.score ?? "—")}</span>`
      : `<span class="attention-pill">이전 버전 장면</span>`;
    const reactions = turn.reactions || {};
    const foreignCount = (reactions.foreign || []).length;
    const reactionCounts = `${(reactions.boards || []).length} 게시판 · ${(reactions.media || []).length} 기사 · ${(reactions.waves || []).length}개 파동${foreignCount ? ` · 해외 ${foreignCount}` : ""}`;
    const waves = (reactions.waves || []).map(wave => `<span>${escapeHTML(wave.circle)}</span>`).join("");
    const followups = (turn.followups || []).map(row => `<button type="button" class="story-followup" data-story-followup="${escapeHTML(row.label)}" data-interaction-id="${escapeHTML(row.interaction_id || "")}">${escapeHTML(row.label)}</button>`).join("");
    const pill = llm ? (String(turn.model || "").startsWith("gemini:") ? "GEMINI FICTION" : "LOCAL LLM FICTION") : turn.source === "scheduled" ? "기억의 재등장" : chat ? "OFFLINE CHAT" : "BUTTON FICTION";
    return `<article class="story-turn card">
      <div class="story-turn__rail"><b>${escapeHTML(turn.sequence)}</b><span></span></div>
      <div class="story-turn__body">
        <header><div><span class="source-pill ${llm || chat ? "source-pill--creative" : "source-pill--manual"}">${pill}</span><span>${escapeHTML(scene.visibility || "")}</span>${attentionBadge}</div><time>${escapeHTML(turn.created_at || "")}</time></header>
        <h3>${escapeHTML(scene.title || "세계선 개입")}</h3>
        <div class="story-turn__input">${escapeHTML(input.category_label || "")} · ${escapeHTML(input.situation_label || "")}${input.user_text ? ` · “${escapeHTML(input.user_text)}”` : ""}</div>
        <div class="story-turn__prose">${safeMarkdown(scene.response || scene.summary || "")}</div>
        <div class="story-turn__fact"><strong>검증 사실</strong><span>${escapeHTML(scene.verified_context || "")}</span></div>
        <div class="story-reaction-summary"><strong>주변 파장</strong><span>${escapeHTML(reactionCounts)}</span></div>
        ${waves ? `<div class="story-wave-strip">${waves}</div>` : ""}
        ${effects ? `<div class="story-effects">${effects}</div>` : ""}
        ${followups ? `<div class="story-followups">${followups}</div>` : ""}
      </div>
    </article>`;
  }).join("") : `<div class="story-empty card"><strong>아직 오늘 만든 장면이 없습니다.</strong><span>카테고리를 고르고 버튼을 누르면 첫 장면이 즉시 저장됩니다.</span></div>`;

  const provider = providerView(dashboard);
  const chatButton = document.getElementById("storyChatButton");
  // Chat is renderer-neutral: the deterministic engine answers without an LLM.
  chatButton.disabled = !open;
  chatButton.textContent = provider.button;
  document.getElementById("storyEventButton").disabled = !open;
  setText("storyModeHint", provider.hint);
  renderRelink(dashboard);
  renderConversation(dashboard);
  refreshStoryInteraction();
}

function renderRelink(dashboard) {
  const notice = document.getElementById("storyRelink");
  if (!notice) return;
  const binding = dashboard.world?.binding || {};
  const pending = binding.state === "relink_pending";
  notice.classList.toggle("is-hidden", !pending);
  if (!pending) return;
  setText("storyRelinkText", "같은 선수의 더 나중 세이브가 읽혔습니다. 다시 연결하기 전까지 기록·검색·내보내기는 가능하지만 새 사건은 만들 수 없습니다.");
  notice.dataset.universeId = binding.universe_id || "";
}

const CHOICE_ACTS_WITH_TEXT = new Set();

function renderConversation(dashboard) {
  const list = document.getElementById("conversationList");
  const props = document.getElementById("storyPropsList");
  if (!list || !props) return;
  const story = dashboard.story || {};
  const turns = [...(story.conversation || [])].reverse();
  const open = story.current?.status !== "sealed";
  setText("conversationCount", `${turns.length}개 대화 · 총 ${story.conversation_total ?? turns.length}회`);
  list.innerHTML = turns.length ? turns.map(turn => {
    const understanding = turn.understanding || {};
    const persona = turn.reply_persona ? `<span class="conversation-turn__persona">${escapeHTML(turn.reply_persona_label || turn.reply_persona)}</span>` : "";
    const modeClass = turn.committed_event_id ? "is-committed" : turn.mode === "사건 제안" ? "is-proposal" : "";
    const commits = { ...(turn.committed_proposals || {}) };
    if (turn.committed_event_id && turn.committed_proposal_id) commits[turn.committed_proposal_id] ||= turn.committed_event_id;
    const unidentifiedCommit = turn.committed_event_id && !Object.keys(commits).length;
    const proposals = (turn.proposed_events || []).map(row => {
      const committed = Boolean(commits[row.proposal_id]);
      const interaction = row.source === "star_interaction" ? (story.interactions || []).find(item => item.interaction_id === row.interaction_id) : null;
      const stale = row.source === "star_interaction" && (row.beat === "start" ? Boolean(interaction) : interaction?.revision !== row.expected_revision);
      const label = `${escapeHTML(row.label || row.type)}${row.target_label ? ` · ${escapeHTML(row.target_label)}` : ""}${row.visibility ? ` · ${escapeHTML(VISIBILITY_LABELS[row.visibility] || row.visibility)}` : ""}`;
      if (committed || unidentifiedCommit) return `<span class="proposal-chip ${committed ? "is-committed" : ""}">${label}${committed ? " · 기록됨" : ""}</span>`;
      return `<button type="button" class="proposal-button" data-confirm-turn="${escapeHTML(turn.turn_id)}" data-confirm-proposal="${escapeHTML(row.proposal_id)}" ${open && !stale ? "" : "disabled"}>${stale ? "후속 대화로 변경됨" : "오늘의 기록으로 확정"} · ${label}</button>`;
    }).join("");
    const choices = (turn.choices || []).map(row => `<button type="button" class="story-followup" data-story-followup="${escapeHTML(row.label)}" data-interaction-id="${escapeHTML(row.interaction_id || "")}">${escapeHTML(row.label)}</button>`).join("");
    const propChange = turn.prop_change?.message ? `<div class="conversation-turn__prop">${escapeHTML(turn.prop_change.message)}</div>` : "";
    return `<article class="conversation-turn card ${modeClass}">
      <div class="conversation-turn__user"><span>${escapeHTML(turn.sequence)}</span><p>${escapeHTML(turn.user_text || "")}</p><em>${escapeHTML(understanding.primary_act || "—")}${understanding.target ? ` → ${escapeHTML(understanding.target)}` : ""} · ${escapeHTML(turn.mode || "")}</em></div>
      <div class="conversation-turn__reply">${persona}<div class="conversation-turn__prose">${safeMarkdown(turn.reply_text || "")}</div>${propChange}${proposals ? `<div class="proposal-row">${proposals}</div>` : ""}${choices ? `<div class="story-followups">${choices}</div>` : ""}</div>
    </article>`;
  }).join("") : `<div class="story-empty card"><strong>아직 오늘 나눈 대화가 없습니다.</strong><span>하고 싶은 말을 적고 ‘대화로 이어가기’를 누르면 LLM 없이도 답이 옵니다. 사건은 확정 버튼으로만 기록됩니다.</span></div>`;
  const chips = [];
  for (const row of story.props || []) {
    const lifetime = row.lifetime;
    const expired = lifetime?.status === "expired";
    const lifetimeLabel = lifetime ? (expired ? " · 자동 재등장 종료" : ` · ${escapeHTML(lifetime.remaining_games)}/${escapeHTML(lifetime.budget_games)}경기`) : "";
    chips.push(`<span class="story-chip ${row.state === "retired" || expired ? "is-muted" : ""}" title="${escapeHTML(row.origin_date || "")}부터 · ${escapeHTML(row.visibility || "")}">${escapeHTML(row.name)} · ${escapeHTML(PROP_STATE_LABELS[row.state] || row.state)}${lifetimeLabel}</span>`);
  }
  for (const row of story.threads || []) {
    if (["resolved", "remembered"].includes(row.state)) continue;
    chips.push(`<span class="story-chip story-chip--thread" title="${escapeHTML(row.opened_on || "")}부터">${escapeHTML(row.label)} · ${escapeHTML(THREAD_STATE_LABELS[row.state] || row.state)}</span>`);
  }
  props.innerHTML = chips.length ? `<span class="story-props__label">이어지는 소재·이야기</span>${chips.join("")}` : "";
}

const VISIBILITY_LABELS = { private: "비공개", clubhouse: "팀 내부", club: "구단", local: "지역", national: "전국", international: "해외" };
const PROP_STATE_LABELS = { seed: "씨앗", establish: "자리 잡음", callback: "다시 등장", variation: "변주", escalation: "확대", reversal: "반전", payoff: "매듭", dormant: "잠잠", rediscovered: "재발견", retired: "정리됨" };
const THREAD_STATE_LABELS = { seeded: "씨앗", noticed: "감지", developing: "전개", pressure: "압박", decision: "결정", consequence: "여파", dormant: "잠잠", resurfaced: "재부상", resolved: "해결", remembered: "기억", contradicted: "충돌", corrected: "정정" };

export function renderPreservedUniverses(library, targetId = "preservedList") {
  const target = document.getElementById(targetId);
  if (!target) return;
  // Both the empty state and normal load settings expose the same library.
  // Opening these records never switches the live save or grants writes.
  const rows = [...(library?.universes || [])].sort((a, b) => Number(Boolean(a.capabilities?.read_only)) - Number(Boolean(b.capabilities?.read_only)));
  if (!rows.length) { target.innerHTML = ""; return; }
  target.innerHTML = `<div class="preserved-list__head"><strong>보존된 세계선</strong><span>세이브가 없어도 기록·기사·커뮤니티·서사를 관람 전용으로 읽을 수 있습니다.</span></div>` + rows.map(row => {
    const counts = row.counts || {};
    const highlights = (row.highlights || []).map(text => `<span>${escapeHTML(text)}</span>`).join("");
    return `<article class="preserved-card card" data-universe-card="${escapeHTML(row.universe_id)}">
      <header><div><strong>${escapeHTML(row.player_name || "이름 미확인")}</strong><span>${escapeHTML(row.team || "팀 미확인")} · 슬롯 ${escapeHTML(row.slot ?? "—")} · ${escapeHTML(row.career_year ? `${row.career_year}년차` : "연차 미확인")}</span></div><span class="badge">${escapeHTML(row.availability_badge || "보존·관람 전용")}</span></header>
      <div class="preserved-card__meta"><span>마지막 날짜 ${escapeHTML(row.last_verified_date || dateText(row.last_date))}</span><span>${escapeHTML(row.preservation_label || "")}</span><span>기록 ${escapeHTML(counts.verified_events ?? 0)} · 서사 ${escapeHTML(counts.story_turns ?? 0)} · 기사 ${escapeHTML(counts.articles ?? 0)} · 게시판 ${escapeHTML(counts.community ?? 0)}</span></div>
      ${highlights ? `<div class="preserved-card__highlights">${highlights}</div>` : ""}
      <div class="preserved-card__actions"><button type="button" class="button button--secondary button--compact" data-museum-universe="${escapeHTML(row.universe_id)}">불러와서 보기</button></div>
      <div class="preserved-card__dates" data-museum-dates="${escapeHTML(row.universe_id)}" tabindex="-1" role="region" aria-label="${escapeHTML(row.player_name || "선수")} 날짜별 보관 기록"></div>
    </article>`;
  }).join("");
}

export function renderMuseumDates(universeId, history) {
  const targets = document.querySelectorAll(`[data-museum-dates="${CSS.escape(universeId)}"]`);
  const rows = history?.history || [];
  const content = rows.length ? `<span class="preserved-card__label">날짜별 역사 · ${rows.length}일 · 관람 전용</span>${rows.map(row => `<button type="button" class="story-followup" data-museum-capsule="${escapeHTML(universeId)}" data-museum-date="${escapeHTML(row.game_date)}">${escapeHTML(row.game_date)} · ${escapeHTML(row.headline || "기록")}</button>`).join("")}` : `<span class="muted">보관된 날짜가 없습니다.</span>`;
  for (const target of targets) {
    target.innerHTML = content + (history?.chronicle?.periods || []).map(row => `<button type="button" class="story-followup" data-period-open="${escapeHTML(row.key)}" data-period-kind="${escapeHTML(row.kind)}" data-period-edition="${escapeHTML(row.edition)}" data-period-universe="${escapeHTML(universeId)}">${escapeHTML(row.key)} · ${{day:"일간",month:"월간",year:"연간"}[row.kind]} 종합 · ${row.edition === "cloud" ? "Gemini" : "로컬"} 판본</button>`).join("");
    target.tabIndex = 0;
  }
}

function currency(value) {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `¥${Math.round(numeric).toLocaleString("ko-KR")}` : "—";
}

const METRIC_EVIDENCE = {
  save_derived: "세이브 계산",
  save_plus_manual: "세이브 + 확인 입력",
  derived: "파생 계산",
  manual_confirmed: "사용자 확인",
  manual_components: "확인 성분 합산",
  batting_runs: "타격 득점 기여",
  baserunning_runs: "주루 득점 기여",
  fielding_runs: "수비 득점 기여",
  positional_runs: "포지션 보정",
  league_adjustment_runs: "리그 보정",
  replacement_runs: "대체선 득점",
  runs_per_win: "1승당 득점",
};

function evidenceText(value) {
  return METRIC_EVIDENCE[value] || value;
}

function metricRows(rows) {
  if (!rows?.length) return `<div class="empty-inline">계산할 지표가 없습니다.</div>`;
  return rows.map(row => `<div class="metric-row ${row.available ? "is-ready" : "is-missing"}">
    <div><strong>${escapeHTML(row.label)}</strong><span>${escapeHTML(row.formula || "")}</span></div>
    <b>${escapeHTML(row.display)}</b>
    ${row.available ? `<em>${escapeHTML(evidenceText(row.provenance || "계산"))}</em>` : `<em>${escapeHTML((row.missing || []).map(evidenceText).join(" · ") || "추가 입력")}</em>`}
  </div>`).join("");
}

function renderHistoryVault(dashboard) {
  const rows = dashboard.history_vault || [];
  const target = document.getElementById("historyVaultList");
  target.innerHTML = rows.length ? rows.map(row => `<button class="vault-row" type="button" data-open-history="${escapeHTML(row.game_date)}">
    <span class="vault-row__date"><b>${escapeHTML(row.game_date)}</b><em class="${row.status === "sealed" ? "is-sealed" : "is-open"}">${row.status === "sealed" ? "마감" : "오늘"}</em></span>
    <span class="vault-row__copy"><strong>${escapeHTML(row.headline)}</strong><small>서사 ${escapeHTML(row.story_turns)} · 기사 보관 ${escapeHTML(row.feed_archives)} · 사건 ${escapeHTML(row.events)}</small></span>
    <span class="vault-row__context">순위 ${escapeHTML(row.standings)} · Top 5 ${escapeHTML(row.top5_stats)}${row.has_value_snapshot ? " · 가치 저장" : ""}</span>
  </button>`).join("") : `<div class="empty-inline">최신 세이브를 확인하거나 오늘의 서사를 만들면 날짜별 캡슐이 시작됩니다.</div>`;
}

function renderLeagueContext(value) {
  const standings = value.standings || [];
  const groups = new Map();
  for (const row of standings) {
    if (!groups.has(row.league)) groups.set(row.league, []);
    groups.get(row.league).push(row);
  }
  document.getElementById("leagueSnapshot").innerHTML = standings.length ? [...groups.entries()].map(([league, rows]) => `<div class="league-block"><strong>${escapeHTML(league)}</strong>${rows.map(row => `<div class="standing-row"><b>${escapeHTML(row.rank)}</b><span>${escapeHTML(row.team)}</span><em>${escapeHTML(row.wins)}승 ${escapeHTML(row.losses)}패 ${escapeHTML(row.ties)}무</em><small>${row.games_back === null || row.games_back === undefined ? "—" : `${escapeHTML(row.games_back)} GB`}</small></div>`).join("")}</div>`).join("") : `<div class="empty-inline">게임 안 순위표를 확인해 입력하면 이 날짜에 고정됩니다.</div>`;

  const boards = value.leaderboards || [];
  document.getElementById("leaderboardSnapshot").innerHTML = boards.length ? boards.map(board => `<div class="leaderboard-block"><header><strong>${escapeHTML(board.label)}</strong><span>${escapeHTML(board.league)} · 내 선수 ${escapeHTML(board.player_rank)}위</span></header>${(board.entries || []).map(row => `<div class="leader-row ${row.is_player ? "is-player" : ""}"><b>${escapeHTML(row.rank)}</b><span>${escapeHTML(row.name)}<small>${escapeHTML(row.team || "")}</small></span><em>${escapeHTML(row.value)}</em></div>`).join("")}</div>`).join("") : `<div class="empty-inline">내 선수가 5위 안에 든 기록을 하나씩 보관할 수 있습니다.</div>`;
}

function renderValueLab(dashboard) {
  const value = dashboard.value_lab || {};
  const index = value.dominance_index || {};
  document.getElementById("dominanceIndex").innerHTML = `<div class="value-index"><div><span>${escapeHTML(index.label || "게임 서사 지배력 지수")}</span><strong>${escapeHTML(index.overall ?? 0)}</strong><em>/100</em></div><p>${escapeHTML(index.method || "")}</p><div class="value-index__parts"><span>타격 ${escapeHTML(index.batting ?? "—")}</span><span>투구 ${escapeHTML(index.pitching ?? "—")}</span><span>WAR 아님</span></div></div>`;
  const saber = value.sabermetrics || {};
  document.getElementById("saberBatting").innerHTML = metricRows(saber.batting || []);
  document.getElementById("saberPitching").innerHTML = metricRows(saber.pitching || []);
  const war = saber.war || {};
  document.getElementById("warMetric").innerHTML = `<div><span class="kicker">WAR GATE</span><strong>${war.available ? escapeHTML(war.display) : "WAR 계산 보류"}</strong><p>${escapeHTML(war.note || "")}</p></div><div class="war-panel__meta"><span>${escapeHTML(war.formula || "")}</span>${war.available ? `<b>${escapeHTML(evidenceText(war.provenance))}</b>` : `<b>필요: ${escapeHTML((war.missing || []).map(evidenceText).join(" · "))}</b>`}</div>`;
  const projection = value.salary_projection || {};
  const salary = document.getElementById("salaryProjection");
  if (!projection.available) {
    salary.innerHTML = `<div class="salary-empty"><strong>현재 연봉을 입력하면 추정 범위를 엽니다.</strong><span>세이버 시장 가치와 게임 내 다음 제안액을 별도로 표시합니다.</span><button class="button button--quiet button--compact" type="button" data-open-value>연봉 입력</button></div>`;
  } else {
    salary.innerHTML = `<div class="salary-hero"><span>다음 계약 중앙 추정</span><strong>${currency(projection.projected_yen)}</strong><em>${currency(projection.low_yen)} — ${currency(projection.high_yen)}</em></div>
      <div class="salary-facts"><div><span>현재 연봉</span><b>${currency(projection.current_yen)}</b></div><div><span>신뢰도</span><b>${escapeHTML(projection.confidence)}</b></div><div><span>Top 5</span><b>${escapeHTML(projection.top5_count)}개</b></div><div><span>보정 이력</span><b>${escapeHTML(projection.calibration_seasons)}시즌</b></div></div>
      <div class="negotiation-band">${(projection.negotiation_rounds || []).map(row => `<div><b>${escapeHTML(row.round)}차</b><span>${escapeHTML(row.label)}</span><strong>${currency(row.target_yen)}</strong></div>`).join("")}</div>
      <div class="market-compare"><span>세이버 시장 가치</span><strong>${projection.saber_market_value_yen === null ? "WAR·환산값 입력 필요" : currency(projection.saber_market_value_yen)}</strong><small>게임 제안과 별도 계산</small></div>
      <p class="salary-disclaimer">${escapeHTML(projection.disclaimer || "")}</p>`;
  }
  renderLeagueContext(value);
}

const BOARD_SKINS = {
  dc: { mark: "DC", brand: "디시인사이드", channel: "프로야구 갤러리", vote: "개념" },
  fmk: { mark: "fmk", brand: "에펨코리아", channel: "야구 게시판", vote: "추천" },
  mlb: { mark: "M", brand: "MLBPARK", channel: "불펜", vote: "공감" },
};

const SOCIAL_SKINS = {
  x: { mark: "X", brand: "X", stream: "타임라인" },
  threads: { mark: "@", brand: "Threads", stream: "대화" },
  instagram: { mark: "◎", brand: "Instagram", stream: "텍스트 캡션" },
  facebook: { mark: "f", brand: "Facebook", stream: "야구 그룹" },
  "japan-translation": { mark: "日", brand: "일본 현지 반응", stream: "한국어 번역" },
  "global-translation": { mark: "地", brand: "글로벌 야구 반응", stream: "한국어 번역" },
};

function surfaceCode(value, registry) {
  const key = String(value || "");
  return Object.hasOwn(registry, key) ? key : "generic";
}

function expressionLabel(row) {
  return ({ gemini: "Gemini 작성", local_llm: "로컬 LLM 작성", builtin: "내장 엔진", mixed: "혼합 작성" })[row.expression_renderer] || "";
}

function expressionChip(row) {
  const label = expressionLabel(row);
  return label ? `<span class="reading-writer">${escapeHTML(label)}</span>` : "";
}

function splitBoardThread(board) {
  const comments = board.comments || [];
  // Older capsules encode the opener as the first comment without a flag.
  // An explicit false must never be reclassified, even in the first position.
  const explicit = comments.findIndex(row => row.is_opener === true);
  const index = explicit >= 0 ? explicit : comments.length && comments[0].is_opener !== false ? 0 : -1;
  return { opener: index >= 0 ? comments[index] : null, replies: comments.filter((_, i) => i !== index) };
}

function readingKey(surface, item) {
  return `${surface}:${item.id || JSON.stringify([item.code, item.title, item.author, item.text])}`;
}

function repliesSection(key, label, content) {
  return `<details class="feed-replies" data-reading-key="${escapeHTML(key)}" open><summary><strong>${escapeHTML(label)}</strong><span class="feed-replies__close">접기 −</span><span class="feed-replies__open">펼치기 +</span></summary>${content}</details>`;
}

function replaceReadingCards(target, html) {
  const states = new Map([...target.querySelectorAll("details[data-reading-key]")].map(node => [node.dataset.readingKey, node.open]));
  target.innerHTML = html;
  target.querySelectorAll("details[data-reading-key]").forEach(node => {
    if (states.has(node.dataset.readingKey)) node.open = states.get(node.dataset.readingKey);
  });
}

function readingOptions(id, rows, initialLabel) {
  const select = document.getElementById(id);
  if (!select) return;
  const previous = select.value;
  const unique = new Map(rows);
  const html = `<option value="">${escapeHTML(initialLabel)}</option>` + [...unique].map(([key, label]) => `<option value="${escapeHTML(key)}">${escapeHTML(label)}</option>`).join("");
  if (select.innerHTML !== html) select.innerHTML = html;
  select.value = unique.has(previous) ? previous : "";
}

export function applyReadingFilters(kind) {
  const community = kind === "community";
  const roots = community ? ["socialList", "communityList"] : ["mediaList"];
  const selected = document.getElementById(`${kind}Filter`)?.value || "";
  const query = (document.getElementById(`${kind}Search`)?.value || "").normalize("NFKC").trim().toLocaleLowerCase();
  let visible = 0;
  let total = 0;
  for (const id of roots) {
    const root = document.getElementById(id);
    for (const card of root.querySelectorAll("[data-reading-surface]")) {
      const text = card.textContent.normalize("NFKC").toLocaleLowerCase();
      const match = (!selected || card.dataset.readingSurface === selected) && (!query || text.includes(query));
      card.hidden = !match;
      visible += Number(match);
      total++;
    }
    const cards = [...root.querySelectorAll("[data-reading-surface]")];
    const shown = cards.filter(node => !node.hidden).length;
    if (community) {
      const count = selected || query ? `${shown} / ${cards.length}` : `${cards.length}`;
      setText(id === "socialList" ? "socialCount" : "boardCount", `${count}개 ${id === "socialList" ? "게시물" : "게시판"}`);
      document.getElementById(id === "socialList" ? "socialHeading" : "forumHeading").hidden = shown === 0 && Boolean(selected || query);
      root.hidden = shown === 0 && (Boolean(selected) || Boolean(query));
    }
  }
  setText(`${kind}ReadingCount`, `${visible} / ${total}건 표시`);
  document.getElementById(`${kind}ReadingEmpty`).hidden = visible > 0 || total === 0;
}

function renderBoardCard(board) {
  const code = surfaceCode(board.code, BOARD_SKINS);
  const skin = BOARD_SKINS[code] || { mark: "B", brand: board.board || "커뮤니티", channel: "가상 야구 토론", vote: "추천" };
  const { opener, replies: comments } = splitBoardThread(board);
  const meta = board.thread_meta || {};
  const metadata = [
    meta.posted_at ? `작성 ${escapeHTML(meta.posted_at)}` : "",
    meta.views !== undefined ? `조회 ${escapeHTML(meta.views)}` : "",
    meta.recommendations !== undefined ? `${escapeHTML(skin.vote)} ${escapeHTML(meta.recommendations)}` : "",
  ].filter(Boolean).join(" · ");
  const rows = comments.map((comment, index) => {
    const depth = Math.max(0, Math.min(2, Number(comment.reply_depth ?? 0) || 0));
    const vote = comment.up !== undefined ? `${skin.vote} ${escapeHTML(comment.up)}` : "";
    const down = comment.down ? ` · 비추천 ${escapeHTML(comment.down)}` : "";
    return `<div class="comment comment--reply comment--depth-${depth}">
      <span class="comment__thread-mark" aria-hidden="true">${depth ? "↳" : "·"}</span>
      <span class="comment__author">${escapeHTML(comment.author || "익명")}</span>
      <span class="comment__text">${escapeHTML(comment.text || "")}</span>
      <span class="comment__time">${escapeHTML(comment.posted_at || "")}</span>
      <span class="comment__up">${vote}${down}</span>
    </div>`;
  }).join("");
  const body = opener ? `<div class="board__post"><div class="board__post-author"><strong>${escapeHTML(opener.author || "익명")}</strong><span>작성자 · ${escapeHTML(opener.posted_at || meta.posted_at || "시간 미기록")}</span></div><p>${escapeHTML(opener.text || "")}</p></div>` : "";
  const thread = `<div class="board__columns" aria-hidden="true"><span>닉네임</span><span>내용</span><span>시간</span><span>${escapeHTML(skin.vote)}</span></div><div class="board__thread">${rows}</div>`;
  return `<article class="board board--${code} card" data-reading-surface="board:${escapeHTML(board.code || "generic")}" data-board="${escapeHTML(board.code || "generic")}" data-community-skin="${code}">
    <header class="board__brandbar">
      <div class="board__brand"><span class="board__mark">${escapeHTML(skin.mark)}</span><span><strong>${escapeHTML(skin.brand)}</strong><small>${escapeHTML(skin.channel)} · 창작 반응</small></span></div>
      <div class="reading-meta">${expressionChip(board)}<span class="board__count">글 1 · 댓글 ${escapeHTML(comments.length)}</span></div>
    </header>
    <div class="board__header">
      <span class="board__label">${escapeHTML(meta.category || skin.channel)}</span>
      <h3>${escapeHTML(board.title || "")}</h3>
      <span class="board__topic-meta">${metadata || `댓글 ${escapeHTML(comments.length)}`}</span>
    </div>
    ${body}
    ${comments.length ? repliesSection(readingKey("board", board), `댓글 ${comments.length}개`, thread) : `<div class="empty-inline">아직 댓글이 없습니다.</div>`}
  </article>`;
}

function renderSocialCard(post) {
  const code = surfaceCode(post.code, SOCIAL_SKINS);
  const skin = SOCIAL_SKINS[code] || { mark: "S", brand: post.platform || "텍스트 SNS", stream: "피드" };
  const replies = (post.replies || []).map(reply => `<div class="social-reply">
    <span class="social-reply__rail" aria-hidden="true"></span>
    <strong>${escapeHTML(reply.author || "익명")}</strong>
    <span>${escapeHTML(reply.text || "")}</span>
    <em>${reply.reactions !== undefined ? `♡ ${escapeHTML(reply.reactions)}` : ""}</em>
  </div>`).join("");
  return `<article class="social-card social-card--${code} card" data-reading-surface="social:${escapeHTML(post.code || "social")}" data-social="${escapeHTML(post.code || "social")}" data-social-skin="${code}">
    <header><span class="social-card__avatar" aria-hidden="true">${escapeHTML(skin.mark)}</span><div><span>${escapeHTML(skin.brand)} · ${escapeHTML(skin.stream)}</span><strong>${escapeHTML(post.author || "가상 계정")}</strong></div><em>${post.reactions !== undefined ? `♡ ${escapeHTML(post.reactions)}` : ""}</em></header>
    <p>${escapeHTML(post.text || "")}</p>
    <div class="social-card__meta">${expressionChip(post)}<span>창작 타임라인 · 반응 수는 가상</span></div>
    ${replies ? repliesSection(readingKey("social", post), `답글 ${post.replies.length}개`, `<div class="social-replies">${replies}</div>`) : ""}
  </article>`;
}

function archivedFeedContent(feed, emptyMessage = "보관된 반응이 없습니다.") {
  const media = feed.media || [];
  const boards = feed.boards || [];
  const social = feed.social || [];
  const articles = media.map(article => {
    const paragraphs = Array.isArray(article.body) ? article.body : String(article.body || "").split(/\r?\n\s*\r?\n/).filter(Boolean);
    return `<article class="article-card card"><div class="article-card__source"><span>${escapeHTML(article.flag || "")} ${escapeHTML(article.outlet || "")}</span><span>ARCHIVED FICTION</span></div><h3>${escapeHTML(article.title)}</h3><div class="article-card__sub">${escapeHTML(article.sub || "")}</div>${paragraphs.map(paragraph => `<p>${escapeHTML(paragraph)}</p>`).join("")}</article>`;
  }).join("");
  const communities = boards.map(renderBoardCard).join("");
  const socials = social.map(renderSocialCard).join("");
  return articles + socials + communities || `<div class="empty-inline card">${escapeHTML(emptyMessage)}</div>`;
}

function directorArchiveInput(turn, capsule) {
  const row = capsule?.story_desk?.turns?.find(item => item.id === turn.id);
  if (!row) return "";
  const world = encodeURIComponent(capsule.story_desk.origin.world_id);
  const images = (row.images || []).map(img => {
    const url = `/api/v1/universes/${world}/story-image/${encodeURIComponent(img.file)}`;
    return `<a href="${url}" target="_blank" rel="noopener noreferrer"><img src="${url}" alt="${escapeHTML(img.name)}" loading="lazy"></a>`;
  }).join("");
  return `<div class="director-user"><span>내가 이끈 장면 · 창작 기록</span><p>${escapeHTML(row.text)}</p></div>${images ? `<div class="director-stored-images">${images}</div>` : ""}`;
}

export function renderHistoryCapsule(capsule) {
  const target = document.getElementById("historyDialogBody");
  const frozen = capsule?.verified_snapshot || {};
  const stats = frozen.stats || {};
  const story = capsule?.story || {};
  const context = capsule?.league_and_value || {};
  const feed = capsule?.combined_feed || {};
  const turns = story.turns || [];
  const events = capsule?.verified_events || [];
  const milestones = capsule?.milestones || [];
  const playerContext = capsule?.personal_context?.items || [];
  const counterparts = capsule?.counterparts || [];
  const memory = capsule?.memory_views || {};
  const memoryWindows = memory.windows || [];
  const memoryIssues = memory.issues || [];
  const memoryWindowHtml = memoryWindows.map(window => `<section class="capsule-memory-window"><header><strong>${escapeHTML(window.label)}</strong><span>${escapeHTML(window.total || 0)}개</span></header>${(window.items || []).map(row => memoryRow(row)).join("") || memoryEmpty("이 구간에 기억이 없습니다.")}</section>`).join("");
  const memoryIssueHtml = memoryIssues.map(row => `<span class="capsule-memory-issue ${row.status === "expired" ? "is-expired" : ""}">${escapeHTML(row.label)} · ${row.status === "expired" ? "당시 자동 재등장 종료" : `${escapeHTML(row.remaining_games)}/${escapeHTML(row.budget_games)}경기 남음`}</span>`).join("");
  const deskMemories = (capsule?.story_desk?.memories || []).map(row => `<div class="capsule-event"><b>${escapeHTML(row.label)}</b><span>${escapeHTML(row.detail)} · ${row.remote_allowed ? "Gemini 참고 허용" : "로컬 전용"}</span></div>`).join("");
  target.innerHTML = `<div class="capsule-head"><div><span class="source-pill source-pill--verified">FROZEN SNAPSHOT</span><h3>${escapeHTML(capsule?.game_date || "날짜 미확인")}</h3><p>${capsule?.status === "sealed" ? "마감된 날짜 · 다시 계산하지 않음" : "현재 열린 날짜"}</p></div><div class="capsule-head__stats"><span>${escapeHTML(stats.pit_K ?? "—")} K</span><span>${escapeHTML(stats.bat_HR ?? "—")} HR</span><span>${escapeHTML(stats.bat_H ?? "—")} H</span><span>${escapeHTML(stats.pit_W ?? "—")} W</span></div></div>
    <section class="capsule-section"><header><strong>사건·마일스톤</strong><span>${events.length + milestones.length}개</span></header>${events.map(row => `<div class="capsule-event"><b>${escapeHTML(EVENT_LABELS[row.kind] || row.kind)}</b><span>${escapeHTML((row.game_lines || []).join(" · ") || "검증 스냅샷")}</span></div>`).join("")}${milestones.map(row => `<div class="capsule-event"><b>🏆 ${escapeHTML(row.label || row.title)}</b><span>${escapeHTML(row.note || "달성일 보관")}</span></div>`).join("") || (!events.length ? `<div class="empty-inline">보관된 사건이 없습니다.</div>` : "")}</section>
    <section class="capsule-section"><header><strong>당시 선수 설정·주변 인물</strong><span>설정 ${playerContext.length} · 인물 ${counterparts.length}</span></header>${playerContext.map(row => `<div class="capsule-event"><b>${escapeHTML(row.label)}</b><span>${escapeHTML(row.detail)} · ${escapeHTML(row.visibility_label || row.visibility || "범위 미기록")}${row.status === "retired" ? " · 당시 보관됨" : ""}</span></div>`).join("")}${counterparts.map(row => `<div class="capsule-event"><b>${escapeHTML(row.label)}</b><span>${escapeHTML((row.role_labels || []).join("·") || "역할 미기록")}${row.note ? ` · ${escapeHTML(row.note)}` : ""}${row.status === "retired" ? " · 당시 보관됨" : ""}</span></div>`).join("") || (!playerContext.length ? `<div class="empty-inline">당시 기록된 선수 설정이나 주변 인물이 없습니다.</div>` : "")}</section>
    <section class="capsule-section"><header><strong>당시의 기억 렌즈</strong><span>검증 경기 ${escapeHTML(memory.eligible_game_count || 0)} · 이슈 ${escapeHTML(memoryIssues.length)}</span></header>${memoryIssueHtml ? `<div class="capsule-memory-issues">${memoryIssueHtml}</div>` : ""}<div class="capsule-memory-grid">${memoryWindowHtml || memoryEmpty("당시 기준으로 투영할 기억이 없습니다.")}</div></section>
    ${deskMemories ? `<section class="capsule-section"><header><strong>당시 서사 작업실 기억</strong><span>인물·장소·취향·복선</span></header>${deskMemories}</section>` : ""}
    <section class="capsule-section"><header><strong>그날의 서사</strong><span>${turns.length}개 장면</span></header>${turns.map(turn => `<article class="capsule-story"><span>${escapeHTML(turn.sequence)} · ${turn.provenance === "llm_generated_fiction" ? (String(turn.model || "").startsWith("gemini:") ? "GEMINI" : "LOCAL LLM") : turn.source === "chat" ? "OFFLINE CHAT" : "BUTTON"}</span><h4>${escapeHTML(turn.scene?.title || "세계선 개입")}</h4>${directorArchiveInput(turn, capsule)}<div>${safeMarkdown(turn.scene?.response || "")}</div></article>`).join("") || `<div class="empty-inline">그날 만든 서사가 없습니다.</div>`}</section>
    <section class="capsule-section"><header><strong>당시 리그·Top 5</strong><span>순위 ${escapeHTML((context.standings || []).length)} · 기록 ${escapeHTML((context.leaderboards || []).length)}</span></header><div class="capsule-context">${(context.standings || []).map(row => `<span><b>${escapeHTML(row.rank)}위</b> ${escapeHTML(row.team)} · ${escapeHTML(row.wins)}-${escapeHTML(row.losses)}</span>`).join("") || `<span class="muted">입력된 순위표 없음</span>`}</div>${(context.leaderboards || []).map(board => `<div class="capsule-board"><strong>${escapeHTML(board.label)} · 내 선수 ${escapeHTML(board.player_rank)}위</strong>${(board.entries || []).map(row => `<span>${escapeHTML(row.rank)}. ${escapeHTML(row.name)} · ${escapeHTML(row.value)}</span>`).join("")}</div>`).join("")}</section>
    <section class="capsule-section"><header><strong>당시 가치 계산</strong><span>${escapeHTML(context.salary_projection?.confidence || "입력 없음")}</span></header><div class="capsule-metrics">${metricRows([...(context.sabermetrics?.batting || []), ...(context.sabermetrics?.pitching || []), ...(context.sabermetrics?.war ? [context.sabermetrics.war] : [])])}</div>${context.salary_projection?.available ? `<div class="capsule-salary"><span>다음 연봉 추정</span><strong>${currency(context.salary_projection.projected_yen)}</strong><em>${currency(context.salary_projection.low_yen)} — ${currency(context.salary_projection.high_yen)}</em></div>` : `<div class="empty-inline">당시 연봉 입력 없음</div>`}</section>
    <section class="capsule-section"><header><strong>당시 기사·커뮤니티·SNS</strong><span>${escapeHTML((feed.media || []).length)} 기사 · ${escapeHTML((feed.boards || []).length)} 게시판 · ${escapeHTML((feed.social || []).length)} SNS</span></header><div class="archive-content">${archivedFeedContent(feed, "그날 보관된 반응 없음")}</div></section>`;
  if (capsule?.chronicle?.length) target.insertAdjacentHTML("beforeend", `<section class="capsule-section"><header><strong>보관된 데일리 종합</strong><span>${capsule.chronicle.length}개 판본 · 소급 종합 포함</span></header>${capsule.chronicle.map(row => `<article class="capsule-story"><span>${escapeHTML(row.model)} · ${escapeHTML(row.at)}</span><div>${safeMarkdown(row.text)}</div></article>`).join("")}</section>`);
}

export function renderArchivedFeed(archive) {
  const target = document.getElementById("archiveDialogBody");
  const feed = archive?.feed || {};
  target.innerHTML = `<div class="archive-meta"><strong>${escapeHTML(archive?.game_date || "날짜 미확인")}</strong><span>이 화면은 당시 생성된 반응을 다시 생성하지 않고 그대로 읽습니다.</span></div>
    <div class="archive-content">${archivedFeedContent(feed)}</div>`;
}

function renderCommunity(feed) {
  const target = document.getElementById("communityList");
  const boards = feed?.boards || [];
  const social = feed?.social || [];
  setText("communityResultNote", expressionSummary([...boards, ...social]));
  const bundle = feed?.reaction_bundle || {};
  const rendererLabels = {
    deterministic: "내장 엔진",
    local_llm_expression: "로컬 LLM",
    gemini_expression: "Gemini",
    mixed_expression: "혼합 엔진",
  };
  const communityRenderer = bundle.surface_sources?.community?.renderer || bundle.renderer;
  setText("feedProviderBadge", rendererLabels[communityRenderer] || "내장 엔진");
  setText("socialCount", `${social.length}개 게시물`);
  setText("boardCount", `${boards.length}개 게시판`);
  const socialTarget = document.getElementById("socialList");
  readingOptions("communityFilter", [
    ...boards.map(row => [`board:${row.code || "generic"}`, BOARD_SKINS[row.code]?.brand || row.board || "게시판"]),
    ...social.map(row => [`social:${row.code || "social"}`, SOCIAL_SKINS[row.code]?.brand || row.platform || "SNS"]),
  ], "모든 게시판·SNS");
  setText("communityStyleNote", `반응 강도 ${currentDashboard?.config?.heat ?? "—"}/10 · 언어 수위 ${currentDashboard?.config?.community_language_level ?? 2}/5 · 다음 생성부터 적용 · 아래는 저장된 반응`);
  if (!social.length) {
    socialTarget.innerHTML = `<div class="empty-inline card">현재 선수의 주목도에서는 공개 SNS 반응이 발생하지 않았습니다.</div>`;
  } else {
    replaceReadingCards(socialTarget, social.map(renderSocialCard).join(""));
  }
  if (!boards.length) {
    target.innerHTML = `<div class="narrative narrative--empty card"><div><strong>생성된 커뮤니티 피드가 없습니다.</strong><p>최신 세이브 확인을 실행하면 검증 기록 기반 반응이 만들어집니다.</p></div></div>`;
    applyReadingFilters("community");
    return;
  }
  replaceReadingCards(target, boards.map(renderBoardCard).join(""));
  applyReadingFilters("community");
}

function expressionSummary(rows) {
  if (!rows.some(row => row.expression_renderer)) return "";
  const counts = new Map();
  for (const row of rows) {
    const label = { gemini: "Gemini", local_llm: "로컬 LLM", builtin: "내장 엔진", mixed: "혼합 작성" }[row.expression_renderer || "builtin"] || "내장 엔진";
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  return `저장된 결과 · ${[...counts].map(([label, count]) => `${label} ${count}건`).join(" · ")}`;
}

function renderMedia(feed) {
  const target = document.getElementById("mediaList");
  const media = feed?.media || [];
  readingOptions("mediaFilter", media.map(row => [row.outlet || "", row.outlet || "매체 미기록"]), "모든 언론 매체");
  setText("articleResultNote", expressionSummary(media));
  const bundle = feed?.reaction_bundle || {};
  const rendererLabels = {
    deterministic: "내장 엔진",
    local_llm_expression: "로컬 LLM",
    gemini_expression: "Gemini",
    mixed_expression: "혼합 엔진",
  };
  const articleRenderer = bundle.surface_sources?.articles?.renderer || bundle.renderer;
  setText("articleProviderBadge", rendererLabels[articleRenderer] || "내장 엔진");
  if (!media.length) {
    target.innerHTML = `<div class="narrative narrative--empty card"><div><strong>생성된 기사가 없습니다.</strong><p>Quick 모드이거나 아직 기준 피드를 만들지 않았습니다.</p></div></div>`;
    applyReadingFilters("media");
    return;
  }
  target.innerHTML = media.map(article => `
    <article class="article-card card" data-reading-surface="${escapeHTML(article.outlet || "")}">
      <div class="article-card__source"><span>${escapeHTML(article.flag)} ${escapeHTML(article.outlet)}</span><div class="reading-meta">${expressionChip(article)}<span>창작 기사</span></div></div>
      <h3>${escapeHTML(article.title)}</h3><div class="article-card__sub">${escapeHTML(article.sub)}</div>
      ${(article.body || []).map(paragraph => `<p>${escapeHTML(paragraph)}</p>`).join("")}
    </article>`).join("");
  applyReadingFilters("media");
}

function inlineMarkdown(value) {
  return escapeHTML(value)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+?)\*(?=[\s).,!?]|$)/g, "$1<em>$2</em>")
    .replace(/(^|[\s(])_([^_\n]+?)_(?=[\s).,!?]|$)/g, "$1<em>$2</em>");
}

function normalizeReadableMarkdown(markdown) {
  let text = String(markdown || "").replace(/\r\n?/g, "\n");
  // Mirror prose_format: preserve literals; repair only mechanical typography.
  text = text.split(/(```[\s\S]*?```|`[^`\n]+`|https?:\/\/[^\s<>]+)/g).map((part, index) => index % 2 ? part : part
    .replace(/[\u00a0\u202f\u3000]/g, " ").replace(/[\u200b\ufeff]/g, "")
    .replace(/([가-힣][.!?。！？])(?=[가-힣])/g, "$1 ")
    .replace(/([.!?。！？]["'”’])(?=[가-힣])/g, "$1 ").replace(/[ \t]{2,}/g, " ")).join("");
  text = text.replace(/([^\s])\s+(?=(?:\*\*|__)\d{1,2}[.)]\s)/g, "$1\n\n");
  text = text.replace(/([^\s])(?=(?:\*\*|__)\d{1,2}[.)]\s)/g, "$1\n\n");
  text = text.replace(/([^#\s])[\t ]+(?=#{1,3}[\t ]+[^#\s])/g, "$1\n\n");
  text = text.replace(/([^#\s])(?=#{1,3}[\t ]+[^#\s])/g, "$1\n\n");
  const titleTerminals = [
    "침묵", "시선", "메아리", "논쟁", "화면", "과열", "기대", "경계", "현상", "여운",
    "반응", "파장", "질문", "관심", "열기", "준비", "복선", "긴장", "관점", "평가",
    "기록", "이야기", "목소리", "분위기", "충격", "열광", "균열", "여파", "초점",
    "분석", "결론", "선택", "결심", "고민", "대화", "약속", "하루", "밤", "아침",
    "루틴", "훈련", "휴식", "승부", "장면", "순간", "변화", "역사", "무게", "중심",
  ];
  const connectiveEndings = ["의", "와", "과", "을", "를", "로", "으로", "향한", "대한", "없는", "있는", "않는", "보이지", "뜨거운", "차가운", "고요한", "조용한", "번역된", "이어진", "남은", "새로운"];
  const bodyStartEndings = ["에서는", "에게서는", "으로부터", "에서", "에게", "에는", "에도", "부터", "까지", "은", "는", "이", "가"];
  const core = value => value.replace(/^[\s"'‘’“”()[\]{}<>《》〈〉「」『』,;:·…—.!?。！？-]+|[\s"'‘’“”()[\]{}<>《》〈〉「」『』,;:·…—.!?。！？-]+$/g, "");
  const splitAtx = payload => {
    const value = payload.trim();
    const explicit = value.match(/^\[([^\]\n]{1,80})\](?:\s+(.*))?$/)
      || value.match(/^(?:\*\*|__)(.{1,80}?)(?:\*\*|__)(?:\s+(.*))?$/);
    if (explicit) return [explicit[1].trim(), String(explicit[2] || "").trim()];
    if (value.length < 36 || !/[.!?。！？]/.test(value)) return [value, ""];
    const words = [...value.matchAll(/\S+/g)];
    if (words.length < 6) return [value, ""];
    let boundary = null;
    for (let index = 1; index < Math.min(8, words.length - 3); index++) {
      const token = core(words[index][0]);
      if (token && titleTerminals.some(ending => token.endsWith(ending))) {
        boundary = words[index].index + words[index][0].length;
        break;
      }
    }
    if (boundary === null) {
      for (let index = 1; index < Math.min(6, words.length - 3); index++) {
        const token = core(words[index][0]);
        const following = core(words[index + 1][0]);
        if (token && !connectiveEndings.some(ending => token.endsWith(ending)) && bodyStartEndings.some(ending => following.endsWith(ending))) {
          boundary = words[index].index + words[index][0].length;
          break;
        }
      }
    }
    if (boundary === null) {
      const word = words[Math.min(3, words.length - 4)];
      boundary = word.index + word[0].length;
    }
    return [value.slice(0, boundary).trim(), value.slice(boundary).trim()];
  };
  const paragraphChunks = line => {
    if (line.length < 180 || /^(>|[-*] )/.test(line)) return [line];
    const sentences = line.split(/(?<=[.!?。！？])[\t ]+|(?<=[.!?。！？]["'”’])[\t ]+/).map(value => value.trim()).filter(Boolean);
    if (sentences.length < 3) return [line];
    const chunks = [];
    let current = [];
    let size = 0;
    for (const sentence of sentences) {
      const projected = size + (current.length ? 1 : 0) + sentence.length;
      if (current.length && (current.length >= 2 || projected > 320)) {
        chunks.push(current.join(" "));
        current = [];
        size = 0;
      }
      current.push(sentence);
      size += (size ? 1 : 0) + sentence.length;
    }
    if (current.length) chunks.push(current.join(" "));
    return chunks;
  };
  const output = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const atx = line.match(/^(#{1,3})[\t ]+(.+)$/);
    if (atx) {
      const [title, remainder] = splitAtx(atx[2]);
      if (output.length && output.at(-1) !== "") output.push("");
      output.push(`${atx[1]} ${title}`);
      if (remainder) output.push("", remainder);
      continue;
    }
    const match = line.match(/^\*\*(\d{1,2}[.)][^\n]*?)\*\*(?:\s+(.*))?$/)
      || line.match(/^__(\d{1,2}[.)][^\n]*?)__(?:\s+(.*))?$/);
    if (match) {
      if (output.length && output.at(-1) !== "") output.push("");
      output.push(`## ${match[1].trim()}`, "");
      if (match[2]?.trim()) output.push(match[2].trim());
    } else {
      paragraphChunks(line).forEach((paragraph, index) => {
        if (index) output.push("");
        output.push(paragraph);
      });
    }
  }
  return output.filter((line, index) => line || (index > 0 && output[index - 1])).join("\n").trim();
}

function safeMarkdown(markdown) {
  const output = [];
  let list = false;
  let quote = false;
  const close = () => {
    if (list) { output.push("</ul>"); list = false; }
    if (quote) { output.push("</blockquote>"); quote = false; }
  };
  for (const raw of normalizeReadableMarkdown(markdown).split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) { close(); continue; }
    if (!line.startsWith(">") && quote) { output.push("</blockquote>"); quote = false; }
    if (!/^[-*] /.test(line) && list) { output.push("</ul>"); list = false; }
    if (line.startsWith("### ")) output.push(`<h3>${inlineMarkdown(line.slice(4))}</h3>`);
    else if (line.startsWith("## ")) output.push(`<h2>${inlineMarkdown(line.slice(3))}</h2>`);
    else if (line.startsWith("# ")) output.push(`<h1>${inlineMarkdown(line.slice(2))}</h1>`);
    else if (/^(-{3,}|\*{3,})$/.test(line)) output.push("<hr>");
    else if (line.startsWith(">")) { if (!quote) { output.push("<blockquote>"); quote = true; } output.push(inlineMarkdown(line.replace(/^>\s?/, ""))); }
    else if (/^[-*] /.test(line)) { if (!list) { output.push("<ul>"); list = true; } output.push(`<li>${inlineMarkdown(line.slice(2))}</li>`); }
    else output.push(`<p>${inlineMarkdown(line)}</p>`);
  }
  close();
  return output.join("");
}

function renderNarrative(markdown) {
  const target = document.getElementById("narrativeContent");
  const provider = providerView();
  const source = currentDashboard?.narrative_source || {};
  const run = currentDashboard?.run || {};
  const model = source.model || run.narrative_model || "";
  const writer = source.provider || (model.startsWith("builtin:") ? "builtin" : model.startsWith("gemini:") ? "gemini" : model ? "local_llm" : "unknown");
  setText("narrativeProviderBadge", markdown ? ({builtin:"내장 엔진 기록",gemini:"GEMINI API",local_llm:"로컬 LLM",unknown:"작성 엔진 미확인"})[writer] || "작성 엔진 미확인" : provider.badge);
  if (!markdown) {
    target.className = "narrative narrative--empty card";
    target.innerHTML = `<div><strong>아직 생성된 서사가 없습니다.</strong><p>설정에서 선택한 서사 엔진으로 검증 기록과 창작 표현을 분리해 생성할 수 있습니다.</p></div>`;
  } else {
    target.className = "narrative card";
    target.innerHTML = safeMarkdown(markdown);
  }
}

function renderDiagnostics(dashboard) {
  const validation = dashboard.validation || {};
  const provenance = dashboard.provenance || {};
  const career = dashboard.career || {};
  const tracking = career.tracking || {};
  const careerSummary = validation.career_summary || {};
  const warnings = validation.warnings || [];
  const cards = [
    ["컨테이너 무결성", validation.container === "verified" ? "통과" : "확인 필요", `${number(validation.verified_chunks, 0)} / ${number(validation.chunk_count, 0)} 청크 HMAC 검증`, validation.container === "verified"],
    ["안정적 파일 읽기", validation.stable_read ? "통과" : "확인 필요", "저장 전후 크기·수정 시각 일치", Boolean(validation.stable_read)],
    ["선수 프로필", validation.identity_method || "—", `${number(validation.identity_candidates, 0)}개 후보 중 후속 기록 검증`, Boolean(validation.identity_method)],
    ["저장 형식", validation.format_signature?.id || "—", validation.format_signature ? `헤더 ${validation.format_signature.header_size} · 시즌 요약 ${validation.format_signature.career_summary_base} / ${validation.format_signature.career_summary_stride} · 프로필 종류 ${(validation.format_signature.profile_record_kinds || []).join(", ") || "—"}` : "형식 서명이 없습니다.", String(validation.format_signature?.career_summary_status || "").startsWith("verified")],
    ["커리어 시즌 요약", String(careerSummary.status || "").startsWith("verified") ? "통과" : "확인 필요", `${number(tracking.previous_seasons_save_verified, 0)}개 과거 시즌 자동 복원 · 고정 간격 ${number(careerSummary.record_stride, "—")}`, String(careerSummary.status || "").startsWith("verified")],
    ["수상 구조", career.award_decoder?.status === "verified" ? "통과" : "이름 추정 안 함", career.award_decoder?.note || "정확한 수상 열거형 구조가 검증되기 전에는 자동 수상명을 만들지 않습니다.", true],
    ["경기 결과", provenance.team_result === "unavailable" ? "미해석" : "검증", "상대·점수·승패를 임의로 단정하지 않음", true],
    ["시각 자료", "힌트 전용", "세이브 검증 수치를 덮어쓰지 않음", true],
    ["경고", warnings.length ? `${warnings.length}개` : "없음", warnings.join(" · ") || "현재 스냅샷에 구조 경고가 없습니다.", warnings.length === 0],
  ];
  document.getElementById("diagnosticsGrid").innerHTML = cards.map(([title, status, copy, good]) => `
    <article class="diagnostic-card card"><div class="diagnostic-card__top"><strong>${escapeHTML(title)}</strong><span class="source-pill ${good ? "source-pill--verified" : "source-pill--hint"}">${escapeHTML(status)}</span></div><p>${escapeHTML(copy)}</p></article>`).join("");
}

export function showLoading(show) {
  document.getElementById("loadingState").classList.toggle("is-hidden", !show);
}

export function showEmpty(message) {
  document.getElementById("loadingState").classList.add("is-hidden");
  document.getElementById("appLayout").classList.add("is-hidden");
  document.getElementById("emptyState").classList.remove("is-hidden");
  setText("emptyMessage", message || "스타플레이어 세이브를 찾지 못했습니다.");
}

export function renderDashboard(dashboard) {
  const nextWorldKey = JSON.stringify([dashboard?.world?.world_id, dashboard?.world?.generation, dashboard?.player?.player_id, dashboard?.config?.save_path]);
  if (readingWorldKey !== nextWorldKey) {
    for (const kind of ["community", "media"]) {
      for (const suffix of ["Filter", "Search"]) {
        const control = document.getElementById(`${kind}${suffix}`);
        if (control) control.value = "";
      }
    }
    for (const id of ["communityList", "socialList"]) {
      document.getElementById(id)?.querySelectorAll("details[data-reading-key]").forEach(node => node.open = true);
    }
    readingWorldKey = nextWorldKey;
  }
  currentDashboard = dashboard;
  setActivityContext(dashboard);
  renderWorkspace(dashboard);
  renderStoryDesk(dashboard, safeMarkdown);
  renderConnectedStory(dashboard, safeMarkdown);
  if (!dashboard || dashboard.empty) {
    showEmpty(dashboard?.error?.message);
    updateHeader(dashboard);
    renderPreservedUniverses(dashboard?.universes);
    return;
  }
  document.getElementById("loadingState").classList.add("is-hidden");
  document.getElementById("emptyState").classList.add("is-hidden");
  document.getElementById("appLayout").classList.remove("is-hidden");
  updateHeader(dashboard);

  const player = dashboard.player || {};
  const date = dashboard.date || {};
  const stats = dashboard.stats || {};
  const world = dashboard.world || {};
  const event = dashboard.event || {};
  const grade = dashboard.career?.player_grade || {};
  setText("versionBadge", dashboard.app?.version || "PREVIEW");
  setText("playerName", player.name || player.display_name || "이름 미확인");
  setText("playerTeam", player.team || "팀 미확인");
  setText("heroDate", `${dateText(date, true)} · SAVE VERIFIED`);
  setText("playerRole", player.pos || ROLE_LABELS[event.role] || "역할 미확인");
  setText("playerGradeBadge", grade.code ? `${grade.code} · ${grade.label}` : "등급 계산 대기");
  const gradeBadge = document.getElementById("playerGradeBadge");
  if (gradeBadge) gradeBadge.title = `${grade.description || ""} · ${grade.score ?? 0}/100`;
  setText("worldLabel", `세계선 ${world.generation || 1}`);
  setText("slotLabel", `슬롯 ${dashboard.config?.save_path ? (player && dashboard.validation ? (dashboard.world?.slot || "00") : "—") : "—"}`);
  setText("heroNoticeText", dashboard.run?.message || eventCopy(event));
  document.getElementById("heroNotice").className = `hero__notice ${event.kind === "NO_CHANGE" ? "is-good" : event.baseline_only ? "is-warn" : ""}`;

  document.getElementById("battingStats").innerHTML = statCells([
    ["AVG", average(stats.bat_AVG), "is-gold"], ["HR", number(stats.bat_HR)], ["RBI", number(stats.bat_RBI)],
    ["H", number(stats.bat_H)], ["AB", number(stats.bat_AB)], ["SB", number(stats.bat_SB)],
  ]);
  document.getElementById("pitchingStats").innerHTML = statCells([
    ["WINS", number(stats.pit_W), "is-accent"], ["IP", number(stats.pit_IP)], ["K", number(stats.pit_K)],
    ["H ALLOWED", number(stats.pit_H)], ["TBF", number(stats.pit_TBF)], ["K/9", number(dashboard.assessment?.k9)],
  ]);
  renderEvent(event);
  renderSpotlight(dashboard);
  renderHistory(dashboard.history || []);
  renderCareer(dashboard);
  renderStory(dashboard);
  renderHistoryVault(dashboard);
  renderValueLab(dashboard);
  renderCommunity(dashboard.feed);
  renderMedia(dashboard.feed);
  renderNarrative(dashboard.narrative);
  renderDiagnostics(dashboard);

  const eventLabel = EVENT_LABELS[event.kind] || event.kind || "대기";
  setText("eventBadge", eventLabel);
  setText("savePath", compactPath(dashboard.config?.save_path));
  setText("sideDate", dateText(date));
  setText("sideWorld", `${world.world_id || "—"} · G${world.generation || 1}`);
  setText("sideChunks", `${number(dashboard.validation?.verified_chunks, 0)} / ${number(dashboard.validation?.chunk_count, 0)}`);
  document.title = `${player.name || "StarPlayer"} · StarPlayer Community Simulator`;
}

function updateHeader(dashboard) {
  const saveOk = Boolean(dashboard && !dashboard.empty && dashboard.config?.save_exists);
  const config = dashboard?.config || {};
  const localReady = Boolean(dashboard?.app?.llm_reachable);
  const connection = config.gemini_usage?.connection || {};
  const geminiConfigured = Boolean(config.gemini_consent && config.gemini_key_present);
  const geminiReady = Boolean(geminiConfigured && connection.status === "verified" && connection.model === config.gemini_model);
  const geminiSelected = config.ai_provider === "gemini";
  const geminiFailed = connection.status === "failed";
  document.getElementById("saveDot").className = `status-dot ${saveOk ? "" : "status-dot--bad"}`;
  setText("saveStatus", saveOk ? "세이브 연결됨" : "세이브 미연결");
  document.getElementById("llmDot").className = `status-dot ${localReady ? "" : "status-dot--muted"}`;
  setText("llmStatus", localReady ? "로컬 LLM 연결됨" : "로컬 LLM 꺼짐");
  document.getElementById("apiDot").className = `status-dot ${geminiFailed ? "status-dot--bad" : geminiSelected && geminiReady ? "" : geminiConfigured ? "status-dot--warn" : "status-dot--muted"}`;
  setText("apiStatus", geminiSelected && geminiReady ? "Gemini API 켜짐" : geminiFailed ? "Gemini 연결 실패" : geminiConfigured ? "Gemini 연결 확인 중" : "Gemini API 꺼짐");
  setText("narrativeButtonLabel", geminiSelected && geminiReady ? "Gemini 서사 생성" : localReady && config.ai_provider === "local_auto" ? "로컬 LLM 서사 생성" : "서사 생성");
  const showLocalOverrides = localReady && config.ai_provider !== "local_auto";
  for (const id of ["localEnrichArticlesButton", "localEnrichCommunityButton", "localNarrativeButton"]) {
    const button = document.getElementById(id);
    if (button) button.classList.toggle("is-hidden", !showLocalOverrides);
  }
  setText(
    "enrichFeedButtonLabel",
    geminiSelected && geminiReady
      ? "Gemini로 전체 함께 만들기"
      : config.ai_provider === "deterministic"
        ? "내장 전체 함께 만들기"
        : localReady
          ? "로컬 LLM으로 전체 함께 만들기"
          : "전체 함께 풍부화",
  );
  setText(
    "enrichArticlesButtonLabel",
    geminiSelected && geminiReady
      ? "Gemini로 기사만 만들기"
      : config.ai_provider === "deterministic"
        ? "내장 기사 다시 만들기"
        : localReady
          ? "로컬 LLM으로 기사만 만들기"
          : "기사만 풍부화",
  );
  setText(
    "enrichCommunityButtonLabel",
    geminiSelected && geminiReady
      ? "Gemini로 커뮤니티·SNS만 만들기"
      : config.ai_provider === "deterministic"
        ? "내장 커뮤니티·SNS 다시 만들기"
        : localReady
          ? "로컬 LLM으로 커뮤니티·SNS만 만들기"
          : "커뮤니티·SNS만 풍부화",
  );
}

export function switchView(name) {
  document.querySelectorAll(".tab").forEach(tab => {
    const active = tab.dataset.view === name;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
    tab.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll(".view").forEach(panel => {
    const active = panel.dataset.panel === name;
    panel.classList.toggle("is-active", active);
    panel.setAttribute("aria-hidden", active ? "false" : "true");
  });
  document.dispatchEvent(new CustomEvent("workspace:mode", {detail:name}));
}

export function renderJob(job) {
  updateJobConsole(job);
}

export function dashboard() { return currentDashboard; }
export { EVENT_LABELS };
