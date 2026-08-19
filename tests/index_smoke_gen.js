// index.html 前端逻辑冒烟测试（Node + DOM stub，无浏览器）
const fs = require('fs');

const html = fs.readFileSync('D:/workspace/deepseekharness_space/langgraph_demo/index.html', 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('未找到 script'); process.exit(1); }

// 最小 DOM stub（仅支撑 escapeHtml / formatRouteContent）
const domStub = `
global.document = {
  createElement(tag) {
    const el = {
      tagName: tag, className: '', _text: '', _html: '',
      children: [], style: {},
      set textContent(v) { this._text = String(v); },
      get textContent() { return this._text; },
      set innerHTML(v) { this._html = String(v); },
      get innerHTML() {
        // 模拟浏览器行为：textContent → innerHTML 时做 HTML 实体转义
        if (this._html !== '') return this._html;
        return this._text
          .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
      },
      appendChild(c) { this.children.push(c); return c; },
      querySelector(sel) {
        const cls = sel.indexOf('.') === 0 ? sel.slice(1) : sel;
        const find = (node) => {
          if (node.className === cls) return node;
          for (const c of node.children || []) { const r = find(c); if (r) return r; }
          return null;
        };
        return find(this);
      },
      get outerHTML() {
        const inner = this.children.length
          ? this.children.map(c => c.outerHTML).join('')
          : this.innerHTML;
        return '<' + this.tagName + ' class="' + this.className + '">' + inner + '</' + this.tagName + '>';
      }
    };
    return el;
  },
  getElementById() {
    // 顶层仅做赋值/监听，返回通用 dummy
    return { style: {}, classList: { add() {}, remove() {} }, addEventListener() {}, value: '', textContent: '', innerHTML: '' };
  },
  getElementsByTagName() { return []; },
  addEventListener() {}
};
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.window = { location: { pathname: '/' } };
`;

const testCode = `
let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log('  ✅ ' + name); }
  else { fail++; console.log('  ❌ ' + name); }
}

// 1. escapeHtml 防注入
const xss = '<img src=x onerror=alert(1)>';
const esc = escapeHtml(xss);
check('escapeHtml 转义 <img>', !esc.includes('<img') && esc.includes('&lt;img'));
check('转义后无任何未转义标签字符', !/<[^&]/.test(esc));

// 2. 非路线文本：返回转义文本，不产生结构化容器
const plain = '这是一段普通聊天内容，包含 <b>HTML</b>';
const r1 = formatRouteContent(plain);
check('非路线文本被转义', !r1.includes('<b>') && r1.includes('&lt;b&gt;'));
check('非路线文本无 route-info-container', !r1.includes('route-info-container'));

// 3. 后端 ROUTE_SYSTEM_PROMPT 格式的路线文本 → 结构化容器
const routeText = [
  '🚄 第1段：市内交通 → 出发站',
  '从 北京 到 北京南站',
  '距离: 15.2km, 用时: 25分钟',
  '🚄 第2段：火车',
  '车次: G1234, 时间: 08:00→12:00, 二等座: ¥500',
  '🚄 第3段：市内交通 → 目的地',
  '从 上海虹桥站 到 上海市中心',
  '距离: 10.0km, 用时: 20分钟',
  '📊 总统计',
  '总距离: 500km, 总耗时: 280分钟, 总费用: ¥550'
].join('\\n');
const r2 = formatRouteContent(routeText);
check('路线文本触发结构化容器', r2.includes('route-info-container'));
check('路线包含统计块', r2.includes('route-stats'));
check('路线包含火车块', r2.includes('train-info'));
check('路线包含总览标题', r2.includes('route-header'));

// 4. 路线内容里的 XSS 也被净化
const routeXss = '🚄 第1段：市内交通\\n距离: 15km<img src=x onerror=alert(1)>\\n总距离: 15km';
const r3 = formatRouteContent(routeXss);
check('路线内 XSS 被净化', !r3.includes('<img'));

console.log('\\n结果: ' + pass + ' 通过, ' + fail + ' 失败');
process.exit(fail ? 1 : 0);
`;

const full = domStub + '\n' + m[1] + '\n' + testCode;
fs.writeFileSync(process.env.TEMP + '/index_smoke.js', full, 'utf8');
