import * as api from "./api.js";
import {getLinkedSourceIds, clearLinkedSources} from "./connected-story.js";

const el = id => document.getElementById(id);
const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
let hooks = {}, data = {}, app = {}, markdown = escape, worldKey = "", busy = false, pending = null;
let selectedImages = new Set(), loadedTurns = [], rememberedRows = "";
let editorOrigin = null, referenceOrigin = null;
const drafts = new Map();
const draftFields = ["directorText", "directorAction", "directorMode", "directorVisibility", "directorLength"];

function canWrite() {
  return Boolean(data.origin) && !app.empty && app.world?.read_only !== true
    && app.world?.binding?.live !== false && app.world?.capabilities?.read_only !== true;
}
function refreshButtons() {
  for (const id of ["directorNote", "directorNewMemory", "directorImport"]) el(id).disabled = busy || !canWrite();
  for (const button of document.querySelectorAll("[data-director-edit], [data-director-retire], [data-director-remember]")) button.disabled = busy || !canWrite();
  el("directorLocal").disabled = busy || !canWrite() || !app.app?.llm_reachable;
  el("directorGemini").disabled = busy || !canWrite() || !app.config?.gemini_consent || !app.config?.gemini_key_present;
  el("directorStatus").textContent = busy ? "작성 중입니다. 진행 확인과 취소는 하단 작업 콘솔에서 가능합니다. 실패하면 입력과 기존 기록을 유지합니다."
    : "직접 기록은 모델 없이 사용합니다. 로컬·Gemini는 사용 가능한 엔진만 선택할 수 있으며 자동 전환·자동 실행하지 않습니다.";
}

export function setDirectorBusy(value) { busy = value; if (el("directorDesk")) refreshButtons(); }

function renderActions() {
  const rows = data.catalog?.actions || [];
  const query = el("directorActionSearch").value.toLowerCase().trim();
  const group = el("directorGroup").value;
  const current = el("directorAction").value || "story_free";
  const filtered = rows.filter(row => (!group || row.group === group) && (!query || `${row.label} ${row.note}`.toLowerCase().includes(query)));
  el("directorAction").innerHTML = filtered.map(row => `<option value="${escape(row.id)}">${escape(row.group)} · ${escape(row.label)}</option>`).join("");
  if (filtered.some(row => row.id === current)) el("directorAction").value = current;
  el("directorActionCount").textContent = `${filtered.length} / ${rows.length}`;
  renderRule();
}

function renderRule() {
  const row = data.catalog?.actions?.find(row => row.id === el("directorAction").value);
  el("directorRule").innerHTML = row ? `<strong>${escape(row.status_label)}</strong> · ${escape(row.note)} <a href="${escape(row.source_detail.url)}" target="_blank" rel="noopener noreferrer">근거 보기</a>` : "검색 결과가 없습니다. 분류나 검색어를 바꿔 주세요.";
  const fiction = !row || ["fiction", "fiction_only"].includes(row.status);
  el("directorMode").options[1].disabled = fiction;
  if (fiction) el("directorMode").value = "fiction";
  el("directorObservedField").classList.toggle("is-hidden", el("directorMode").value !== "observed");
}

export function refreshDirectorCaptures(items) {
  if (!el("directorImages")) return;
  const available = new Set((items || []).map(row => row.name));
  selectedImages = new Set([...selectedImages].filter(name => available.has(name)));
  el("directorImages").innerHTML = (items || []).map(row => `<label class="director-image"><input type="checkbox" data-director-image="${escape(row.name)}" ${selectedImages.has(row.name) ? "checked" : ""}><img src="/api/v1/capture-image/${encodeURIComponent(row.name)}" alt="첨부 후보" loading="lazy"><span>${escape(row.name)}</span></label>`).join("") || '<p class="muted">추가한 이미지가 없습니다.</p>';
  el("directorImageCount").textContent = `${selectedImages.size}장`;
}

