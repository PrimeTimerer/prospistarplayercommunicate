/* Reparent existing controls once; retain their handlers, drafts and public IDs. */
const el = id => document.getElementById(id);
const modes = {
  overview:["개요", "선수와 오늘의 변화를 확인합니다."],
  records:["기록실", "성적·수상·지난 날짜와 보존 세계선을 열람합니다."],
  history:["경기 피드", "검증된 경기와 기록 변화입니다. 직접 만드는 장면은 서사 모드에 모았습니다."],
  community:["커뮤니티", "반응 읽기와 대화·작성을 구분합니다. 대화는 입력만으로 시작하고 자료 연결은 선택입니다."],
  media:["기사", "기사 읽기와 대화·작성을 구분합니다. 제보·초안·편집 방향을 적어 기사를 이어가세요."],
  narrative:["서사", "자유 대화로 바로 시작하세요. 행동 이벤트와 자동 서사도 이 모드 안에서 사용할 수 있습니다."],
  chronicle:["종합 스토리", "저장된 기사·커뮤니티·서사를 일간·월간·연간으로 정리합니다. 새 자료만 이어서 처리합니다."],
  diagnostics:["진단", "검증 정보와 출처를 확인합니다. 모든 모델 작업은 하단 콘솔에 표시됩니다."],
};
let hooks = {}, currentMode = "overview", returnFocus = null;
const selected = {narrative:"chat", community:"read", media:"read"};
const parts = {};

function makePart(mode, key, label, nodes) {
  const section = document.createElement("section");
  section.id = `work-${mode}-${key}`;
  section.className = "workspace-part";
  section.setAttribute("aria-label", label);
  for (const node of nodes) if (node) section.append(node);
  (parts[mode] ||= {})[key] = section;
  return section;
}

function arrange(mode, definitions) {
  const panel = el(`panel-${mode}`);
  const nav = document.createElement("div");
  nav.className = "workspace-sections";
  nav.setAttribute("role", "group");
  nav.setAttribute("aria-label", `${modes[mode][0]} 작업 선택`);
  for (const [key, label, nodes] of definitions) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.dataset.workspaceMode = mode;
    button.dataset.workspacePart = key;
    button.setAttribute("aria-controls", `work-${mode}-${key}`);
    nav.append(button);
    panel.append(makePart(mode, key, label, nodes));
  }
  panel.prepend(nav);
  selectPart(mode, selected[mode]);
}

function selectPart(mode, key) {
  if (!parts[mode]?.[key]) return;
  selected[mode] = key;
  for (const [name, section] of Object.entries(parts[mode])) section.hidden = name !== key;
  for (const button of document.querySelectorAll(`[data-workspace-mode="${mode}"]`)) {
    button.setAttribute("aria-pressed", String(button.dataset.workspacePart === key));
  }
}

export function showWorkspacePart(mode, key, scroll = false) {
  hooks.switchView?.(mode);
  selectPart(mode, key);
  if (scroll) el(`panel-${mode}`)?.scrollIntoView({block:"start", behavior:"smooth"});
}

export function openSharedTools(key) {
  if (!el(`shared-${key}`)) return;
  returnFocus = document.activeElement;
  el("sharedTools").hidden = false;
  for (const section of document.querySelectorAll("[data-shared-panel]")) section.hidden = section.dataset.sharedPanel !== key;
  for (const button of document.querySelectorAll("[data-shared-tool]")) button.setAttribute("aria-expanded", String(button.dataset.sharedTool === key));
  const section = el(`shared-${key}`);
  el("sharedToolsTitle").textContent = {images:"이미지·화면 캡처", memory:"세계선 기억·인물", voices:"상대팀·OB·선수", save:"세이브 연결 상태"}[key];
  section.focus({preventScroll:true});
  el("sharedTools").scrollIntoView({block:"start", behavior:"smooth"});
}

function closeSharedTools() {
  el("sharedTools").hidden = true;
  for (const button of document.querySelectorAll("[data-shared-tool]")) button.setAttribute("aria-expanded", "false");
  if (returnFocus?.isConnected) returnFocus.focus({preventScroll:true});
}

export function renderWorkspace(data) {
  if (!el("workspaceReadiness")) return;
  const linked = !data?.empty && data?.world?.binding?.live !== false && data?.world?.read_only !== true && data?.world?.capabilities?.read_only !== true;
  const local = Boolean(data?.app?.llm_reachable);
  const cloud = Boolean(data?.config?.gemini_key_present && data?.config?.gemini_consent);
  el("workspaceReadiness").textContent = !linked ? "열람 전용 · 작성하려면 세이브를 연결해 주세요."
    : `직접 기록 가능 · 로컬 ${local ? "연결됨" : "꺼짐"} · Gemini ${cloud ? "설정됨" : "미설정"}`;
  el("workspaceReadiness").dataset.ready = String(linked);
  const instructions = !linked ? "연결되지 않은 세계선은 열람만 가능합니다. 공통 도구의 세이브·연결을 확인해 주세요."
    : `입력 → 작성 방법 선택 → 아래 대화에서 확인. 자료 연결·이미지·기억은 필요한 경우에만 추가합니다.${!local && !cloud ? " 모델 없이 직접 기록할 수 있습니다. 모델을 쓰려면 상단 AI·연결에서 설정하세요." : " Gemini를 선택할 때만 이번 전송 동의가 필요합니다."}`;
  for (const hint of document.querySelectorAll(".workspace-composer-hint")) hint.textContent = instructions;
}

