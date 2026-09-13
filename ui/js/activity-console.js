/* Request metadata only: never retain prompts, image bytes, API keys or raw bodies. */
const el = id => document.getElementById(id);
const entries = new Map();
let context = {}, selectedId = null, nextId = 0, mounted = false, renderedId = null;
const active = row => ["queued","running","reconnecting"].includes(row?.status);
const names = {check:"세이브 확인",feed:"전체 반응",feed_articles:"기사",feed_community:"커뮤니티·SNS",narrative:"자동 서사",director:"대화 작성",chronicle:"종합 스토리"};
const directNames = {
  "/api/v1/story/chat":"행동·이벤트 대화",
  "/api/v1/story/event":"행동 이벤트 기록",
  "/api/v1/attachments/analyze-llm":"이미지 모델 해석",
  "/api/v1/attachments/analyze-local":"이미지 로컬 분석",
  "/api/v1/providers/gemini/test":"Gemini 연결 시험",
  "/api/v1/providers/gemini/auto-activate":"Gemini 연결 확인",
  "/api/v1/providers/local/start":"로컬 모델 시작",
  "/api/v1/providers/local/stop":"로컬 모델 종료",
};
const time = () => new Date().toTimeString().slice(0,8);
const line = message => ({time:time(),message});

export function setActivityContext(value) { context = value || {}; }

function remember(row, focus=false) {
  entries.set(row.id,row);
  if (focus || !selectedId) selectedId = row.id;
  // An automatic connection check must not steal a generation console.
  if (!entries.has(selectedId)) selectedId = row.id;
  while (entries.size > 24) {
    const old = [...entries.values()].find(item=>!active(item) && item.id!==selectedId);
    if (!old) break;
    entries.delete(old.id);
  }
  paint();
}

function providerName(value) {
  value = typeof value === "string" ? value : "";
  if (["Gemini", "로컬 LLM", "내장·직접 기록", "혼합 작성"].includes(value)) return value;
  if (value === "gemini" || value?.startsWith("gemini")) return "Gemini";
  if (["local","local_llm","llm","local_only","local_auto"].includes(value) || value?.startsWith("llama")) return "로컬 LLM";
  if (["note","deterministic","builtin"].includes(value) || value.startsWith("builtin:")) return "내장·직접 기록";
  if (value === "mixed") return "혼합 작성";
  return "설정된 엔진";
}

export function beginRequest(path, body) {
  const job = path.startsWith("/api/v1/jobs/") && !path.endsWith("/cancel");
  const title = directNames[path] || (job ? "작업 요청" : null);
  if (!title) return null;
  const preference = body.provider || body.renderer_preference || context.config?.ai_provider;
  const provider = path.includes("/providers/gemini/") ? "Gemini" : path.includes("/providers/local/") || path.endsWith("/local") ? "로컬 LLM" : providerName(preference);
  const id = `request-${++nextId}`;
  const auto = path.endsWith("/auto-activate");
  const channel = ["article","community"].includes(body.channel) ? body.channel : "story";
  const row = {id,title,provider,channel,kind:"request",status:"running",started_at:new Date().toISOString(),
    phase: `${title} · 서버 응답 대기`, logs:[line(`${provider} · ${title} 요청을 보냈습니다.`)],direct:true};
  remember(row, !auto && ![...entries.values()].some(item=>active(item) && !item.direct));
  return id;
}

export function finishRequest(id, result, error) {
  if (!id || !entries.has(id)) return;
  if (result?.job) {
    const provider = entries.get(id).provider;
    const channel = entries.get(id).channel;
    entries.delete(id);
    if (selectedId===id) selectedId=null;
    updateJobConsole({...result.job, ui_provider:provider, ui_channel:channel});
    return;
  }
  const before = entries.get(id);
  const failed = error || result?.dashboard?.run?.provider_failed;
  const provider = result?.renderer || result?.provider;
  const summary = failed ? (error?.message || result.dashboard.run.message || "모델 작업 실패 · 기존 내용 유지")
    : result?.fallback ? "모델 응답을 완료하지 못해 내장 엔진 결과를 사용했습니다."
    : `${before.title} 완료${provider ? ` · ${providerName(provider)}` : ""}${result?.turn?.proposed_events?.length && !result.item ? " · 사건으로 기록하려면 제안 확정이 필요합니다." : ""}`;
  remember({...before,provider:provider ? providerName(provider) : before.provider,status:failed ? "failed" : result?.fallback ? "fallback" : "completed",
    phase:summary,finished_at:new Date().toISOString(),logs:[...before.logs,line(summary)]});
}

