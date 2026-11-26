// cdp_notice.js —— 打开注册证公示页，抓：完整 API JSON、页面可见文本、XHR 请求列表、整页截图
// 用法: node cdp_notice.js <regNum> <barCode>
const BROWSER = process.env.CDP || 'http://127.0.0.1:9333';
const fs = require('fs');
const UA = 'Mozilla/5.0 (Linux; Android 14; 23013RK75C) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36';

async function main() {
  const regno = process.argv[2];
  const barCode = process.argv[3];
  if (!regno || !barCode) { console.error('用法: node cdp_notice.js <regNum> <barCode>'); process.exit(1); }

  const pageUrl = `https://pub.sbj.cnipa.gov.cn/toas-pub-prod/portalui-pub-prod/registerNoticeMobile?regNum=${regno}&&barCode=${barCode}`;

  const ver = await (await fetch(BROWSER + '/json/version')).json();
  const ws = new WebSocket(ver.webSocketDebuggerUrl);
  const pending = new Map();
  const requests = [];
  let id = 0;
  const sendRaw = (method, params = {}, sessionId) => new Promise((res, rej) => {
    const msg = { id: ++id, method, params };
    if (sessionId) msg.sessionId = sessionId;
    pending.set(msg.id, { res, rej });
    ws.send(JSON.stringify(msg));
  });
  // 瑞数挑战会让 target 重新导航，这类错误统一重试
  const send = async (method, params = {}, sessionId) => {
    for (let t = 0; t < 8; t++) {
      try { return await sendRaw(method, params, sessionId); }
      catch (e) {
        if (!/navigated or closed|Cannot find context|Execution context|Target closed/i.test(e.message)) throw e;
        await new Promise((r) => setTimeout(r, 3000));
      }
    }
    throw new Error(method + ' failed after retries');
  };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === 'Network.requestWillBeSent' && m.params && m.params.request) {
      const u = m.params.request.url;
      if (u.includes('pubnotice') || u.includes('pubInfo') || u.includes('renew') || u.includes('extension')) {
        requests.push({ url: u, method: m.params.request.method, postData: m.params.request.postData || null });
      }
    }
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
  await send('Network.enable', {}, sessionId);
  await send('Emulation.setUserAgentOverride', { userAgent: UA, platform: 'Android' }, sessionId);
  await send('Emulation.setDeviceMetricsOverride', { width: 412, height: 915, deviceScaleFactor: 2.6, mobile: true }, sessionId);

  const ev = async (expr, awaitPromise = false) => {
    for (let t = 0; t < 6; t++) {
      try {
        return await send('Runtime.evaluate', { expression: expr, awaitPromise, returnByValue: true }, sessionId);
      } catch (e) {
        if (!/navigated or closed|Cannot find context|Execution context/i.test(e.message)) throw e;
        await new Promise((r) => setTimeout(r, 2500));
      }
    }
    throw new Error('evaluate failed after retries');
  };

  let ready = false;
  for (let a = 0; a < 8 && !ready; a++) {
    await send('Page.navigate', { url: pageUrl }, sessionId).catch(() => {});
    await new Promise((r) => setTimeout(r, 6000));
    for (let i = 0; i < 12; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      const r = await send('Runtime.evaluate', {
        expression: `(()=>{const h=document.documentElement.innerHTML||'';return (h.length>400 && !h.includes('#ENCODED#'))?'Y':'N'})()`,
        returnByValue: true,
      }, sessionId);
      if (r.result.value === 'Y') { ready = true; break; }
    }
    if (!ready) console.error(`attempt ${a + 1}: 仍在挑战页，重试…`);
  }
  if (!ready) { console.error('FAILED: WAF 未通过'); process.exit(3); }

  // 等页面把数据渲染出来
  await new Promise((r) => setTimeout(r, 6000));

  const dump = await ev(`(async () => {
    const reg = ${JSON.stringify(regno)}, bc = ${JSON.stringify(barCode)};
    let api = null;
    try {
      const r = await fetch('/toas-pub-prod/pub-prod-api/pubnotice/web/pubInfo/mobile/getPubInfo/' + reg + '/' + bc, { credentials: 'include' });
      const t = await r.text();
      try { api = JSON.parse(t); } catch (e) { api = { _raw: t.slice(0, 2000) }; }
    } catch (e) { api = { _err: String(e) }; }
    const text = (document.body.innerText || '').replace(/\\n{3,}/g, '\\n\\n');
    const links = [...document.querySelectorAll('a,button,[role=tab],.tab,.van-tab')]
      .map(el => (el.innerText || '').trim()).filter(Boolean).slice(0, 60);
    return JSON.stringify({ api, text, links });
  })()`, true);

  const data = JSON.parse(dump.result.value);
  const payload = { regno, barCode, pageUrl, requests: dedupe(requests), api: data.api, pageText: data.text, clickables: data.links };
  const outJson = `assets/cert/_notice_${regno}.json`;
  fs.writeFileSync(outJson, JSON.stringify(payload, null, 1), 'utf8');
  console.log('JSON -> ' + outJson);
  console.log('接口请求数 ' + payload.requests.length + '，页面文本 ' + data.text.length + ' 字');

  // 整页截图
  try {
    const m = await send('Page.getLayoutMetrics', {}, sessionId);
    const h = Math.min(4000, Math.ceil(m.cssContentSize.height));
    await send('Emulation.setDeviceMetricsOverride', { width: 412, height: h, deviceScaleFactor: 2, mobile: true }, sessionId);
    await new Promise((r) => setTimeout(r, 1500));
    const shot = await send('Page.captureScreenshot', { format: 'png' }, sessionId);
    fs.writeFileSync(`assets/cert/_notice_${regno}.png`, Buffer.from(shot.data, 'base64'));
    console.log('PNG  -> assets/cert/_notice_' + regno + '.png  (' + h + 'px)');
  } catch (e) { console.error('截图失败: ' + e.message); }

  await send('Target.closeTarget', { targetId });
  ws.close();
}

function dedupe(list) {
  const seen = new Set();
  const out = [];
  for (const r of list) {
    const k = r.url;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(r);
  }
  return out;
}

main().catch((e) => { console.error('ERR', e.message); process.exit(1); });
