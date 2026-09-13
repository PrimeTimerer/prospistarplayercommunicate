import * as api from "./api.js";
import {initStoryDesk, setDirectorBusy, refreshDirectorCaptures} from "./story-desk.js";
import {initConnectedStory, setConnectedBusy, loadSelectedChronicle} from "./connected-story.js";
import {initWorkspace, showWorkspacePart} from "./workspace.js";
import {initActivityConsole, reportJobConnectionError} from "./activity-console.js";
import {
  applyReadingFilters,
  dashboard,
  renderArchivedFeed,
  renderDashboard,
  renderHistoryCapsule,
  renderJob,
  renderMuseumDates,
  renderPreservedUniverses,
  refreshStoryInteraction,
  selectStoryCategory,
  showEmpty,
  showLoading,
  switchView,
} from "./render.js";

const byId = id => document.getElementById(id);
let activeJobId = null;
let pollTimer = null;
let capturePollTimer = null;
let captureHotkeysActive = false;
let captureMutationActive = false;
let knownCaptureNames = new Set();
let lastCaptureEventAt = null;
let geminiAutoActivationStarted = false;
let localModelPollTimer = null;
let currentAttachmentLlmText = "";

function heatLabel(value) {
  const heat = Math.max(1, Math.min(10, Number(value) || 7));
  if (heat <= 2) return "담백";
  if (heat <= 4) return "순한맛";
  if (heat <= 6) return "보통";
  if (heat <= 8) return "매운맛";
  return "매우 매운맛";
}

function updateHeatOutput(value) {
  const heat = Math.max(1, Math.min(10, Number(value) || 7));
  const label = `${heat} · ${heatLabel(heat)}`;
  byId("heatOutput").textContent = label;
  byId("heatInput").setAttribute("aria-valuetext", label);
  const reactionHeat = byId("attachmentReactionHeat");
  if (reactionHeat) reactionHeat.textContent = `현재 맵기 농도 ${label} · 사실과 반응 수에는 영향 없음`;
}

function languageLevelLabel(value) {
  const level = Math.max(1, Math.min(5, Number(value) || 2));
  return ({
    1: "정중",
    2: "자연스러운 구어",
    3: "거친 커뮤니티",
    4: "상스러운 현실",
    5: "극한 커뮤니티",
  })[level];
}

function updateLanguageLevelOutput(value) {
  const level = Math.max(1, Math.min(5, Number(value) || 2));
  const label = `${level} · ${languageLevelLabel(level)}`;
  byId("languageLevelOutput").textContent = label;
  byId("languageLevelInput").setAttribute("aria-valuetext", label);
  renderLanguagePreview(level);
}

function renderLanguagePreview(level) {
  // Original, fixed examples. Previewing a register must never call a provider.
  const examples = {
    dc: ["상대 팬인데 잘하네요. 인정합니다.", "상대 팬인데 저건 인정ㅋㅋ 우리 타선 힘내자", "와 미쳤네ㅋㅋ 우리 팀 오늘도 빡세겠다", "저걸 또 잡네 ㅅㅂㅋㅋ 우리도 좀 살자", "씨발 저걸 또 잡냐ㅋㅋ 잘하는 건 인정하는데 우리도 좀 살자"],
    fmk: ["상대 팀 팬이지만 좋은 플레이는 인정합니다.", "우리 팀 상대로만 좀 쉬면 안 되냐ㅋㅋ 인정은 한다", "이러면 상대 팬은 개빡치지ㅋㅋ 잘하긴 하네", "우리한테만 이러냐ㅋㅋ 진짜 존나 잘하긴 한다", "존나 잘하네 인정. 근데 우리 팀도 좀 먹고살자ㅋㅋ"],
    mlb: ["좋은 플레이입니다. 상대 팬으로서도 인정할 만합니다.", "잘하는 건 맞죠. 다만 한 장면과 시즌 평가는 나눠야겠습니다.", "인정할 건 인정하죠. 그렇다고 다른 선수까지 내려칠 필요는 없고요.", "잘한다고 했지 비교를 전부 끝내자고 한 적은 없습니다. 기준은 같아야죠.", "인정과 맹신은 다릅니다. 불리할 때만 기준을 바꾸면 비교가 아니라 팬심이죠."],
  };
  for (const [code, rows] of Object.entries(examples)) byId(`languagePreview-${code}`).textContent = rows[level - 1];
}

function toast(message, tone = "") {
  const item = document.createElement("div");
  item.className = `toast ${tone ? `is-${tone}` : ""}`;
  item.textContent = message;
  byId("toastRegion").appendChild(item);
  window.setTimeout(() => item.remove(), 4800);
}

function renderNotificationState(config = dashboard()?.config || {}) {
  const status = byId("notificationStatus");
  if (!("Notification" in window)) {
    status.textContent = "이 실행 환경은 시스템 알림을 지원하지 않습니다. 앱 안쪽 알림은 계속 표시됩니다.";
    return;
  }
  const permission = window.Notification.permission;
  status.textContent = permission === "granted"
    ? (config.notifications_enabled ? "시스템 알림 사용 중 · 작업 완료와 실패를 알려줍니다." : "권한 허용됨 · 설정을 켜면 사용합니다.")
    : permission === "denied"
      ? "Windows 또는 브라우저에서 알림이 차단되어 있습니다. 앱 안쪽 알림만 표시됩니다."
      : "아직 시스템 알림 권한을 요청하지 않았습니다.";
}

function sendSystemNotification(title, body, { force = false, tag = "starmodefeed" } = {}) {
  const config = dashboard()?.config || {};
  if (!force && !config.notifications_enabled) return false;
  if (!("Notification" in window) || window.Notification.permission !== "granted") return false;
  if (!force && document.hasFocus()) return false;
  try {
    const item = new window.Notification(title, {
      body: String(body || ""),
      icon: `${window.location.origin}/assets/starmodefeed-mark.png`,
      tag,
    });
    item.onclick = () => { window.focus(); item.close(); };
    window.setTimeout(() => item.close(), 12000);
    return true;
  } catch {
    return false;
  }
}

async function requestNotificationAccess({ test = false } = {}) {
  if (!("Notification" in window)) {
    renderNotificationState();
    toast("이 실행 환경에서는 시스템 알림을 사용할 수 없습니다.", "error");
    return false;
  }
  let permission = window.Notification.permission;
  if (permission === "default") permission = await window.Notification.requestPermission();
  const allowed = permission === "granted";
  if (allowed) {
    if (test) sendSystemNotification("StarPlayer 알림 시험", "작업 완료와 실패를 이 방식으로 알려드립니다.", { force: true, tag: "starmodefeed-test" });
    toast(test ? "시스템 알림 시험을 보냈습니다." : "시스템 알림 권한을 확인했습니다.", "good");
  } else {
    byId("notificationsEnabledInput").checked = false;
    toast("시스템 알림 권한이 허용되지 않았습니다. 앱 안쪽 알림은 계속 작동합니다.", "error");
  }
  renderNotificationState({ ...(dashboard()?.config || {}), notifications_enabled: byId("notificationsEnabledInput").checked });
  return allowed;
}

function setBusy(busy) {
  setDirectorBusy(busy);
  setConnectedBusy(busy);
  for (const id of ["checkButton", "enrichFeedButton", "enrichArticlesButton", "enrichCommunityButton", "localEnrichArticlesButton", "localEnrichCommunityButton", "narrativeButton", "localNarrativeButton", "captureButton", "burstCaptureButton", "rescanButton"]) {
    const element = byId(id);
    if (element) element.disabled = busy;
  }
}

function applyTheme(theme) {
  const value = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = value;
  try { localStorage.setItem("smf-theme", value); } catch { /* local preference only */ }
}

function initialTheme(configTheme) {
  try { return localStorage.getItem("smf-theme") || configTheme || "dark"; }
  catch { return configTheme || "dark"; }
}

async function loadBootstrap() {
  showLoading(true);
  try {
    const payload = await api.bootstrap();
    applyTheme(initialTheme(payload.dashboard?.config?.theme));
    renderDashboard(payload.dashboard);
    populateSettings(payload.dashboard?.config || {});
    renderCaptureStatus(payload.dashboard?.captures || { items: [] }, false);
    scheduleCapturePoll();
    scheduleLocalModelPoll(payload.dashboard?.app?.local_model || {});
    void autoActivateGemini(payload.dashboard);
    if (payload.job) {
      renderJob(payload.job);
      if (["queued", "running"].includes(payload.job.status)) pollJob(payload.job.id);
    }
  } catch (error) {
    showEmpty(error.message);
    toast(error.message, "error");
  }
}

function currentOptions() {
  const value = dashboard()?.config || {};
  return {
    heat: value.heat,
    community_language_level: value.community_language_level,
    mode: value.mode,
    platforms: value.platforms,
    auto_capture: value.auto_capture,
  };
}

async function startJob(path, options) {
  setBusy(true);
  try {
    const payload = await api.post(path, options || {});
    activeJobId = payload.job.id;
    renderJob(payload.job);
    pollJob(activeJobId);
  } catch (error) {
    if (error.code === "JOB_RUNNING" && error.jobId) {
      pollJob(error.jobId);
      toast("이미 실행 중인 작업을 이어서 표시합니다.");
    } else {
      setBusy(false);
      toast(error.message, "error");
    }
  }
}

