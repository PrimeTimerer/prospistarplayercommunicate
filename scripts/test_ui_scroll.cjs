#!/usr/bin/env node
/* Exercise the real UI with synthetic, read-only HTTP fixtures; no app service or LLM. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");

const uiRoot = path.resolve(__dirname, "../ui");
const option = name => process.argv.find(value => value.startsWith(`--${name}=`))?.split("=").slice(1).join("=");
const outputDir = option("output-dir") ? path.resolve(option("output-dir")) : null;
const text = "기록과 감정의 흐름을 오래 보관하는 화면입니다. 긴 문단도 사라지지 않고 마지막 문장까지 읽을 수 있어야 합니다.";
const turns = Array.from({ length: 3 }, (_, index) => ({
  sequence: index + 1, provenance: "button_generated_fiction",
  scene: { title: `장면 ${index + 1}`, response: `${Array(8).fill(text).join("\n\n")}\n\n장면 끝 ${index + 1}` },
}));
const feed = {
  media: Array.from({ length: 19 }, (_, index) => ({
    flag: "", outlet: `보관 매체 ${index + 1}`, title: `검증용 기사 ${index + 1}`,
    sub: "보관된 기사 요약", body: [text, text, `기사 본문 끝 ${index + 1}`],
  })),
  boards: Array.from({ length: 23 }, (_, index) => ({
    code: `fixture-${index}`, board: `보관 게시판 ${index + 1}`, title: `보관 토론 ${index + 1}`,
    comments: Array.from({ length: 6 }, (_, comment) => ({
      author: `독자 ${comment + 1}`, text: `${text} 댓글 끝 ${index + 1}-${comment + 1}`, up: comment,
    })),
  })),
};
const memoryRows = Array.from({ length: 18 }, (_, index) => ({
  memory_id: `memory-${index + 1}`, kind: index % 2 ? "conversation" : "story",
  label: `검증 기억 ${index + 1}`, detail: `${text} 기억 원문 끝 ${index + 1}`,
  game_date: `2027-07-${String(25 - index).padStart(2, "0")}`,
}));
const memoryViews = {
  eligible_game_count: 18,
  issues: [
    { label: "햄버거 약속", status: "active", remaining_games: 2, budget_games: 5, created_on: "2027-07-24", visibility: "private" },
    { label: "끝난 긴장", status: "expired", remaining_games: 0, budget_games: 3, created_on: "2027-07-20", visibility: "private" },
  ],
  windows: [
    { key: "short_recent_3", label: "최근 3경기", total: 3, items: memoryRows.slice(0, 3) },
    { key: "medium_recent_4_10", label: "4~10경기 전", total: 7, items: memoryRows.slice(3, 10) },
    { key: "long_season", label: "이번 시즌", total: 8, items: memoryRows.slice(10) },
  ],
  anchors: [{ memory_id: "anchor-1", kind: "milestone", label: "시즌 탈삼진 기준 통과", detail: "보관된 사실", game_date: "2027-07-25" }],
};
const capsule = {
  game_date: "2027-07-25", status: "sealed", verified_snapshot: { stats: { pit_K: 388, bat_HR: 110, bat_H: 206, pit_W: 17 } },
  verified_events: [{ kind: "NEW_GAME", game_lines: ["타격: 5타수 5안타"] }],
  milestones: [{ label: "시즌 탈삼진 기준 통과", note: "보관된 사실" }],
  personal_context: { items: [
    { context_id: "context-history", label: "그날의 햄버거 취향", detail: "당시 세계선 설정 그대로", visibility_label: "비공개", status: "active" },
  ] },
  counterparts: [
    { entity_id: "counterpart-history", label: "그날의 가상 후배", role_labels: ["후배 선수"], note: "당시 이름과 관계 메모", status: "active" },
  ],
  story: { turns }, memory_views: memoryViews, combined_feed: feed,
  league_and_value: {
    standings: [{ rank: 1, team: "검증 구단", wins: 80, losses: 40 }],
    sabermetrics: { batting: Array.from({ length: 10 }, (_, index) => ({
      label: `검증 지표 ${index + 1}`, display: ".730", formula: "H / AB", available: true, provenance: "save_derived",
    })) },
  },
};
const dashboard = {
  empty: false, config: {
    save_exists: true, save_path: "fixture/00/StarPlayer.dat", theme: "dark", platforms: ["dc"],
    community_language_level: 2, notifications_enabled: false,
    ai_provider: "gemini", gemini_consent: true, gemini_key_present: true,
    gemini_key_source: "windows_account", gemini_model: "gemini-fixture",
    gemini_models: ["gemini-fixture"],
    gemini_usage: {
      today: { calls: 2, total_tokens: 345 },
      total: { calls: 7, prompt_tokens: 800, output_tokens: 400, total_tokens: 1200, cached_tokens: 50, thoughts_tokens: 25 },
      last_operation: { at: "2026-09-04T12:30:00+09:00", operation: "cinematic_narrative", model: "gemini-fixture", status: "success", error_code: null },
      connection: { status: "verified", tested_at: "2026-09-04T12:00:00+09:00", model: "gemini-fixture" },
    },
  },
  player: { name: "스크롤 검증 선수", team: "검증 구단" }, date: { year: 2027, month: 7, day: 27 },
  app: {
    version: "SCROLL TEST", llm_reachable: false,
    local_model: { state: "off", reachable: false, owned: false, can_start: false, can_stop: false, automatic_start: false },
    community_style_database: { source_count: 12, profile_count: 9, storage_mode: "derived_features_only" },
    runtime_web_search: false,
  }, world: { world_id: "scroll-fixture", slot: "00" },
  stats: capsule.verified_snapshot.stats, feed, story: { current: { turns }, memory_views: memoryViews },
  narrative: `**1. 첫 장면.** ${Array(15).fill(text).join(" ")} **2. 다음 장면.** ${Array(15).fill(text).join(" ")}`,
  captures: { items: [], hotkeys: { active: false, supported: false } },
  history_vault: Array.from({ length: 30 }, (_, index) => ({
    game_date: `2027-07-${String(30 - index).padStart(2, "0")}`, status: "sealed", headline: `보관 날짜 ${index + 1}`,
    story_turns: 3, feed_archives: 1, events: 1, standings: 1, top5_stats: 1,
  })),
  daily_archive: [{ id: "fixture-archive", game_date: "2027-07-25", headline: "원문 보관 보기", board_count: 23, article_count: 19 }],
};
const library = { universes: [
  { universe_id: "scroll-fixture", player_name: "스크롤 검증 선수", team: "검증 구단", slot: "00", availability_badge: "연결됨", capabilities: { read_only: false, can_view: true } },
  { universe_id: "preserved-fixture", player_name: "보존 선수", team: "지난 구단", slot: "01", availability_badge: "보존·관람 전용", capabilities: { read_only: true, can_view: true } },
] };
const museumHistory = Array.from({ length: 70 }, (_, index) => ({
  game_date: new Date(Date.UTC(2027, 6, 30 - index)).toISOString().slice(0, 10),
  headline: `보존 기록 ${index + 1} · ${text}`, status: "sealed",
}));
const requests = [];
const errors = [];
const results = [];
if (process.argv.includes("--reading-fixture")) {
  dashboard.config.heat = 7;
  dashboard.player.name = "가상 에이스";
  feed.boards = ["dc", "fmk", "mlb"].map((code, index) => ({
    id: `reading-${code}`, code, board: code, title: ["상대 팬인데 오늘은 인정ㅋㅋ", "이 장면만 벌써 몇 번째 보냐", "좋은 플레이와 시즌 평가는 나눠서 봅시다"][index],
    expression_renderer: ["gemini", "local_llm", "builtin"][index],
    thread_meta: { category: "야구", views: 1024, recommendations: 27, posted_at: "21:04" },
    comments: [
      { is_opener: true, author: "가상야구팬", posted_at: "21:04", text: "가상 에이스, 우리 팀 상대로만 좀 쉬면 안 되냐ㅋㅋ\n\n잘하는 건 인정하는데 상대 팬은 속이 쓰리다." },
      { is_opener: false, author: "원정석", text: "그러게ㅋㅋ 인정", up: 12, posted_at: "21:05" },
      { author: "야구노트", text: "좋은 장면 맞죠. 다만 한 경기로 시즌 비교까지 끝내긴 이릅니다.\n서로 같은 기준으로 보자는 얘기입니다.", up: 4, reply_depth: 1 },
      { author: "홈팬", text: "오늘만은 좀 기뻐하자ㅋㅋ", up: 19, reply_depth: 1 },
    ],
  }));
  feed.social = ["x", "threads", "instagram", "facebook", "japan-translation", "global-translation"].map((code, index) => ({
    id: `reading-${code}`, code, author: "@fictional_fan", expression_renderer: index % 2 ? "local_llm" : "gemini",
    text: "가상 에이스의 하루.\n이런 날에는 기록표보다 관중석 소리가 먼저 떠오른다.", reactions: 128,
    replies: [{ id: "reply-1", author: "가상 관중", text: "퇴근길에 다시 보는 중ㅋㅋ", reactions: 4 }, { id: "reply-2", author: "다른 팬", text: "상대편이라 아쉽지만 인정합니다.", reactions: 2 }],
  }));
  feed.media = feed.media.slice(0, 3).map((row, index) => ({ ...row, expression_renderer: ["gemini", "local_llm", "builtin"][index] }));
}
const server = http.createServer(async (request, response) => {
  const pathname = new URL(request.url, "http://localhost").pathname;
  if (pathname === "/fixture-audit") {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ requests, synthetic: true }));
    return;
  }
  if (pathname.startsWith("/api/")) {
    requests.push(`${request.method} ${pathname}`);
    let payload;
    if (request.method === "POST" && pathname === "/api/v1/providers/gemini/auto-activate") {
      for await (const _chunk of request) { /* drain the synthetic body */ }
      payload = { ok: true, connected: true, activated: true, attempted: false, cached: true, status: "verified", config: dashboard.config };
    } else if (request.method !== "GET") { response.writeHead(405).end(); return; }
    if (pathname === "/api/v1/bootstrap") payload = { csrf_token: "synthetic-only", dashboard };
    if (pathname === "/api/v1/captures") payload = { captures: dashboard.captures };
    if (pathname === "/api/v1/providers/local/status") payload = { ok: true, status: { state: "off", reachable: false, owned: false, can_start: false, can_stop: false, automatic_start: false, message: "합성 로컬 모델 상태" } };
    if (pathname === "/api/v1/universes") payload = library;
    if (/^\/api\/v1\/universes\/[^/]+\/history$/.test(pathname)) payload = { history: museumHistory, read_only: true };
    if (/^\/api\/v1\/universes\/[^/]+\/history\//.test(pathname)) payload = { capsule: { ...capsule, game_date: pathname.split("/").at(-1), read_only: true } };
    if (pathname.startsWith("/api/v1/history/")) payload = { capsule };
    if (pathname.startsWith("/api/v1/archive/")) payload = { archive: { game_date: "2027-07-25", feed } };
    if (pathname === "/api/v1/saves") payload = { saves: Array.from({ length: 30 }, (_, index) => ({
      path: `fixture/${index}/StarPlayer.dat`, slot: String(index), identity_status: "verified",
      player: { name: `검증 선수 ${index}`, team: "검증 구단", age: 24 },
    })) };
    response.writeHead(payload ? 200 : 404, { "Content-Type": "application/json" });
    response.end(JSON.stringify(payload || { ok: false }));
    return;
  }
  const relative = pathname === "/" ? "index.html" : decodeURIComponent(pathname.slice(1));
  const filePath = path.resolve(uiRoot, relative);
  if (!filePath.startsWith(`${uiRoot}${path.sep}`)) { response.writeHead(403).end(); return; }
  try {
    const content = await fs.readFile(filePath);
    const types = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2" };
    response.writeHead(200, { "Content-Type": types[path.extname(filePath)] || "application/octet-stream" });
    response.end(content);
  } catch { response.writeHead(404).end(); }
});

