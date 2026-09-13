/* Pure renderer/parity checks: no browser, provider, user profile or app process. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {spawnSync} = require("node:child_process");
const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "ui/js/render.js"), "utf8");
const scope = {};
vm.createContext(scope);
const slice = (start, end) => source.slice(source.indexOf(`function ${start}(`), source.indexOf(`function ${end}(`));
vm.runInContext(slice("escapeHTML", "number") + slice("inlineMarkdown", "renderNarrative"), scope);
const quoted = '"동료들은 경기가 끝난 뒤에도 서로의 준비를 지켜보며 조용히 다음 날의 계획을 이야기했다."';
const examples = [
  "선수는\u202f\u202f돌아왔다.다음\u200b날도 준비했다.  타율 0.674와 1,000탈삼진이다.",
  "https://example.invalid/문장.다음 `값.다음  문장` 일반 문장이다.다음이다.",
  Array(6).fill(quoted).join(" "),
  "라커룸에서는누군가가남긴농담이경기후에도계속이어졌고선수들은각자조용히웃으며다음날을준비했다.".repeat(6),
  "## 영상실의 침묵\n\n첫 문단이다.\n\n둘째 문단이다.",
  "**1. 첫 장면.** 본문 하나. **2. 두 번째 장면.** 본문 둘.",
  "## 기록의 다음 장\n\n500탈삼진과 25승까지 각각 1개씩 남았다.",
  "ㅇㅇ (192.0)\n지랄ㅋㅋ 다음 경기는 또 어찌 되려나.\n\n다른 팬\n준비나 잘해라.",
  "## T+30분, 게시판\n\n성적표에는 0.674와 1,000이 적혀 있다.",
];
const python = spawnSync("python", ["-X", "utf8", "-c",
  "import json,sys; from prose_format import normalize_generated_markdown as f; print(json.dumps([f(x) for x in json.load(sys.stdin)],ensure_ascii=False))"],
  {cwd: root, input: JSON.stringify(examples), encoding: "utf8", windowsHide: true});
assert.equal(python.status, 0, python.stderr);
const expected = JSON.parse(python.stdout);
let checks = 0;
examples.forEach((value, index) => {
  const normalized = scope.normalizeReadableMarkdown(value);
  assert.equal(normalized, expected[index], `Python/JS parity ${index}`); checks++;
  assert.equal(scope.normalizeReadableMarkdown(normalized), normalized, `Stable reflow ${index}`); checks++;
});
const html = scope.safeMarkdown(examples[5]);
assert.equal((html.match(/<h2>/g) || []).length, 2); checks++;
assert.equal((html.match(/<p>/g) || []).length, 2); checks++;
assert.ok(!scope.safeMarkdown('<script>alert("fixture")</script>').includes("<script>")); checks++;
assert.ok(scope.safeMarkdown(examples[7]).includes("지랄ㅋㅋ")); checks++;
assert.ok(scope.safeMarkdown(examples[0]).includes("0.674")); checks++;
console.log(JSON.stringify({ok: true, checks, limits: "Pure real-renderer functions; no native layout or live inference."}));