async function pollJob(jobId) {
  activeJobId = jobId;
  if (pollTimer) window.clearTimeout(pollTimer);
  let receivedJob = null;
  try {
    const payload = await api.get(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
    const job = payload.job;
    receivedJob = job;
    renderJob(job);
    if (["queued", "running"].includes(job.status)) {
      setBusy(true);
      pollTimer = window.setTimeout(() => pollJob(jobId), 750);
      return;
    }
    activeJobId = null;
    setBusy(false);
    if (job.status === "completed") {
      if (job.result) {
        renderDashboard(job.result);
        populateSettings(job.result.config || {});
        renderCaptureStatus(job.result.captures || { items: [] }, false);
      }
      const completedMessage = job.result?.run?.message || (job.kind === "narrative"
        ? "시네마틱 서사를 완성했습니다."
        : job.kind === "feed_articles"
          ? "기사 작성을 마쳤습니다."
          : job.kind === "feed_community"
            ? "커뮤니티·SNS 작성을 마쳤습니다."
            : job.kind === "feed"
              ? "기사·게시판·SNS 전체 반응을 완성했습니다."
              : "최신 세이브 반영을 마쳤습니다.");
      const providerFailed = Boolean(job.result?.run?.provider_failed);
        const providerFallback = Boolean(job.result?.run?.fallback);
        toast(completedMessage, providerFailed ? "error" : providerFallback ? "info" : "good");
      suggestProtagonistSpellings(job.result);
      sendSystemNotification(
          providerFailed || providerFallback ? "StarPlayer 모델 작업 확인" : "StarPlayer 작업 완료",
        completedMessage,
        { tag: `starmodefeed-job-${job.kind}` },
      );
      if (job.kind === "narrative") showWorkspacePart("narrative", "cinema");
      if (job.kind === "director") {
        const mode = ({article:"media", community:"community"})[job.result?.run?.director_channel] || "narrative";
        showWorkspacePart(mode, mode === "narrative" ? "chat" : "compose");
      }
      if (["feed", "feed_community"].includes(job.kind)) showWorkspacePart("community", "read");
      if (job.kind === "feed_articles") showWorkspacePart("media", "read");
    } else {
      const message = job.error?.message || "작업을 완료하지 못했습니다.";
      toast(message, "error");
      sendSystemNotification("StarPlayer 작업 실패", message, { tag: `starmodefeed-job-${job.kind}` });
    }
    if (job.kind === "chronicle") {
      switchView("chronicle");
      await loadSelectedChronicle();
    }
  } catch (error) {
    if (receivedJob && !["queued", "running"].includes(receivedJob.status)) {
      activeJobId = null;
      setBusy(false);
      toast(`작업 상태는 확인됐지만 결과 화면을 읽지 못했습니다. ${error.message}`, "error");
      return;
    }
    reportJobConnectionError(jobId, error);
    if (error.code === "JOB_NOT_FOUND") {
      activeJobId = null;
      setBusy(false);
      toast("서버에서 이전 작업 정보를 찾지 못했습니다. 보관된 결과를 확인해 주세요.", "error");
      return;
    }
    // Losing the status connection does not mean the server stopped generating.
    pollTimer = window.setTimeout(() => pollJob(jobId), 3000);
  }
}

function usageNumber(value) {
  return new Intl.NumberFormat("ko-KR").format(Math.max(0, Number(value) || 0));
}

function usageTime(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}

function renderGeminiUsage(usage = {}, config = {}) {
  const today = usage.today || {};
  const total = usage.total || {};
  const last = usage.last_operation || null;
  const connection = usage.connection || {};
  const selected = config.ai_provider === "gemini";
  const verified = connection.status === "verified" && connection.model === config.gemini_model;
  const ready = Boolean(config.gemini_consent && config.gemini_key_present && verified);
  const dot = byId("geminiUsageDot");
  dot.className = `status-dot ${connection.status === "failed" ? "status-dot--bad" : selected ? (ready ? "" : "status-dot--warn") : "status-dot--muted"}`;
  byId("geminiTodayCalls").textContent = usageNumber(today.calls);
  byId("geminiTodayTokens").textContent = usageNumber(today.total_tokens);
  byId("geminiTotalCalls").textContent = usageNumber(total.calls);
  byId("geminiTotalTokens").textContent = usageNumber(total.total_tokens);
  byId("geminiUsageDetail").textContent = `입력 ${usageNumber(total.prompt_tokens)} · 출력 ${usageNumber(total.output_tokens)} · 캐시 ${usageNumber(total.cached_tokens)} · 사고 ${usageNumber(total.thoughts_tokens)}`;
  const operationLabels = {
    cinematic_narrative: "장문 서사",
    story_chat: "자유 대화",
    starplayer_expression: "스타플레이어 대화",
    attachment_vision: "이미지 해석",
    reaction_bundle: "기사·게시판·SNS 전체 반응",
    reaction_articles: "기사",
    reaction_community: "커뮤니티·SNS",
  };
  if (last) {
    const state = last.status === "success" ? "완료" : `실패 · ${last.error_code || "REMOTE_ERROR"}`;
    byId("geminiUsageLast").textContent = `마지막 ${operationLabels[last.operation] || "호출"} ${state} · ${usageTime(last.at) || "시각 미기록"}${last.model ? ` · ${last.model}` : ""}`;
  } else if (connection.status === "verified") {
    byId("geminiUsageLast").textContent = `연결 시험 통과 · ${usageTime(connection.tested_at) || "시각 미기록"}${connection.model ? ` · ${connection.model}` : ""}`;
  } else if (connection.status === "failed") {
    byId("geminiUsageLast").textContent = `마지막 연결 시험 실패 · ${usageTime(connection.tested_at) || "시각 미기록"}`;
  } else {
    byId("geminiUsageLast").textContent = "아직 이 앱에서 사용한 기록이 없습니다.";
  }
}

const LOCAL_MODEL_STATE_LABELS = {
  off: "꺼짐 · 수동 시작 대기",
  starting: "모델 불러오는 중",
  owned: "이 앱에서 실행 중",
  external: "외부 서버 연결됨",
  failed: "시작 또는 연결 실패",
};

function renderLocalModelControl(status = {}) {
  const state = status.state || "off";
  const dot = byId("localLlmControlDot");
  dot.className = `status-dot ${state === "owned" || state === "external" ? "" : state === "starting" ? "status-dot--warn" : state === "failed" ? "status-dot--bad" : "status-dot--muted"}`;
  byId("localLlmControlState").textContent = LOCAL_MODEL_STATE_LABELS[state] || state;
  byId("localLlmControlMessage").textContent = status.message || "저장된 경로만으로는 모델을 실행하지 않습니다.";
  const launcher = byId("localLlmLauncherInput").value.trim();
  const busy = state === "starting" || state === "owned";
  byId("localLlmStartButton").disabled = busy || state === "external" || !launcher;
  byId("localLlmStopButton").disabled = !status.can_stop;
}

function scheduleLocalModelPoll(status = {}) {
  if (localModelPollTimer) window.clearTimeout(localModelPollTimer);
  localModelPollTimer = null;
  const settingsOpen = Boolean(byId("settingsDialog")?.open);
  const delay = status.state === "starting" ? 1000 : settingsOpen && status.state === "owned" ? 3000 : 10000;
  localModelPollTimer = window.setTimeout(refreshLocalModelStatus, delay);
}

function applyLocalModelStatus(status = {}) {
  const current = dashboard();
  const previous = current?.app?.local_model || {};
  if (current && (previous.state !== status.state || Boolean(previous.reachable) !== Boolean(status.reachable))) {
    renderDashboard({
      ...current,
      app: { ...current.app, local_model: status, llm_reachable: Boolean(status.reachable) },
    });
  }
  renderLocalModelControl(status);
  scheduleLocalModelPoll(status);
}

async function refreshLocalModelStatus({ notify = false } = {}) {
  try {
    const result = await api.get("/api/v1/providers/local/status");
    applyLocalModelStatus(result.status || {});
    if (notify) toast(result.status?.message || "로컬 LLM 상태를 확인했습니다.", "good");
    return result.status || {};
  } catch (error) {
    renderLocalModelControl({ state: "failed", message: error.message, can_stop: false });
    if (notify) toast(error.message, "error");
    return null;
  }
}

async function startLocalModel() {
  const launcher = byId("localLlmLauncherInput").value.trim();
  if (!launcher) {
    toast("로컬 모델 실행 파일 경로를 입력해 주세요.", "error");
    return;
  }
  if (!window.confirm("지정한 로컬 모델 서버를 지금 시작할까요? 큰 모델은 GPU·메모리를 많이 사용할 수 있으며, 이 앱에서 시작한 서버는 앱을 닫을 때 함께 종료됩니다.")) return;
  const button = byId("localLlmStartButton");
  button.disabled = true;
  try {
    const result = await api.post("/api/v1/providers/local/start", {
      confirmed: true,
      llm_launcher: launcher,
    });
    const current = dashboard();
    if (current) current.config = { ...current.config, llm_launcher: launcher };
    applyLocalModelStatus(result.status || {});
    toast(result.status?.message || "로컬 모델 실행기를 시작했습니다.", "good");
  } catch (error) {
    toast(error.message, "error");
    await refreshLocalModelStatus();
  } finally {
    button.disabled = false;
  }
}

async function stopLocalModel() {
  if (!window.confirm("이 앱에서 시작한 로컬 LLM 프로세스를 종료할까요? 외부에서 켠 서버에는 영향을 주지 않습니다.")) return;
  const button = byId("localLlmStopButton");
  button.disabled = true;
  try {
    const result = await api.post("/api/v1/providers/local/stop", { confirmed: true });
    applyLocalModelStatus(result.status || {});
    toast("이 앱에서 시작한 로컬 LLM을 종료했습니다.", "good");
  } catch (error) {
    toast(error.message, "error");
    await refreshLocalModelStatus();
  } finally {
    button.disabled = false;
  }
}

function renderCommunityCorpus(app = dashboard()?.app || {}) {
  const manifest = app.community_style_database || {};
  const sources = Math.max(0, Number(manifest.source_count) || 0);
  const profiles = Math.max(0, Number(manifest.profile_count) || 0);
  byId("communityCorpusState").textContent = sources && profiles
    ? `출처 원장 ${sources}곳 · 표면 프로필 ${profiles}종 · ${manifest.storage_mode || "derived_features_only"}`
    : "파생 스타일 자료를 확인하지 못했습니다.";
  byId("communityCorpusDetail").textContent = app.runtime_web_search
    ? "승인된 검색 도구가 출처를 남기고 파생 특성만 로컬 자료로 갱신합니다."
    : "현재는 검증된 로컬 스타일 자료만 검색해 로컬 LLM과 Gemini에 동일하게 제공합니다. 런타임 웹 검색은 꺼져 있습니다.";
}

function populateSettings(config) {
  byId("savePathInput").value = config.save_path || "";
  byId("heatInput").value = config.heat || 7;
  updateHeatOutput(config.heat || 7);
  byId("protagonistAliasesInput").value = (config.protagonist_aliases || []).join(", ");
  byId("languageLevelInput").value = config.community_language_level || 2;
  updateLanguageLevelOutput(config.community_language_level || 2);
  byId("modeInput").value = config.mode || "standard";
  byId("personaInput").value = config.persona || "";
  byId("autoCaptureInput").checked = Boolean(config.auto_capture);
  byId("localLlmLauncherInput").value = config.llm_launcher || "";
  byId("aiProviderInput").value = config.ai_provider || "local_auto";
  const modelSelect = byId("geminiModelInput");
  const models = config.gemini_models?.length ? config.gemini_models : ["gemini-3.5-flash-lite"];
  modelSelect.replaceChildren(...models.map(model => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    return option;
  }));
  modelSelect.value = models.includes(config.gemini_model) ? config.gemini_model : models[0];
  byId("geminiConcurrencyInput").value = String(config.gemini_batch_concurrency || 5);
  byId("geminiConsentInput").checked = Boolean(config.gemini_consent);
  byId("notificationsEnabledInput").checked = Boolean(config.notifications_enabled);
  byId("geminiApiKeyInput").value = "";
  byId("geminiApiKeyInput").placeholder = config.gemini_key_present ? "Windows 계정 암호화 키 저장됨" : "저장된 키는 다시 표시하지 않습니다";
  byId("geminiStatus").textContent = config.gemini_key_present
    ? `키 설정됨 · ${config.gemini_key_source?.startsWith("environment:") ? "환경 변수" : "Windows 계정 암호화"}`
    : "Gemini 키가 설정되지 않았습니다.";
  renderLocalModelControl(dashboard()?.app?.local_model || {});
  renderCommunityCorpus(dashboard()?.app || {});
  renderGeminiUsage(config.gemini_usage || {}, config);
  renderNotificationState(config);
  document.querySelectorAll('input[name="platform"]').forEach(input => {
    input.checked = (config.platforms || []).includes(input.value);
  });
}

