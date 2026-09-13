/* Pure renderer tests: no browser, network, provider, or user data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const nodes = {};
const context = vm.createContext({ document: { getElementById: id => nodes[id] } });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../ui/js/render.js'), 'utf8').replace(/^import .*;$/gm, '').replace(/^export /gm, ''), context);
const run = source => vm.runInContext(source, context);
let passed = 0;
function check(name, fn) { fn(); passed++; console.log(`PASS ${name}`); }
context.board = { id: 'test', code: 'dc', title: '원글 제목', expression_renderer: 'gemini', comments: [
  { author: '작성자', text: '첫째 단락\n\n둘째 단락', is_opener: true },
  { author: '독자', text: '답글입니다.', is_opener: false },
] };
check('opener separated, not counted twice', () => {
  const html = run('renderBoardCard(board)');
  assert.equal((html.match(/첫째 단락/g) || []).length, 1);
  assert.ok(html.includes('board__post'));
  assert.ok(html.includes('댓글 1개'));
  assert.ok(html.includes('data-reading-key="board:test" open'));
});
check('legacy opener remains readable', () => assert.equal(run('splitBoardThread({comments:[{text:"old"},{text:"reply"}]}).opener.text'), 'old'));
check('explicit false is not promoted to opener', () => assert.equal(run('splitBoardThread({comments:[{is_opener:false,text:"reply"}]}).opener'), null));
check('explicit late opener is separated without losing any reply', () => {
  assert.equal(run('splitBoardThread({comments:[{is_opener:false,text:"reply"},{is_opener:true,text:"body"}]}).opener.text'), 'body');
  assert.equal(run('splitBoardThread({comments:[{is_opener:false},{is_opener:true}]}).replies.length'), 1);
});
check('empty thread is safe', () => assert.ok(run('renderBoardCard({comments:[]})').includes('아직 댓글이 없습니다')));
check('hostile stored strings remain escaped', () => {
  const html = run('renderBoardCard({id:"><script>",title:"<img onerror=x>",comments:[{text:"<script>x</script>"}]})');
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;script&gt;'));
});
check('writer label follows saved result, not configured provider', () => {
  assert.equal(run('expressionLabel({expression_renderer:"local_llm"})'), '로컬 LLM 작성');
  assert.equal(run('expressionLabel({expression_renderer:"mixed"})'), '혼합 작성');
  assert.equal(run('expressionLabel({})'), '');
});
check('social text, breaks and replies survive rendering', () => {
  const html = run('renderSocialCard({id:"post",code:"x",text:"첫 줄\\n다음 줄",replies:[{text:"끝 답글"}]})');
  assert.ok(html.includes('첫 줄\n다음 줄'));
  assert.ok(html.includes('끝 답글'));
  assert.ok(html.includes('답글 1개'));
});
const socialCard = { dataset: { readingSurface: 'social:x' }, textContent: '가상선수 ＭＶＰ SNS 답글' };
const boardCard = { dataset: { readingSurface: 'board:dc' }, textContent: '가상선수 작성자 원정석 ㅋㅋ' };
for (const id of ['communityFilter', 'communitySearch', 'communityReadingCount', 'communityReadingEmpty', 'socialHeading', 'forumHeading']) nodes[id] = { value: '' };
nodes.socialList = { querySelectorAll: () => [socialCard] };
nodes.communityList = { querySelectorAll: () => [boardCard] };
check('platform filtering is view-only', () => {
  nodes.communityFilter.value = 'board:dc';
  run('applyReadingFilters("community")');
  assert.equal(socialCard.hidden, true); assert.equal(boardCard.hidden, false);
  assert.equal(nodes.communityReadingCount.textContent, '1 / 2건 표시');
});
check('case and fullwidth search includes reply content', () => {
  nodes.communityFilter.value = ''; nodes.communitySearch.value = 'mvp';
  run('applyReadingFilters("community")');
  assert.equal(socialCard.hidden, false); assert.equal(boardCard.hidden, true);
});
check('no matches can be reset without losing cards', () => {
  nodes.communitySearch.value = '없는내용'; run('applyReadingFilters("community")');
  assert.equal(nodes.communityReadingEmpty.hidden, false);
  nodes.communitySearch.value = ''; run('applyReadingFilters("community")');
  assert.equal(nodes.communityReadingEmpty.hidden, true);
  assert.equal(nodes.communityReadingCount.textContent, '2 / 2건 표시');
});
check('folded state survives replacement of the same item', () => {
  const old = [{ dataset: { readingKey: 'a' }, open: false }, { dataset: { readingKey: 'b' }, open: true }];
  const fresh = [{ dataset: { readingKey: 'a' }, open: true }, { dataset: { readingKey: 'new' }, open: true }];
  let replaced = false;
  context.target = { querySelectorAll: () => replaced ? fresh : old, set innerHTML(value) { replaced = true; } };
  run('replaceReadingCards(target, "replacement")');
  assert.equal(fresh[0].open, false); assert.equal(fresh[1].open, true);
});
console.log(JSON.stringify({ passed }));
