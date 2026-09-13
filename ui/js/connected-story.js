import * as api from "./api.js";
import {openSharedTools, showWorkspacePart} from "./workspace.js";

const el = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const labels = {article:"기사", community:"커뮤니티", story:"서사"};
let hooks = {}, app = {}, markdown = esc, worldKey = "", busy = false, periodKind = "day", museum = null;
let reading = null, readSequence = 0, contextDirty = false, pending = {}, lastRevision = "";
const selections = {article:new Set(), community:new Set(), story:new Set()};
const sourceRows = {article:[], community:[], story:[]};
const loaded = {article:[], community:[]}, rendered = {}, sourceNames = new Map(), drafts = new Map();

function writable() { return Boolean(app.chronicle?.origin) && !app.empty && app.world?.read_only !== true && app.world?.binding?.live !== false && app.world?.capabilities?.read_only !== true; }
function sourceControls(channel) {
  return `<details class="card connected-links"><summary>다른 기사·커뮤니티·서사를 연결 <span id="${channel}LinkCount">0개 선택</span></summary><div class="connected-links__body">
    <div class="button-row"><button class="button button--quiet button--compact" type="button" data-source-load="${channel}">이 세계선 자료 불러오기</button><label class="field"><span>자료 검색</span><input type="search" data-source-search="${channel}" placeholder="날짜·제목·종류"></label></div>
    <p class="muted">최대 8개. 공개 채널은 비공개 장면을 연결하지 않으며, Gemini는 별도 전송 허용도 필요합니다.</p>
    <div id="${channel}SelectedSources" class="connected-selected"></div>
    <div id="${channel}SourceChoices" class="connected-source-list" tabindex="0" role="region" aria-label="${labels[channel]}에 연결할 자료 목록"></div>
  </div></details>`;
}

function mount(channel) {
  el(`${channel}Conversation`).innerHTML = `<article class="card connected-compose"><header><div><span class="kicker">CONNECTED ${channel === "article" ? "NEWSROOM" : "CONVERSATION"}</span><h3>${labels[channel]} 대화 작업실</h3></div><span id="${channel}ConversationCount" class="badge">0개</span></header>
    <p class="muted">${channel === "article" ? "제보·기사 초안·편집 방향을 적고 후속 보도를 함께 만드세요." : "원글을 쓰거나 이전 댓글에 답하고, 기사·서사의 흐름을 팬들의 대화로 이어가세요."} 공개 창작이며 실제 게시나 실제 인물의 발언이 아닙니다.</p>
    <label class="field"><span>${channel === "article" ? "내용·제보·편집 지시" : "내가 쓰는 글·댓글·대화 방향"}</span><textarea id="${channel}Compose" rows="4" maxlength="8000" placeholder="${channel === "article" ? "오늘 인터뷰를 바탕으로 기사를 써줘. 직전 상대팀 OB의 평가와 다음 상대팀 선수의 경계심을 다른 관점으로 담아줘." : "방금 나온 기사에 상대 팬이 반박하고, 홈 팬이 농담으로 받아치는 흐름을 이어줘."}"></textarea></label>
    <div id="${channel}LinkControls">${sourceControls(channel)}</div>
    <div><button class="button button--quiet button--compact" type="button" data-open-voices>상대팀·OB·선수 참고 설정</button></div>
    <div class="director-controls"><label class="field"><span>분량</span><select id="${channel}Length"><option value="short">짧게</option><option value="standard" selected>충분히</option><option value="long">길게</option></select></label></div>
    <label class="checkbox"><input id="${channel}RemoteRecall" type="checkbox"><span>로컬·직접 작성한 이 내용도 이후 Gemini에서 참고 허용</span></label>
    <label class="checkbox"><input id="${channel}RemoteConsent" type="checkbox"><span>이번 입력·허용된 연결 문맥의 Google 전송에 동의</span></label>
    <div class="button-row"><button id="${channel}Note" class="button button--quiet" data-channel-send="${channel}" data-provider="note" type="button">입력만 기록</button><button id="${channel}Local" class="button button--secondary" data-channel-send="${channel}" data-provider="local" type="button">로컬로 작성·답변</button><button id="${channel}Gemini" class="button button--primary" data-channel-send="${channel}" data-provider="gemini" type="button">Gemini로 작성·답변</button></div>
    <p class="muted">성적·수상은 검증 원장을 유지합니다. 아래 대화는 기존 피드를 덮어쓰지 않으며 다른 채널과 종합 스토리에서 참고할 수 있습니다.</p></article>
    <button id="${channel}Older" class="button button--quiet button--compact is-hidden" data-channel-older="${channel}" type="button">이전 대화 더 보기</button><div id="${channel}ConversationTurns" class="connected-turns" tabindex="0" role="region" aria-label="${labels[channel]} 대화 기록"></div>`;
}