function applyProviderConfig(config) {
  if (!config) return;
  const current = dashboard();
  if (current) renderDashboard({ ...current, config });
  if (!byId("settingsDialog")?.open) populateSettings(config);
  else renderGeminiUsage(config.gemini_usage || {}, config);
}

async function autoActivateGemini(sourceDashboard = dashboard(), { force = false, notify = false } = {}) {
  const config = sourceDashboard?.config || sourceDashboard || {};
  if (!force && geminiAutoActivationStarted) return;
  if (config.ai_provider === "deterministic" || !config.gemini_consent || !config.gemini_key_present) return;
  geminiAutoActivationStarted = true;
  byId("apiDot").className = "status-dot status-dot--warn";
  byId("apiStatus").textContent = "Gemini 연결 확인 중";
  try {
    const result = await api.post("/api/v1/providers/gemini/auto-activate", {});
    applyProviderConfig(result.config);
    if (result.connected && (notify || result.activation_changed)) {
      toast(result.cached ? "최근 확인 결과로 Gemini를 켰습니다." : "Gemini 연결을 확인하고 자동으로 켰습니다.", "good");
    } else if (!result.connected && result.attempted) {
      toast(result.message || "Gemini 연결을 확인하지 못해 내장·로컬 엔진을 유지합니다.", "error");
    }
  } catch (error) {
    byId("apiDot").className = "status-dot status-dot--bad";
    byId("apiStatus").textContent = "Gemini 연결 실패";
    if (notify) toast(error.message, "error");
  }
}

async function showSaveCandidates() {
  const target = byId("saveCandidates");
  target.innerHTML = `<span class="muted save-candidates__status">세이브를 검증하고 선수 정보를 읽고 있습니다.</span>`;
  try {
    const payload = await api.get("/api/v1/saves");
    if (!payload.saves.length) {
      target.innerHTML = `<span class="muted">검색된 StarPlayer.dat가 없습니다.</span>`;
      return;
    }
    target.innerHTML = "";
    for (const save of payload.saves) {
      const button = document.createElement("button");
      button.className = "save-candidate";
      button.type = "button";
      button.title = save.path;

      const verified = save.identity_status === "verified" && Boolean(save.player?.name);
      const selected = save.path === byId("savePathInput").value;
      if (selected) button.classList.add("is-selected");
      if (!verified) {
        button.classList.add("is-unavailable");
        button.disabled = true;
      }

      const identity = document.createElement("span");
      identity.className = "save-candidate__identity";
      const name = document.createElement("strong");
      name.className = "save-candidate__name";
      name.textContent = verified ? save.player.name : "선수 정보 확인 불가";
      const meta = document.createElement("span");
      meta.className = "save-candidate__meta";
      const ageLabel = verified && Number.isFinite(Number(save.player.age))
        ? `${Number(save.player.age)}세`
        : "나이 미확인";
      if (verified) {
        const date = save.date || {};
        const gameDate = date.year
          ? `${date.year}.${String(date.month || 0).padStart(2, "0")}.${String(date.day || 0).padStart(2, "0")}`
          : "날짜 미확인";
        meta.textContent = `${save.player.team || "소속팀 미확인"} · ${ageLabel} · ${gameDate}`;
      } else {
        meta.textContent = save.identity_message || "현재 버전에서 검증할 수 없는 세이브입니다.";
      }
      const path = document.createElement("small");
      path.className = "save-candidate__path";
      path.textContent = save.path;
      identity.append(name, meta, path);

      const badges = document.createElement("span");
      badges.className = "save-candidate__badges";
      const slot = document.createElement("b");
      slot.className = "save-candidate__slot";
      slot.textContent = `SLOT ${save.slot}`;
      badges.appendChild(slot);
      if (selected) {
        const current = document.createElement("em");
        current.textContent = "현재 선택";
        badges.appendChild(current);
      }
      button.append(identity, badges);
      if (verified) {
        button.setAttribute(
          "aria-label",
          `${save.player.name}, ${save.player.team || "소속팀 미확인"}, ${ageLabel}, 슬롯 ${save.slot}`,
        );
        button.addEventListener("click", () => {
          byId("savePathInput").value = save.path;
          target.querySelectorAll(".save-candidate").forEach(row => {
            row.classList.remove("is-selected");
            row.querySelector(".save-candidate__badges em")?.remove();
          });
          button.classList.add("is-selected");
          const current = document.createElement("em");
          current.textContent = "현재 선택";
          badges.appendChild(current);
          toast(`${save.player.name} · ${save.player.team || "소속팀 미확인"} · ${ageLabel}를 선택했습니다.`);
        });
      }
      target.appendChild(button);
    }
  } catch (error) {
    const message = document.createElement("span");
    message.className = "muted";
    message.textContent = `검색 실패: ${error.message}`;
    target.replaceChildren(message);
  }
}

async function showPreservedLibrary() {
  const target = byId("settingsPreservedList");
  target.textContent = "보존된 세계선을 읽고 있습니다.";
  try {
    const payload = await api.get("/api/v1/universes");
    renderPreservedUniverses(payload, "settingsPreservedList");
  } catch (error) {
    target.textContent = `보존 기록 조회 실패: ${error.message}`;
  }
}

function openSettings(scan = false) {
  populateSettings(dashboard()?.config || {});
  const dialog = byId("settingsDialog");
  if (!dialog.open) dialog.showModal();
  void refreshLocalModelStatus();
  if (scan || !byId("saveCandidates").children.length) showSaveCandidates();
  showPreservedLibrary();
}

function setInput(id, value = "") {
  byId(id).value = value ?? "";
}

function openSeasonDialog(itemId = null) {
  const row = (dashboard()?.career?.seasons || []).find(item => item.id === itemId) || null;
  const currentYear = Number(dashboard()?.career?.tracking?.current_season_year || new Date().getFullYear());
  const stats = row?.stats || {};
  setInput("seasonIdInput", row?.id);
  setInput("seasonYearInput", row?.season_year || Math.max(1936, currentYear - 1));
  setInput("seasonTeamInput", row?.team || dashboard()?.player?.team || "");
  setInput("seasonBatAB", stats.bat_AB || "");
  setInput("seasonBatH", stats.bat_H || "");
  setInput("seasonBatHR", stats.bat_HR || "");
  setInput("seasonBatRBI", stats.bat_RBI || "");
  setInput("seasonBatR", stats.bat_R || "");
  setInput("seasonBatSO", stats.bat_SO || "");
  setInput("seasonBatSB", stats.bat_SB || "");
  setInput("seasonPitIP", stats.pit_IP === "0" ? "" : stats.pit_IP);
  setInput("seasonPitTBF", stats.pit_TBF || "");
  setInput("seasonPitH", stats.pit_H || "");
  setInput("seasonPitK", stats.pit_K || "");
  setInput("seasonPitW", stats.pit_W || "");
  setInput("seasonNoteInput", row?.note || "");
  setTextContent("seasonDialogTitle", row ? `${row.season_year} 시즌 수정` : "지난 시즌 기록");
  if (!byId("seasonDialog").open) byId("seasonDialog").showModal();
}

function setTextContent(id, value) {
  byId(id).textContent = value;
}

async function saveSeason() {
  const payload = {
    id: byId("seasonIdInput").value || undefined,
    season_year: byId("seasonYearInput").value,
    team: byId("seasonTeamInput").value.trim(),
    stats: {
      bat_AB: byId("seasonBatAB").value,
      bat_H: byId("seasonBatH").value,
      bat_HR: byId("seasonBatHR").value,
      bat_RBI: byId("seasonBatRBI").value,
      bat_R: byId("seasonBatR").value,
      bat_SO: byId("seasonBatSO").value,
      bat_SB: byId("seasonBatSB").value,
      pit_IP: byId("seasonPitIP").value,
      pit_TBF: byId("seasonPitTBF").value,
      pit_H: byId("seasonPitH").value,
      pit_K: byId("seasonPitK").value,
      pit_W: byId("seasonPitW").value,
    },
    note: byId("seasonNoteInput").value.trim(),
  };
  byId("saveSeasonButton").disabled = true;
  try {
    const result = await api.post("/api/v1/career/season", payload);
    renderDashboard(result.dashboard);
    byId("seasonDialog").close();
    switchView("records");
    toast(`${result.item.season_year} 시즌을 통산 기록에 반영했습니다.`, "good");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    byId("saveSeasonButton").disabled = false;
  }
}

function openHonorDialog(itemId = null) {
  const row = (dashboard()?.career?.honors || []).find(item => item.id === itemId) || null;
  setInput("honorIdInput", row?.id);
  setInput("honorKindInput", row?.kind || "japan_series_champion");
  setInput("honorDateInput", row?.occurred_on);
  setInput("honorYearInput", row?.season_year || dashboard()?.career?.tracking?.current_season_year || "");
  setInput("honorCountInput", row?.count || 1);
  setInput("honorTitleInput", row?.title);
  setInput("honorTeamInput", row?.team || dashboard()?.player?.team || "");
  setInput("honorNoteInput", row?.note);
  setTextContent("honorDialogTitle", row ? "수상·기념 기록 수정" : "수상·기념 기록");
  if (!byId("honorDialog").open) byId("honorDialog").showModal();
}

async function saveHonor() {
  const payload = {
    id: byId("honorIdInput").value || undefined,
    kind: byId("honorKindInput").value,
    occurred_on: byId("honorDateInput").value || null,
    season_year: byId("honorYearInput").value,
    count: byId("honorCountInput").value,
    title: byId("honorTitleInput").value.trim(),
    team: byId("honorTeamInput").value.trim(),
    note: byId("honorNoteInput").value.trim(),
  };
  byId("saveHonorButton").disabled = true;
  try {
    const result = await api.post("/api/v1/career/honor", payload);
    renderDashboard(result.dashboard);
    byId("honorDialog").close();
    switchView("records");
    toast("수상·기념 기록을 연표에 반영했습니다.", "good");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    byId("saveHonorButton").disabled = false;
  }
}

async function removeCareerItem(entity, itemId) {
  const label = entity === "season" ? "지난 시즌 기록" : "수상·기념 기록";
  if (!window.confirm(`${label}을 삭제할까요? 이 작업은 통산 누계를 다시 계산합니다.`)) return;
  try {
    const result = await api.post("/api/v1/career/remove", { entity, id: itemId });
    renderDashboard(result.dashboard);
    switchView("records");
    toast(`${label}을 삭제했습니다.`, "good");
  } catch (error) {
    toast(error.message, "error");
  }
}