function renderTurns() {
  const area = el("directorTurns");
  const signature = JSON.stringify(loadedTurns.map(row => row.id));
  if (rememberedRows === signature) return;
  rememberedRows = signature;
  const nearEnd = area.scrollHeight - area.scrollTop - area.clientHeight < 100;
  area.innerHTML = loadedTurns.map(row => `<article class="director-turn" data-director-turn="${escape(row.id)}">
    <header><span>${escape(row.game_date)} · ${escape(row.label)}</span><span>${row.model ? escape(row.model) : "직접 기록"} · ${escape(data.visibility?.[row.visibility] || row.visibility)}</span></header>
    <div class="director-user"><span>내가 이끄는 장면</span><p>${escape(row.text)}</p></div>
    ${row.model ? `<div class="director-reply narrative">${markdown(row.reply)}</div>` : ""}
    ${row.images?.length ? `<div class="director-stored-images">${row.images.map(img => `<a href="/api/v1/story-desk/image/${encodeURIComponent(img.file)}" target="_blank" rel="noopener noreferrer"><img src="/api/v1/story-desk/image/${encodeURIComponent(img.file)}" loading="lazy" alt="${escape(img.name)}"></a>`).join("")}</div>` : ""}
    <footer><span>창작 기록 · ${row.recall?.turn_ids?.length || 0}개 장면 / ${row.recall?.memory_ids?.length || 0}개 기억 참고${row.remote_allowed ? " · Gemini 재참고 허용" : " · 로컬 보관"}</span><div class="button-row"><button type="button" class="button button--quiet button--compact" data-director-remember="${escape(row.id)}">내 입력 기억하기</button>${row.model ? `<button type="button" class="button button--quiet button--compact" data-director-remember="${escape(row.id)}" data-remember-reply="true">응답에서 기억 정리</button>` : ""}${["public", "social"].includes(row.visibility) ? ["article", "community"].map(channel=>`<button type="button" class="button button--quiet button--compact" data-cross-link="${channel}" data-source-id="desk:${escape(row.id)}" data-source-title="${escape(row.label)}">${channel === "article" ? "기사" : "커뮤니티"}로 연결</button>`).join("") : ""}</div></footer>
  </article>`).join("") || '<div class="empty-inline card">첫 장면을 적어 주세요. 짧은 행동, 대사 한마디, 사진 속 사물도 이야기의 시작이 됩니다.</div>';
  if (nearEnd) area.scrollTop = area.scrollHeight;
}

export function renderStoryDesk(next, renderer) {
  if (!el("directorDesk")) return;
  app = next || {};
  data = app.story_desk || {};
  if (renderer) markdown = renderer;
  const key = JSON.stringify([data.origin?.world_id, data.origin?.player_id, data.origin?.generation, data.origin?.game_date]);
  if (worldKey !== key) {
    if (worldKey) drafts.set(worldKey, Object.fromEntries(draftFields.map(id => [id, el(id).value])));
    worldKey = key;
    selectedImages.clear(); loadedTurns = []; rememberedRows = ""; pending = null;
    for (const id of ["directorObserved", "directorImagesConfirmed", "directorRemoteConsent", "directorRemoteRecall", "directorRemember"]) el(id).checked = false;
    el("directorActionSearch").value = "";
    el("directorGroup").innerHTML = '<option value="">모든 분류</option>' + [...new Set((data.catalog?.actions || []).map(row => row.group))].map(group => `<option>${escape(group)}</option>`).join("");
    el("directorAction").innerHTML = "";
    renderActions();
    const saved = drafts.get(key) || {directorText:"", directorAction:"story_free", directorMode:"fiction", directorVisibility:"private", directorLength:"standard"};
    for (const id of draftFields) el(id).value = saved[id] || "";
    renderRule();
  }
  if (pending && next.run?.director_request_id === pending.id && pending.key === worldKey) {
    if (el("directorText").value === pending.text && JSON.stringify([...selectedImages]) === pending.images) {
      el("directorText").value = "";
      clearLinkedSources("story");
      selectedImages.clear(); el("directorImagesConfirmed").checked = false; el("directorObserved").checked = false;
    }
    pending = null;
  }
  const latest = data.turns || [];
  loadedTurns = [...loadedTurns.filter(row => !latest.some(other => other.id === row.id)), ...latest];
  el("directorOwner").textContent = `${app.player?.name || "선수 미연결"} · ${data.origin?.game_date || "—"}`;
  el("directorCoverage").textContent = data.catalog?.coverage_note || "";
  el("directorTurnCount").textContent = `${data.total || 0}개 장면 보관`;
  el("directorOlder").classList.toggle("is-hidden", !data.has_more || loadedTurns.length >= (data.total || 0));
  el("directorMemoryCount").textContent = `${data.memories?.length || 0}개`;
  el("directorMemories").innerHTML = (data.memories || []).map(row => `<article><header><strong>${row.pinned ? "고정 · " : ""}${escape(row.label)}</strong><span>${escape(data.kinds?.[row.kind])} · ${row.remote_allowed ? "Gemini 허용" : "로컬 전용"}</span></header><p>${escape(row.detail)}</p><div class="button-row"><button type="button" class="button button--quiet button--compact" data-director-edit="${escape(row.id)}">수정</button><button type="button" class="button button--quiet button--compact" data-director-retire="${escape(row.id)}">이후 회상에서 제외</button></div></article>`).join("") || '<p class="muted">아직 고정한 기억이 없습니다. 대화 원문은 별도로 계속 보관됩니다.</p>';
  renderTurns(); refreshDirectorCaptures(app.captures?.items || []); refreshButtons();
}