export function initWorkspace(value) {
  hooks = value;
  const hub = document.createElement("section");
  hub.id = "workspaceHub";
  hub.className = "workspace-hub";
  hub.innerHTML = `<div class="workspace-common" aria-label="모든 모드의 공통 도구"><span>공통 도구</span>
    <button type="button" data-shared-tool="images" aria-controls="sharedTools" aria-expanded="false">이미지·캡처</button>
    <button type="button" data-shared-tool="memory" aria-controls="sharedTools" aria-expanded="false">기억·인물</button>
    <button type="button" data-shared-tool="voices" aria-controls="sharedTools" aria-expanded="false">상대팀·OB</button>
    <button type="button" data-shared-tool="save" aria-controls="sharedTools" aria-expanded="false">세이브·연결</button>
    <button type="button" id="workspaceAI">AI·연결 설정</button></div>`;
  el("tabbar").before(hub);
  hub.prepend(el("tabbar"));
  const guide = document.createElement("section");
  guide.className = "workspace-guide";
  guide.innerHTML = '<div><strong id="workspaceModeName"></strong><p id="workspaceModeHelp"></p></div><span id="workspaceReadiness" role="status"></span>';
  hub.after(guide);
  const tools = document.createElement("section");
  tools.id = "sharedTools"; tools.className = "shared-tools card"; tools.hidden = true;
  tools.innerHTML = '<header><div><span class="kicker">SHARED · 현재 세계선</span><h2 id="sharedToolsTitle"></h2></div><button class="button button--quiet button--compact" id="sharedToolsClose" type="button">닫고 작업 계속</button></header><p class="muted">모드를 바꾸지 않고 공통 자료를 관리합니다. 각 입력의 공개 범위·외부 전송 권한은 그대로 유지됩니다.</p>';
  guide.after(tools);
  for (const [key, ids] of Object.entries({images:["captureTools"],memory:["directorMemoryLibrary","storyWorldbook","storyMemory"],voices:["opponentContextTools"],save:["saveStatusTools"]})) {
    const section = document.createElement("section");
    section.id = `shared-${key}`; section.dataset.sharedPanel = key;
    section.tabIndex = -1; section.hidden = true;
    for (const id of ids) section.append(el(id));
    tools.append(section);
  }
  el("opponentContextTools").open = true;
  el("directorMemoryLibrary").open = true;
  const cinemaActions = el("cinematicWorkspace").querySelector(".button-row");
  cinemaActions.append(el("narrativeButton"));
  const storyComposer = el("directorDesk").querySelector(".director-compose");
  const primaryInput = el("directorText").closest("label");
  storyComposer.prepend(primaryInput);
  const actionLibrary = el("directorDesk").querySelector(".director-library");
  storyComposer.insertBefore(actionLibrary, el("directorAction").closest("label"));
  primaryInput.after(el("storyLinkControls"));
  arrange("narrative", [["chat","자유 대화",[el("directorDesk")]], ["events","행동·이벤트",[el("legacyStoryWorkspace")]], ["cinema","자동 서사",[el("cinematicWorkspace")]]]);
  for (const [mode, channel] of [["media","article"],["community","community"]]) {
    const composer = el(`${channel}Conversation`);
    const readers = [...el(`panel-${mode}`).children].filter(node => node !== composer);
    arrange(mode, [["read",mode === "media" ? "기사 읽기·자동 작성" : "반응 읽기·자동 작성",readers], ["compose","대화·직접 작성",[composer]]]);
  }
  for (const area of document.querySelectorAll(".director-compose, .connected-compose")) {
    const hint = document.createElement("p"); hint.className = "workspace-composer-hint"; hint.setAttribute("role","status"); area.prepend(hint);
    const quick = document.createElement("div"); quick.className = "workspace-compose-tools";
    quick.innerHTML = '<span>선택 자료</span><button type="button" data-shared-tool="images" aria-controls="sharedTools" aria-expanded="false">이미지·캡처</button><button type="button" data-shared-tool="memory" aria-controls="sharedTools" aria-expanded="false">기억·인물</button><button type="button" data-shared-tool="voices" aria-controls="sharedTools" aria-expanded="false">상대팀·OB</button>';
    area.insertBefore(quick, area.querySelector("textarea")?.closest("label") || null);
  }
  const historyLink = document.createElement("button");
  historyLink.type = "button"; historyLink.className = "button button--quiet button--compact";
  historyLink.textContent = "서사에서 행동·이벤트 만들기";
  historyLink.addEventListener("click",()=>showWorkspacePart("narrative","events",true));
  el("panel-history").prepend(historyLink);
  el("workspaceAI").addEventListener("click",()=>hooks.openAI?.());
  el("sharedToolsClose").addEventListener("click",closeSharedTools);
  tools.addEventListener("keydown",event=>{if(event.key === "Escape") {event.preventDefault(); closeSharedTools();}});
  document.addEventListener("click", event=>{
    const common = event.target.closest("[data-shared-tool]");
    if (common) return openSharedTools(common.dataset.sharedTool);
    const part = event.target.closest("[data-workspace-part]");
    if (part) selectPart(part.dataset.workspaceMode,part.dataset.workspacePart);
  });
  document.addEventListener("workspace:mode",event=>{
    currentMode = event.detail;
    el("workspaceModeName").textContent = modes[currentMode]?.[0] || "";
    el("workspaceModeHelp").textContent = modes[currentMode]?.[1] || "";
  });
  const observer = new ResizeObserver(()=>document.documentElement.style.setProperty("--appbar-height",`${document.querySelector(".appbar").getBoundingClientRect().height}px`));
  observer.observe(document.querySelector(".appbar"));
  document.dispatchEvent(new CustomEvent("workspace:mode",{detail:currentMode}));
}