export function updateJobConsole(job) {
  if (!job) { selectedId = null; paint(); return; }
  const before = entries.get(job.id);
  const run = job.result?.run || {};
  const provider = run.provider || run.provider_audit?.provider || run.narrative_model || run.model || before?.provider || job.ui_provider || "";
  const failed = job.status === "completed" && job.result?.run?.provider_failed;
  const fallback = job.status === "completed" && !failed && Boolean(run.fallback ||
    (run.narrative_model?.startsWith("builtin:") && run.provider_audit?.fallback_chain?.length));
  const channel = job.result?.run?.director_channel || before?.channel || job.ui_channel;
  const title = job.kind === "director" ? `${({article:"기사",community:"커뮤니티"})[channel] || "서사"} 대화` : names[job.kind] || "모델 작업";
  remember({id:job.id,kind:job.kind,progress:job.progress,started_at:job.started_at || job.created_at,
    finished_at:job.finished_at,logs:(job.logs || []).map(row=>({time:row.time,message:row.message})),
    title,channel,provider:providerName(provider),direct:false,
    status:failed ? "failed" : fallback ? "fallback" : job.status,
    phase:run.message || job.error?.message || job.phase,
    destination:job.kind === "director" ? ({article:["media","compose"],community:["community","compose"]}[channel] || ["narrative","chat"])
      : {narrative:["narrative","cinema"],feed:["community","read"],feed_community:["community","read"],feed_articles:["media","read"],chronicle:["chronicle",null]}[job.kind],
  }, !before);
}

export function reportJobConnectionError(id,error) {
  const before = entries.get(id);
  if (!before) return;
  const lost = error.code === "JOB_NOT_FOUND";
  const message = lost ? "서버에서 작업 정보를 찾지 못했습니다. 기존 결과를 확인해 주세요. 자동 재작성하지 않습니다."
    : "상태 연결 재확인 중 · 서버 작업의 성공·실패는 아직 확인되지 않았습니다.";
  remember({...before,status:lost ? "unknown" : "reconnecting",phase:message,logs:before.status === "reconnecting" && !lost ? before.logs : [...(before.logs || []),line(`${message} ${error.message}`)]});
}

function displayRow() { return entries.get(selectedId); }

function paint() {
  if (!mounted) return;
  const row = displayRow();
  const running = active(row);
  const state = {queued:"준비",running:"실행 중",reconnecting:"연결 재확인",unknown:"확인 불가",completed:"완료",fallback:"대체 완료",failed:"실패",cancelled:"취소됨"}[row?.status] || "대기";
  el("jobState").textContent = state;
  el("jobState").className = `job-state ${running ? "is-running" : row?.status === "completed" ? "is-complete" : row?.status === "fallback" ? "is-fallback" : row ? "is-failed" : ""}`;
  el("jobPhase").textContent = row?.phase || "모드에 관계없이 로컬·Gemini 작업 상태와 로그가 여기에 표시됩니다.";
  el("jobPhase").title = el("jobPhase").textContent;
  if (running && row.direct) el("jobProgress").removeAttribute("value");
  else el("jobProgress").value = row?.status === "completed" ? 100 : Number(row?.progress || 0);
  el("jobCancelButton").hidden = !running || row.direct || row.status === "reconnecting";
  el("jobCancelButton").dataset.jobId = row?.id || "";
  el("activityResult").hidden = !row?.destination || running;
  const signature = JSON.stringify([...entries.values()].map(item=>[item.id,item.title,item.status]));
  if (el("activitySelect").dataset.signature !== signature) {
    el("activitySelect").replaceChildren(...[...entries.values()].reverse().map(item=>{
      const option = document.createElement("option"); option.value=item.id;
      option.textContent=`${item.title} · ${active(item) ? "진행 중" : item.status === "completed" ? "완료" : item.status === "fallback" ? "대체 완료" : item.status === "cancelled" ? "취소" : item.status === "unknown" ? "확인 불가" : "실패"}`; return option;
    }));
    const idle = document.createElement("option"); idle.value=""; idle.textContent="작업 선택"; el("activitySelect").prepend(idle);
    el("activitySelect").dataset.signature = signature;
  }
  el("activitySelect").value = row?.id || "";
  el("activitySelect").hidden = !entries.size;
  const log = el("jobLog");
  const logs = row?.logs || [];
  const logSignature = JSON.stringify(logs);
  if (log.dataset.signature !== logSignature || renderedId !== row?.id) {
    const previous = log.scrollTop;
    const follow = renderedId !== row?.id || log.scrollHeight-log.clientHeight-previous < 24;
    log.replaceChildren(...logs.map(item=>{
      const div=document.createElement("div"); div.className="job-log__row";
      for (const value of [item.time,item.message]) {const span=document.createElement("span");span.textContent=value;div.append(span);} return div;
    }));
    if (!logs.length) log.textContent = "실행한 작업의 진행·대기·완료·오류를 확인할 수 있습니다.";
    log.dataset.signature=logSignature; renderedId=row?.id;
    log.scrollTop=follow ? log.scrollHeight : previous;
  }
  updateElapsed();
  for (const mirror of document.querySelectorAll(".dialog-activity")) {
    mirror.hidden = !row;
    mirror.textContent = row ? `${state} · ${row.title}\n${row.phase || ""}\n${logs.at(-1)?.message || ""}` : "";
  }
}

