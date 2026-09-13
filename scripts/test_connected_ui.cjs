/* Full connected flow against a disposable real backend and mock inference. */
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {chromium} = require("playwright");
const fs = require("node:fs/promises"), path = require("node:path");
const output = path.resolve(process.argv.find(a=>a.startsWith("--output-dir="))?.slice(13) || "artifacts/connected-ui");
let child, browser, stderr="";
const checks=[], errors=[];
async function check(name, fn) { try { await fn(); checks.push({name,passed:true}); } catch(e) {checks.push({name,passed:false,error:e.stack});} console.log(`${checks.at(-1).passed ? "PASS" : "FAIL"} ${name}`); }
async function main() {
  await fs.mkdir(output,{recursive:true});
  child=spawn("python",["-u","scripts/serve_director_fixture.py"],{cwd:path.resolve(__dirname,".."),windowsHide:true});
  child.stderr.on("data",d=>stderr+=d);
  const origin=await new Promise((resolve,reject)=>{let out="";child.stdout.on("data",d=>{out+=d;if(out.includes("\n"))resolve(JSON.parse(out.split("\n")[0]).url);});child.on("exit",c=>reject(Error(`Fixture ${c}: ${stderr}`)));});
  browser=await chromium.launch({headless:true,channel:"chrome"});
  const page=await browser.newPage({viewport:{width:1280,height:800}});
  page.on("pageerror",e=>errors.push(e.message));
  await page.route("**/*",r=>r.request().url().startsWith(origin) || r.request().url().startsWith("data:") ? r.continue() : r.abort());
  await page.goto(origin);
  const tab=async key=>{
    await page.locator(`#tab-${key}`).click();
    if (["media","community"].includes(key)) await page.locator(`[data-workspace-mode="${key}"][data-workspace-part="compose"]`).click();
  };
  async function job(button) {
    const [response]=await Promise.all([page.waitForResponse(r=>r.url().includes("/api/v1/jobs/") && r.request().method()==="POST"),page.locator(`#${button}`).click()]);
    assert.equal(response.status(),202,await response.text());
    await page.waitForFunction(()=>!document.getElementById("articleNote").disabled);
  }
  async function read() { await Promise.all([page.waitForResponse(r=>r.url().includes("/api/v1/chronicle?")),page.locator("#chronicleRead").click()]); await page.waitForFunction(()=>document.getElementById("chroniclePending").textContent.includes("조각")); }
  const parts=()=>page.locator("#chronicleHistory > .director-turn").count();
  await tab("media");
  await check("article and community composers coexist with original feeds",async()=>{
    for(const id of ["articleNote","articleLocal","articleGemini","communityNote","communityLocal","communityGemini"])assert.equal(await page.locator(`#${id}`).isEnabled(),true);
    assert.equal(await page.locator("#mediaList").count(),1);
    assert.equal(await page.locator("#communityList").count(),1);
  });
  await tab("chronicle");
  await check("opponent voices saved locally with explicit cloud permission",async()=>{
    await page.locator('#workspaceHub [data-shared-tool="voices"]').click();
    await page.locator("#previousOpponent").fill("한신");await page.locator("#upcomingOpponent").fill("요미우리");
    await page.locator("#opponentPeople").fill("한신 | OB | 가상 선배 | 엄격하지만 공정한 평가\n요미우리 | 선수 | 가상 라이벌 | 다음 맞대결을 기다린다");
    await page.locator("#saveOpponentContext").click();
    await page.waitForFunction(()=>document.getElementById("opponentContextStatus").textContent.includes("저장됨"));
    assert.match(await page.locator("#opponentContextStatus").innerText(),/로컬 전용/);
  });
  await tab("media");
  await check("article local conversation records user input and answer",async()=>{
    await page.locator("#articleCompose").fill("공개 인터뷰에서 햄버거 약속을 했다. 직전 OB와 다음 상대 선수가 서로 다르게 반응하는 기사를 써줘.");
    await page.locator("#articleRemoteRecall").check();await job("articleLocal");
    assert.equal(await page.locator("#articleConversationTurns .director-turn").count(),1);
    assert.equal(await page.locator("#articleCompose").inputValue(),"");
    assert.equal(await page.locator("#panel-media").evaluate(n=>n.classList.contains("is-active")),true);
    assert.equal(await page.locator("#articleConversationTurns .director-reply h2").count(),2);
  });
  await check("article reply links into community without copying or publishing twice",async()=>{
    await page.locator('#articleConversationTurns [data-cross-link="community"]').click();
    assert.match(await page.locator("#communityLinkCount").textContent(),/1개/);
    await page.locator("#communityCompose").fill("기사에 상대 팬이 반박하고 홈 팬이 햄버거 영수증으로 농담한다.");
    await job("communityLocal");
    assert.equal(await page.locator("#communityConversationTurns .director-turn").count(),1);
    assert.match(await page.locator("#communityConversationTurns footer").innerText(),/연결 1개/);
  });
  await check("community links into public director conversation",async()=>{
    await page.locator('#communityConversationTurns [data-cross-link="story"]').click();
    assert.match(await page.locator("#storyLinkCount").textContent(),/1개/);
    await page.locator("#directorVisibility").selectOption("public");
    await page.locator("#directorText").fill("그 팬의 농담을 본 동료가 식당 지도를 꺼낸다.");await job("directorLocal");
    assert.equal(await page.locator('#directorTurns [data-cross-link="article"]').count(),1);
    assert.match(await page.locator("#storyLinkCount").textContent(),/0개/);
  });
  await tab("media");
  await check("source picker excludes private legacy scenes and opens original",async()=>{
    await page.locator("#articleLinkControls summary").click();
    await page.locator('[data-source-load="article"]').click();
    await page.waitForFunction(()=>document.querySelectorAll("#articleSourceChoices .connected-source-row").length>0);
    assert.doesNotMatch(await page.locator("#articleSourceChoices").innerText(),/함께 햄버거를 먹은 장면 0/);
    await page.locator("#articleSourceChoices [data-source-read]").first().click();
    await page.locator("#connectedSourceDialog").waitFor({state:"visible"});
    assert.equal(await page.locator("#connectedSourceDialog").isVisible(),true);
    await page.locator("#connectedSourceClose").click();
  });
  await tab("chronicle");
  await check("daily synthesis saves separate chronological checkpoints",async()=>{
    await page.locator("#chronicleCallBudget").selectOption("6");await read();
    assert.equal(await parts(),0);
    await page.locator("#chronicleDirection").fill("기사와 농담이 연결된 과정을 중심으로 정리해 줘.");
    await job("chronicleGenerate");
    await page.waitForFunction(()=>document.querySelectorAll("#chronicleHistory > .director-turn").length>0);
    assert.match(await page.locator("#chronicleHistory").innerText(),/이어갈 핵심/);
    assert.match(await page.locator("#chroniclePending").innerText(),/미반영 0조각/);
  });
  let initial=await parts();
  await check("unchanged summary creates no new checkpoint",async()=>{
    await job("chronicleGenerate");assert.equal(await parts(),initial);
    assert.match(await page.locator("#toastRegion").innerText(),/호출하지 않았습니다/);
  });
  await tab("media");
  await page.locator("#articleCompose").fill("뒤늦은 후속 기사: 햄버거 값은 결국 동료가 냈다.");await job("articleNote");
  await tab("chronicle");
  await check("new article increments an existing daily edition",async()=>{
    await read();assert.doesNotMatch(await page.locator("#chroniclePending").innerText(),/미반영 0조각/);
    await job("chronicleGenerate");await page.waitForFunction(n=>document.querySelectorAll("#chronicleHistory > .director-turn").length>n,initial);
    assert.match(await page.locator("#chronicleHistory").innerText(),/후속 기사/);
  });
  for(const kind of ["month","year"]) await check(`${kind} synthesis uses independent read-only editorial scope`,async()=>{
    await page.locator(kind==="month" ? "#chronicleMonth" : "#chronicleYear").click();
    assert.equal(await page.locator("#chronicleDirectionField").isVisible(),false);
    await read();await job("chronicleGenerate");
    await page.waitForFunction(()=>document.querySelectorAll("#chronicleHistory > .director-turn").length>0);
  });
  await check("Gemini requires per-request consent and keeps its own edition",async()=>{
    await page.locator("#chronicleEdition").selectOption("gemini");await read();assert.equal(await parts(),0);
    await page.locator("#chronicleGenerate").click();assert.match(await page.locator("#toastRegion").innerText(),/동의/);
    await page.locator("#chronicleRemoteConsent").check();await job("chronicleGenerate");
    await page.waitForFunction(()=>document.getElementById("chronicleHistory").textContent.includes("gemini:"));
    assert.match(await page.locator("#chronicleHistory").innerText(),/gemini:/);
  });
  await check("archive reload preserves summaries and named voices",async()=>{
    await page.reload();await tab("chronicle");
    await page.locator('#workspaceHub [data-shared-tool="voices"]').click();
    assert.equal(await page.locator("#previousOpponent").inputValue(),"한신");
    assert.match(await page.locator("#chronicleLibrary").textContent(),/연간/);
  });
  for(const [width,height,theme] of [[1280,800,"dark"],[1280,800,"light"],[390,844,"light"]]) {
    await page.setViewportSize({width,height});await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
    for(const key of ["media","community","chronicle"]) await check(`${key} width and contrast layout ${width}/${theme}`,async()=>{
      await tab(key);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
      if(key==="chronicle")await read();
      await page.screenshot({path:path.join(output,`${key}-${width}-${theme}.png`)});
    });
  }
  await check("source dialog fits a short viewport and keeps keyboard scrolling",async()=>{
    await page.setViewportSize({width:900,height:420});await tab("media");
    await page.locator("#articleLinkControls summary").click();await page.locator('[data-source-load="article"]').click();
    await page.locator("#articleSourceChoices [data-source-read]").first().click();await page.locator("#connectedSourceDialog").waitFor({state:"visible"});
    const value=await page.locator("#connectedSourceDialog").evaluate(d=>{const r=d.getBoundingClientRect(),b=d.querySelector(".settings-panel__body"); b.scrollTop=b.scrollHeight; return {top:r.top,bottom:r.bottom,outer:d.scrollHeight-d.clientHeight,reached:b.scrollTop+b.clientHeight>=b.scrollHeight-2};});
    assert.ok(value.top>=0 && value.bottom<=422 && value.outer<=2 && value.reached,JSON.stringify(value));
    await page.keyboard.press("Escape");assert.equal(await page.locator("#connectedSourceDialog").isVisible(),false);
  });
  await check("museum library opens stored monthly synthesis without granting writes",async()=>{
    await page.setViewportSize({width:1280,height:800});
    await page.locator("#settingsButton").click();
    await page.evaluate(async()=>{
      const {renderPreservedUniverses,renderMuseumDates}=await import("/js/render.js");
      const h=await(await fetch("/api/v1/universes/world-one/history")).json();
      renderPreservedUniverses({universes:[{universe_id:"world-one",player_name:"보존 검증 선수",capabilities:{read_only:true}}]},"settingsPreservedList");
      renderMuseumDates("world-one",h);
    });
    await page.locator('#settingsPreservedList [data-period-kind="month"]').first().click();
    await page.waitForFunction(()=>document.getElementById("chronicleOwner").textContent.includes("관람 전용"));
    assert.equal(await page.locator("#chronicleGenerate").isDisabled(),true);
    await page.waitForFunction(()=>document.querySelectorAll("#chronicleHistory > .director-turn").length>0);
    await page.locator("#chronicleReturnLive").click();
    assert.equal(await page.locator("#chronicleGenerate").isEnabled(),true);
  });
  await check("read-only world disables all new mutation controls",async()=>{
    await page.evaluate(async()=>{const {renderDashboard}=await import("/js/render.js");const d=(await(await fetch("/api/v1/bootstrap")).json()).dashboard;d.world.binding={live:false};renderDashboard(d);});
    for(const id of ["articleNote","communityLocal","articleGemini","chronicleGenerate","saveOpponentContext"])assert.equal(await page.locator(`#${id}`).isDisabled(),true);
  });
  await check("no script errors",async()=>assert.deepEqual(errors,[]));
}
(async()=>{try{await main();}catch(e){checks.push({name:"runner",passed:false,error:e.stack});console.error(e);}finally{
  if(browser)await browser.close();if(child&&child.exitCode===null){const end=new Promise(r=>child.once("exit",r));child.stdin.end("quit\n");await end;}
  await fs.writeFile(path.join(output,"report.json"),JSON.stringify({checks,errors,stderr,synthetic:true},null,2));
  console.log(`${checks.filter(c=>c.passed).length}/${checks.length} passed`);process.exitCode=checks.some(c=>!c.passed)?1:0;
}})();