function openMemory(row = {}) {
  editorOrigin = structuredClone(data.origin);
  el("directorMemoryId").value = row.id || "";
  el("directorMemoryKind").innerHTML = Object.entries(data.kinds || {}).map(([id,label]) => `<option value="${id}">${escape(label)}</option>`).join("");
  el("directorMemoryKind").value = row.kind || "background";
  el("directorMemoryLabel").value = row.label || "";
  el("directorMemoryDetail").value = row.detail || "";
  el("directorMemorySource").value = row.source_url || "";
  el("directorMemoryPinned").checked = row.pinned === true;
  el("directorMemoryRemote").checked = row.remote_allowed === true;
  el("directorMemoryVisibility").value = row.visibility || "private";
  el("directorMemoryDialog").showModal();
}

async function mutateMemory(payload, dialog) {
  const result = await api.post("/api/v1/story-desk/memory", payload);
  hooks.renderDashboard(result.dashboard);
  dialog?.close(); hooks.toast("세계선 기억을 저장했습니다.", "good");
}

async function send(provider) {
  if (busy || !canWrite()) return;
  const action = data.catalog?.actions?.find(row => row.id === el("directorAction").value);
  if (!action) { hooks.toast("행동을 먼저 선택해 주세요.", "error"); return; }
  const text = el("directorText").value.trim();
  if (!text && action.id === "story_free") { el("directorText").focus(); return; }
  if (provider === "gemini" && !el("directorRemoteConsent").checked) { hooks.toast("이번 Gemini 전송에 동의해 주세요.", "error"); return; }
  if (selectedImages.size && !el("directorImagesConfirmed").checked) { hooks.toast("선택한 이미지의 선수·날짜 연결을 확인해 주세요.", "error"); return; }
  if (el("directorRemember").checked && text.length > 4000) { hooks.toast("4,000자가 넘는 고정 기억은 나눠 저장해 주세요. 대화 원문 전체는 보관할 수 있습니다.", "error"); return; }
  const id = crypto.randomUUID();
  pending = {id, key:worldKey, text:el("directorText").value, images:JSON.stringify([...selectedImages])};
  await hooks.startJob("/api/v1/jobs/director", {desk_origin:data.origin, request_id:id, action_id:action.id,
    source_ids:getLinkedSourceIds("story"),
    text, provider, visibility:el("directorVisibility").value, length:el("directorLength").value,
    mode:el("directorMode").value, action_confirmed:el("directorObserved").checked,
    images:[...selectedImages], images_confirmed:el("directorImagesConfirmed").checked,
    remote_consent:el("directorRemoteConsent").checked, allow_remote_recall:el("directorRemoteRecall").checked,
    remember_input:el("directorRemember").checked});
}