function refreshButtons() {
  for (const channel of ["article", "community"]) {
    el(`${channel}Note`).disabled = busy || !writable();
    el(`${channel}Local`).disabled = busy || !writable() || !app.app?.llm_reachable;
    el(`${channel}Gemini`).disabled = busy || !writable() || !app.config?.gemini_key_present || !app.config?.gemini_consent;
  }
  el("saveOpponentContext").disabled = busy || !writable() || Boolean(museum);
  const remote = el("chronicleEdition").value === "gemini";
  el("chronicleGenerate").disabled = busy || !writable() || Boolean(museum) || (remote ? !app.config?.gemini_key_present || !app.config?.gemini_consent : !app.app?.llm_reachable);
  el("chronicleRead").disabled = busy || (!writable() && !museum && !app.chronicle?.origin);
  for (const button of document.querySelectorAll("[data-cross-link], [data-source-toggle]")) button.disabled = busy || !writable();
}

export function setConnectedBusy(value) { busy = value; if (el("articleNote")) refreshButtons(); }
export function getLinkedSourceIds(channel="story") { return [...selections[channel]]; }
export function clearLinkedSources(channel) { selections[channel].clear(); if(el(`${channel}LinkCount`)) renderSources(channel); }

function renderSources(channel) {
  if (!el(`${channel}LinkCount`)) return;
  el(`${channel}LinkCount`).textContent = `${selections[channel].size}개 선택`;
  el(`${channel}SelectedSources`).innerHTML = [...selections[channel]].map(id => `<button class="connected-chip" type="button" data-source-remove="${channel}" data-source-id="${esc(id)}">${esc(sourceNames.get(id) || id)} ×</button>`).join("");
  const search = document.querySelector(`[data-source-search="${channel}"]`).value.trim().toLowerCase();
  const all = sourceRows[channel].filter(row => !search || `${row.date} ${row.title} ${labels[row.channel] || row.channel}`.toLowerCase().includes(search));
  el(`${channel}SourceChoices`).innerHTML = all.slice(0,120).map(row => `<div class="connected-source-row"><label><input type="checkbox" data-source-toggle="${channel}" value="${esc(row.id)}" ${selections[channel].has(row.id) ? "checked" : ""}><span><strong>${esc(row.title)}</strong><small>${esc(row.date)} · ${esc(labels[row.channel] || row.channel)} · ${row.remote_allowed ? "Gemini 허용" : "로컬 전용"}</small></span></label><button class="text-button" type="button" data-source-read="${esc(row.id)}">원문</button></div>`).join("") + (all.length > 120 ? '<p class="muted">최근 120개 표시 · 검색하면 이전 자료도 찾을 수 있습니다.</p>' : "");
  if (el("articleNote")) refreshButtons();
}

async function loadSources(channel) {
  const key = worldKey;
  const value = await api.get(`/api/v1/editorial/sources?channel=${channel}`);
  if (worldKey !== key || value.origin.world_id !== app.story_desk?.origin?.world_id) return;
  sourceRows[channel] = value.sources;
  for (const row of value.sources) sourceNames.set(row.id, row.title);
  renderSources(channel);
}

export function linkToChannel(channel, id, title) {
  if (!writable()) return;
  if (selections[channel].size >= 8 && !selections[channel].has(id)) { hooks.toast("연결 자료는 최대 8개입니다.", "error"); return; }
  selections[channel].add(id); sourceNames.set(id, title || id); renderSources(channel);
  showWorkspacePart(channel === "article" ? "media" : channel === "story" ? "narrative" : "community", channel === "story" ? "chat" : "compose");
  const control = el(channel === "story" ? "directorText" : `${channel}Compose`);
  control.focus(); control.scrollIntoView({block:"center", behavior:"smooth"});
  hooks.toast("연결 자료를 선택했습니다. 이어갈 내용을 입력해 주세요.");
}

