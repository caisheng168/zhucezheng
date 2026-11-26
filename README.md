# 商标注册证 / 续展证明生成技能

给一个 8 位商标注册号，自动从官方公示库反查要素，套模板出图。

- **注册证**：1280×1831，含 PDF417、二维码、商标图样、商品项目、注册人、地址、日期
- **续展证明**：1586×2245，含 PDF417、小字编码、注册号、类别、续展止日

两套版式共用一套渲染引擎和字体，输出可被扫码枪/手机正常识别。

---

## 30 秒跑通

```bash
# 1. 装 Python 依赖
pip install pillow numpy pdf417gen qrcode zxing-cpp

# 2. 装字体（关键，缺了数字字形会变）
copy assets\fonts\AdobeSongStd-Light.otf C:\Windows\Fonts\

# 3. 跑一个号
python tools\cert_from_regno.py 42500948
```

看到 `[4/4] 输出 ...` 就成功了，图在 `assets/cert/out_42500948.jpg`。

想验证续展：

```bash
python tools\cert_from_regno.py 15615867 --renewal
```

---

## 目录结构

```
tools/
  cert_gen.py           渲染引擎（配置驱动，读 JSON 画图）
  cert_from_regno.py    ★ 主入口：查号 → 取要素 → 出图
  cdp_probe.js          旧库抓取（CDP 驱动 Edge，过瑞数 WAF）
  cdp_pub.js            新门户枚举（同上）
  qq_server.py          [可选] NapCat OneBot11 接入
  autostart.ps1         [可选] 开机自启 Edge + 服务
  backup_pack.py        打包脚本

cert/
  42500948.json         注册证版式配置（字段坐标、字体、字号）
  ext_template.json     续展证明版式配置
  SKILL.md              方法论（坐标怎么量、字重怎么标定）
  REQUEST_TEMPLATE.md   需求模板（要用户提供什么）
  logo_cache/           按注册号缓存的商标图样

assets/
  cert/template.jpg     注册证空白模板 1280×1831
  cert/ext/template.jpg 续展证明空白模板 1586×2245
  cert/ext/samples/     三张真实续展样本（核对用）
  fonts/                AdobeSongStd-Light.otf（数字字形用）

aurabot/
  trademark-cert/index.mjs   Aurabot 插件模块（/证书、/续展）
```

---

## 命令

```bash
# 自动分派（有续展记录出续展，否则出注册证）
python tools\cert_from_regno.py 15615867

# 强制续展（名下没有续展记录会报错，不降级）
python tools\cert_from_regno.py 15615867 --renewal

# 指定输出路径 + 只吐路径（给插件用）
python tools\cert_from_regno.py 15615867 --out D:\tmp\a.jpg --quiet

# 用已抓好的字段直接出图（跳过网络）
python tools\cert_from_regno.py 15615867 --json cert\某号.json
```

退出码：`0` 成功，`2` 失败（stderr 里是 `ERROR: ...`）。

---

## 详细部署

换机器、接 Aurabot、配开机自启、排查故障，看 **[DEPLOY.md](DEPLOY.md)**。

---

## 注意事项

- 生成的证只用于已核实的商标信息核对，不替代官方证书
- 公开库对个人和小微企业会脱敏（`*******`），这类字段需要人工补全，脚本会打 `!!` 提醒
- 签发局长签名固定在模板上，脚本会校验注册日期是否在申长雨任期内（2018-05-01 ~ 2026-10）