export function initStoryDesk(callbacks) {
  hooks = callbacks;
  const safe = handler => async event => { try { await handler(event); } catch (error) { hooks.toast(error.message, "error"); } };
  for (const id of ["directorGroup", "directorActionSearch"]) el(id).addEventListener("input", renderActions);
  for (const id of ["directorAction", "directorMode"]) el(id).addEventListener("change", () => { el("directorObserved").checked = false; renderRule(); });
  for (const [id, provider] of [["directorNote","note"],["directorLocal","local"],["directorGemini","gemini"]]) el(id).addEventListener("click", safe(() => send(provider)));
  el("directorText").addEventListener("keydown", safe(async event => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      await send(app.config?.ai_provider === "gemini" ? "gemini" : app.app?.llm_reachable ? "local" : "note");
    }
  }));
  el("directorImages").addEventListener("change", event => {
    const name = event.target.dataset.directorImage;
    if (!name) return;
    if (event.target.checked && selectedImages.size >= 4) { event.target.checked = false; hooks.toast("최대 4장까지 선택할 수 있습니다.", "error"); return; }
    if (event.target.checked) selectedImages.add(name); else selectedImages.delete(name);
    el("directorImagesConfirmed").checked = false;
    el("directorImageCount").textContent = `${selectedImages.size}장`;
  });
  el("directorImageInput").addEventListener("change", safe(async event => {
    const key = worldKey;
    for (const file of [...event.target.files].slice(0, 4 - selectedImages.size)) {
      if (file.size > 12 * 1024 * 1024) throw new Error("각 이미지가 12MB 이하여야 합니다.");
      const encoded = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); });
      if (key !== worldKey) throw new Error("세계선이 바뀌었습니다. 이미지를 다시 선택해 주세요.");
      const result = await api.post("/api/v1/upload", {data:encoded});
      if (key !== worldKey) throw new Error("이미지 추가 중 세계선이 바뀌어 자동 선택하지 않았습니다.");
      if (result.image?.name) selectedImages.add(result.image.name);
    }
    event.target.value = ""; el("directorImagesConfirmed").checked = false;
    const result = await api.get("/api/v1/captures");
    if (key === worldKey) refreshDirectorCaptures(result.captures?.items || []);
  }));
  el("directorNewMemory").addEventListener("click", () => openMemory());
  el("directorMemoryForm").addEventListener("submit", safe(async event => {
    event.preventDefault(); await mutateMemory({desk_origin:editorOrigin, id:el("directorMemoryId").value,
      kind:el("directorMemoryKind").value, label:el("directorMemoryLabel").value, detail:el("directorMemoryDetail").value,
      source_url:el("directorMemorySource").value, pinned:el("directorMemoryPinned").checked,
      remote_allowed:el("directorMemoryRemote").checked, visibility:el("directorMemoryVisibility").value}, el("directorMemoryDialog"));
  }));
  el("directorMemories").addEventListener("click", safe(async event => {
    const edit = event.target.closest("[data-director-edit]");
    if (edit) openMemory(data.memories.find(row => row.id === edit.dataset.directorEdit));
    const retire = event.target.closest("[data-director-retire]");
    if (retire) await mutateMemory({desk_origin:data.origin, operation:"retire", id:retire.dataset.directorRetire});
  }));
  el("directorTurns").addEventListener("click", event => {
    const button = event.target.closest("[data-director-remember]");
    if (!button) return;
    const row = loadedTurns.find(row => row.id === button.dataset.directorRemember);
    const text = button.dataset.rememberReply ? row.reply : row.text;
    openMemory({label:row.label, detail:text.slice(0, 4000)});
    if (text.length > 4000) hooks.toast("기억 편집에 앞 4,000자를 옮겼습니다. 필요한 내용으로 정리해 주세요. 대화 원문 전체는 그대로 보관되어 있습니다.");
  });
  el("directorOlder").addEventListener("click", safe(async () => {
    const key = worldKey;
    el("directorOlder").disabled = true;
    try {
      const result = await api.get(`/api/v1/story-desk?before=${encodeURIComponent(loadedTurns[0]?.id || "")}`);
      if (key !== worldKey || result.story_desk.origin.world_id !== data.origin.world_id) return;
      loadedTurns = [...result.story_desk.turns.filter(row => !loadedTurns.some(other => other.id === row.id)), ...loadedTurns];
      el("directorOlder").classList.toggle("is-hidden", !result.story_desk.has_more); renderTurns();
    } finally { el("directorOlder").disabled = false; }
  }));
  el("directorImport").addEventListener("click", () => {
    referenceOrigin = structuredClone(data.origin);
    el("directorReferenceNote").textContent = data.reference_pack.note;
    el("directorReferenceRemote").checked = false;
    el("directorReferenceItems").innerHTML = data.reference_pack.items.map(row => `<label class="director-reference"><input type="checkbox" value="${escape(row.id)}"><span><strong>${escape(row.label)}</strong><span>${escape(row.detail)}</span></span></label>`).join("");
    el("directorReferenceDialog").showModal();
  });
  el("directorReferenceSave").addEventListener("click", safe(async () => {
    await mutateMemory({desk_origin:referenceOrigin, operation:"import_reference", remote_allowed:el("directorReferenceRemote").checked,
      ids:[...el("directorReferenceItems").querySelectorAll("input:checked")].map(input => input.value)}, el("directorReferenceDialog"));
  }));
  for (const button of document.querySelectorAll("[data-director-close]")) button.addEventListener("click", () => button.closest("dialog").close());
}
