// cdp_pub.js —— 新门户 getPubInfo 枚举：给定注册号，自动轮版式码/版本/序号找出 barCode
// 用法: node cdp_pub.js <regNum> [maxVersion]
const BROWSER = process.env.CDP || 'http://127.0.0.1:9333';
const fs = require('fs');
const UA = 'Mozilla/5.0 (Linux; Android 14; 23013RK75C) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36';

async function main() {
  const regno = process.argv[2];
  const maxV = parseInt(process.argv[3] || '2', 10);
  const pageUrl = `https://pub.sbj.cnipa.gov.cn/toas-pub-prod/portalui-pub-prod/registerNoticeMobile?regNum=${regno}`;

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
  await send('Emulation.setUserAgentOverride', { userAgent: UA, platform: 'Android' }, sessionId);
  await send('Emulation.setDeviceMetricsOverride', { width: 412, height: 915, deviceScaleFactor: 2.6, mobile: true }, sessionId);

  // 带重试的 evaluate（瑞数挑战会让 target 重新导航）
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

  // 过 WAF：反复导航直到页面骨架能跑 JS（有 document.body 且没有 #ENCODED# 挑战页）
  let ready = false;
  for (let a = 0; a < 6 && !ready; a++) {
    await send('Page.navigate', { url: pageUrl }, sessionId);
    for (let i = 0; i < 10; i++) {
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

  const expr = `(async () => {
    const reg = ${JSON.stringify(regno)};
    const layouts = ['1021','1023','1011'];
    const jobs = [];
    for (const L of layouts)
      for (let v = 1; v <= ${maxV}; v++)
        for (let i = 1; i <= 30; i++)
          jobs.push('B' + L + 'TMZC000000' + reg + 'ZCZC' + String(v).padStart(2,'0') + '000' + String(i).padStart(2,'0'));
    const hits = [];
    let tested = 0;
    for (let k = 0; k < jobs.length; k += 10) {
      await Promise.all(jobs.slice(k, k + 10).map(async bc => {
        try {
          const r = await fetch('/toas-pub-prod/pub-prod-api/pubnotice/web/pubInfo/mobile/getPubInfo/' + reg + '/' + bc, { credentials: 'include' });
          const t = await r.text();
          tested++;
          if (t && !t.includes('"data":null') && t.length > 40) {
            let d = null;
            try { d = JSON.parse(t).data; } catch (e) { d = null; }
            hits.push({ bc, data: d });
          }
        } catch (e) {}
      }));
    }
    return JSON.stringify({ tested, hits });
  })()`;
  const r = await ev(expr, true);
  const out = JSON.parse(r.result.value);
  console.log(JSON.stringify({ regno, tested: out.tested, hits: out.hits.map(h => h.bc) }, null, 1));
  if (out.hits.length) {
    fs.writeFileSync(`assets/cert/_pub_${regno}.json`, JSON.stringify(out, null, 1));
    console.log('FIELDS ' + JSON.stringify(out.hits[0].data));
  }
  await send('Target.closeTarget', { targetId });
  ws.close();
}
main().catch((e) => { console.error('ERR', e.message); process.exit(1); });