function worldbookOrigin() {
  try { return JSON.parse(byId("storyWorldbook").dataset.origin || "{}"); }
  catch { return {}; }
}

function refreshWorldContextFields() {
  const hobby = byId("worldContextBasis").value === "fictional_experience";
  byId("worldContextProficiencyField").classList.toggle("is-hidden", !hobby);
  const isPublic = byId("worldContextVisibility").value === "public";
  byId("worldContextRemoteAllowed").disabled = !isPublic;
  if (!isPublic) byId("worldContextRemoteAllowed").checked = false;
}

function resetWorldContextForm() {
  for (const id of ["worldContextId", "worldContextRevision", "worldContextLabel", "worldContextDetail"]) byId(id).value = "";
  byId("worldContextBasis").selectedIndex = 0;
  byId("worldContextVisibility").value = "private";
  byId("worldContextRemoteAllowed").checked = false;
  byId("cancelWorldContextButton").classList.add("is-hidden");
  byId("saveWorldContextButton").textContent = "설정 저장";
  refreshWorldContextFields();
}

function editWorldContext(contextId) {
  const row = (dashboard()?.story?.personal_context?.items || []).find(item => item.context_id === contextId && item.status === "active");
  if (!row) return;
  byId("storyWorldbook").open = true;
  byId("worldContextId").value = row.context_id;
  byId("worldContextRevision").value = row.revision;
  byId("worldContextBasis").value = row.basis;
  byId("worldContextLabel").value = row.label || "";
  byId("worldContextDetail").value = row.detail || "";
  byId("worldContextProficiency").value = row.proficiency || byId("worldContextProficiency").options[0]?.value || "";
  byId("worldContextVisibility").value = row.visibility === "national" ? "public" : row.visibility;
  byId("worldContextRemoteAllowed").checked = Boolean(row.remote_allowed);
  byId("cancelWorldContextButton").classList.remove("is-hidden");
  byId("saveWorldContextButton").textContent = "설정 수정";
  refreshWorldContextFields();
  byId("worldContextLabel").focus();
}

