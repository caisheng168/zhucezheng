# 商标注册证生成系统 · 技能备份

> 备份日期：2026-11-24 ｜ 已验证样本：10 个注册号
> 这套东西的作用：**给一个商标注册号，产出一张填好全部要素的注册证图**。

---

## 一分钟上手

```bash
# 1. 起一个无头 Edge（WAF 需要真实浏览器会话，纯 HTTP 直连一律 406）
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" ^
  --headless=new --remote-debugging-port=9333 ^
  --user-data-dir=<临时目录> --no-first-run --disable-gpu about:blank

# 2. 一个号，一条命令
python tools/cert_from_regno.py 60497423

# 3. 出图 assets/cert/out_60497423.jpg
```

可选参数：

```bash
--out <路径>           指定输出文件
--logo-scale 1.5       图样放大倍数（默认读配置，当前 2.0）
--json <记录.json>     跳过联网，直接用已抓好的 getPubInfo 字段
```

---

## 目录结构

```
skill_backup_20261124/
├── README.md                  ← 本文件
├── SKILL.md                   改字引擎的方法论（字号校准/擦除/字重）
├── REQUEST_TEMPLATE.md        给用户填空的需求模板
├── docs/
│   ├── 旧版-四项要素反查.md     只覆盖旧公示库的早期版本
│   └── 新版-三项要素反查.md     现行版本，补了新门户 + 枚举（必读）
├── tools/
│   ├── cert_from_regno.py     主流程：反查 → 组装 → 出图
│   ├── cert_gen.py            渲染引擎：配置驱动的模板改字
│   ├── cdp_probe.js           旧公示库取数（#fl）
│   └── cdp_pub.js             新门户枚举 barCode + 取字段
├── cert/42500948.json         模板坐标配置（10 个字段）
└── assets/cert/
    ├── template.jpg           固定模板（1280×1831）
    └── logo_cache/            图样缓存 {注册号}.jpg
```

---

## 依赖

```bash
pip install pillow numpy pdf417gen qrcode
pip install zxing-cpp        # 可选，用于解码自检
```

Node 侧只用内置 `fetch` / `WebSocket`（Node ≥ 22 自带），无需 npm 包。
需要本机装有 Edge 或 Chrome。

---

## 架构与数据流

```
注册号
  │
  ├─[1] 旧公示库 wsgs.sbj.cnipa.gov.cn:9443 ─→ input#fl 的 JSON
  │        命中且带 fileBarcode → 直接用
  │        空（新证常态）↓
  │
  ├─[2] 新门户 pub.sbj.cnipa.gov.cn 同源接口
  │        枚举 barCode：版式码{1021,1023,1011} × 版本01-02 × 序号01-30
  │        并发 10 个，命中即停
  │        → getPubInfo 返回完整字段
  │
  ├─[3] 小字日期：按注册年份三档推算
  ├─[4] 图样：getFdfsUrl 前缀 + imageUrl → 缓存下载
  └─[5] cert_gen 按 cert/42500948.json 的坐标绘制 → out_{注册号}.jpg
```

---

## 关键知识

### 1. 两个数据源，缺一不可

| | 旧公示库 | 新门户 |
|---|---|---|
| 地址 | `wsgs.sbj.cnipa.gov.cn:9443/tmpu/puInfoView/puMobileRe.html?regNum=` | `pub.sbj.cnipa.gov.cn/.../registerNoticeMobile?regNum=` |
| 取数 | `input#fl` 的 value（服务端渲染） | 同源接口 `/toas-pub-prod/pub-prod-api/pubnotice/web/pubInfo/mobile/getPubInfo/{regNo}/{barCode}` |
| 覆盖 | 2025 年前的老证 | 新旧都有，**2026 年的新证只在这里** |
| 局限 | 新证大量缺失，返回 `total:0` 是常态 | 必须带 barCode 才能打开，要枚举 |

**最大的坑**：旧库返回 `total:0` **不等于没有这张证**。2026 年注册的编号在旧库必然查不到。碰到空结果必须走新门户枚举。

真实案例：90475554（2026-09-28 注册）、88881568（2026-06-21 注册）在旧库都是 0 条，新门户分别命中 `B1021...ZCZC0100012` 和 `B1023...ZCZC0100018`。

### 2. barCode 结构

```
B + 版式码(4) + 业务码(4) + 编号(14) + 文件类型(4) + 版本位(2) + 000 + 发文序号(2)
B   1021       TMZC        00000090475554   ZCZC         01         000   12
```

- **版式码**：1021 为主，**1023、1011 真实存在**（88881568 就是 1023）。三种都要轮。
- **业务码**：TMZC 注册证；BHFS 变更、BFZC 补发、TMZR 转让、TMBG 变更、TMXZ 续展。
- **编号**：注册证 = 6 个 0 + 8 位注册号。
- **版本位**：第几次打印，与票面小字的 `D0X` 一一对应（`ZCZC02` ↔ `D02T`）。
- **发文序号**：不可推导，只能枚举。实测命中集中在版本 01 的序号 10–25。

### 3. 小字（PDF417 下方那行）日期规则

格式：`*{业务码}{注册号}D0{版本}T{YYMMDD}*`

| 注册年份 | 规则 |
|---|---|
| ≤ 2021 | 注册日 +21 天；周六→周五、周日→周一 |
| 2022–2024 | printDate 直接当票面日 |
| ≥ 2025 | printDate **−1 天**（主路）；少数 −3 天（落点常在周五） |

**周一特例**：printDate 落在周一时，−1（周日）和 −3（周五）都出现过，无法硬判。按 −1 先给值，并提示用户拿实物核对。