function renderTurns(channel) {
  const area = el(`${channel}ConversationTurns`);
  const signature = JSON.stringify(loaded[channel].map(row=>row.id));
  if (rendered[channel] === signature) return;
  rendered[channel] = signature;
  const nearEnd = area.scrollHeight - area.scrollTop - area.clientHeight < 120;
  area.innerHTML = loaded[channel].map(row => `<article class="director-turn" data-channel-turn="${esc(row.id)}"><header><span>${esc(row.game_date)} · ${labels[channel]}</span><span>${esc(row.model || "직접 기록")} · ${row.remote_allowed ? "Gemini 재참고 허용" : "로컬 보관"}</span></header><div class="director-user"><span>내 입력</span><p>${esc(row.text)}</p></div>${row.model ? `<div class="director-reply narrative">${markdown(row.reply)}</div>` : ""}<footer><span>공개 창작 · 연결 ${row.source_ids?.length || 0}개</span><div class="button-row">${["article","community","story"].map(next=>`<button class="button button--quiet button--compact" type="button" data-cross-link="${next}" data-source-id="desk:${esc(row.id)}" data-source-title="${esc(row.label)}">${next===channel ? "이 내용에 답하기" : `${labels[next]}로 연결`}</button>`).join("")}</div></footer></article>`).join("") || '<div class="empty-inline card">아직 대화가 없습니다. 위 입력창에서 첫 내용을 남겨 주세요.</div>';
  if (nearEnd) area.scrollTop = area.scrollHeight;
}

async function sendChannel(channel, provider) {
  if (busy || !writable()) return;
  const text = el(`${channel}Compose`).value.trim();
  if (!text) { el(`${channel}Compose`).focus(); return; }
  if (provider === "gemini" && !el(`${channel}RemoteConsent`).checked) { hooks.toast("이번 Google 전송에 동의해 주세요.", "error"); return; }
  const id = crypto.randomUUID();
  pending[channel] = {id, worldKey, text:el(`${channel}Compose`).value};
  await hooks.startJob("/api/v1/jobs/director", {desk_origin:app.story_desk.origin, request_id:id, channel, action_id:"story_free", mode:"fiction", text,
    provider, visibility:"public", length:el(`${channel}Length`).value, source_ids:[...selections[channel]],
    remote_consent:el(`${channel}RemoteConsent`).checked, allow_remote_recall:el(`${channel}RemoteRecall`).checked});
}

function periodKey() { return el(periodKind === "day" ? "chronicleDate" : periodKind === "month" ? "chronicleMonthInput" : "chronicleYearInput").value; }
function setKind(kind) {
  periodKind = kind; reading = null; readSequence++;
  for (const [key,id] of Object.entries({day:"chronicleDate",month:"chronicleMonthInput",year:"chronicleYearInput"})) el(id).hidden = key !== kind;
  for (const button of document.querySelectorAll("[data-chronicle-kind]")) button.setAttribute("aria-pressed", String(button.dataset.chronicleKind===kind));
  el("chronicleDirectionField").hidden = kind !== "day" || Boolean(museum);
  el("chronicleDateLabel").textContent = kind === "day" ? "게임 날짜" : kind === "month" ? "게임 월" : "게임 연도";
  el("chroniclePending").textContent = "보관본·미반영 자료 확인을 누르면 호출 없이 준비 상태를 확인합니다.";
  el("chronicleHistory").replaceChildren();
}

function renderPeriod(value) {
  reading = value;
  if (!museum && app.chronicle) app.chronicle.origin = value.origin;
  el("chroniclePending").textContent = `${value.key} · ${value.edition === "cloud" ? "Gemini" : "로컬"} 판본 · 미반영 ${value.pending_chunks}조각 / ${value.pending_batches}묶음 · 이미 반영 ${value.unchanged_chunks}조각`;
  const directions = value.directions.length ? `<details class="card connected-context"><summary>데일리 편집 지시 ${value.directions.length}개</summary>${value.directions.map(r=>`<p class="connected-original">${esc(r.text)}</p>`).join("")}</details>` : "";
  el("chronicleHistory").innerHTML = directions + value.checkpoints.map((row,index)=>`<article class="director-turn"><header><strong>${index+1}. ${index ? "추가·정정 종합" : "첫 종합"} · ${esc(row.key)}</strong><span>${esc(row.model)} · ${esc(row.at)}</span></header><div class="director-reply narrative">${markdown(row.text)}</div><footer><details><summary>이번에 반영한 자료 ${row.changes.length}조각</summary><ul>${row.changes.map(change=>`<li>${esc(change.title)} · ${{added:"새 자료",revised:"정정",removed:"제외"}[change.change] || "변경"}</li>`).join("")}</ul></details><span>앞선 원문과 판본은 보관됩니다.</span></footer></article>`).join("") + (value.checkpoints.length ? "" : '<div class="empty-inline card">이 기간의 종합은 아직 없습니다. 원문 자료는 그대로 보관되어 있습니다.</div>');
  refreshButtons();
}