function updateElapsed() {
  if (!mounted) return;
  const row = displayRow();
  if (!row) { el("jobSummary").textContent=""; return; }
  const seconds = Math.max(0,Math.floor(((row.finished_at ? Date.parse(row.finished_at) : Date.now())-Date.parse(row.started_at || row.created_at))/1000));
  const progress = row.direct ? "" : `${Math.round(Number(row.progress || 0))}% 진행 · `;
  el("jobSummary").textContent = `${progress}${row.provider || ""} · ${active(row) ? "경과" : "소요"} ${Number.isFinite(seconds) ? `${Math.floor(seconds/60)}분 ${String(seconds%60).padStart(2,"0")}초` : "확인 중"}${row.direct && active(row) ? " · 응답 대기 (진행률 미제공)" : ""}`;
}

export function initActivityConsole({showResult}) {
  const card = el("jobCard");
  if (!card) return;
  card.classList.add("activity-console");
  card.setAttribute("aria-label","모든 모드의 작업 상태 콘솔");
  card.setAttribute("aria-live","off");
  const head=card.querySelector(".side-card__heading");
  head.firstElementChild.textContent="작업 콘솔";
  head.insertAdjacentHTML("beforeend",'<label class="activity-selector"><span class="visually-hidden">이 세션의 작업 선택</span><select id="activitySelect" hidden></select></label><button class="text-button" id="activityResult" type="button" hidden>결과 보기</button><button class="button button--quiet button--compact" id="activityExpand" type="button" aria-expanded="false" aria-controls="jobLog">로그 넓게</button>');
  el("jobPhase").setAttribute("role","status");
  el("jobCancelButton").classList.add("button--compact");
  head.append(el("jobCancelButton"));
  document.body.append(card);
  mounted=true;
  el("activitySelect").addEventListener("change",()=>{selectedId=el("activitySelect").value;paint();});
  el("activityExpand").addEventListener("click",()=>{
    const expanded=card.classList.toggle("is-expanded");
    el("activityExpand").setAttribute("aria-expanded",String(expanded));
    el("activityExpand").textContent=expanded ? "로그 작게" : "로그 넓게";
  });
  el("activityResult").addEventListener("click",()=>{const row=displayRow(); if(row?.destination) showResult(...row.destination,true);});
  // Dialogs occupy the browser top layer; mirror status there without duplicate controls.
  for (const dialog of document.querySelectorAll("dialog")) {
    const mirror=document.createElement("div");mirror.className="dialog-activity";mirror.hidden=true;mirror.setAttribute("role","status");
    const body = dialog.querySelector(".settings-panel__body");
    if (body) body.after(mirror); else dialog.append(mirror);
  }
  new ResizeObserver(()=>document.documentElement.style.setProperty("--activity-height",`${card.getBoundingClientRect().height}px`)).observe(card);
  window.setInterval(updateElapsed,1000);
  paint();
}
