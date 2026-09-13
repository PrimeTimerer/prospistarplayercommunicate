#!/usr/bin/env node
/* Headless form/render verification against synthetic packaged-backend samples. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright");

const option = name => process.argv.find(value => value.startsWith("--" + name + "="))?.split("=").slice(1).join("=");
const uiRoot = path.resolve(__dirname, "../ui");
const results = [];
const errors = [];
const posts = [];
let samples;
let active;
let nextResult;
const token = "synthetic-interaction-ui-only";
const server = http.createServer(async (request, response) => {
  try {
    const pathname = new URL(request.url, "http://127.0.0.1").pathname;
    if (pathname.startsWith("/api/")) {
      let payload;
      if (request.method === "POST") {
        let text = "";
        for await (const chunk of request) text += chunk;
        posts.push({ path: pathname, body: JSON.parse(text), token: request.headers["x-starmode-token"] });
        if (!nextResult) throw new Error("Unexpected mutation in synthetic UI: " + pathname);
        payload = nextResult;
        nextResult = null;
        active = payload.dashboard || active;
      } else if (pathname === "/api/v1/bootstrap") payload = { ok: true, csrf_token: token, dashboard: active, job: null };
      else if (pathname === "/api/v1/captures") payload = { ok: true, captures: active.captures };
      else if (pathname === "/api/v1/providers/local/status") payload = { ok: true, status: { state: "off", reachable: false, owned: false, can_start: false, can_stop: false, automatic_start: false, message: "합성 로컬 모델 상태" } };
      else if (pathname === "/api/v1/universes") payload = { ok: true, universes: [] };
      else if (pathname === "/api/v1/health") payload = { ok: true, offline: true };
      else payload = { ok: true, job: null };
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify(payload));
      return;
    }
    const relative = pathname === "/" ? "index.html" : decodeURIComponent(pathname.slice(1));
    const target = path.resolve(uiRoot, relative);
    if (!target.startsWith(uiRoot + path.sep)) { response.writeHead(403).end(); return; }
    const types = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2" };
    response.writeHead(200, { "Content-Type": types[path.extname(target)] || "application/octet-stream" });
    response.end(await fs.readFile(target));
  } catch (error) {
    errors.push(error.message);
    response.writeHead(500, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ ok: false, error: { message: error.message } }));
  }
});

async function gate(name, work) {
  try { results.push({ name, passed: true, detail: await work() }); }
  catch (error) { results.push({ name, passed: false, error: error.message }); }
  console.log((results.at(-1).passed ? "PASS " : "FAIL ") + name);
}

async function paint(page, dashboard) {
  active = dashboard;
  await page.evaluate(async value => (await import("/js/render.js")).renderDashboard(value), dashboard);
}

async function main() {
  if (!option("samples") || !option("output-dir")) throw new Error("--samples and --output-dir are required");
  const report = JSON.parse(await fs.readFile(path.resolve(option("samples")), "utf8"));
  assert.ok(report.ok && report.normal_shutdown, "Backend samples must come from a passing isolated run");
  samples = report.samples;
  active = samples.initial;
  const output = path.resolve(option("output-dir"));
  await fs.mkdir(output); // A new evidence directory is mandatory.
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const origin = "http://127.0.0.1:" + server.address().port;
  const browser = await chromium.launch({ headless: true, ...(option("channel") ? { channel: option("channel") } : {}) });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await context.route("**/*", route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(4000);
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(origin);
    await gate("runtime version badge matches the backend", async () => {
      assert.equal(await page.locator("#versionBadge").innerText(), active.app.version);
    });
    await page.locator('[data-view="history"]').click();
    await page.locator('[data-story-category="starplayer"]').waitFor();
    await gate("legacy choices remain intact", async () => {
      assert.equal(await page.locator("[data-story-category]").count(), 8);
      for (const category of active.story.catalog.categories) {
        await page.locator('[data-story-category="' + category.id + '"]').click();
        assert.equal(await page.locator("#storySituation option").count(), category.situations.length);
      }
    });
    await page.locator('[data-story-category="starplayer"]').click();
    await paint(page, samples.worldbook);
    await page.locator('[data-story-category="starplayer"]').click();
    await gate("worldbook renders stored player context and stable counterparts", async () => {
      await page.locator("#storyWorldbook").evaluate(node => { node.open = true; });
      assert.equal(await page.locator("#worldContextList .worldbook-row").count(), 1);
      assert.equal(await page.locator("#worldCounterpartList .worldbook-row").count(), 1);
      assert.match(await page.locator("#worldContextList").innerText(), /햄버거/);
      assert.match(await page.locator("#worldCounterpartList").innerText(), /검증 후배/);
      await page.locator("[data-edit-world-context]").click();
      assert.equal(await page.inputValue("#worldContextLabel"), "햄버거");
      assert.ok(await page.locator("#cancelWorldContextButton").isVisible());
      await page.locator("#cancelWorldContextButton").click();
      await page.selectOption("#worldContextVisibility", "public");
      assert.equal(await page.locator("#worldContextRemoteAllowed").isDisabled(), false);
      await page.check("#worldContextRemoteAllowed");
      await page.selectOption("#worldContextVisibility", "private");
      assert.ok(await page.locator("#worldContextRemoteAllowed").isDisabled());
      assert.equal(await page.isChecked("#worldContextRemoteAllowed"), false);
    });
    await gate("worldbook form sends an origin-bound additive mutation", async () => {
      await page.selectOption("#worldContextBasis", "user_preference");
      await page.fill("#worldContextLabel", "새 원정 루틴");
      await page.fill("#worldContextDetail", "이 세계선에만 남길 설정");
      nextResult = { item: samples.worldbook.story.personal_context.items[0], dashboard: samples.worldbook };
      await Promise.all([
        page.waitForResponse(response => response.url().endsWith("/api/v1/worldbook/context")),
        page.locator("#saveWorldContextButton").click(),
      ]);
      const sent = posts.at(-1);
      assert.equal(sent.path, "/api/v1/worldbook/context");
      assert.equal(sent.body.label, "새 원정 루틴");
      assert.equal(sent.body.visibility, "private");
      assert.equal(sent.body.remote_allowed, false);
      assert.deepEqual(sent.body.world_origin, { universe_id: samples.initial.world.world_id, protagonist_id: String(samples.initial.player.id), game_date: samples.initial.story.game_date });
    });
    await gate("new text inputs inherit the existing dark and light field styles", async () => {
      for (const theme of ["dark", "light"]) {
        if (await page.locator("html").getAttribute("data-theme") !== theme) await page.locator("#themeButton").click();
        const fields = await page.locator("#storyInteractionPanel input[type=text]").evaluateAll(nodes => nodes.map(node => ({
          id: node.id, background: getComputedStyle(node).backgroundColor, color: getComputedStyle(node).color,
          height: node.getBoundingClientRect().height, radius: getComputedStyle(node).borderRadius,
          reference: getComputedStyle(document.getElementById("storyText")).backgroundColor,
        })));
        assert.equal(fields.length, 3);
        for (const field of fields) {
          assert.equal(field.background, field.reference);
          assert.notEqual(field.color, field.background);
          assert.ok(field.height >= 34 && field.radius !== "0px", JSON.stringify(field));
        }
      }
      await page.locator("#themeButton").click();
    });
    await gate("four actions, private scopes, no LLM requirement", async () => {
      assert.equal(await page.locator("#storySituation option").count(), 4);
      assert.deepEqual(await page.locator("#storyVisibility option").evaluateAll(nodes => nodes.map(node => node.value)), ["private", "clubhouse"]);
      assert.equal(await page.locator("#storyTarget option").count(), 3);
      assert.equal(await page.locator("#storyChatButton").isDisabled(), false);
      assert.match(await page.locator("#storyChatButton").innerText(), /LLM 없이/);
    });
    await gate("learning observation controls are explicit", async () => {
      await page.selectOption("#storySituation", "learning");
      assert.equal(await page.locator("#storyLearningProgress option").count(), 4);
      assert.ok(await page.locator("#storyLearningField").isVisible());
      await page.selectOption("#storyInteractionMode", "user_confirmed");
      assert.ok(await page.locator("#storyActionConfirmField").isVisible());
      await page.check("#storyActionConfirmed");
      await page.selectOption("#storyLearningProgress", "acquired");
      assert.equal(await page.isChecked("#storyActionConfirmed"), false);
      assert.match(await page.locator("#storyInteractionRule").innerText(), /자동|직접|확인/);
    });
    await gate("solo walks do not retain a named partner", async () => {
      await page.selectOption("#storySituation", "outing_walk");
      await page.fill("#storyParticipantName", "이전 상대");
      await page.selectOption("#storyTarget", "self");
      assert.equal(await page.inputValue("#storyParticipantName"), "");
      assert.ok(await page.locator("#storyParticipantName").isDisabled());
      await page.selectOption("#storySituation", "player_exchange");
      assert.equal(await page.locator("#storyParticipantName").isDisabled(), false);
    });
    await page.selectOption("#storyTarget", "rookie");
    const counterpartId = samples.worldbook.story.counterparts[0].entity_id;
    await page.selectOption("#storyCounterpart", counterpartId);
    await page.selectOption("#storyInteractionMode", "fictional_intervention");
    await page.selectOption("#storyVisibility", "private");
    await page.fill("#storyInteractionPlace", "비공개 검증 가게");
    await page.fill("#storyInteractionTopic", "햄버거");
    await gate("button sends action, participant, evidence and displayed origin", async () => {
      nextResult = samples.started;
      await page.locator("#storyEventButton").click();
      await page.waitForFunction(id => document.getElementById("storyInteraction").value === id, samples.started.interaction_id);
      const sent = posts.at(-1);
      assert.equal(sent.path, "/api/v1/story/event");
      assert.equal(sent.token, token);
      assert.equal(sent.body.category, "starplayer");
      assert.equal(sent.body.participant_name, "검증 후배");
      assert.equal(sent.body.participant_entity_id, counterpartId);
      assert.equal(sent.body.interaction_topic, "햄버거");
      assert.equal(sent.body.interaction_mode, "fictional_intervention");
      assert.equal(sent.body.action_confirmed, false);
      assert.deepEqual(sent.body.interaction_origin, { universe_id: samples.initial.world.world_id, protagonist_id: String(samples.initial.player.id), game_date: samples.initial.story.game_date });
    });
    await gate("bound conversation previews without silently creating an event", async () => {
      nextResult = samples.preview;
      await page.fill("#storyText", "햄버거 값은 삼진으로 계산하자 ㅋㅋ");
      await page.locator("#storyChatButton").click();
      await page.locator(".proposal-button").first().waitFor();
      const sent = posts.at(-1);
      assert.equal(sent.path, "/api/v1/story/chat");
      assert.equal(sent.body.interaction_id, samples.started.interaction_id);
      assert.equal(sent.body.renderer_preference, "deterministic");
      assert.match(await page.locator("#conversationList").innerText(), /확정 전/);
      assert.equal(await page.locator(".proposal-button").first().isDisabled(), false);
    });
    await gate("confirmation and followups retain the encounter binding", async () => {
      nextResult = samples.confirmed;
      await page.locator(".proposal-button").first().click();
      await page.locator(".proposal-chip.is-committed").first().waitFor();
      assert.equal(posts.at(-1).body.confirm_turn_id, samples.preview.turn.turn_id);
      await page.locator('#storyTimeline [data-interaction-id="' + samples.started.interaction_id + '"]').first().click();
      assert.equal(await page.inputValue("#storyInteraction"), samples.started.interaction_id);
      assert.ok(await page.inputValue("#storyText"));
    });
    await gate("stale proposals are visibly disabled", async () => {
      await paint(page, samples.stale.dashboard);
      const stale = page.locator(".proposal-button", { hasText: "후속 대화로 변경됨" });
      assert.ok(await stale.count());
      assert.ok(await stale.first().isDisabled());
    });
    await gate("encounter library does not truncate earlier meetings", async () => {
      const many = structuredClone(samples.confirmed.dashboard);
      many.story.interactions = Array.from({ length: 70 }, (_, index) => ({ ...many.story.interactions[0], interaction_id: "synthetic-meeting-" + index, label: "검증 만남 " + index + " · " + "오래 남길 이야기 ".repeat(8) }));
      await paint(page, many);
      assert.equal(await page.locator("#storyInteraction option").count(), 71);
      await page.selectOption("#storyInteraction", "synthetic-meeting-69");
      assert.match(await page.locator("#storyInteractionStatus").innerText(), /검증 만남 69/);
    });
    for (const viewport of [{ width: 1280, height: 900 }, { width: 900, height: 650 }, { width: 390, height: 844 }, { width: 900, height: 420 }]) {
      await gate("interaction form reaches its controls at " + viewport.width + "x" + viewport.height, async () => {
        await page.setViewportSize(viewport);
        await page.locator("#storyInteractionPanel").scrollIntoViewIfNeeded();
        const geometry = await page.locator("#storyInteractionPanel").evaluate(panel => ({
          viewport: innerWidth, width: panel.getBoundingClientRect().width, client: panel.clientWidth, scroll: panel.scrollWidth,
          fields: [...panel.querySelectorAll("input,select")].filter(node => node.getBoundingClientRect().width > 0).map(node => ({ id: node.id, left: node.getBoundingClientRect().left, right: node.getBoundingClientRect().right })),
        }));
        assert.ok(geometry.scroll <= geometry.client + 2, JSON.stringify(geometry));
        for (const field of geometry.fields) assert.ok(field.left >= 0 && field.right <= geometry.viewport + 2, JSON.stringify(field));
        await page.locator("#storyChatButton").scrollIntoViewIfNeeded();
        assert.ok(await page.locator("#storyChatButton").evaluate(node => { const rect = node.getBoundingClientRect(); return rect.top >= 0 && rect.bottom <= innerHeight; }));
        if ([1280, 390].includes(viewport.width)) await page.screenshot({ path: path.join(output, "interaction-" + viewport.width + ".png"), fullPage: true });
        return geometry;
      });
    }
    await gate("date or protagonist change clears stale action input", async () => {
      await page.selectOption("#storyInteraction", "");
      await page.selectOption("#storyCounterpart", "");
      await page.fill("#storyParticipantName", "옛 날짜의 상대");
      await page.fill("#storyInteractionTopic", "옛 날짜의 약속");
      await page.fill("#storyText", "오늘의 말");
      await paint(page, samples.next_day);
      assert.equal(await page.inputValue("#storyParticipantName"), "");
      assert.equal(await page.inputValue("#storyInteractionTopic"), "");
      assert.equal(await page.inputValue("#storyText"), "");
      assert.equal(await page.inputValue("#storyInteraction"), "");
      const origin = JSON.parse(await page.locator("#storyInteractionPanel").getAttribute("data-origin"));
      assert.equal(origin.game_date, "2027-07-25");
    });
    await gate("switching back restores the legacy controls and hint", async () => {
      await page.locator('[data-story-category="' + samples.initial.story.catalog.categories[0].id + '"]').click();
      assert.equal(await page.locator("#storyInteractionPanel").isVisible(), false);
      assert.match(await page.locator("#storyModeHint").innerText(), /로컬 LLM 꺼짐/);
      assert.equal(await page.locator("#storyEventButton").innerText(), "버튼으로 이벤트 만들기");
      assert.equal(await page.locator("#storyVisibility option").count(), samples.initial.story.catalog.visibility.length);
    });
    await gate("no browser/runtime errors or unplanned writes", async () => {
      assert.deepEqual(errors, []);
      assert.equal(posts.length, 4);
      assert.ok(posts.every(row => ["/api/v1/worldbook/context", "/api/v1/story/event", "/api/v1/story/chat"].includes(row.path) && row.token === token));
    });
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
    await fs.writeFile(path.join(output, "results.json"), JSON.stringify({ synthetic_only: true, headless: true, results, errors, posts }, null, 2) + "\n", { flag: "wx" });
  }
  if (results.some(row => !row.passed) || errors.length) process.exitCode = 1;
}

main().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