export async function loadSelectedChronicle() {
  if (!periodKey() || (!writable() && !museum && !app.chronicle?.origin)) return;
  const serial = ++readSequence, key = worldKey, target = museum;
  const path = target ? `/api/v1/universes/${encodeURIComponent(target)}/chronicle/${periodKind}/${encodeURIComponent(periodKey())}?provider=${el("chronicleEdition").value}`
    : `/api/v1/chronicle?kind=${periodKind}&key=${encodeURIComponent(periodKey())}&provider=${el("chronicleEdition").value}`;
  const result = await api.get(path);
  if (serial !== readSequence || worldKey !== key || museum !== target) return;
  renderPeriod(result.chronicle);
}

async function generatePeriod() {
  if (busy || !writable() || museum) return;
  const provider = el("chronicleEdition").value;
  if (provider === "gemini" && !el("chronicleRemoteConsent").checked) { hooks.toast("이번 종합의 Google 전송에 동의해 주세요.", "error"); return; }
  const id = crypto.randomUUID();
  pending.period = {id, worldKey, text:el("chronicleDirection").value};
  await hooks.startJob("/api/v1/jobs/chronicle", {chronicle_origin:app.chronicle.origin, request_id:id,
    kind:periodKind, key:periodKey(), provider, text:periodKind === "day" ? el("chronicleDirection").value : "",
    max_calls:Number(el("chronicleCallBudget").value), remote_consent:el("chronicleRemoteConsent").checked});
}

function populateContext(row={}) {
  if (contextDirty) return;
  el("previousOpponent").value = row?.previous_team || "";
  el("upcomingOpponent").value = row?.upcoming_team || "";
  el("opponentPeople").value = (row?.people || []).map(r=>[r.team, {ob:"OB",player:"선수",analyst:"해설"}[r.role], r.name, r.note, r.source_url].join(" | ")).join("\n");
  el("opponentRemote").checked = row?.remote_allowed === true;
  el("opponentContextStatus").textContent = row?.at ? `저장됨 · ${row.game_date} · ${row.remote_allowed ? "Gemini 허용" : "로컬 전용"}` : "직전·다음 팀이 확인되지 않으면 비워 둘 수 있습니다.";
}

