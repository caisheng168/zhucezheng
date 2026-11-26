// cdp_probe.js —— 用本机 Edge 通过 CDP 打开商标查询页，执行完 WAF 的 JS 挑战后取 #fl 的值
// 用法: node cdp_probe.js "https://...regNum=42500948" [waitMs]
const BROWSER = process.env.CDP || 'http://127.0.0.1:9333';

async function main() {
  const url = process.argv[2];
  const waitMs = parseInt(process.argv[3] || '9000', 10);
  if (!url) { console.error('need url'); process.exit(2); }

  const ver = await (await fetch(BROWSER + '/json/version')).json();
  const ws = new WebSocket(ver.webSocketDebuggerUrl);
  const pending = new Map();
  let id = 0;

  const send = (method, params = {}, sessionId) => new Promise((res, rej) => {
    const msg = { id: ++id, method, params };
    if (sessionId) msg.sessionId = sessionId;
    pending.set(msg.id, { res, rej });
    ws.send(JSON.stringify(msg));
  });

  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };

  await new Promise((r) => { ws.onopen = r; });

  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
  await send('Page.enable', {}, sessionId);
  await send('Runtime.enable', {}, sessionId);
  // 模拟移动端视口 + 触摸
  await send('Emulation.setUserAgentOverride', {
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
    platform: 'iPhone',
    acceptLanguage: 'zh-CN,zh;q=0.9',
  }, sessionId);
  await send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 3, mobile: true,
  }, sessionId);
  await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);

  await send('Page.navigate', { url }, sessionId);
  await new Promise((r) => setTimeout(r, waitMs));

  const expr = `(() => {
    const el = document.querySelector('#fl');
    const body = document.body ? document.body.innerText.slice(0, 400) : '';
    return JSON.stringify({
      hasFl: !!el,
      flValue: el ? String(el.value).slice(0, 4000) : null,
      title: document.title,
      bodyHead: body,
      cookies: document.cookie
    });
  })()`;
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true }, sessionId);
  const out = r.result && r.result.value;
  console.log(out || JSON.stringify(r));

  await send('Target.closeTarget', { targetId });
  ws.close();
}

main().catch((e) => { console.error('ERR', e.message); process.exit(1); });
