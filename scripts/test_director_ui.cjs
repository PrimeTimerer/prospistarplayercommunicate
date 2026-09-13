/* Real UI/API, disposable fixture, mock providers; no user browser or live inference. */
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs/promises");
const { chromium } = require("playwright");
const root = path.resolve(__dirname, "..");
const reportArg = process.argv.find(arg => arg.startsWith("--output-dir="));
const output = reportArg ? path.resolve(reportArg.slice(13)) : null;
const checks = [], errors = [];
let browser, child, stderr = "";
async function check(name, fn) {
  try { await fn(); checks.push({name, passed:true}); }
  catch (error) { checks.push({name, passed:false, error:error.message}); }
  console.log(`${checks.at(-1).passed ? "PASS" : "FAIL"} ${name}`);
}
async function main() {
  child = spawn(process.env.PYTHON || "python", ["-u", path.join(__dirname, "serve_director_fixture.py")], {cwd:root, windowsHide:true});
  child.stderr.on("data", value => { stderr += value; });
  const origin = await new Promise((resolve, reject) => {
    let text = "";
    child.stdout.on("data", value => { text += value; if (text.includes("\n")) { try { resolve(JSON.parse(text.split("\n")[0]).url); } catch (error) { reject(error); } } });
    child.on("exit", code => reject(new Error(`Fixture exited ${code}: ${stderr}`)));
  });
  browser = await chromium.launch({headless:true, channel:"chrome"});
  const page = await browser.newPage({viewport:{width:1280,height:800}});
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", route => route.request().url().startsWith(origin) || route.request().url().startsWith("data:") ? route.continue() : route.abort());
  await page.goto(origin);
  await page.locator("#tab-narrative").click();
  await check("71 sourced choices and explicit provider buttons", async () => {
    assert.equal(await page.locator("#directorAction option").count(), 71);
    assert.equal(await page.locator("#directorAction").inputValue(), "story_free");
    for (const id of ["directorNote", "directorLocal", "directorGemini"]) assert.equal(await page.locator(`#${id}`).isEnabled(), true);
  });
  await check("previous conversations load without duplicate rows", async () => {
    assert.equal(await page.locator("[data-director-turn]").count(), 30);
    await page.locator("#directorOlder").click();
    await page.waitForFunction(() => document.querySelectorAll("[data-director-turn]").length === 35);
  });
  await check("unsupported game action cannot be confirmed as observed", async () => {
    await page.locator("#directorAction").selectOption("active_draft");
    assert.equal(await page.locator('#directorMode option[value="observed"]').evaluate(option => option.disabled), true);
    assert.match(await page.locator("#directorRule").innerText(), /게임 미지원/);
    await page.locator("#directorAction").selectOption("story_free");
  });
  await check("no-model record persists complete user direction", async () => {
    await page.locator("#directorText").fill("오늘 햄버거를 먹으며 가족의 회계 농담을 한다.\n\n영수증은 삼촌에게 남긴다.");
    await page.locator("#directorNote").click();
    await page.waitForFunction(() => document.getElementById("directorTurnCount").textContent.includes("36"));
    assert.equal(await page.locator("#directorText").inputValue(), "");
    assert.match(await page.locator("[data-director-turn]").last().innerText(), /영수증은 삼촌/);
  });
  await check("local chat uses paragraph renderer and remembers assistant context", async () => {
    await page.locator("#directorText").fill("그 OB에게 같이 치즈버거 먹자고 농담한다.");
    await page.locator("#directorLocal").click();
    await page.waitForFunction(() => document.getElementById("directorTurnCount").textContent.includes("37"));
    assert.equal(await page.locator("[data-director-turn]").last().locator(".director-reply h2").count(), 2);
    await page.locator("[data-director-turn]").last().locator('[data-remember-reply="true"]').click();
    assert.match(await page.locator("#directorMemoryDetail").inputValue(), /가상 OB/);
    await page.locator("#directorMemoryLabel").fill("OB와 치즈버거 약속");
    await page.locator("#directorMemoryForm button[type=submit]").click();
    await page.waitForFunction(() => !document.getElementById("directorMemoryDialog").open);
    assert.match(await page.locator("#directorMemories").textContent(), /OB와 치즈버거/);
  });
  await check("Gemini consent required and successful response retains writer", async () => {
    await page.locator("#directorText").fill("공개된 약속을 이어서 쓴다.");
    await page.locator("#directorGemini").click();
    assert.match(await page.locator("#toastRegion").innerText(), /동의/);
    await page.locator("#directorRemoteConsent").check();
    await page.locator("#directorGemini").click();
    await page.waitForFunction(() => document.getElementById("directorTurnCount").textContent.includes("38"));
    assert.match(await page.locator("[data-director-turn]").last().innerText(), /gemini:/);
  });
  await check("provider failure preserves input and old scenes", async () => {
    await page.locator("#directorText").fill("실패 검증 장면");
    await page.locator("#directorLocal").click();
    await page.waitForFunction(() => document.getElementById("directorLocal").disabled === false);
    assert.equal(await page.locator("#directorText").inputValue(), "실패 검증 장면");
    assert.match(await page.locator("#directorTurnCount").innerText(), /38/);
  });
  await check("selected image uploads, attaches and remains available in history", async () => {
    await page.locator(".director-attachments summary").click();
    await page.locator("#directorImageInput").setInputFiles({name:"fixture.png", mimeType:"image/png", buffer:Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=", "base64")});
    await page.waitForFunction(() => document.getElementById("directorImageCount").textContent === "1장");
    await page.locator("#directorImagesConfirmed").check();
    await page.locator("#directorText").fill("사진의 사물을 소재로 한다.");
    await page.locator("#directorLocal").click();
    await page.waitForFunction(() => document.getElementById("directorTurnCount").textContent.includes("39"));
    const src = await page.locator("[data-director-turn]").last().locator(".director-stored-images img").getAttribute("src");
    assert.equal((await page.request.get(origin + src)).status(), 200);
    const file = src.split("/").at(-1);
    assert.equal((await page.request.get(`${origin}/api/v1/universes/world-one/story-image/${file}`)).status(), 200);
  });
  await page.locator('#workspaceHub [data-shared-tool="memory"]').click();
  await check("reference import requires selection and keeps external recall opt-in", async () => {
    await page.locator("#directorImport").click();
    assert.equal(await page.locator("#directorReferenceRemote").isChecked(), false);
    assert.equal(await page.locator("#directorReferenceItems input:checked").count(), 0);
    await page.locator('#directorReferenceItems input[value="burger"]').check();
    await page.locator("#directorReferenceSave").click();
    await page.waitForFunction(() => !document.getElementById("directorReferenceDialog").open);
    assert.match(await page.locator("#directorMemories").innerText(), /햄버거/);
  });
  const command = value => new Promise(resolve => { child.stdout.once("data", resolve); child.stdin.write(`${value}\n`); });
  const rerender = async () => page.evaluate(async () => {
    const {renderDashboard} = await import("/js/render.js");
    const response = await fetch("/api/v1/bootstrap");
    renderDashboard((await response.json()).dashboard);
  });
  await check("world switch isolates drafts and clears one-shot consent", async () => {
    await page.locator("#directorText").fill("첫 선수의 미완성 장면");
    await command("new-world"); await rerender();
    assert.equal(await page.locator("#directorText").inputValue(), "");
    assert.equal(await page.locator("[data-director-turn]").count(), 0);
    assert.equal(await page.locator("#directorRemoteConsent").isChecked(), false);
    await page.locator("#directorText").fill("다른 선수의 장면");
    await command("original-world"); await rerender();
    assert.equal(await page.locator("#directorText").inputValue(), "첫 선수의 미완성 장면");
  });
  await check("local button follows explicit model availability", async () => {
    await command("offline"); await rerender();
    assert.equal(await page.locator("#directorLocal").isEnabled(), false);
    assert.equal(await page.locator("#directorGemini").isEnabled(), true);
    assert.equal(await page.locator("#directorNote").isEnabled(), true);
  });
  for (const [width,height,theme] of [[1280,720,"dark"],[1280,720,"light"],[390,844,"light"]]) {
    await page.setViewportSize({width,height});
    await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
    await check(`desk width ${width} / ${theme}`, async () => {
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
    });
    for (const [button,id] of [["directorNewMemory","directorMemoryDialog"],["directorImport","directorReferenceDialog"]]) {
      await page.locator(`#${button}`).click();
      await check(`${id} scroll ${width} / ${theme}`, async () => {
        const metrics = await page.locator(`#${id}`).evaluate(dialog => {
          const body = dialog.querySelector(".settings-panel__body"), rect = dialog.getBoundingClientRect();
          body.scrollTop = body.scrollHeight;
          return {top:rect.top,bottom:rect.bottom,viewport:innerHeight,outer:dialog.scrollHeight-dialog.clientHeight,
            reached:body.scrollTop+body.clientHeight >= body.scrollHeight-2, horizontal:body.scrollWidth-body.clientWidth};
        });
        assert.ok(metrics.top >= 0 && metrics.bottom <= height+2 && metrics.outer <= 2 && metrics.reached && metrics.horizontal <= 2, JSON.stringify(metrics));
      });
      if (output) await page.screenshot({path:path.join(output, `${id}-${width}-${theme}.png`)});
      await page.locator(`#${id} [data-director-close]`).click();
    }
  }
  await check("read-only binding disables all generation entry buttons", async () => {
    await page.evaluate(async () => {
      const {renderDashboard} = await import("/js/render.js");
      const data = await (await fetch("/api/v1/bootstrap")).json();
      data.dashboard.world.binding = {live:false,state:"preserved_read_only"};
      renderDashboard(data.dashboard);
    });
    for (const id of ["directorNote", "directorLocal", "directorGemini", "directorNewMemory", "directorImport"]) assert.equal(await page.locator(`#${id}`).isDisabled(), true);
  });
  await check("no browser script errors", async () => assert.deepEqual(errors, []));
}
(async () => {
  try { if (output) await fs.mkdir(output, {recursive:true}); await main(); }
  catch (error) { checks.push({name:"runner",passed:false,error:error.stack}); console.error(error); }
  finally {
    if (browser) await browser.close();
    if (child && child.exitCode === null) { const ended = new Promise(resolve => child.once("exit", resolve)); child.stdin.end("quit\n"); await ended; }
    if (output) await fs.writeFile(path.join(output,"ui-report.json"), JSON.stringify({checks, errors, stderr, synthetic:true},null,2));
    console.log(`${checks.filter(row=>row.passed).length}/${checks.length} passed`);
    process.exitCode = checks.some(row=>!row.passed) ? 1 : 0;
  }
})();