export function renderConnectedStory(next, renderer) {
  if (!el("articleNote")) return;
  app = next || {}; if (renderer) markdown = renderer;
  const origin = app.chronicle?.origin || app.story_desk?.origin;
  const key = JSON.stringify([origin?.world_id,origin?.player_id,origin?.generation,origin?.game_date]);
  if (key !== worldKey) {
    if (worldKey) drafts.set(worldKey, {article:el("articleCompose").value, community:el("communityCompose").value, period:el("chronicleDirection").value});
    worldKey = key; museum = null; reading = null; readSequence++; pending = {}; contextDirty = false; lastRevision = ""; sourceNames.clear();
    for (const channel of ["article","community","story"]) { selections[channel].clear(); sourceRows[channel]=[]; renderSources(channel); }
    for (const channel of ["article","community"]) { loaded[channel]=[]; rendered[channel]=""; el(`${channel}Compose`).value=drafts.get(key)?.[channel] || ""; el(`${channel}RemoteConsent`).checked=false; el(`${channel}RemoteRecall`).checked=false; }
    el("chronicleDirection").value=drafts.get(key)?.period || ""; el("chronicleRemoteConsent").checked=false;
    const day = origin?.game_date || ""; el("chronicleDate").value=day; el("chronicleMonthInput").value=day.slice(0,7); el("chronicleYearInput").value=day.slice(0,4);
    setKind("day"); el("chronicleReturnLive").classList.add("is-hidden");
  }
  for (const channel of ["article","community"]) {
    const data = app.editorial_desk?.[channel] || {};
    if (pending[channel] && pending[channel].id === app.run?.director_request_id && pending[channel].worldKey === worldKey) {
      if (el(`${channel}Compose`).value === pending[channel].text) { el(`${channel}Compose`).value=""; selections[channel].clear(); renderSources(channel); }
      pending[channel]=null;
    }
    const latest = data.turns || [];
    loaded[channel]=[...loaded[channel].filter(r=>!latest.some(n=>n.id===r.id)),...latest];
    el(`${channel}ConversationCount`).textContent=`${data.total || 0}개 대화`;
    el(`${channel}Older`).classList.toggle("is-hidden", !data.has_more || loaded[channel].length >= data.total);
    renderTurns(channel);
  }
  if (pending.period && pending.period.id === app.run?.chronicle_request_id && pending.period.worldKey === worldKey) {
    if (el("chronicleDirection").value===pending.period.text) el("chronicleDirection").value="";
    pending.period=null;
  }
  if (!museum) el("chronicleOwner").textContent=`${app.player?.name || "선수 미연결"} · ${origin?.game_date || "—"}`;
  populateContext(app.chronicle?.context);
  el("chronicleLibrary").innerHTML=(app.chronicle?.periods || []).map(row=>`<button class="story-followup" type="button" data-period-open="${esc(row.key)}" data-period-kind="${row.kind}" data-period-edition="${row.edition}">${esc(row.key)} · ${{day:"일간",month:"월간",year:"연간"}[row.kind]} · ${row.edition==="cloud" ? "Gemini" : "로컬"} · ${row.covered_chunks}조각 반영</button>`).join("") || '<p class="muted">아직 보관된 종합이 없습니다.</p>';
  const revision=JSON.stringify([origin, app.chronicle?.origin]);
  if (revision !== lastRevision && !museum) {
    lastRevision=revision;
    if (el("panel-chronicle").classList.contains("is-active")) loadSelectedChronicle().catch(e=>hooks.toast(e.message,"error"));
  }
  refreshButtons();
}

function openPeriod(kind,key,edition,universe=null) {
  museum=universe; setKind(kind);
  el(kind==="day" ? "chronicleDate" : kind==="month" ? "chronicleMonthInput" : "chronicleYearInput").value=key;
  el("chronicleEdition").value=edition==="cloud" ? "gemini" : "local";
  if (universe) { document.querySelectorAll("dialog[open]").forEach(dialog=>dialog.close()); el("chronicleOwner").textContent="보존 세계선 · 관람 전용"; }
  el("chronicleReturnLive").classList.toggle("is-hidden", !universe);
  hooks.switchView("chronicle"); refreshButtons(); return loadSelectedChronicle();
}