async function saveWorldContext() {
  const contextId = byId("worldContextId").value;
  const payload = {
    context_id: contextId || undefined,
    expected_revision: contextId ? Number(byId("worldContextRevision").value) : undefined,
    basis: byId("worldContextBasis").value,
    label: byId("worldContextLabel").value.trim(),
    detail: byId("worldContextDetail").value.trim(),
    proficiency: byId("worldContextBasis").value === "fictional_experience" ? byId("worldContextProficiency").value : undefined,
    visibility: byId("worldContextVisibility").value,
    remote_allowed: byId("worldContextRemoteAllowed").checked,
    world_origin: worldbookOrigin(),
  };
  const button = byId("saveWorldContextButton");
  button.disabled = true;
  try {
    const result = await api.post("/api/v1/worldbook/context", payload);
    resetWorldContextForm();
    renderDashboard(result.dashboard);
    byId("storyWorldbook").open = true;
    toast(contextId ? "세계선 설정의 새 개정을 저장했습니다." : "세계선 설정을 저장했습니다.", "good");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function retireWorldContext(contextId) {
  const row = (dashboard()?.story?.personal_context?.items || []).find(item => item.context_id === contextId && item.status === "active");
  if (!row || !window.confirm(`‘${row.label}’을 보관할까요? 기록과 과거 장면은 남고 새 장면의 자동 참조만 멈춥니다.`)) return;
  try {
    const result = await api.post("/api/v1/worldbook/context/retire", {
      context_id: row.context_id,
      expected_revision: row.revision,
      world_origin: worldbookOrigin(),
    });
    resetWorldContextForm();
    renderDashboard(result.dashboard);
    byId("storyWorldbook").open = true;
    toast("설정을 기록 보관 상태로 전환했습니다.", "good");
  } catch (error) {
    toast(error.message, "error");
  }
}

function resetWorldCounterpartForm() {
  for (const id of ["worldCounterpartId", "worldCounterpartRevision", "worldCounterpartName", "worldCounterpartAliases", "worldCounterpartNote"]) byId(id).value = "";
  byId("worldCounterpartRole").selectedIndex = 0;
  byId("cancelWorldCounterpartButton").classList.add("is-hidden");
  byId("saveWorldCounterpartButton").textContent = "인물 저장";
}

function editWorldCounterpart(entityId) {
  const row = (dashboard()?.story?.counterparts || []).find(item => item.entity_id === entityId && item.status === "active");
  if (!row) return;
  byId("storyWorldbook").open = true;
  byId("worldCounterpartId").value = row.entity_id;
  byId("worldCounterpartRevision").value = row.identity_revision;
  byId("worldCounterpartName").value = row.label || "";
  byId("worldCounterpartRole").value = row.roles?.[0] || byId("worldCounterpartRole").options[0]?.value || "";
  byId("worldCounterpartAliases").value = (row.aliases || []).join(", ");
  byId("worldCounterpartNote").value = row.note || "";
  byId("cancelWorldCounterpartButton").classList.remove("is-hidden");
  byId("saveWorldCounterpartButton").textContent = "인물 수정";
  byId("worldCounterpartName").focus();
}

async function saveWorldCounterpart() {
  const entityId = byId("worldCounterpartId").value;
  const payload = {
    entity_id: entityId || undefined,
    expected_revision: entityId ? Number(byId("worldCounterpartRevision").value) : undefined,
    canonical_name: byId("worldCounterpartName").value.trim(),
    role: byId("worldCounterpartRole").value,
    aliases: byId("worldCounterpartAliases").value.trim(),
    note: byId("worldCounterpartNote").value.trim(),
    world_origin: worldbookOrigin(),
  };
  const button = byId("saveWorldCounterpartButton");
  button.disabled = true;
  try {
    const result = await api.post("/api/v1/worldbook/counterpart", payload);
    resetWorldCounterpartForm();
    renderDashboard(result.dashboard);
    byId("storyWorldbook").open = true;
    toast(entityId ? "인물 식별자의 새 개정을 저장했습니다." : "이 세계선의 반복 등장 인물을 저장했습니다.", "good");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function retireWorldCounterpart(entityId) {
  const row = (dashboard()?.story?.counterparts || []).find(item => item.entity_id === entityId && item.status === "active");
  if (!row || !window.confirm(`‘${row.label}’을 보관할까요? 과거 관계와 장면은 남고 새 만남 목록에서만 빠집니다.`)) return;
  try {
    const result = await api.post("/api/v1/worldbook/counterpart/retire", {
      entity_id: row.entity_id,
      expected_revision: row.identity_revision,
      world_origin: worldbookOrigin(),
    });
    resetWorldCounterpartForm();
    renderDashboard(result.dashboard);
    byId("storyWorldbook").open = true;
    toast("인물을 기록 보관 상태로 전환했습니다.", "good");
  } catch (error) {
    toast(error.message, "error");
  }
}

function storyPayload() {
  const payload = {
    category: byId("storyCategoryGrid").dataset.category,
    situation: byId("storySituation").value,
    target: byId("storyTarget").value,
    tone: byId("storyTone").value,
    visibility: byId("storyVisibility").value,
    user_text: byId("storyText").value.trim(),
  };
  const lifetimeGames = byId("storyLifetimeGames").value.trim();
  if (lifetimeGames) payload.issue_lifetime_games = Number(lifetimeGames);
  if (payload.category === "starplayer") {
    Object.assign(payload, {
      interaction_id: byId("storyInteraction").value || undefined,
      participant_entity_id: byId("storyCounterpart").value || undefined,
      participant_name: byId("storyParticipantName").value.trim(),
      interaction_place: byId("storyInteractionPlace").value.trim(),
      interaction_topic: byId("storyInteractionTopic").value.trim(),
      interaction_mode: byId("storyInteractionMode").value,
      action_confirmed: byId("storyActionConfirmed").checked,
      learning_progress: byId("storyLearningProgress").value,
      interaction_origin: JSON.parse(byId("storyInteractionPanel").dataset.origin || "{}"),
    });
  }
  return payload;
}

function selectInteraction(interactionId) {
  if (!interactionId || !(dashboard()?.story?.interactions || []).some(row => row.interaction_id === interactionId)) return;
  selectStoryCategory("starplayer");
  byId("storyInteraction").value = interactionId;
  refreshStoryInteraction();
}

async function createStoryTurn(useChat = false) {
  const payload = storyPayload();
  if (useChat && !payload.user_text) {
    toast("이어갈 말을 입력해 주세요.", "error");
    byId("storyText").focus();
    return;
  }
  const eventButton = byId("storyEventButton");
  const chatButton = byId("storyChatButton");
  eventButton.disabled = true;
  chatButton.disabled = true;
  try {
    if (useChat) {
      const provider = dashboard()?.config?.ai_provider || "local_auto";
      payload.renderer_preference = payload.category === "starplayer" && provider !== "gemini"
        ? "deterministic"
        : provider === "gemini"
        ? "gemini"
        : provider === "deterministic"
          ? "deterministic"
          : "auto";
    }
    const result = await api.post(useChat ? "/api/v1/story/chat" : "/api/v1/story/event", payload);
    renderDashboard(result.dashboard);
    selectInteraction(result.interaction_id || result.item?.scene?.interaction?.interaction_id);
    if (!useChat && payload.category === "starplayer") byId("storyActionConfirmed").checked = false;
    byId("storyText").value = "";
    const proposedLifetime = (result.turn?.proposed_events || []).some(row => row.issue_lifetime_games);
    const appliedLifetime = Boolean(result.item?.retrieval_lifetime || result.turn?.prop_change?.prop?.lifetime);
    if (payload.issue_lifetime_games && (proposedLifetime || appliedLifetime)) byId("storyLifetimeGames").value = "";
    showWorkspacePart("narrative", "events");
    if (!useChat) toast("버튼 이벤트를 오늘 세계선에 저장했습니다.", "good");
    else if (result.renderer === "deterministic") {
      const message = result.item ? "장면을 오늘의 기록으로 남겼습니다." : (result.turn?.proposed_events || []).length ? "답을 받았습니다. 사건으로 남기려면 확정 버튼을 누르세요." : "답을 받았습니다.";
      toast(`${result.fallback ? "로컬 LLM 응답에 실패해 LLM 없이 이어갑니다. " : ""}${message}`, "good");
    }
    else toast(["gemini", "gemini_expression"].includes(result.renderer) ? "Gemini 표현을 오늘 세계선에 이어 붙였습니다." : "로컬 LLM 장면을 오늘 세계선에 이어 붙였습니다.", "good");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    const open = dashboard()?.story?.current?.status !== "sealed";
    eventButton.disabled = !open;
    chatButton.disabled = !open;
  }
}

async function confirmProposal(turnId, proposalId) {
  const statement = byId("storyText").value.trim();
  try {
    const result = await api.post("/api/v1/story/chat", { confirm_turn_id: turnId, confirm_proposal_id: proposalId, statement, tone: byId("storyTone").value });
    renderDashboard(result.dashboard);
    selectInteraction(result.item?.scene?.interaction?.interaction_id);
    byId("storyActionConfirmed").checked = false;
    byId("storyText").value = "";
    toast("사건을 오늘의 기록으로 확정했습니다.", "good");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function relinkUniverse() {
  const universeId = byId("storyRelink").dataset.universeId;
  if (!universeId) return;
  const button = byId("storyRelinkButton");
  button.disabled = true;
  try {
    await api.post(`/api/v1/universes/${encodeURIComponent(universeId)}/relink/confirm`, { note: "user confirmed from the story room" });
    toast("세계선을 다시 연결했습니다. 최신 세이브를 확인합니다.", "good");
    await startJob("/api/v1/jobs/check", currentOptions());
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function openMuseumDates(universeId) {
  try {
    const payload = await api.get(`/api/v1/universes/${encodeURIComponent(universeId)}/history`);
    renderMuseumDates(universeId, payload);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function openMuseumCapsule(universeId, gameDate) {
  const dialog = byId("historyDialog");
  const status = document.createElement("div");
  status.className = "empty-inline";
  status.textContent = `${gameDate} 보존 세계선 캡슐을 읽고 있습니다.`;
  byId("historyDialogBody").replaceChildren(status);
  if (!dialog.open) dialog.showModal();
  try {
    const payload = await api.get(`/api/v1/universes/${encodeURIComponent(universeId)}/history/${encodeURIComponent(gameDate)}`);
    renderHistoryCapsule(payload.capsule);
  } catch (error) {
    status.textContent = error.message;
    byId("historyDialogBody").replaceChildren(status);
    toast(error.message, "error");
  }
}

function editorRows(targetId, kind, count) {
  const target = byId(targetId);
  if (target.children.length) return;
  if (kind === "standings") {
    target.innerHTML = `<div class="table-editor__head"><span>순위</span><span>팀</span><span>승</span><span>패</span><span>무</span><span>게임차</span></div>${Array.from({ length: count }, (_, index) => `<div class="editor-row editor-row--standings" data-editor-row><b>${index + 1}</b><input data-field="team" type="text" maxlength="80" aria-label="${index + 1}위 팀"><input data-field="wins" type="number" min="0" aria-label="${index + 1}위 승"><input data-field="losses" type="number" min="0" aria-label="${index + 1}위 패"><input data-field="ties" type="number" min="0" aria-label="${index + 1}위 무"><input data-field="games_back" type="number" step="0.5" min="-99" max="99" aria-label="${index + 1}위 게임차"></div>`).join("")}`;
  } else if (kind === "leaderboard") {
    target.innerHTML = `<div class="table-editor__head table-editor__head--leader"><span>순위</span><span>선수</span><span>팀</span><span>값</span><span>내 선수</span></div>${Array.from({ length: count }, (_, index) => `<div class="editor-row editor-row--leader" data-editor-row><b>${index + 1}</b><input data-field="name" type="text" maxlength="80" aria-label="${index + 1}위 선수"><input data-field="team" type="text" maxlength="80" aria-label="${index + 1}위 팀"><input data-field="value" type="text" maxlength="40" aria-label="${index + 1}위 기록 값"><label><input data-field="is_player" type="checkbox" aria-label="${index + 1}위가 내 선수">나</label></div>`).join("")}`;
  } else {
    target.innerHTML = `<div class="table-editor__head table-editor__head--offer"><span>시즌</span><span>갱신 전 연봉</span><span>갱신 후 연봉</span></div>${Array.from({ length: count }, () => `<div class="editor-row editor-row--offer" data-editor-row><input data-field="season_year" type="number" min="1936" max="9999" aria-label="계약 시즌"><input data-field="before_yen" type="number" min="0" step="10000" aria-label="갱신 전 연봉"><input data-field="after_yen" type="number" min="0" step="10000" aria-label="갱신 후 연봉"></div>`).join("")}`;
  }
}

function setEditorRow(row, values = {}) {
  row.querySelectorAll("[data-field]").forEach(input => {
    const value = values[input.dataset.field];
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = value ?? "";
  });
}

function openLeagueDialog() {
  editorRows("standingsEditor", "standings", 6);
  editorRows("leaderboardEditor", "leaderboard", 5);
  const value = dashboard()?.value_lab || {};
  const standings = value.standings || [];
  const league = standings[0]?.league || value.leaderboards?.[0]?.league || "센트럴 리그";
  setInput("standingsLeagueInput", league);
  setInput("leaderboardLeagueInput", league);
  [...byId("standingsEditor").querySelectorAll("[data-editor-row]")].forEach((row, index) => setEditorRow(row, standings.filter(item => item.league === league).find(item => item.rank === index + 1) || {}));
  const board = value.leaderboards?.[0] || null;
  setInput("leaderboardKeyInput", board?.stat_key || "");
  setInput("leaderboardLabelInput", board?.label || "");
  if (board?.league) setInput("leaderboardLeagueInput", board.league);
  [...byId("leaderboardEditor").querySelectorAll("[data-editor-row]")].forEach((row, index) => setEditorRow(row, board?.entries?.find(item => item.rank === index + 1) || {}));
  if (!byId("leagueDialog").open) byId("leagueDialog").showModal();
}

function readEditorRows(targetId) {
  return [...byId(targetId).querySelectorAll("[data-editor-row]")].map((row, index) => {
    const value = { rank: index + 1 };
    row.querySelectorAll("[data-field]").forEach(input => { value[input.dataset.field] = input.type === "checkbox" ? input.checked : input.value.trim(); });
    return value;
  });
}

async function saveStandings() {
  const league = byId("standingsLeagueInput").value.trim();
  const rows = readEditorRows("standingsEditor").filter(row => row.team).map(row => ({ ...row, league }));
  if (!rows.length) { toast("한 팀 이상 입력해 주세요.", "error"); return; }
  byId("saveStandingsButton").disabled = true;
  try {
    const result = await api.post("/api/v1/day-context", { standings: rows });
    renderDashboard(result.dashboard);
    toast("오늘 날짜의 리그 순위표를 저장했습니다.", "good");
  } catch (error) { toast(error.message, "error"); }
  finally { byId("saveStandingsButton").disabled = false; }
}

async function saveLeaderboard() {
  const entries = readEditorRows("leaderboardEditor").filter(row => row.name || row.value);
  const payload = {
    stat_key: byId("leaderboardKeyInput").value.trim(),
    label: byId("leaderboardLabelInput").value.trim(),
    league: byId("leaderboardLeagueInput").value.trim(),
    entries,
  };
  byId("saveLeaderboardButton").disabled = true;
  try {
    const result = await api.post("/api/v1/day-context", { leaderboard: payload });
    renderDashboard(result.dashboard);
    toast("내 선수가 포함된 Top 5 기록을 저장했습니다.", "good");
  } catch (error) { toast(error.message, "error"); }
  finally { byId("saveLeaderboardButton").disabled = false; }
}

const VALUE_INPUTS = {
  saberBatBB: "bat_BB", saberBatHBP: "bat_HBP", saberBat2B: "bat_2B", saberBat3B: "bat_3B", saberBatSF: "bat_SF", saberBatCS: "bat_CS",
  saberPitER: "pit_ER", saberPitBB: "pit_BB", saberPitHBP: "pit_HBP", saberPitHR: "pit_HR", saberFipConstant: "fip_constant",
  saberManualWar: "manual_war", saberYenPerWar: "yen_per_war",
};
const SALARY_INPUTS = {
  salaryCurrent: "current_yen", salaryPrevious: "previous_yen", salaryMissions: "mission_successes",
  salaryManagerEval: "manager_eval", salaryClubEval: "club_eval", salaryStarLevel: "star_level",
};

function openValueDialog() {
  editorRows("salaryOfferEditor", "offer", 3);
  const value = dashboard()?.value_lab || {};
  const saber = value.saber_inputs || {};
  const salary = value.salary_inputs || {};
  Object.entries(VALUE_INPUTS).forEach(([id, key]) => setInput(id, saber[key]));
  Object.entries(SALARY_INPUTS).forEach(([id, key]) => setInput(id, salary[key]));
  [...byId("salaryOfferEditor").querySelectorAll("[data-editor-row]")].forEach((row, index) => setEditorRow(row, salary.actual_offers?.[index] || {}));
  if (!byId("valueDialog").open) byId("valueDialog").showModal();
}

async function saveValueContext() {
  const saber_inputs = {};
  const salary = {};
  Object.entries(VALUE_INPUTS).forEach(([id, key]) => { saber_inputs[key] = byId(id).value.trim(); });
  Object.entries(SALARY_INPUTS).forEach(([id, key]) => { salary[key] = byId(id).value.trim(); });
  salary.actual_offers = readEditorRows("salaryOfferEditor")
    .map(({ season_year, before_yen, after_yen }) => ({ season_year, before_yen, after_yen }))
    .filter(row => row.season_year || row.before_yen || row.after_yen);
  byId("saveValueButton").disabled = true;
  try {
    const result = await api.post("/api/v1/day-context", { saber_inputs, salary });
    renderDashboard(result.dashboard);
    byId("valueDialog").close();
    switchView("records");
    toast("오늘 날짜의 세이버·연봉 입력을 저장했습니다.", "good");
  } catch (error) { toast(error.message, "error"); }
  finally { byId("saveValueButton").disabled = false; }
}

async function openHistoryCapsule(gameDate) {
  const dialog = byId("historyDialog");
  byId("historyDialogBody").innerHTML = `<div class="empty-inline">${gameDate} 세계선 캡슐을 읽고 있습니다.</div>`;
  if (!dialog.open) dialog.showModal();
  try {
    const payload = await api.get(`/api/v1/history/${encodeURIComponent(gameDate)}`);
    renderHistoryCapsule(payload.capsule);
  } catch (error) {
    byId("historyDialogBody").innerHTML = `<div class="empty-inline">${error.message}</div>`;
    toast(error.message, "error");
  }
}

async function openArchive(archiveId) {
  const dialog = byId("archiveDialog");
  byId("archiveDialogBody").innerHTML = `<div class="empty-inline">보관된 반응을 읽고 있습니다.</div>`;
  if (!dialog.open) dialog.showModal();
  try {
    const payload = await api.get(`/api/v1/archive/${encodeURIComponent(archiveId)}`);
    renderArchivedFeed(payload.archive);
  } catch (error) {
    byId("archiveDialogBody").innerHTML = `<div class="empty-inline">${error.message}</div>`;
    toast(error.message, "error");
  }
}

function suggestProtagonistSpellings(result) {
  // The model wrote the player under a spelling the app was never told about.
  // Offer it once; registering stays the user's decision.
  const found = result?.run?.protagonist_spelling_candidates || [];
  const known = (result?.config?.protagonist_aliases || []).map(value => value.trim());
  const fresh = found.filter(value => value && !known.includes(value));
  if (!fresh.length) return;
  const hint = byId("protagonistAliasHint");
  if (hint) {
    hint.textContent = `이번 실행에서 모델이 "${fresh.join('", "')}" 표기를 썼습니다. 같은 선수로 인정하려면 위 칸에 추가하고 저장하세요.`;
  }
  toast(`모델이 "${fresh[0]}" 표기를 썼습니다. 설정 > 주인공 한국어 표기에 등록하면 같은 선수로 인정합니다.`, "info");
}

async function saveSettings() {
  const platforms = [...document.querySelectorAll('input[name="platform"]:checked')].map(input => input.value);
  if (!platforms.length) {
    toast("커뮤니티를 하나 이상 선택해 주세요.", "error");
    return;
  }
  if (byId("notificationsEnabledInput").checked && (!("Notification" in window) || window.Notification.permission !== "granted")) {
    await requestNotificationAccess();
  }
  const payload = {
    save_path: byId("savePathInput").value.trim(),
    heat: Number(byId("heatInput").value),
    community_language_level: Number(byId("languageLevelInput").value),
    protagonist_aliases: byId("protagonistAliasesInput").value.split(",").map(value => value.trim()).filter(Boolean),
    mode: byId("modeInput").value,
    platforms,
    persona: byId("personaInput").value.trim(),
    auto_capture: byId("autoCaptureInput").checked,
    llm_launcher: byId("localLlmLauncherInput").value.trim() || null,
    ai_provider: byId("aiProviderInput").value,
    gemini_model: byId("geminiModelInput").value,
    gemini_batch_concurrency: Number(byId("geminiConcurrencyInput").value),
    gemini_consent: byId("geminiConsentInput").checked,
    notifications_enabled: byId("notificationsEnabledInput").checked,
    theme: document.documentElement.dataset.theme,
  };
  const geminiKey = byId("geminiApiKeyInput").value.trim();
  if (geminiKey) payload.gemini_api_key = geminiKey;
  byId("saveSettingsButton").disabled = true;
  try {
    await api.post("/api/v1/settings", payload);
    byId("settingsDialog").close();
    toast("설정을 저장했습니다.", "good");
    const refreshed = await api.bootstrap();
    renderDashboard(refreshed.dashboard);
    populateSettings(refreshed.dashboard?.config || payload);
    geminiAutoActivationStarted = false;
    await autoActivateGemini(refreshed.dashboard, { force: true, notify: true });
  } catch (error) {
    toast(error.message, "error");
  } finally {
    byId("saveSettingsButton").disabled = false;
  }
}

async function testGemini() {
  const button = byId("geminiTestButton");
  const status = byId("geminiStatus");
  button.disabled = true;
  status.textContent = "선수·경기 데이터 없이 모델 접근만 확인하고 있습니다.";
  try {
    const result = await api.post("/api/v1/providers/gemini/test", {
      gemini_api_key: byId("geminiApiKeyInput").value.trim() || undefined,
      gemini_model: byId("geminiModelInput").value,
      gemini_consent: byId("geminiConsentInput").checked,
      activate_on_success: true,
    });
    if (result.activated) applyProviderConfig(result.config);
    status.textContent = result.activated
      ? `${result.model} 연결 확인 · Gemini 서사 엔진 켜짐`
      : `${result.model} 연결 확인 · 설정을 저장하면 자동으로 켜집니다`;
    renderGeminiUsage(result.usage || {}, {
      ai_provider: byId("aiProviderInput").value,
      gemini_consent: byId("geminiConsentInput").checked,
      gemini_key_present: Boolean(byId("geminiApiKeyInput").value.trim()) || Boolean(dashboard()?.config?.gemini_key_present),
    });
    toast("Gemini 연결을 확인했습니다.", "good");
  } catch (error) {
    status.textContent = error.message;
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function clearGeminiKey() {
  if (!window.confirm("이 Windows 계정에 저장한 Gemini API 키를 지울까요? 환경 변수의 키는 변경하지 않습니다.")) return;
  const button = byId("geminiClearButton");
  button.disabled = true;
  try {
    const result = await api.post("/api/v1/settings", { clear_gemini_api_key: true });
    populateSettings(result.config);
    toast("저장된 Gemini API 키를 지웠습니다.", "good");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function readDataURL(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error(`${file.name}을 읽지 못했습니다.`));
    reader.readAsDataURL(file);
  });
}

async function uploadFiles(files) {
  let added = 0;
  captureMutationActive = true;
  for (const file of [...files].slice(0, 8)) {
    if (file.size > 12 * 1024 * 1024) {
      toast(`${file.name}: 12MB를 초과했습니다.`, "error");
      continue;
    }
    try {
      const data = await readDataURL(file);
      const payload = await api.post("/api/v1/upload", { data });
      if (payload.image?.name) added += 1;
    } catch (error) {
      toast(error.message, "error");
    }
  }
  byId("imageInput").value = "";
  await refreshCaptures(false);
  captureMutationActive = false;
  if (added) toast(`${added}장의 이미지를 시각 힌트로 추가했습니다.`, "good");
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function captureTime(value) {
  const date = new Date(value || 0);
  if (Number.isNaN(date.getTime())) return "시간 미확인";
  return new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
}

function renderCaptureStatus(status, announce = false) {
  const items = status?.items || [];
  refreshDirectorCaptures(items);
  byId("captureCount").textContent = `${items.length}장`;
  const hotkeys = status?.hotkeys;
  const hotkeyStatus = byId("hotkeyStatus");
  captureHotkeysActive = Boolean(hotkeys?.active);
  if (hotkeys?.active) {
    hotkeyStatus.textContent = "전역 단축키 사용 가능 · 게임 화면에서도 작동";
    hotkeyStatus.className = "is-active";
  } else if (hotkeys?.supported) {
    hotkeyStatus.textContent = "전역 등록 충돌 · 앱 창 안 단축키는 사용 가능";
    hotkeyStatus.className = "is-limited";
  } else {
    hotkeyStatus.textContent = "앱 창 안 단축키 사용 가능";
    hotkeyStatus.className = "";
  }

  const previous = knownCaptureNames;
  const current = new Set(items.map(item => item.name));
  const added = items.filter(item => !previous.has(item.name));
  knownCaptureNames = current;
  if (announce && added.length) toast(`단축키 캡처 ${added.length}장이 추가됐습니다.`, "good");

  const eventAt = status?.last_event?.at;
  if (announce && eventAt && eventAt !== lastCaptureEventAt && status.last_event.ok === false) {
    toast(status.last_event.message || "단축키 캡처에 실패했습니다.", "error");
  }
  if (eventAt) lastCaptureEventAt = eventAt;

  const gallery = byId("captureGallery");
  gallery.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "capture-empty";
    empty.textContent = "캡처를 누적하면 여기에 여러 장이 표시됩니다.";
    gallery.appendChild(empty);
    return;
  }
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "capture-item";
    const image = document.createElement("img");
    image.src = `${item.image_url}?v=${encodeURIComponent(item.captured_at || "")}`;
    image.alt = `${item.kind === "upload" ? "추가 이미지" : "게임 화면 캡처"} ${item.name}`;
    image.loading = "lazy";
    const meta = document.createElement("div");
    meta.className = "capture-item__meta";
    const name = document.createElement("strong");
    name.textContent = item.name;
    const detail = document.createElement("span");
    detail.textContent = `${captureTime(item.captured_at)} · ${formatBytes(item.bytes)}`;
    meta.append(name, detail);
    const remove = document.createElement("button");
    remove.className = "capture-item__remove";
    remove.type = "button";
    remove.dataset.removeCapture = item.name;
    remove.setAttribute("aria-label", `${item.name} 삭제`);
    remove.textContent = "×";
    const read = document.createElement("button");
    read.className = "capture-item__read";
    read.type = "button";
    read.dataset.readCapture = item.name;
    read.textContent = "내용 읽기";
    meta.append(read);
    card.append(image, meta, remove);
    gallery.appendChild(card);
  }
}

async function refreshCaptures(announce = true) {
  try {
    const payload = await api.get("/api/v1/captures");
    renderCaptureStatus(payload.captures, announce);
  } catch (error) {
    if (!announce) toast(error.message, "error");
  }
}

function scheduleCapturePoll() {
  if (capturePollTimer) window.clearTimeout(capturePollTimer);
  capturePollTimer = window.setTimeout(async () => {
    await refreshCaptures(!captureMutationActive);
    scheduleCapturePoll();
  }, 1500);
}

// ---- Local attachment review (offline screen parsers, master plan 8.12/17.4) ----
const SCREEN_LABELS = { game_result: "경기 결과", batting_log: "타격 기록", batting_stats: "야수 성적", unknown: "알 수 없는 화면" };
const PATH_LABELS = { known_screen_parser: "게임 화면 파서", ocr_only: "문자 인식만", no_local_ocr: "로컬 문자 인식 사용 불가", unsupported: "지원하지 않는 형식" };
const DECISION_LABELS = { user_confirmed: "사용자 확인 사실로 반영", story_prop: "창작 소재로만 사용", session_only: "이번 대화에서만 사용", ignore: "무시" };
const VALIDATION_LABELS = { ok: "검증됨", warn: "확인 필요", fail: "검증 실패" };
let currentAttachment = null;

function formatObservationValue(field, value) {
  if (value === null || value === undefined) return "-";
  if (Array.isArray(value)) {
    if (field === "batting.plate_appearances") return value.map(row => `${row.position || ""}${row.result || ""}`).join(" · ");
    if (field === "batting.table") return `${value.length}명`;
    return value.map(row => (row === null ? "·" : String(row))).join("-");
  }
  if (typeof value === "object") {
    if (field === "game.result") {
      const outcome = value.outcome === "win" ? "승" : value.outcome === "loss" ? "패" : "무";
      return `${outcome} ${value.runs_for}-${value.runs_against} (${value.opponent || ""})`;
    }
    if (field === "game.head_to_head") return `${value.wins}승 ${value.losses}패 ${value.ties}무`;
    if (field === "game.home_runs") return `${value.hitter} ${value.count}개 (${(value.numbers || []).join(", ")})`;
    return Object.entries(value).map(([key, item]) => `${key} ${Array.isArray(item) ? item.join(",") : item}`).join(" · ");
  }
  if (field === "batting.average" && typeof value === "number") return value.toFixed(3).replace(/^0/, "");
  return String(value);
}

function renderAttachmentReview(record, status) {
  currentAttachment = record;
  byId("attachmentIdInput").value = record.attachment_id || "";
  byId("attachmentNameInput").value = record.file_name || "";
  const ocrLines = record.ocr?.line_count ?? 0;
  const confidence = Math.round((record.screen_confidence || 0) * 100);
  byId("attachmentSummary").textContent = `${record.file_name} · ${SCREEN_LABELS[record.screen_type] || record.screen_type} · ${PATH_LABELS[record.analysis_path] || record.analysis_path} · 인식 ${ocrLines}줄 · 화면 신뢰도 ${confidence}% · LLM 사용 안 함`;
  const binding = record.binding || {};
  const bindingBox = byId("attachmentBinding");
  bindingBox.replaceChildren();
  const bindingText = document.createElement("p");
  bindingText.textContent = `연결: ${binding.game_date || "날짜 없음"} (${binding.date_source === "screen" ? "화면에서 읽음" : "열린 날짜"}) · 선수 ${binding.protagonist_seen ? "확인됨" : "미확인"} · 신뢰도 ${Math.round((binding.confidence || 0) * 100)}%`;
  bindingBox.appendChild(bindingText);
  if (binding.date_conflict) {
    const warn = document.createElement("p");
    warn.className = "attachment-warn";
    warn.textContent = `화면 날짜(${binding.game_date})가 열린 날짜(${binding.current_game_date})와 다릅니다. 마감된 날짜에는 반영할 수 없습니다.`;
    bindingBox.appendChild(warn);
  }
  for (const dup of record.duplicates || []) {
    const note = document.createElement("p");
    note.className = "attachment-warn";
    note.textContent = `같은 내용의 이미지가 이미 있습니다: ${dup.file_name}`;
    bindingBox.appendChild(note);
  }
  const needsConfirm = Boolean(binding.requires_confirmation || binding.date_conflict);
  byId("attachmentBindingConfirmRow").hidden = !needsConfirm;
  byId("attachmentBindingConfirm").checked = false;
  const list = byId("attachmentObservations");
  list.replaceChildren();
  const observations = record.observations || [];
  if (!observations.length) {
    const empty = document.createElement("p");
    empty.className = "form-note";
    empty.textContent = record.analysis_path === "no_local_ocr"
      ? "이 PC에는 로컬 문자 인식 엔진이 없습니다. 아래에 내용을 직접 적으면 창작 소재로 남습니다."
      : "알려진 게임 화면으로 읽히지 않았습니다. 아래에 내용을 직접 적어 주세요.";
    list.appendChild(empty);
  }
  for (const row of observations) {
    const item = document.createElement("div");
    item.className = `attachment-row is-${row.validation || "ok"}`;
    const head = document.createElement("div");
    head.className = "attachment-row__head";
    const label = document.createElement("strong");
    label.textContent = row.label || row.field;
    const value = document.createElement("span");
    value.className = "attachment-row__value";
    value.textContent = formatObservationValue(row.field, row.value);
    const badge = document.createElement("span");
    badge.className = "attachment-badge";
    badge.textContent = `${VALIDATION_LABELS[row.validation] || row.validation} · ${Math.round((row.confidence || 0) * 100)}%`;
    head.append(label, value, badge);
    const select = document.createElement("select");
    select.dataset.observationId = row.observation_id;
    for (const [key, text] of Object.entries(DECISION_LABELS)) {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = text;
      if (key === "user_confirmed" && row.validation === "fail") option.disabled = true;
      select.appendChild(option);
    }
    const suggested = row.suggested_decision || "session_only";
    select.value = row.validation === "fail" && suggested === "user_confirmed" ? "ignore" : suggested;
    item.append(head, select);
    const notes = [row.note, row.cross_check].filter(Boolean);
    if (notes.length) {
      const note = document.createElement("p");
      note.className = "attachment-row__note";
      note.textContent = notes.join(" ");
      item.appendChild(note);
    }
    list.appendChild(item);
  }
  byId("attachmentDescriptionInput").value = record.manual_description || "";
  byId("attachmentLlmHint").hidden = true;
  byId("attachmentLlmHint").textContent = "";
  currentAttachmentLlmText = "";
  byId("attachmentUseLlmHintButton").hidden = true;
  const storedScope = ["private", "community", "public"].includes(record.reaction_scope) ? record.reaction_scope : "private";
  const scopeInput = byId("attachmentForm").querySelector(`input[name=attachmentReactionScope][value="${storedScope}"]`);
  if (scopeInput) scopeInput.checked = true;
  updateHeatOutput(dashboard()?.config?.heat || byId("heatInput").value || 7);
  const visionButton = byId("attachmentLlmButton");
  visionButton.hidden = !status?.llm_vision_reachable;
  visionButton.dataset.provider = status?.vision_provider || "";
  visionButton.textContent = status?.vision_provider === "gemini" ? "Gemini로 이미지 해석" : "로컬 LLM으로 해석 보강";
  const committed = record.status === "committed";
  byId("attachmentCommitButton").disabled = committed;
  byId("attachmentCommitButton").textContent = committed ? "이미 반영됨" : "이미지 내용 반영";
  byId("attachmentDiscardButton").disabled = committed;
  if (!byId("attachmentDialog").open) byId("attachmentDialog").showModal();
}

async function openAttachmentReview(name) {
  toast("이미지를 이 PC 안에서 읽는 중입니다. LLM과 네트워크는 사용하지 않습니다.", "info");
  try {
    const result = await api.post("/api/v1/attachments/analyze-local", { names: [name] });
    const record = (result.attachments || [])[0];
    if (!record) throw new Error("분석 결과가 없습니다.");
    renderAttachmentReview(record, result.status);
  } catch (error) {
    toast(error.message, "error");
  }
}

function attachmentDecisions() {
  const decisions = {};
  for (const select of byId("attachmentObservations").querySelectorAll("select[data-observation-id]")) {
    decisions[select.dataset.observationId] = select.value;
  }
  return decisions;
}

function attachmentRetention() {
  const checked = byId("attachmentForm").querySelector("input[name=attachmentRetention]:checked");
  return checked ? checked.value : "keep_original";
}

function attachmentReactionScope() {
  const checked = byId("attachmentForm").querySelector("input[name=attachmentReactionScope]:checked");
  return checked ? checked.value : "private";
}

async function commitAttachment() {
  if (!currentAttachment) return;
  const payload = {
    attachment_id: currentAttachment.attachment_id,
    decisions: attachmentDecisions(),
    manual_description: byId("attachmentDescriptionInput").value.trim() || null,
    retention: attachmentRetention(),
    reaction_scope: attachmentReactionScope(),
    binding_confirmed: byId("attachmentBindingConfirm").checked,
  };
  byId("attachmentCommitButton").disabled = true;
  try {
    const result = await api.post("/api/v1/attachments/commit", payload);
    renderDashboard(result.dashboard);
    if (result.capture) renderCaptureStatus(result.capture);
    const facts = (result.facts || []).length;
    const props = (result.props || []).length;
    const counts = result.reaction?.counts || {};
    const spread = result.reaction?.scope === "private"
      ? "외부 반응 없음"
      : `기사 ${counts.articles || 0} · 게시판 ${counts.boards || 0} · SNS ${counts.social_posts || 0}`;
    toast(`이미지 내용을 반영했습니다. 사실 ${facts}건, 창작 소재 ${props}건 · ${spread}.`, "good");
    byId("attachmentDialog").close();
    currentAttachment = null;
  } catch (error) {
    toast(error.message, "error");
    byId("attachmentCommitButton").disabled = false;
  }
}

async function discardAttachment() {
  if (!currentAttachment) { byId("attachmentDialog").close(); return; }
  try {
    await api.post("/api/v1/attachments/discard", { attachment_id: currentAttachment.attachment_id });
    toast("이미지 분석 결과를 폐기했습니다. 원본은 그대로 남습니다.", "info");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    byId("attachmentDialog").close();
    currentAttachment = null;
  }
}

async function enrichAttachmentWithLlm() {
  if (!currentAttachment) return;
  const button = byId("attachmentLlmButton");
  const provider = button.dataset.provider || "local_llm";
  if (provider === "gemini" && !window.confirm("현재 이미지 1장을 Google Gemini API로 전송해 화면 내용을 해석할까요? 결과는 검증되지 않은 힌트로만 표시되고 자동 저장되지 않습니다.")) return;
  button.disabled = true;
  try {
    const result = await api.post("/api/v1/attachments/analyze-llm", { name: currentAttachment.file_name, provider, consent: true });
    const hint = byId("attachmentLlmHint");
    hint.hidden = false;
    hint.textContent = `[${result.provider === "gemini" ? "GEMINI 이미지 힌트" : "로컬 LLM 힌트"} · 검증되지 않음 · ${result.model || ""}] ${result.observations_text || ""}`;
    currentAttachmentLlmText = result.observations_text || "";
    byId("attachmentUseLlmHintButton").hidden = !currentAttachmentLlmText;
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function useAttachmentLlmHint() {
  if (!currentAttachmentLlmText) return;
  const description = byId("attachmentDescriptionInput");
  description.value = currentAttachmentLlmText.slice(0, Number(description.maxLength) || 500);
  description.focus();
  toast("해석 힌트를 설명칸에 옮겼습니다. 내용을 직접 확인·수정한 뒤 공개 범위를 선택해 주세요.", "info");
}

async function removeCapture(name) {
  captureMutationActive = true;
  try {
    const payload = await api.post("/api/v1/captures/remove", { name });
    renderCaptureStatus(payload.captures, false);
    toast("시각 힌트 한 장을 삭제했습니다.", "good");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    captureMutationActive = false;
  }
}

async function capture(count = 1) {
  byId("captureButton").disabled = true;
  byId("burstCaptureButton").disabled = true;
  captureMutationActive = true;
  try {
    const payload = await api.post("/api/v1/capture", { count, interval_ms: 650 });
    if (!payload.capture.ok) throw new Error(payload.capture.message);
    await refreshCaptures(false);
    toast(payload.capture.message, "good");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    captureMutationActive = false;
    byId("captureButton").disabled = false;
    byId("burstCaptureButton").disabled = false;
  }
}

function bindEvents() {
  byId("tabbar").addEventListener("click", event => {
    const tab = event.target.closest("[data-view]");
    if (tab) switchView(tab.dataset.view);
  });
  byId("tabbar").addEventListener("keydown", event => {
    if (!event.target.matches('[role="tab"]')) return;
    const tabs = [...byId("tabbar").querySelectorAll('[role="tab"]')];
    const current = tabs.indexOf(event.target);
    let next = null;
    if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
    if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    if (next === null) return;
    event.preventDefault();
    switchView(tabs[next].dataset.view);
    tabs[next].focus();
  });
  document.addEventListener("click", event => {
    const control = event.target.closest("[data-switch-view]");
    if (control) switchView(control.dataset.switchView);
  });
  byId("checkButton").addEventListener("click", () => startJob("/api/v1/jobs/check", currentOptions()));
  byId("enrichFeedButton").addEventListener("click", () => startJob("/api/v1/jobs/feed", currentOptions()));
  byId("enrichArticlesButton").addEventListener("click", () => startJob("/api/v1/jobs/feed/articles", currentOptions()));
  byId("enrichCommunityButton").addEventListener("click", () => startJob("/api/v1/jobs/feed/community", currentOptions()));
  byId("localEnrichArticlesButton").addEventListener("click", () => startJob("/api/v1/jobs/feed/articles/local", currentOptions()));
  byId("localEnrichCommunityButton").addEventListener("click", () => startJob("/api/v1/jobs/feed/community/local", currentOptions()));
  byId("jobCancelButton").addEventListener("click", async () => {
    const jobId = byId("jobCancelButton").dataset.jobId || activeJobId;
    if (!jobId) return;
    try {
      const payload = await api.post("/api/v1/jobs/cancel", { id: jobId });
      renderJob(payload.job);
      toast("작업을 취소했습니다.", "info");
    } catch (error) {
      toast(error.message || "작업을 취소하지 못했습니다.", "error");
    }
  });
  byId("narrativeButton").addEventListener("click", () => startJob("/api/v1/jobs/narrative", currentOptions()));
  byId("localNarrativeButton").addEventListener("click", () => startJob("/api/v1/jobs/narrative/local", currentOptions()));
  byId("settingsButton").addEventListener("click", () => openSettings());
  for (const kind of ["community", "media"]) {
    byId(`${kind}Filter`).addEventListener("change", () => applyReadingFilters(kind));
    byId(`${kind}Search`).addEventListener("input", () => applyReadingFilters(kind));
    byId(`${kind}ResetFilter`).addEventListener("click", () => {
      byId(`${kind}Filter`).value = "";
      byId(`${kind}Search`).value = "";
      applyReadingFilters(kind);
    });
  }
  byId("communityStyleButton").addEventListener("click", () => {
    openSettings();
    byId("settingsVoice").scrollIntoView({ block: "start", behavior: "instant" });
    byId("languageLevelInput").focus({ preventScroll: true });
  });
  byId("localLlmStartButton").addEventListener("click", startLocalModel);
  byId("localLlmStopButton").addEventListener("click", stopLocalModel);
  byId("localLlmRefreshButton").addEventListener("click", () => refreshLocalModelStatus({ notify: true }));
  byId("localLlmLauncherInput").addEventListener("input", () => renderLocalModelControl(dashboard()?.app?.local_model || {}));
  byId("geminiTestButton").addEventListener("click", testGemini);
  byId("geminiClearButton").addEventListener("click", clearGeminiKey);
  byId("notificationTestButton").addEventListener("click", () => requestNotificationAccess({ test: true }));
  byId("notificationsEnabledInput").addEventListener("change", async event => {
    if (event.target.checked) await requestNotificationAccess();
    else renderNotificationState({ ...(dashboard()?.config || {}), notifications_enabled: false });
  });
  byId("addSeasonButton").addEventListener("click", () => openSeasonDialog());
  byId("addHonorButton").addEventListener("click", () => openHonorDialog());
  byId("editLeagueButton").addEventListener("click", openLeagueDialog);
  byId("editValueButton").addEventListener("click", openValueDialog);
  byId("storyEventButton").addEventListener("click", () => createStoryTurn(false));
  byId("storyChatButton").addEventListener("click", () => createStoryTurn(true));
  for (const id of ["storySituation", "storyTarget", "storyInteraction", "storyCounterpart", "storyInteractionMode"]) {
    byId(id).addEventListener("change", refreshStoryInteraction);
  }
  for (const id of ["storySituation", "storyTarget", "storyCounterpart", "storyInteractionMode", "storyParticipantName", "storyInteractionPlace", "storyInteractionTopic", "storyLearningProgress"]) {
    byId(id).addEventListener("change", () => { byId("storyActionConfirmed").checked = false; });
  }
  byId("worldContextBasis").addEventListener("change", refreshWorldContextFields);
  byId("worldContextVisibility").addEventListener("change", refreshWorldContextFields);
  byId("saveWorldContextButton").addEventListener("click", saveWorldContext);
  byId("cancelWorldContextButton").addEventListener("click", resetWorldContextForm);
  byId("saveWorldCounterpartButton").addEventListener("click", saveWorldCounterpart);
  byId("cancelWorldCounterpartButton").addEventListener("click", resetWorldCounterpartForm);
  byId("storyWorldbook").addEventListener("click", event => {
    const editContext = event.target.closest("[data-edit-world-context]");
    if (editContext) { editWorldContext(editContext.dataset.editWorldContext); return; }
    const retireContext = event.target.closest("[data-retire-world-context]");
    if (retireContext) { retireWorldContext(retireContext.dataset.retireWorldContext); return; }
    const editCounterpart = event.target.closest("[data-edit-world-counterpart]");
    if (editCounterpart) { editWorldCounterpart(editCounterpart.dataset.editWorldCounterpart); return; }
    const retireCounterpart = event.target.closest("[data-retire-world-counterpart]");
    if (retireCounterpart) retireWorldCounterpart(retireCounterpart.dataset.retireWorldCounterpart);
  });
  byId("storyCategoryGrid").addEventListener("click", event => {
    const category = event.target.closest("[data-story-category]");
    if (category) selectStoryCategory(category.dataset.storyCategory);
  });
  byId("storyTimeline").addEventListener("click", event => {
    const followup = event.target.closest("[data-story-followup]");
    if (!followup) return;
    selectInteraction(followup.dataset.interactionId);
    byId("storyText").value = followup.dataset.storyFollowup;
    byId("storyText").focus();
  });
  byId("conversationList").addEventListener("click", event => {
    const confirm = event.target.closest("[data-confirm-turn]");
    if (confirm) { confirmProposal(confirm.dataset.confirmTurn, confirm.dataset.confirmProposal); return; }
    const followup = event.target.closest("[data-story-followup]");
    if (!followup) return;
    selectInteraction(followup.dataset.interactionId);
    byId("storyText").value = followup.dataset.storyFollowup;
    byId("storyText").focus();
  });
  byId("storyRelinkButton").addEventListener("click", relinkUniverse);
  for (const id of ["preservedList", "settingsPreservedList"]) {
    byId(id).addEventListener("click", event => {
      const capsule = event.target.closest("[data-museum-capsule]");
      if (capsule) { openMuseumCapsule(capsule.dataset.museumCapsule, capsule.dataset.museumDate); return; }
      const universe = event.target.closest("[data-museum-universe]");
      if (universe) openMuseumDates(universe.dataset.museumUniverse);
    });
  }
  byId("emptySettingsButton").addEventListener("click", () => openSettings(true));
  byId("emptyRescanButton").addEventListener("click", () => openSettings(true));
  byId("rescanButton").addEventListener("click", () => openSettings(true));
  byId("settingsRescanButton").addEventListener("click", () => { showSaveCandidates(); showPreservedLibrary(); });
  byId("settingsJumpbar").addEventListener("click", event => {
    const button = event.target.closest("[data-settings-section]");
    if (!button) return;
    const section = byId(button.dataset.settingsSection);
    section.scrollIntoView({ block: "start", behavior: "instant" });
    section.focus({ preventScroll: true });
  });
  byId("saveSettingsButton").addEventListener("click", saveSettings);
  byId("saveSeasonButton").addEventListener("click", saveSeason);
  byId("saveHonorButton").addEventListener("click", saveHonor);
  byId("saveStandingsButton").addEventListener("click", saveStandings);
  byId("saveLeaderboardButton").addEventListener("click", saveLeaderboard);
  byId("saveValueButton").addEventListener("click", saveValueContext);
  byId("heatInput").addEventListener("input", event => updateHeatOutput(event.target.value));
  byId("languageLevelInput").addEventListener("input", event => updateLanguageLevelOutput(event.target.value));
  byId("captureButton").addEventListener("click", () => capture(1));
  byId("burstCaptureButton").addEventListener("click", () => capture(3));
  byId("imageInput").addEventListener("change", event => uploadFiles(event.target.files));
  byId("themeButton").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
  byId("captureGallery").addEventListener("click", event => {
    const read = event.target.closest("[data-read-capture]");
    if (read) { openAttachmentReview(read.dataset.readCapture); return; }
    const remove = event.target.closest("[data-remove-capture]");
    if (remove) removeCapture(remove.dataset.removeCapture);
  });
  document.addEventListener("keydown", event => {
    if (event.ctrlKey || event.shiftKey || event.altKey || event.metaKey || event.repeat || captureHotkeysActive) return;
    if (event.key === "F8") { event.preventDefault(); capture(1); }
    if (event.key === "F9") { event.preventDefault(); capture(3); }
  });
  byId("settingsDialog").addEventListener("click", event => {
    if (event.target === byId("settingsDialog")) byId("settingsDialog").close();
  });
  byId("settingsDialog").addEventListener("close", () => {
    if (localModelPollTimer) window.clearTimeout(localModelPollTimer);
    localModelPollTimer = null;
    scheduleLocalModelPoll(dashboard()?.app?.local_model || {});
  });
  byId("attachmentDiscardButton").addEventListener("click", () => discardAttachment());
  byId("attachmentCommitButton").addEventListener("click", () => commitAttachment());
  byId("attachmentLlmButton").addEventListener("click", () => enrichAttachmentWithLlm());
  byId("attachmentUseLlmHintButton").addEventListener("click", () => useAttachmentLlmHint());
  for (const id of ["seasonDialog", "honorDialog", "leagueDialog", "valueDialog", "historyDialog", "archiveDialog", "attachmentDialog"]) {
    byId(id).addEventListener("click", event => {
      if (event.target === byId(id)) byId(id).close();
    });
  }
  byId("panel-records").addEventListener("click", event => {
    const editSeason = event.target.closest("[data-edit-season]");
    if (editSeason) { openSeasonDialog(editSeason.dataset.editSeason); return; }
    const editHonor = event.target.closest("[data-edit-honor]");
    if (editHonor) { openHonorDialog(editHonor.dataset.editHonor); return; }
    const remove = event.target.closest("[data-remove-career]");
    if (remove) { removeCareerItem(remove.dataset.removeCareer, remove.dataset.itemId); return; }
    const archive = event.target.closest("[data-open-archive]");
    if (archive) { openArchive(archive.dataset.openArchive); return; }
    const history = event.target.closest("[data-open-history]");
    if (history) { openHistoryCapsule(history.dataset.openHistory); return; }
    const value = event.target.closest("[data-open-value]");
    if (value) openValueDialog();
  });
}

initStoryDesk({startJob, toast, renderDashboard});
initConnectedStory({startJob, toast, renderDashboard, switchView});
initWorkspace({switchView, openAI:()=>{
  openSettings();
  byId("settingsAI").scrollIntoView({block:"start", behavior:"instant"});
  byId("aiProviderInput").focus({preventScroll:true});
}});
initActivityConsole({showResult:showWorkspacePart});
bindEvents();
loadBootstrap();