async function check(name, callback) {
  try { const detail = await callback(); results.push({ name, passed: true, detail }); }
  catch (error) { results.push({ name, passed: false, error: error.message }); }
  console.log(`${results.at(-1).passed ? "PASS" : "FAIL"} ${name}`);
}

async function dialogMetrics(page, id) {
  return page.locator(`#${id}`).evaluate(dialog => {
    const body = dialog.querySelector(".settings-panel__body");
    const bounds = dialog.getBoundingClientRect();
    const clipped = [...body.querySelectorAll(".capsule-section, .board, .article-card")]
      .filter(node => getComputedStyle(node).overflowY === "hidden" && node.scrollHeight > node.clientHeight + 2)
      .map(node => ({ className: node.className, client: node.clientHeight, scroll: node.scrollHeight }));
    return { top: bounds.top, bottom: bounds.bottom, viewport: innerHeight, client: body.clientHeight, scroll: body.scrollHeight, clipped,
      outerOverflow: dialog.scrollHeight > dialog.clientHeight + 2,
      horizontalOverflow: body.scrollWidth > body.clientWidth + 2 };
  });
}

async function checkScroll(page, id, lastSelector) {
  const body = page.locator(`#${id} .settings-panel__body`);
  const metrics = await dialogMetrics(page, id);
  assert.ok(metrics.top >= 0 && metrics.bottom <= metrics.viewport, `Dialog outside viewport: ${JSON.stringify(metrics)}`);
  assert.equal(metrics.clipped.length, 0, `Clipped cards: ${JSON.stringify(metrics.clipped)}`);
  assert.equal(metrics.outerOverflow, false, "The dialog itself must not become a second scroll container");
  assert.equal(metrics.horizontalOverflow, false, "Only input tables may scroll horizontally");
  const scrollable = metrics.scroll > metrics.client + 2;
  if (scrollable) {
    await body.evaluate(node => { node.scrollTop = 0; });
    await body.hover({ position: { x: 20, y: 20 } });
    await page.mouse.wheel(0, 400);
    await page.waitForFunction(selector => document.querySelector(selector).scrollTop > 0, `#${id} .settings-panel__body`);
    await body.focus();
    await page.keyboard.press("Control+End");
    await page.waitForFunction(selector => {
      const node = document.querySelector(selector);
      return node.scrollTop + node.clientHeight >= node.scrollHeight - 2;
    }, `#${id} .settings-panel__body`);
  }
  const visible = await page.locator(lastSelector).last().evaluate(node => {
    const region = node.closest(".settings-panel__body").getBoundingClientRect();
    const rect = node.getBoundingClientRect();
    return rect.bottom <= region.bottom + 2 && rect.bottom > region.top;
  });
  assert.ok(visible, `Last content is not reachable: ${lastSelector}`);
  const controls = page.locator(`#${id} .settings-panel__header .icon-button, #${id} .settings-panel__footer`);
  for (const control of await controls.all()) {
    assert.ok(await control.evaluate(node => { const rect = node.getBoundingClientRect(); return rect.top >= 0 && rect.bottom <= innerHeight; }), "Close/save controls must remain visible");
  }
  return metrics;
}