export function initConnectedStory(callbacks) {
  hooks=callbacks; mount("article"); mount("community"); el("storyLinkControls").innerHTML=sourceControls("story");
  const safe = fn => async event => { try { await fn(event); } catch(error) { hooks.toast(error.message,"error"); } };
  document.addEventListener("click", safe(async event=>{
    if(event.target.closest("[data-open-voices]")) {
      openSharedTools("voices"); el("opponentContextForm").closest("details").open=true;
      el("previousOpponent").focus(); el("opponentContextForm").scrollIntoView({block:"center"}); return;
    }
    let button=event.target.closest("[data-source-load]"); if(button) return loadSources(button.dataset.sourceLoad);
    button=event.target.closest("[data-source-remove]"); if(button) { selections[button.dataset.sourceRemove].delete(button.dataset.sourceId); renderSources(button.dataset.sourceRemove); return; }
    button=event.target.closest("[data-source-read]"); if(button) {
      const key=worldKey; const result=await api.get(`/api/v1/editorial/source?id=${encodeURIComponent(button.dataset.sourceRead)}`); if(key!==worldKey) return;
      el("connectedSourceTitle").textContent=result.source.title;
      el("connectedSourceBody").innerHTML=`<p class="muted">${esc(result.source.date)} · ${esc(result.source.basis)} · 현재 보관된 자료</p><div class="connected-original">${esc(result.source.text)}</div>`;
      el("connectedSourceDialog").showModal(); return;
    }
    button=event.target.closest("[data-channel-send]"); if(button) return sendChannel(button.dataset.channelSend,button.dataset.provider);
    button=event.target.closest("[data-cross-link]"); if(button) return linkToChannel(button.dataset.crossLink,button.dataset.sourceId,button.dataset.sourceTitle);
    button=event.target.closest("[data-channel-older]"); if(button) {
      const channel=button.dataset.channelOlder,key=worldKey; const value=await api.get(`/api/v1/story-desk?channel=${channel}&before=${encodeURIComponent(loaded[channel][0]?.id || "")}`);
      if(key!==worldKey) return; loaded[channel]=[...value.story_desk.turns.filter(r=>!loaded[channel].some(n=>n.id===r.id)),...loaded[channel]];
      button.classList.toggle("is-hidden",!value.story_desk.has_more); renderTurns(channel); return;
    }
    button=event.target.closest("[data-period-open]"); if(button) return openPeriod(button.dataset.periodKind,button.dataset.periodOpen,button.dataset.periodEdition,button.dataset.periodUniverse || null);
  }));
  document.addEventListener("input",event=>{ const channel=event.target.dataset.sourceSearch; if(channel) renderSources(channel); });
  document.addEventListener("change",event=>{ const channel=event.target.dataset.sourceToggle; if(!channel) return;
    if(busy || !writable()) {renderSources(channel); return;}
    if(event.target.checked && selections[channel].size>=8) {event.target.checked=false; hooks.toast("연결 자료는 최대 8개입니다.","error");return;}
    if(event.target.checked) selections[channel].add(event.target.value); else selections[channel].delete(event.target.value); renderSources(channel);
  });
  for(const channel of ["article","community"]) el(`${channel}Compose`).addEventListener("keydown",safe(async event=>{
    if((event.ctrlKey || event.metaKey) && event.key==="Enter" && !event.isComposing) {event.preventDefault(); return sendChannel(channel,app.config?.ai_provider==="gemini" ? "gemini" : app.app?.llm_reachable ? "local" : "note");}
  }));
  for(const button of document.querySelectorAll("[data-chronicle-kind]")) button.addEventListener("click",()=>setKind(button.dataset.chronicleKind));
  for(const id of ["chronicleEdition","chronicleDate","chronicleMonthInput","chronicleYearInput"]) el(id).addEventListener("change",()=>{readSequence++;reading=null;el("chronicleHistory").replaceChildren();el("chroniclePending").textContent="선택 기간의 보관본·미반영 자료를 확인해 주세요.";refreshButtons();});
  el("chronicleRead").addEventListener("click",safe(loadSelectedChronicle));
  el("chronicleGenerate").addEventListener("click",safe(generatePeriod));
  el("chronicleReturnLive").addEventListener("click",safe(()=>openPeriod("day",app.story_desk.origin.game_date,"local")));
  el("connectedSourceClose").addEventListener("click",()=>el("connectedSourceDialog").close());
  el("opponentContextForm").addEventListener("input",()=>{contextDirty=true;});
  el("opponentContextForm").addEventListener("submit",safe(async event=>{
    event.preventDefault(); if(busy || !writable() || museum) return;
    const people=el("opponentPeople").value.split("\n").map(line=>line.trim()).filter(Boolean).map(line=>{
      const parts=line.split("|").map(part=>part.trim()); if(parts.length<3 || parts.length>5) throw new Error("인물은 팀 | OB/선수/해설 | 이름 | 성향 | 출처 순서로 한 줄씩 입력해 주세요.");
      return {team:parts[0],role:({OB:"ob",ob:"ob",선수:"player",해설:"analyst"})[parts[1]],name:parts[2],note:parts[3] || "",source_url:parts[4] || ""};
    });
    const key=worldKey; const result=await api.post("/api/v1/editorial/context",{chronicle_origin:app.chronicle.origin,
      previous_team:el("previousOpponent").value,upcoming_team:el("upcomingOpponent").value,people,remote_allowed:el("opponentRemote").checked});
    if(key!==worldKey) return; contextDirty=false; hooks.renderDashboard(result.dashboard); hooks.toast("상대팀·인물 참고를 이 세계선에 저장했습니다.","good");
  }));
  refreshButtons();
}