已确认样本（2025 年后）：84730481 −1、87752123 −1、88678045 −1、89290619 −1、86281398 −1、90449078 −1、83084921 −3。即 8 例里 7 个 −1。

待核实的周一案例：90475554（printDate 2026-10-19，候选 261018 / 261016）、88881568（2026-07-13，候选 260712 / 260710）。

### 4. 图样下载

```js
// 页面 JS 原文
$.ajax({type:"POST", url:"puInfoView/puMobileRe/getFdfsUrl.html",
        success:function(data){ fdfsUrl = data; }});
$("#zczc_tb_wj img:eq(2)").attr("src", fdfsUrl + obj.tmUrl);
```

1. `POST /tmpu/puInfoView/puMobileRe/getFdfsUrl.html` → 返回前缀
   实测：`https://sbgswj.sbj.cnipa.gov.cn:9443/tmann`
2. 图样 URL = 前缀 + `tmUrl` / `imageUrl`（形如 `/group3/M00/B0/A7/xxx.jpg`）
3. 下载无需 cookie，带移动端 UA + Referer 即可：

```bash
curl -k -A "<移动端UA>" -e "https://wsgs.sbj.cnipa.gov.cn:9443/" "<前缀><路径>"
```

验证：42500948 从官网下的图样与用户手动提供的文件**逐像素一致（diff 0.00）**。

### 5. 排版引擎（cert_gen.py）

- **字号校准**：不用字体 bbox 估，改为渲染→二值化→量真实墨迹高→二分字号，与原文逐像素等高
- **小字/证号/日期**：逐字排版 + 字距求解填满原宽度，**禁止整行横向拉伸**（会把竖笔画压细，看着像没加粗）
- **折行**：注册人 / 地址 / 商品行都按「字号不变、超宽折行」处理；地址优先在「路/街/号/区/园/楼/村」后断
- **加粗**：商品行前缀（第 X 类：）单独加粗，与标题同字重；正文常规
- **图标尺寸**：面积匹配 × `scale`（默认 2.0），中心固定 (631, 657)
- **擦除**：上下干净行中位色垂直渐变 + 纸纹噪点 + 边缘羽化 + kill_dark 清暗点 + 中值滤波

---

## 实战坑位清单

1. **WAF**：两个域名都是瑞数动态防护，纯 HTTP 直连（含 curl_cffi 模拟 TLS）一律 406，返回带 `#ENCODED#` 的挑战页。只能靠真实浏览器页面内 fetch。
2. **并发**：枚举时并发 10–12 个为宜，30 个以上会 `GAC pipe read timed out`。
3. **target 导航**：瑞数挑战会让 CDP target 重新导航，`Runtime.evaluate` 会报 `Inspected target navigated or closed`，必须带重试。
4. **GBK 控制台**：Windows 控制台 GBK 编不了 `−`（U+2212）、`×` 等字符，print 会抛 `UnicodeEncodeError`，脚本里一律用 ASCII 减号。
5. **JSON 截断**：正文截断后 `json.loads` 会报 `Extra data`，用 `json.JSONDecoder().raw_decode()` 只取第一个对象。
6. **打码**：个人名义 + 部分企业，公开接口返回的 `applyCnName` / `applyCnAddr` 带星号（`王正利***…`、`上海市奉贤区***`），无法自动还原，需人工补全。
7. **endDate 可能为 null**：新证取 `extenEndDate`。
8. **goods 格式不统一**：旧库带 `第 33 类 : …` 前缀，新门户有的不带。统一处理：剥掉前缀 → 规范标点 → 重新拼 `第 {cls} 类：`。
9. **旧库 record 与新门户字段名不同**：`appPerson/appAddr/intCls/tmUrl` ↔ `applyCnName/applyCnAddr/intlCls/imageUrl`。

---

## 已验证样本（10 个）

| 注册号 | 类别 | 商标 | 注册日 | barCode | 数据源 |
|---|---|---|---|---|---|
| 42500948 | 33 | FENJIU | 2020-09-07 | B1021…0100014 | 旧库 |
| 69964742 | 33 | weifuni 威芙尼 | 2023-08-21 | B1021…0100011 | 旧库 |
| 60497423 | 42 | 用星研 | 2022-05-07 | B1021…0100017 | 旧库 |
| 74714248 | 35 | 尖饼谷 | 2024-04-14 | B1021…0100010 | 旧库 |
| 72788257 | 43 | 三洪叔 | 2024-01-14 | B1021…0100010 | 旧库 |
| 79703046 | 5 | 神草福 | 2025-01-07 | B1021…0100018 | 旧库 |
| 63262117 | — | — | 2022-09-14 | — | 旧库 |
| 90475554 | 28 | KEENKEEP | 2026-09-28 | B1021…0100012 | **新门户枚举** |
| 88881568 | 43 | 惜食盒子 | 2026-06-21 | **B1023**…0100018 | **新门户枚举** |
| 90449078 | — | — | — | B1021…0100014 | 新门户枚举 |

---

## 已知限制

- 打码字段无法自动还原
- 周一日期两可，需实物回传定案
- 枚举靠 WAF 放行，连续查多个号偶尔会卡在挑战页，需重排队重试
- 印章与「申长雨」签名沿用模板原图，未做替换

---

## 恢复方法

把 `tools/` `cert/42500948.json` `assets/cert/template.jpg` 放回工作区对应位置，脚本里的相对路径（`BASE = 上两级目录`）就能跑起来。换模板时只需改 `cert/42500948.json` 里 10 个字段的 `region` 坐标，代码不用动。
