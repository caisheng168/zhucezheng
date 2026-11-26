/** @type {import('../aurabot').AurabotModule} */
// 商标注册证：给 8 位注册号 → 反查要素 → 生成证书图片 → 发出去
// 实际生成交给工作区的 tools/cert_from_regno.py（Python + Pillow），本模块只做调度
import { spawn } from 'node:child_process'
import { join } from 'node:path'
import { existsSync, mkdirSync, unlinkSync } from 'node:fs'

const WS = 'C:\\Users\\Administrator\\Documents\\deepseek-harness\\default-workspace'

export default {
  id: 'trademark-cert',
  name: '商标注册证',
  version: '1.0.0',
  api: 1,
  description: '按 8 位商标注册号反查要素，生成商标注册证图片',
  defaults: {
    pythonPath: 'C:\\Users\\Administrator\\.dsh\\dsh-runtimes\\dsh-primary-runtime\\dependencies\\python\\python.exe',
    scriptPath: WS + '\\tools\\cert_from_regno.py',
    workdir: WS,
    autoDetect: true,
    needAt: true,
    onlyOwner: true,
    whitelist: '',
    cooldownMin: 10,
    timeoutSec: 300,
    hintText: '收到，查一下，约半分钟',
  },
  settings: [
    { key: 'autoDetect', label: '自动识别号码', type: 'switch', hint: '聊天里出现纯 8 位数字时自动生成' },
    { key: 'needAt', label: '群里需要 @', type: 'switch', hint: '关掉则群里任何 8 位数字都触发（容易误触）' },
    { key: 'onlyOwner', label: '只认主人', type: 'switch', hint: '关掉则白名单里的人也能触发' },
    { key: 'whitelist', label: '白名单 QQ / 群号', type: 'textarea', hint: '逗号或空格分隔；留空表示不限' },
    { key: 'cooldownMin', label: '缓存分钟', type: 'number', min: 0, max: 1440, step: 1, hint: '同号在这个时间内重复请求，直接复用上次的图' },
    { key: 'timeoutSec', label: '超时秒数', type: 'number', min: 30, max: 900, step: 10 },
    { key: 'hintText', label: '等待提示语', type: 'text', hint: '留空则不发提示，生成完直接给图' },
    { key: 'pythonPath', label: 'Python 路径', type: 'text' },
    { key: 'scriptPath', label: '生成脚本路径', type: 'text' },
    { key: 'workdir', label: '脚本工作目录', type: 'text' },
  ],

  setup(api) {
    const outDir = api.dataDir('out')
    try { mkdirSync(outDir, { recursive: true }) } catch { }

    /** 正在处理的号码，避免同号并发开多个 python */
    const inflight = new Set()
    /** regno -> { ts, path } */
    const cache = new Map()

    /** 调 python 脚本生成一张证；返回 { ok, path } 或 { ok:false, msg } */
    function gen(regno, xz = false) {
      return new Promise((resolve) => {
        const s = api.settings()
        const out = join(outDir, regno + (xz ? '_xz' : '') + '.jpg')
        try { if (existsSync(out)) unlinkSync(out) } catch { }

        let child
        try {
          const args = [s.scriptPath, regno, '--out', out, '--quiet']
          if (xz) args.push('--renewal')
          child = spawn(s.pythonPath, args, {
            cwd: s.workdir,
            windowsHide: true,
            env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' },
          })
        } catch (e) {
          resolve({ ok: false, msg: '启动 Python 失败：' + e.message })
          return
        }

        let so = ''
        let se = ''
        let done = false
        const finish = (v) => { if (!done) { done = true; resolve(v) } }

        const timer = setTimeout(() => {
          try { child.kill() } catch { }
          finish({ ok: false, msg: `超时（${s.timeoutSec} 秒）` })
        }, Math.max(30, Number(s.timeoutSec) || 300) * 1000)

        child.stdout.on('data', (d) => { so += d.toString('utf8') })
        child.stderr.on('data', (d) => { se += d.toString('utf8') })
        child.on('error', (e) => { clearTimeout(timer); finish({ ok: false, msg: '启动失败：' + e.message }) })
        child.on('close', (code) => {
          clearTimeout(timer)
          if (code === 0 && existsSync(out)) {
            finish({ ok: true, path: out })
            return
          }
          const m = /ERROR:\s*(.+)/.exec(se)
          const msg = (m ? m[1] : (se || so || `退出码 ${code}`)).split('\n')[0].trim().slice(0, 200)
          finish({ ok: false, msg: msg || '生成失败' })
        })
      })
    }

    /** 完整流程：去重 → 缓存 → 生成 → 发图 */
    async function handle(chatId, regno, msg, { hint = true, xz = false } = {}) {
      const s = api.settings()
      const key = xz ? regno + '#xz' : regno

      const hit = cache.get(key)
      const ttl = Math.max(0, Number(s.cooldownMin) || 0) * 60000
      if (hit && ttl > 0 && Date.now() - hit.ts < ttl && existsSync(hit.path)) {
        api.log.info(`[cache] ${regno}`)
        await api.sendImage(chatId, hit.path)
        return
      }

      if (inflight.has(key)) {
        api.log.info(`[dup] ${regno} 正在处理，跳过`)
        return
      }
      inflight.add(key)

      try {
        if (hint && s.hintText) {
          if (msg) await api.reply(msg, s.hintText)
          else await api.sendText(chatId, s.hintText)
        }

        const t0 = Date.now()
        const r = await gen(regno, xz)
        const sec = ((Date.now() - t0) / 1000).toFixed(1)

        if (!r.ok) {
          api.log.warn(`[fail] ${regno} ${sec}s ${r.msg}`)
          await api.sendText(chatId, `生成失败：${r.msg}`)
          return
        }

        cache.set(key, { ts: Date.now(), path: r.path })
        api.log.info(`[ok] ${regno} ${sec}s ${r.path}`)
        await api.sendImage(chatId, r.path)
      } catch (e) {
        api.log.error(`[err] ${regno} ${e && e.message}`)
        try { await api.sendText(chatId, '出错了：' + (e && e.message ? e.message : e)) } catch { }
      } finally {
        inflight.delete(key)
      }
    }

    // ---- 主人命令：/证书 12345678 ----
    api.command({
      name: '证书',
      aliases: ['商标证', '商标证书'],
      desc: '按商标注册号生成注册证：/证书 15615867',
      run: async (arg, msg) => {
        const regno = String(arg || '').trim()
        if (!/^\d{7,15}$/.test(regno)) return '用法：/证书 8位注册号，例如 /证书 15615867'
        // 提示语直接用命令的返回值回（不走 handle 的 hint，免得发两遍）
        handle(msg.chatId, regno, null, { hint: false }).catch((e) => api.log.error('cmd: ' + (e && e.message)))
        return api.settings().hintText || ('收到，查一下：' + regno)
      },
    })

    // ---- 主人命令：/续展 12345678（强制走续展证明，不降级到注册证）----
    api.command({
      name: '续展',
      aliases: ['商标续展', '续展证明'],
      desc: '按商标注册号生成续展证明：/续展 15615867',
      run: async (arg, msg) => {
        const regno = String(arg || '').trim()
        if (!/^\d{7,15}$/.test(regno)) return '用法：/续展 8位注册号，例如 /续展 15615867'
        handle(msg.chatId, regno, null, { hint: false, xz: true }).catch((e) => api.log.error('cmd-xz: ' + (e && e.message)))
        return '收到，查一下续展：' + regno
      },
    })

    // ---- 自动识别聊天里的号码 ----
    api.on('message', (msg) => {
      const s = api.settings()
      if (!s.autoDetect) return false
      if (msg.kind === 'group' && s.needAt && !msg.mentioned) return false
      if (s.onlyOwner && !api.isOwner(msg.userId)) return false

      const wl = String(s.whitelist || '').split(/[,，\s]+/).filter(Boolean)
      if (wl.length && !wl.includes(String(msg.userId)) && !wl.includes(String(msg.groupId || ''))) return false

      const m = /^\s*(\d{7,15})\s*$/.exec(msg.text || '')
      if (!m) return false

      handle(msg.chatId, m[1], msg).catch((e) => api.log.error('msg: ' + (e && e.message)))
      return true
    })

    // ---- 工具：让她自己判断什么时候用（只给主人）----
    api.tool(({ chat, sessionId }) => {
      if (chat.kind === 'system') return []
      return api.defineTool({
        name: 'trademark_cert',
        description:
          '根据商标注册号生成一张商标注册证图片并发给对方。当对方给出商标注册号、想查注册证信息或要证书图片时使用。',
        properties: {
          regno: { type: 'string', description: '商标注册号，通常是 8 位数字' },
        },
        required: ['regno'],
        execute: async ({ regno }, info) => {
          const sid = (info && info.sessionId) || sessionId
          if (!api.core.hub.turnSendersAllOwner(sid)) {
            return { ok: false, message: '只有主人能用这个工具' }
          }
          const r = String(regno || '').trim()
          if (!/^\d{7,15}$/.test(r)) return { ok: false, message: '注册号格式不对，应该是 8 位数字' }

          const t0 = Date.now()
          const hit = cache.get(r)
          const ttl = Math.max(0, Number(api.settings().cooldownMin) || 0) * 60000
          let path = hit && ttl > 0 && Date.now() - hit.ts < ttl && existsSync(hit.path) ? hit.path : null

          if (!path) {
            const res = await gen(r)
            if (!res.ok) return { ok: false, message: res.msg }
            path = res.path
            cache.set(r, { ts: Date.now(), path })
          }

          try {
            await api.sendImage(chat.chatId, path)
          } catch (e) {
            return { ok: false, message: '图生成好了但发送失败：' + (e && e.message) }
          }
          return { ok: true, message: `商标 ${r} 的注册证已生成并发到聊天里（${((Date.now() - t0) / 1000).toFixed(1)} 秒）` }
        },
      })
    })

    // ---- 让她知道有这本事 ----
    api.rule(() =>
      '你会按商标注册号生成商标注册证图片：对方给出号码想要证书时，用 trademark_cert 工具生成并发过去。'
    )

    api.log.info(`模块已加载，输出目录 ${outDir}`)
  },
}
