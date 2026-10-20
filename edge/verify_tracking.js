// 在 Node 中模拟浏览器环境，验证官网 tracking.html 的查询逻辑是否真的能跑通。
// 用法: node verify_tracking.js <tracking.html路径>

const fs = require('fs');
const path = require('path');

const htmlPath = process.argv[2];
const html = fs.readFileSync(htmlPath, 'utf8');

// 提取最后一个内联 script 块
const blocks = [...html.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)];
const code = blocks[blocks.length - 1][1];

// ---- 构造最小 DOM 桩 ----
const nodes = {};
function makeNode(id) {
  return {
    id, value: '', textContent: '', innerHTML: '', href: '',
    style: {},
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); }
    },
    focus() {},
    scrollIntoView() {}
  };
}
['trackingInput', 'loading', 'trackingResult', 'notFound', 'fallbackActions',
 'carrierLink', 'resultTitle', 'resultWaybill', 'resultStatus',
 'resultInfo', 'resultTimeline'].forEach(id => nodes[id] = makeNode(id));

global.document = {
  getElementById: (id) => nodes[id] || (nodes[id] = makeNode(id)),
  querySelector: () => makeNode('x')
};
// 注意：不要覆盖原生 AbortController / fetch，否则页面里的
// fetch(url, {signal}) 会因 signal 无效而直接抛错。
global.window = global;
global.setTimeout = setTimeout;
global.console = console;

// EVI18n 桩（官网真实由 js/i18n.js 提供）
global.EVI18n = { currentLang: 'zh', setLanguage(){} };

// ---- 注入并执行页面 JS ----
// 注意：页面里用 const 声明的变量在 eval 内部作用域，eval 外部无法直接访问，
// 因此在执行前把代码包进 IIFE 并把需要的符号挂到 globalThis 上。
const wrapped = `
(function(){
${code}
globalThis.__trackCargo = trackCargo;
globalThis.__setEndpoints = function(list){ TRACK_ENDPOINTS.length = 0; list.forEach(u=>TRACK_ENDPOINTS.push(u)); };
})();
`;
eval(wrapped);
const trackCargo = globalThis.__trackCargo;

// ---- 跑测试 ----
const U = process.argv[3];
if (U) {
  globalThis.__setEndpoints([U]);
  // 模拟官网同源环境：data/track-endpoints.json 可正常读到
  const realFetch = globalThis.fetch;
  globalThis.fetch = function (url, opts) {
    if (typeof url === 'string' && url.indexOf('track-endpoints.json') >= 0) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ endpoints: [U] })
      });
    }
    return realFetch(url, opts);
  };
}

(async () => {
  const tests = [
    { ref: '275612693', desc: '真实 Maersk 订舱号' },
    { ref: 'MSKU1659467', desc: '箱号（demo 样例，有完整轨迹）' },
    { ref: 'NOTEXIST12345', desc: '不存在的单号（应降级深链）' }
  ];

  for (const t of tests) {
    nodes.trackingInput.value = t.ref;
    await trackCargo();
    const ok = nodes.trackingResult.classList.contains('show');
    const nf = nodes.notFound.classList.contains('show');
    console.log('\n=== ' + t.desc + ' (' + t.ref + ') ===');
    console.log('  结果卡片显示:', ok, '| 未找到区显示:', nf);
    if (ok) {
      console.log('  标题:', nodes.resultTitle.textContent);
      console.log('  单号:', nodes.resultWaybill.textContent);
      console.log('  状态HTML:', nodes.resultStatus.innerHTML.replace(/<[^>]+>/g, '').trim());
      const infoRows = (nodes.resultInfo.innerHTML.match(/info-item/g) || []).length;
      console.log('  信息网格字段数:', infoRows);
      const tlItems = (nodes.resultTimeline.innerHTML.match(/timeline-item/g) || []).length;
      console.log('  时间线节点数:', tlItems);
    } else if (nf) {
      console.log('  兜底深链:', nodes.carrierLink.href);
      console.log('  兜底区可见:', nodes.fallbackActions.style.display);
    }
  }
})().catch(e => { console.error('TEST ERROR', e); process.exit(1); });