async function main() {
  const { chromium } = require("playwright");
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true, ignoreDefaultArgs: ["--hide-scrollbars"], ...(option("channel") ? { channel: option("channel") } : {}) });
  try {
    const context = await browser.newContext();
    await context.route("**/*", route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(4000);
    page.on("pageerror", error => errors.push(error.message));
    for (const viewport of [{ width: 1280, height: 800 }, { width: 900, height: 650 }, { width: 390, height: 844 }, { width: 900, height: 420 }]) {
      await page.setViewportSize(viewport);
      await page.goto(origin);
      await page.locator(".vault-row").first().waitFor({ state: "attached" });
      await check(`${viewport.width}x${viewport.height} independent reaction controls`, async () => {
        const checks = [
          ["media", ["#articleProviderBadge", "#enrichArticlesButton"]],
          ["community", ["#feedProviderBadge", "#enrichCommunityButton", "#enrichFeedButton"]],
        ];
        for (const [view, selectors] of checks) {
          await page.locator(`[data-view="${view}"]`).click();
          for (const selector of selectors) {
            const node = page.locator(selector);
            await node.waitFor({ state: "visible" });
            await node.scrollIntoViewIfNeeded();
            assert.ok(await node.evaluate(element => {
              const rect = element.getBoundingClientRect();
              return rect.left >= -2 && rect.right <= innerWidth + 2 && rect.top >= -2 && rect.bottom <= innerHeight + 2;
            }), `${selector} must remain inside the viewport`);
          }
        }
        assert.ok((await page.locator("#enrichArticlesButton").innerText()).includes("기사만"));
        assert.ok((await page.locator("#enrichCommunityButton").innerText()).includes("커뮤니티·SNS만"));
        assert.ok((await page.locator("#enrichFeedButton").innerText()).includes("전체 함께"));
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
        return { article: true, community: true, whole: true };
      });
      await page.locator('[data-view="history"]').click();
      await page.locator('#workspaceHub [data-shared-tool="memory"]').click();
      await page.locator("#storyMemory summary").click();
      await check(`${viewport.width}x${viewport.height} layered memory and finite issues`, async () => {
        const body = await page.locator("#storyMemory").innerText();
        assert.match(body, /햄버거 약속/);
        assert.match(body, /2\/5경기 남음/);
        assert.match(body, /자동 재등장 종료 · 기록 보존/);
        assert.match(body, /최근 3경기/);
        assert.match(body, /4~10경기 전/);
        assert.match(body, /이번 시즌/);
        assert.match(body, /시즌 탈삼진 기준 통과/);
        const windows = page.locator("#storyMemoryWindows");
        assert.equal(await windows.getAttribute("tabindex"), "0");
        assert.ok(await windows.evaluate(node => node.scrollWidth <= node.clientWidth + 2));
      });
      await check(`${viewport.width}x${viewport.height} independent provider lamps`, async () => {
        assert.match(await page.locator("#llmDot").getAttribute("class"), /status-dot--muted/);
        assert.equal(await page.locator("#llmStatus").innerText(), "로컬 LLM 꺼짐");
        assert.doesNotMatch(await page.locator("#apiDot").getAttribute("class"), /status-dot--muted|status-dot--warn|status-dot--bad/);
        assert.equal(await page.locator("#apiStatus").innerText(), "Gemini API 켜짐");
        assert.equal(await page.locator("#narrativeButtonLabel").innerText(), "Gemini 서사 생성");
      });
      await check(`${viewport.width}x${viewport.height} local override controls follow read-only status`, async () => {
        const ids = ["localEnrichArticlesButton", "localEnrichCommunityButton", "localNarrativeButton"];
        const hiddenFlags = () => page.evaluate(ids => ids.map(id =>
          document.getElementById(id).classList.contains("is-hidden")), ids);
        assert.ok((await hiddenFlags()).every(Boolean));
        await page.evaluate(async source => {
          const { renderDashboard } = await import("/js/render.js");
          renderDashboard({
            ...source,
            app: {
              ...source.app,
              llm_reachable: true,
              local_model: { state: "external", reachable: true, owned: false, can_start: false, can_stop: false, automatic_start: false },
            },
          });
        }, dashboard);
        assert.ok((await hiddenFlags()).every(value => !value));
        await page.evaluate(async source => {
          const { renderDashboard } = await import("/js/render.js");
          renderDashboard(source);
        }, dashboard);
        assert.ok((await hiddenFlags()).every(Boolean));
        return { controls: ids.length, startsModel: false };
      });
      await page.locator('[data-view="records"]').click();
      const label = `${viewport.width}x${viewport.height}`;
      const cases = [
        ["historyDialog", ".vault-row", "#historyDialogBody .board:last-child .comment:last-child"],
        ["archiveDialog", "[data-open-archive]", "#archiveDialogBody .board:last-child .comment:last-child"],
        ["settingsDialog", "#settingsButton", "#settingsDialog .form-section:last-child"],
        ["seasonDialog", "#addSeasonButton", "#seasonNoteInput"],
        ["honorDialog", "#addHonorButton", "#honorDialog .form-note:last-child"],
        ["leagueDialog", "#editLeagueButton", "#saveLeaderboardButton"],
        ["valueDialog", "#editValueButton", "#salaryOfferEditor"],
      ];
      for (const [id, trigger, last] of cases) {
        await page.locator(trigger).first().click();
        if (id === "historyDialog") await page.locator("#historyDialogBody .capsule-section").first().waitFor();
        if (id === "archiveDialog") await page.locator("#archiveDialogBody .board").first().waitFor({ state: "attached" });
        if (id === "settingsDialog") {
          await page.locator("#settingsRescanButton").click();
          await page.locator(".save-candidate").first().waitFor({ state: "attached" });
          await page.locator('#settingsPreservedList [data-universe-card="preserved-fixture"]').waitFor({ state: "attached" });
          await check(`${label} live save and museum coexist`, async () => {
            assert.equal(await page.locator("#settingsPreservedList .preserved-card").count(), 2);
            assert.ok((await page.locator("#settingsPreservedList").innerText()).includes("보존 선수"));
            assert.ok(await page.locator("#emptyState").isHidden());
            assert.ok(await page.locator("#saveCandidates .save-candidate").count() > 0);
            assert.equal(await page.locator("#geminiTodayCalls").innerText(), "2");
            assert.equal(await page.locator("#geminiTodayTokens").innerText(), "345");
            assert.equal(await page.locator("#geminiTotalCalls").innerText(), "7");
            assert.equal(await page.locator("#geminiTotalTokens").innerText(), "1,200");
            const settingsText = await page.locator("#settingsDialog").innerText();
            assert.match(settingsText, /로컬 LLM을 자동으로 실행하지 않습니다/);
            assert.match(settingsText, /실행 중인 로컬 LLM 우선 · 없으면 내장 엔진/);
          });
          await check(`${label} language notification and corpus settings`, async () => {
            assert.equal(await page.locator("#languageLevelInput").inputValue(), "2");
            assert.equal(await page.locator("#languageLevelOutput").innerText(), "2 · 자연스러운 구어");
            assert.equal(await page.locator("#notificationsEnabledInput").isChecked(), false);
            assert.ok((await page.locator("#notificationStatus").innerText()).length > 0);
            assert.match(await page.locator("#communityCorpusState").innerText(), /출처 원장 12곳 · 표면 프로필 9종/);
            assert.match(await page.locator("#communityCorpusDetail").innerText(), /런타임 웹 검색은 꺼져 있습니다/);
            assert.ok(await page.locator("#settingsDialog .settings-panel__body").evaluate(node => node.scrollWidth <= node.clientWidth + 2));
          });
          await check(`${label} settings navigation and unchanged fast default`, async () => {
            const beforePath = await page.locator("#savePathInput").inputValue();
            assert.equal(await page.locator("#geminiConcurrencyInput").inputValue(), "5");
            const buttons = page.locator("#settingsJumpbar button");
            assert.equal(await buttons.count(), 5);
            for (const button of await buttons.all()) {
              await button.click();
              const id = await button.getAttribute("data-settings-section");
              assert.equal(await page.evaluate(() => document.activeElement.id), id);
              assert.ok(await page.locator(`#${id}`).evaluate(node => {
                const box = node.getBoundingClientRect();
                const body = node.closest(".settings-panel__body").getBoundingClientRect();
                return box.top < body.bottom && box.bottom > body.top;
              }));
            }
            assert.equal(await page.locator("#savePathInput").inputValue(), beforePath);
            await page.locator('[data-settings-section="settingsAI"]').click();
            const input = await page.locator("#geminiApiKeyInput").evaluate(node => {
              const style = getComputedStyle(node);
              return { size: parseFloat(style.fontSize), radius: parseFloat(style.borderRadius), type: node.type };
            });
            assert.ok(input.size >= 13 && input.radius >= 6, JSON.stringify(input));
            assert.equal(input.type, "password");
          });
          if (outputDir && viewport.width === 1280) {
            await page.locator("#geminiUsagePanel").scrollIntoViewIfNeeded();
            await page.screenshot({ path: path.join(outputDir, "gemini-provider-settings.png") });
          }
          await check(`${label} all museum dates and read-only capsule`, async () => {
            const beforePath = await page.locator("#savePathInput").inputValue();
            await page.locator('#settingsPreservedList [data-museum-universe="preserved-fixture"]').click();
            const dates = page.locator('#settingsPreservedList [data-museum-dates="preserved-fixture"]');
            await dates.locator("button").last().waitFor({ state: "attached" });
            assert.equal(await dates.locator("button").count(), 70);
            await dates.evaluate(node => { node.scrollIntoView({ block: "center" }); node.scrollTop = 0; });
            await dates.hover({ position: { x: 12, y: 20 } });
            await page.mouse.wheel(0, 160);
            await page.waitForFunction(() => document.querySelector('#settingsPreservedList [data-museum-dates="preserved-fixture"]').scrollTop > 0);
            await dates.focus();
            await page.keyboard.press("Control+End");
            await page.waitForFunction(() => {
              const node = document.querySelector('#settingsPreservedList [data-museum-dates="preserved-fixture"]');
              return node.scrollTop + node.clientHeight >= node.scrollHeight - 2;
            });
            assert.ok(await dates.evaluate(node => node.scrollWidth <= node.clientWidth + 2));
            await dates.locator("button").last().click();
            await page.locator("#historyDialogBody .capsule-story").first().waitFor({ state: "attached" });
            assert.ok((await page.locator("#historyDialogBody").innerText()).includes(museumHistory.at(-1).game_date));
            await checkScroll(page, "historyDialog", "#historyDialogBody .comment:last-child");
            await page.keyboard.press("Escape");
            assert.ok(await page.locator("#settingsDialog").evaluate(node => node.open));
            assert.equal(await page.locator("#savePathInput").inputValue(), beforePath);
            if (outputDir) {
              await dates.evaluate(node => node.scrollIntoView({ block: "center" }));
              await page.screenshot({ path: path.join(outputDir, `museum-${label}.png`) });
            }
          });
        }
        await check(`${label} ${id}`, () => checkScroll(page, id, last));
        if (id === "historyDialog") {
          await check(`${label} frozen full text`, async () => {
            assert.equal(await page.locator("#historyDialogBody .capsule-story").count(), 3);
            assert.equal(await page.locator("#historyDialogBody .article-card p").count(), 57);
            // 2.5.21 separates each opener from its five replies, preserving all six texts.
            assert.equal(await page.locator("#historyDialogBody .board__post").count(), 23);
            assert.equal(await page.locator("#historyDialogBody .comment").count(), 115);
            assert.equal(await page.locator("#historyDialogBody .board__post, #historyDialogBody .comment").count(), 138);
            assert.ok((await page.locator("#historyDialogBody").innerText()).includes("댓글 끝 23-6"));
            assert.ok((await page.locator("#historyDialogBody").innerText()).includes("그날의 햄버거 취향"));
            assert.ok((await page.locator("#historyDialogBody").innerText()).includes("그날의 가상 후배"));
          });
          if (outputDir) {
            await fs.mkdir(outputDir, { recursive: true });
            await page.locator("#historyDialogBody").evaluate(node => { node.scrollTop = 0; });
            await page.screenshot({ path: path.join(outputDir, `history-${label}.png`) });
          }
        }
        if (id === "leagueDialog" && viewport.width === 390) {
          await check(`${label} horizontal editor`, async () => {
            const table = page.locator("#leaderboardEditor");
            await table.evaluate(node => { node.scrollIntoView({ block: "center" }); node.scrollLeft = node.scrollWidth; });
            assert.ok(await table.evaluate(node => node.scrollLeft > 0));
            await table.locator("input").last().focus();
            assert.ok(await table.locator("input").last().evaluate(node => {
              const rect = node.getBoundingClientRect(); const bounds = node.closest(".table-editor").getBoundingClientRect();
              return rect.right <= bounds.right + 2 && rect.left >= bounds.left - 2;
            }));
          });
        }
        await page.keyboard.press("Escape");
        assert.equal(await page.locator(`#${id}`).evaluate(node => node.open), false, "Escape must close the dialog");
      }
      await check(`${label} vault wheel and keyboard`, async () => {
        const vault = page.locator("#historyVaultList");
        await vault.evaluate(node => { node.scrollTop = 0; });
        await vault.hover({ position: { x: 12, y: 20 } });
        await page.mouse.wheel(0, 250);
        await page.waitForFunction(() => document.getElementById("historyVaultList").scrollTop > 0);
        await vault.focus();
        await page.keyboard.press("End");
        await page.waitForFunction(() => {
          const node = document.getElementById("historyVaultList");
          return node.scrollTop + node.clientHeight >= node.scrollHeight - 2;
        });
      });
      await check(`${label} job log reading position`, async () => {
        const snapshot = await page.evaluate(async () => {
          const { renderJob } = await import("/js/render.js");
          const log = document.getElementById("jobLog");
          const makeJob = (id, count) => ({ id, status: "running", logs: Array.from({ length: count }, (_, i) => ({ time: "12:00", message: `검증 로그 ${i}` })) });
          renderJob(makeJob("scroll-fixture", 60));
          const initialTail = log.scrollTop + log.clientHeight >= log.scrollHeight - 2;
          log.scrollTop = 0;
          renderJob(makeJob("scroll-fixture", 61));
          const preserved = log.scrollTop === 0;
          log.scrollTop = log.scrollHeight;
          renderJob(makeJob("scroll-fixture", 62));
          const followed = log.scrollTop + log.clientHeight >= log.scrollHeight - 2;
          log.scrollTop = 0;
          renderJob(makeJob("next-fixture", 60));
          const newJobTail = log.scrollTop + log.clientHeight >= log.scrollHeight - 2;
          return { initialTail, preserved, followed, newJobTail };
        });
        assert.ok(Object.values(snapshot).every(Boolean), JSON.stringify(snapshot));
        return snapshot;
      });
      await check(`${label} honest elapsed time and per-surface writers`, async () => {
        const snapshot = await page.evaluate(async source => {
          const { renderDashboard, renderJob } = await import("/js/render.js");
          renderDashboard({ ...source, feed: {
            ...source.feed,
            media: source.feed.media.slice(0, 2).map((row, i) => ({ ...row, expression_renderer: i ? "builtin" : "gemini" })),
            boards: source.feed.boards.slice(0, 1).map(row => ({ ...row, expression_renderer: "local_llm" })),
          } });
          renderJob({ id: "timing", status: "completed", progress: 100, started_at: "2026-09-05T10:00:00Z", finished_at: "2026-09-05T10:01:13Z", logs: [] });
          const result = {
            job: document.getElementById("jobSummary").textContent,
            articles: document.getElementById("articleResultNote").textContent,
            community: document.getElementById("communityResultNote").textContent,
          };
          renderDashboard(source);
          renderJob(null);
          result.legacy = document.getElementById("articleResultNote").textContent;
          result.idle = document.getElementById("jobSummary").textContent;
          return result;
        }, dashboard);
        assert.match(snapshot.job, /100% 진행.*소요 1분 13초/);
        assert.match(snapshot.articles, /Gemini 1건.*내장 엔진 1건/);
        assert.match(snapshot.community, /로컬 LLM 1건/);
        assert.equal(snapshot.legacy, "");
        assert.equal(snapshot.idle, "");
        return snapshot;
      });
      for (const [view, last] of [["history", "#storyTimeline .story-turn:last-child"], ["community", "#communityList .comment:last-child"], ["media", "#mediaList .article-card:last-child p:last-child"], ["narrative", "#narrativeContent p:last-child"]]) {
        await check(`${label} ${view} page scroll`, async () => {
          await page.locator(`[data-view="${view}"]`).click();
          if (view === "history") {
            await page.locator('#tab-narrative').click();
            await page.locator('[data-workspace-mode="narrative"][data-workspace-part="events"]').click();
          }
          if (view === "narrative") await page.locator('[data-workspace-mode="narrative"][data-workspace-part="cinema"]').click();
          const node = page.locator(last).last();
          const bounds = await node.evaluate(async element => {
            await document.fonts.ready;
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            element.scrollIntoView({ block: "end", behavior: "instant" });
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const bounds = element.getBoundingClientRect();
            return { top: bounds.top, bottom: bounds.bottom, viewport: innerHeight, scrollY };
          });
          assert.ok(bounds.bottom <= bounds.viewport + 2 && bounds.bottom > 0, JSON.stringify(bounds));
          assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
          if (view === "narrative") {
            assert.equal(await page.locator("#narrativeContent h2").count(), 2);
            assert.equal(await page.locator("#narrativeContent p").count(), 2);
            const separated = await page.locator("#narrativeContent").evaluate(node => {
              const firstHeading = node.querySelector("h2");
              const firstParagraph = node.querySelector("p");
              return firstParagraph.getBoundingClientRect().top > firstHeading.getBoundingClientRect().bottom;
            });
            assert.ok(separated, "Numbered local-model scenes must render as separated sections");
            if (outputDir && viewport.width === 1280) {
              await page.locator("#narrativeContent").evaluate(node => node.scrollIntoView({ block: "start" }));
              await page.screenshot({ path: path.join(outputDir, "normalized-narrative.png") });
            }
          }
        });
      }
    }
    await page.setViewportSize({ width: 900, height: 650 });
    await page.goto(origin);
    await page.locator(".vault-row").first().waitFor({ state: "attached" });
    await page.locator("#themeButton").click();
    await page.locator('[data-view="records"]').click();
    await page.locator(".vault-row").first().click();
    await page.locator("#historyDialogBody .comment").first().waitFor({ state: "attached" });
    await check("light theme history scroll", () => checkScroll(page, "historyDialog", "#historyDialogBody .comment:last-child"));
    await check("visible scrollbar track click", async () => {
      const track = await page.locator("#historyDialogBody").evaluate(node => {
        node.scrollTop = 0;
        const bounds = node.getBoundingClientRect();
        const gutter = node.offsetWidth - node.clientWidth;
        const x = bounds.right - gutter / 2;
        const y = bounds.top + node.clientHeight * 0.75;
        return { gutter, x, y, target: document.elementFromPoint(x, y)?.id };
      });
      assert.ok(track.gutter > 0, "A real scrollbar gutter must be available");
      // Native scrollbar hit testing follows the painted thumb, not just scrollTop.
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.mouse.click(track.x, track.y, { delay: 120 });
      await page.waitForFunction(() => document.getElementById("historyDialogBody").scrollTop > 0 || !document.getElementById("historyDialog").open).catch(() => {});
      const state = await page.locator("#historyDialogBody").evaluate(node => ({ open: node.closest("dialog").open, scrollTop: node.scrollTop }));
      if (outputDir) await page.screenshot({ path: path.join(outputDir, "scrollbar-track.png") });
      assert.ok(state.open && state.scrollTop > 0, JSON.stringify({ track, state }));
    });
    await page.setViewportSize({ width: 900, height: 420 });
    await check("open dialog follows viewport resize", () => checkScroll(page, "historyDialog", "#historyDialogBody .comment:last-child"));
    await page.keyboard.press("Escape");
    await check("collapsed Gemini headings receive visible sections and paragraph indents", async () => {
      const snapshot = await page.evaluate(async dashboard => {
        const { renderDashboard } = await import("/js/render.js");
        renderDashboard({ ...dashboard, narrative: "## 영상실의 고요한 침묵 요코하마의 영상실에서 푸른 빛이 흘렀다. 집중은 이어졌다. ## 코칭스태프의 시선 문가에 기대어 화면을 보던 코치들은 말을 아꼈다. 미세한 오차를 살폈다." });
        const headings = [...document.querySelectorAll("#narrativeContent h2")];
        const paragraphs = [...document.querySelectorAll("#narrativeContent p")];
        const gap = headings.length && paragraphs.length
          ? paragraphs[0].getBoundingClientRect().top - headings[0].getBoundingClientRect().bottom
          : -1;
        return {
          headings: headings.map(node => node.textContent),
          paragraphs: paragraphs.map(node => node.textContent),
          separated: headings.length === 2 && paragraphs.length === 2 && headings[0].nextElementSibling === paragraphs[0] && gap >= 0,
          gap,
          indented: paragraphs.every(node => parseFloat(getComputedStyle(node).textIndent) > 0),
          marked: headings.every(node => parseFloat(getComputedStyle(node).borderLeftWidth) > 0),
        };
      }, dashboard);
      assert.deepEqual(snapshot.headings, ["영상실의 고요한 침묵", "코칭스태프의 시선"]);
      assert.ok(snapshot.paragraphs[0].startsWith("요코하마의 영상실"), JSON.stringify(snapshot));
      assert.ok(snapshot.paragraphs[1].startsWith("문가에 기대어"), JSON.stringify(snapshot));
      assert.ok(snapshot.separated && snapshot.indented && snapshot.marked, JSON.stringify(snapshot));
      return snapshot;
    });
    await check("archived text compatibility and escaping", async () => {
      const snapshot = await page.evaluate(async () => {
        const { renderArchivedFeed, renderHistoryCapsule } = await import("/js/render.js");
        const feed = { media: [{ title: "원문", body: '첫 문단\n\n<img src=x onerror="window.injected=true">두 번째 문단' }],
          boards: [{ title: "원문 댓글", comments: [{ author: "독자", text: "<script>window.injected=true</script>", up: 0 }] }] };
        const before = JSON.stringify(feed);
        renderArchivedFeed({ feed });
        renderHistoryCapsule({ combined_feed: feed });
        const bodies = [document.getElementById("archiveDialogBody"), document.getElementById("historyDialogBody")];
        const escaped = bodies.every(body => body.querySelectorAll(".article-card p").length === 2 && !body.querySelector("img,script") && body.textContent.includes("<img src=x"));
        renderHistoryCapsule({ combined_feed: { media: [{ body: ["한 개 기사"] }] } });
        const noFalseEmpty = !document.querySelector("#historyDialogBody .archive-content").textContent.includes("그날 보관된 반응 없음");
        return { escaped, unchanged: JSON.stringify(feed) === before, noFalseEmpty, notExecuted: !window.injected };
      });
      assert.ok(Object.values(snapshot).every(Boolean), JSON.stringify(snapshot));
      return snapshot;
    });
    await check("independent proposal buttons and legacy compatibility", async () => {
      const snapshot = await page.evaluate(async dashboard => {
        const { renderDashboard } = await import("/js/render.js");
        const turn = { turn_id: "proposal-test", sequence: 1, game_date: "2027-07-27", mode: "사건 제안", reply_text: "서로 다른 두 선택", user_text: "감독에게 섭섭하지만 팀은 감싸고 싶어", proposed_events: [
          { proposal_id: "first", label: "첫 선택", type: "SP.CLUB.COACH.VENT" },
          { proposal_id: "second", label: "두 번째 선택", type: "SP.MEDIA.DEFEND" },
        ] };
        const state = { ...dashboard, story: { ...dashboard.story, conversation: [turn] } };
        renderDashboard(state);
        const buttons = () => [...document.querySelectorAll("#conversationList .proposal-row > *")].map(node =>
          node.matches("button") ? `${node.dataset.confirmProposal}:${node.disabled ? "disabled" : "ready"}` : node.classList.contains("is-committed") ? "recorded" : "unknown");
        const initial = buttons();
        turn.committed_proposals = { first: "first-event" };
        turn.committed_event_id = "first-event";
        turn.committed_proposal_id = "first";
        renderDashboard(state);
        const afterFirst = buttons();
        turn.committed_proposals.second = "second-event";
        renderDashboard(state);
        const afterBoth = buttons();
        delete turn.committed_proposals;
        renderDashboard(state);
        const legacy = buttons();
        return { initial, afterFirst, afterBoth, legacy };
      }, dashboard);
      assert.deepEqual(snapshot, { initial: ["first:ready", "second:ready"], afterFirst: ["recorded", "second:ready"], afterBoth: ["recorded", "recorded"], legacy: ["recorded", "second:ready"] });
      return snapshot;
    });
    if (option("editorial-samples")) {
      const samples = JSON.parse(await fs.readFile(path.resolve(option("editorial-samples")), "utf8"));
      const publication = samples.find(sample => sample.case === "quiet-established-star").feed;
      for (const width of [1280, 390]) {
        await page.setViewportSize({ width, height: 844 });
        await page.evaluate(async state => {
          const { renderDashboard } = await import("/js/render.js");
          renderDashboard(state);
        }, { ...dashboard, player: publication.player, feed: publication });
        await page.locator('[data-view="media"]').click();
        await check(`${width} editorial paragraphs and translations`, async () => {
          const cards = page.locator("#mediaList .article-card");
          assert.equal(await cards.count(), publication.media.length);
          for (let i = 0; i < publication.media.length; i++) {
            const paragraphs = cards.nth(i).locator("p");
            assert.deepEqual(await paragraphs.allTextContents(), publication.media[i].body);
            assert.ok(await paragraphs.evaluateAll(nodes => nodes.slice(1).every((node, index) =>
              node.getBoundingClientRect().top > nodes[index].getBoundingClientRect().bottom)), "Every original/translation paragraph must retain its gap");
          }
          await cards.last().locator("p").last().scrollIntoViewIfNeeded();
          assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
          if (outputDir) {
            await cards.first().scrollIntoViewIfNeeded();
            await page.screenshot({ path: path.join(outputDir, `editorial-${width}.png`) });
          }
          return { articles: publication.media.length, paragraphs: publication.media.reduce((n, a) => n + a.body.length, 0) };
        });
        for (const theme of ["dark", "light"]) {
          await check(`${width} ${theme} readable type and contrast`, async () => {
            await page.evaluate(theme => document.documentElement.setAttribute("data-theme", theme), theme);
            const metrics = await page.locator("#mediaList .article-card").first().evaluate(node => {
              const body = getComputedStyle(node.querySelector("p"));
              const caption = getComputedStyle(node.querySelector(".article-card__source"));
              const surface = getComputedStyle(node);
              const luminance = color => {
                const rgb = color.match(/[\d.]+/g).slice(0, 3).map(n => Number(n) / 255).map(n => n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4);
                return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
              };
              const contrast = color => {
                const a = luminance(color), b = luminance(surface.backgroundColor);
                return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
              };
              return { font: parseFloat(body.fontSize), line: parseFloat(body.lineHeight), caption: parseFloat(caption.fontSize), contrast: contrast(body.color), captionContrast: contrast(caption.color) };
            });
            assert.ok(metrics.font >= 15 && metrics.line / metrics.font >= 1.7 && metrics.caption >= 11, JSON.stringify(metrics));
            assert.ok(metrics.contrast >= 4.5 && metrics.captionContrast >= 4.5, JSON.stringify(metrics));
            if (outputDir) {
              await page.locator("#mediaList .article-card").first().scrollIntoViewIfNeeded();
              await page.screenshot({ path: path.join(outputDir, `reading-${theme}-${width}.png`) });
            }
            return metrics;
          });
        }
        await page.locator('[data-view="community"]').click();
        await check(`${width} editorial opener and replies remain readable`, async () => {
          const boards = page.locator("#communityList .board");
          assert.equal(await boards.count(), publication.boards.length);
          const knownSkins = await boards.evaluateAll(nodes => nodes.map(node => node.dataset.communitySkin).filter(value => ["dc", "fmk", "mlb"].includes(value)));
          assert.deepEqual([...new Set(knownSkins)].sort(), ["dc", "fmk", "mlb"]);
          const knownBorders = await Promise.all(["dc", "fmk", "mlb"].map(code =>
            page.locator(`[data-community-skin="${code}"]`).first().evaluate(node => getComputedStyle(node).borderTopColor)));
          assert.equal(new Set(knownBorders).size, 3, "Known communities must not be label swaps of one card skin");
          for (let i = 0; i < publication.boards.length; i++) {
            const comments = boards.nth(i).locator(".comment");
            assert.equal(await comments.count(), publication.boards[i].comments.length);
            assert.ok((await comments.first().innerText()).includes(publication.boards[i].posts[0].text));
          }
          await boards.last().locator(".comment").last().scrollIntoViewIfNeeded();
          assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
          if (outputDir) {
            await page.locator('[data-community-skin="dc"]').first().scrollIntoViewIfNeeded();
            await page.screenshot({ path: path.join(outputDir, `community-${width}.png`) });
            if (width === 1280) {
              const previousTheme = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
              await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
              await page.screenshot({ path: path.join(outputDir, "community-dark-1280.png") });
              await page.evaluate(theme => theme === null
                ? document.documentElement.removeAttribute("data-theme")
                : document.documentElement.setAttribute("data-theme", theme), previousTheme);
            }
          }
        });
      }
    }
    await check("read-only isolation", async () => {
      assert.deepEqual(errors, []);
      assert.ok(requests.every(request => request.startsWith("GET ") || request === "POST /api/v1/providers/gemini/auto-activate"),
        "Museum reads may perform the metadata-only provider check, but no world mutation or generation request is permitted");
    });
  } finally { await browser.close(); }
}

if (process.argv.includes("--serve-only")) {
  server.listen(0, "127.0.0.1", () => console.log(`SYNTHETIC_UI_ORIGIN=http://127.0.0.1:${server.address().port}`));
} else main().catch(error => results.push({ name: "runner", passed: false, error: error.stack })).finally(async () => {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  const report = { passed: results.filter(result => result.passed).length, failed: results.filter(result => !result.passed).length, results, errors, requests };
  if (outputDir) { await fs.mkdir(outputDir, { recursive: true }); await fs.writeFile(path.join(outputDir, "results.json"), `${JSON.stringify(report, null, 2)}\n`); }
  console.log(JSON.stringify({ passed: report.passed, failed: report.failed, failures: results.filter(result => !result.passed), outputDir }, null, 2));
  process.exitCode = report.failed ? 1 : 0;
});
