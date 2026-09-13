/* Disposable real app, mocked inference and loopback-only requests. */
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {chromium} = require("playwright");
const fs=require("node:fs/promises"), path=require("node:path");
const out=path.resolve(process.argv.find(a=>a.startsWith("--output-dir="))?.slice(13) || "artifacts/workspace-ui");
let child,browser,stderr="";
const checks=[],errors=[];
async function check(name,fn) {try {await fn();checks.push({name,passed:true});}catch(error){checks.push({name,passed:false,error:error.stack});}console.log(`${checks.at(-1).passed ? "PASS" : "FAIL"} ${name}`);}
async function main() {
  await fs.mkdir(out,{recursive:true});
  child=spawn("python",["-u","scripts/serve_director_fixture.py"],{cwd:path.resolve(__dirname,".."),windowsHide:true});
  child.stderr.on("data",d=>stderr+=d);
  const origin=await new Promise((resolve,reject)=>{let s="";child.stdout.on("data",d=>{s+=d;if(s.includes("\n"))resolve(JSON.parse(s.split("\n")[0]).url);});child.on("exit",code=>reject(Error(`${code}: ${stderr}`)));});
  browser=await chromium.launch({headless:true,channel:"chrome"});
  const page=await browser.newPage({viewport:{width:1280,height:800}});
  page.setDefaultTimeout(5000);
  page.on("pageerror",e=>errors.push(e.message));
  await page.route("**/*",r=>r.request().url().startsWith(origin)||r.request().url().startsWith("data:") ? r.continue() : r.abort());
  await page.goto(origin);
  await page.locator("#appLayout:not(.is-hidden)").waitFor();
  const part=async(mode,key)=>{await page.locator(`#tab-${mode}`).click();await page.locator(`[data-workspace-mode="${mode}"][data-workspace-part="${key}"]`).click();};
  const common=async key=>page.locator(`#workspaceHub [data-shared-tool="${key}"]`).click();
  await check("all original IDs are unique",async()=>{
    const duplicates=await page.evaluate(()=>{const ids=[...document.querySelectorAll("[id]")].map(n=>n.id);return ids.filter((id,i)=>ids.indexOf(id)!==i);});assert.deepEqual(duplicates,[]);
  });
  await check("story operations move out of game feed without removal",async()=>{
    assert.equal(await page.locator("#panel-history #storyText").count(),0);
    assert.equal(await page.locator("#work-narrative-events #storyText").count(),1);
    assert.equal(await page.locator("#work-narrative-cinema #narrativeButton").count(),1);
    assert.equal(await page.locator("#historyList").count(),1);
  });
  await check("all eight modes retain a visible console on deep scroll",async()=>{
    for(const mode of ["overview","records","history","community","media","narrative","chronicle","diagnostics"]) {
      await page.locator(`#tab-${mode}`).click();await page.evaluate(()=>window.scrollTo(0,document.body.scrollHeight));
      const box=await page.locator("#jobCard").boundingBox();assert.ok(box.y>=0 && box.y+box.height<=801,mode);
      assert.equal(await page.locator("#jobLog").isVisible(),true,mode);
    }
  });
  await part("narrative","chat");
  await check("shared tools retain active mode and draft",async()=>{
    await page.locator("#directorText").fill("햄버거 약속 초안 유지");
    for(const key of ["images","memory","voices","save"]) {
      await common(key);assert.equal(await page.locator(`#shared-${key}`).isVisible(),true);
      assert.equal(await page.locator("#tab-narrative").getAttribute("aria-selected"),"true");
      await page.locator("#sharedToolsClose").click();
      assert.equal(await page.locator("#directorText").inputValue(),"햄버거 약속 초안 유지");
    }
  });
  await check("story submodes preserve event and chat drafts",async()=>{
    await part("narrative","events");await page.locator("#storyText").fill("동료와 만나기로 한다.");
    await part("narrative","cinema");assert.equal(await page.locator("#narrativeButton").isVisible(),true);
    await part("narrative","chat");assert.equal(await page.locator("#directorText").inputValue(),"햄버거 약속 초안 유지");
    await part("narrative","events");assert.equal(await page.locator("#storyText").inputValue(),"동료와 만나기로 한다.");
  });
  await part("media","compose");
  await check("article conversation separated from feed but neither lost",async()=>{
    assert.equal(await page.locator("#articleCompose").isVisible(),true);
    assert.equal(await page.locator("#mediaList").isVisible(),false);
    await page.locator("#articleCompose").fill("오늘의 공개 인터뷰를 기사로 이어줘.");
    await part("media","read");assert.equal(await page.locator("#enrichArticlesButton").isVisible(),true);
    await part("media","compose");assert.match(await page.locator("#articleCompose").inputValue(),/인터뷰/);
  });
  await check("article local job opens its result and exposes authentic logs",async()=>{
    await page.locator("#articleLocal").click();
    await page.waitForFunction(()=>document.querySelectorAll("#articleConversationTurns .director-turn").length===1);
    assert.equal(await page.locator("#articleConversationTurns").isVisible(),true);
    assert.match(await page.locator("#jobLog").innerText(),/저장|완료/);
    assert.equal(await page.locator("#jobState").innerText(),"완료");
  });
  await check("cross-channel links select destination composer",async()=>{
    await page.locator('#articleConversationTurns [data-cross-link="community"]').click();
    assert.equal(await page.locator("#communityCompose").isVisible(),true);
    assert.match(await page.locator("#communityLinkCount").textContent(),/1개/);
  });
  await check("source and OB controls do not require switching to chronicles",async()=>{
    await page.locator('#communityConversation [data-open-voices]').click();
    assert.equal(await page.locator("#previousOpponent").isVisible(),true);
    assert.equal(await page.locator("#tab-community").getAttribute("aria-selected"),"true");
    await page.locator("#sharedToolsClose").click();
  });
  await page.route("**/api/v1/story/chat",async r=>{await new Promise(resolve=>setTimeout(resolve,800));await r.fulfill({json:{ok:true,renderer:"gemini"}});});
  await check("legacy synchronous Gemini chat shows pending then completion",async()=>{
    await page.evaluate(async()=>{const api=await import('/js/api.js');window.requestResult=api.post('/api/v1/story/chat',{renderer_preference:'gemini',user_text:'PRIVATE_SENTINEL'});});
    assert.match(await page.locator("#jobPhase").innerText(),/応答|응답 대기/);
    assert.equal(await page.locator("#jobProgress").getAttribute("value"),null);
    assert.equal(await page.locator("#jobCancelButton").isVisible(),false);
    await page.evaluate(()=>window.requestResult);
    assert.match(await page.locator("#jobPhase").innerText(),/Gemini/);
    assert.equal(await page.locator("#jobCard").innerText().then(t=>t.includes("PRIVATE_SENTINEL")),false);
  });
  await page.route("**/api/v1/attachments/analyze-llm",async r=>{await new Promise(resolve=>setTimeout(resolve,700));await r.fulfill({status:503,json:{ok:false,error:{message:"합성 모델 오류 <b>그대로 표시</b>"}}});});
  await check("image request and safe error visible inside top-layer modal",async()=>{
    await page.evaluate(()=>document.getElementById("attachmentDialog").showModal());
    await page.evaluate(async()=>{const api=await import('/js/api.js');window.imageResult=api.post('/api/v1/attachments/analyze-llm',{provider:'local_llm'}).catch(e=>e.message);});
    assert.match(await page.locator("#attachmentDialog .dialog-activity").innerText(),/이미지 모델 해석/);
    await page.evaluate(()=>window.imageResult);
    assert.equal(await page.locator("#jobState").innerText(),"실패");
    assert.match(await page.locator("#jobLog").innerText(),/<b>그대로 표시<\/b>/);
    assert.equal(await page.locator("#jobLog b").count(),0);
    await page.evaluate(()=>document.getElementById("attachmentDialog").close());
  });
  await check("console history and expanded log controls work",async()=>{
    assert.ok(await page.locator("#activitySelect option").count()>=3);
    await page.locator("#activityExpand").click();assert.equal(await page.locator("#activityExpand").getAttribute("aria-expanded"),"true");
    const id=await page.locator("#activitySelect option").evaluateAll(options=>options.find(o=>o.textContent.includes("행동·이벤트 대화")).value);
    await page.locator("#activitySelect").selectOption(id);assert.match(await page.locator("#jobPhase").innerText(),/대화/);
    await page.locator("#activityExpand").click();
  });
  await check("polling disconnect never claims server completion",async()=>{
    await page.evaluate(async()=>{const console=await import('/js/activity-console.js');console.updateJobConsole({id:'pending-test',kind:'feed_community',status:'running',progress:18,logs:[{time:'12:00:00',message:'Gemini 응답 대기'}]});console.reportJobConnectionError('pending-test',new Error('연결 중단'));});
    assert.equal(await page.locator("#jobState").innerText(),"연결 재확인");
    assert.match(await page.locator("#jobPhase").innerText(),/아직 확인되지/);
  });
  await check("lost server job is unknown, not an invented failure or completion",async()=>{
    await page.evaluate(async()=>{const c=await import('/js/activity-console.js');const e=new Error('작업 없음');e.code='JOB_NOT_FOUND';c.reportJobConnectionError('pending-test',e);});
    assert.equal(await page.locator("#jobState").innerText(),"확인 불가");
    assert.equal(await page.locator("#jobCancelButton").isVisible(),false);
  });
  await check("built-in narrative fallback never claims Gemini completion",async()=>{
    await page.evaluate(async()=>{const c=await import('/js/activity-console.js');c.updateJobConsole({id:'fallback-test',kind:'narrative',status:'completed',progress:100,ui_provider:'gemini',logs:[{time:'12:00:00',message:'숫자 검증 보류'}],result:{run:{narrative_model:'builtin:deterministic-feed',provider_audit:{provider:'builtin',fallback_chain:[{code:'UNSUPPORTED_NUMERIC_CLAIM'}]},fallback:true,message:'내장 엔진 기록으로 대체했습니다.'}}});});
    assert.equal(await page.locator('#jobState').innerText(),'대체 완료');
    assert.match(await page.locator('#jobSummary').innerText(),/내장/);
    assert.doesNotMatch(await page.locator('#jobSummary').innerText(),/Gemini/);
    assert.match(await page.locator('#jobPhase').innerText(),/대체/);
  });
  await check("successful local fallback shows its actual writer",async()=>{
    await page.evaluate(async()=>{const c=await import('/js/activity-console.js');c.updateJobConsole({id:'local-fallback-test',kind:'narrative',status:'completed',progress:100,ui_provider:'gemini',logs:[],result:{run:{narrative_model:'qwen-fixture',provider_audit:{provider:'local_llm'}}}});});
    assert.equal(await page.locator('#jobState').innerText(),'완료');
    assert.match(await page.locator('#jobSummary').innerText(),/로컬 LLM/);
  });
  await check("saved narrative badge follows content even after another job or setting change",async()=>{
    const dashboard=await page.evaluate(async()=>{const api=await import('/js/api.js');return (await api.get('/api/v1/bootstrap')).dashboard;});
    await page.evaluate(async d=>{const r=await import('/js/render.js');r.renderDashboard({...d,narrative:'## 기존 기록\n\n저장된 서사다.',narrative_source:{provider:'builtin',model:'builtin:deterministic-feed'},config:{...d.config,ai_provider:'gemini'},run:{changed:false}});},dashboard);
    assert.equal(await page.locator('#narrativeProviderBadge').innerText(),'내장 엔진 기록');
    await page.evaluate(async d=>{const r=await import('/js/render.js');r.renderDashboard({...d,narrative:'옛 서사',narrative_source:{provider:'unknown'},run:{changed:false}});},dashboard);
    assert.equal(await page.locator('#narrativeProviderBadge').innerText(),'작성 엔진 미확인');
  });
  for(const [label,width,height,theme] of [["desktop-dark",1440,900,"dark"],["narrow-dark",390,720,"dark"],["short-light",900,540,"light"]]) {
    await page.setViewportSize({width,height});await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
    await part("narrative","chat");await page.locator("#directorText").scrollIntoViewIfNeeded();
    await check(`${label} console and page stay within viewport`,async()=>{
      const b=await page.locator("#jobCard").boundingBox();assert.ok(b.x>=-1 && b.y>=0 && b.x+b.width<=width+1 && b.y+b.height<=height+1);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
      await page.evaluate(()=>window.scrollTo(0,document.body.scrollHeight));
      const end=await page.locator(".side-column .disclaimer").boundingBox();assert.ok(end.y+end.height<b.y,"page tail clears fixed console");
    });
    await part("narrative","chat");
    await page.locator("#directorText").evaluate(n=>n.scrollIntoView({block:"center",behavior:"instant"}));
    await page.screenshot({path:path.join(out,`${label}.png`),fullPage:false});
  }
  await check("no browser runtime errors",async()=>assert.deepEqual(errors,[]));
}
(async()=>{try{await main();}catch(e){checks.push({name:"harness",passed:false,error:e.stack});}finally{await browser?.close();child?.stdin.end("quit\n");await fs.mkdir(out,{recursive:true});await fs.writeFile(path.join(out,"report.json"),JSON.stringify({checks,stderr},null,2));console.log(JSON.stringify({passed:checks.filter(c=>c.passed).length,total:checks.length,report:out}));process.exitCode=checks.every(c=>c.passed)?0:1;}})();
