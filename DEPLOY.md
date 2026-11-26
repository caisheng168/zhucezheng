# 部署指南

从零到跑通，一台 Windows 机器大约 10 分钟。

---

## 一、环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Windows | 10 / 11 | 脚本用了 `CREATE_NO_WINDOW`、`AppData` 路径等 |
| Python | 3.10+ | 实测 3.12.14 |
| Node.js | **22+** | 用了内置 `fetch` 和 `WebSocket`，低于 22 会报 `WebSocket is not defined` |
| Microsoft Edge | 任意较新版本 | Windows 自带；抓门户必须真实 Chromium 内核 |

### Python 包

```bash
pip install pillow numpy pdf417gen qrcode zxing-cpp
```

| 包 | 用途 |
|---|---|
| `pillow` | 图像合成、文字渲染 |
| `numpy` | 墨迹测量、颜色统计 |
| `pdf417gen` | 生成 PDF417 条码（左上角那个） |
| `qrcode` | 生成二维码 |
| `zxing-cpp` | 解码自检（可选，但强烈建议留着） |

### 字体（**最容易漏的一步**）

注册证的数字要贴合票面印刷体，用的是 `AdobeSongStd-Light`：

```powershell
copy assets\fonts\AdobeSongStd-Light.otf C:\Windows\Fonts\
```

不装会退回系统宋体（`simsun.ttc`），字形偏宽、笔画偏粗，一眼能看出和票面不是一种字。

其余字体 Windows 自带，无需处理：

```
C:\Windows\Fonts\simsun.ttc    宋体（正文汉字）
C:\Windows\Fonts\simhei.ttf    黑体（条形码下小字）
C:\Windows\Fonts\msyh.ttc      微软雅黑（界面）
```

装完字体可以用这条命令确认：

```powershell
python -c "import os;print(os.path.exists(r'C:\Windows\Fonts\AdobeSongStd-Light.otf'))"
```

---

## 二、首次验证

### 1. 跑一个注册证

```powershell
cd <解压目录>
python tools\cert_from_regno.py 42500948
```

预期输出：

```
[1/4] 旧库查询 42500948 …
[2/4] B1021TMZC00000042500948ZCZC0100014 | 第 33 类 | FENJIU | 注册 2020-09-07 | 小字 *TMZC42500948D01T200928*
[3/4] 图样 42500948.jpg
[4/4] 输出 assets\cert\out_42500948.jpg
```

第一次跑会慢几秒——脚本会自动拉起一个无头 Edge（CDP 9333），日志里会看到 `已自动拉起 Edge（CDP 9333）`。

### 2. 跑一个续展证明

```powershell
python tools\cert_from_regno.py 15615867 --renewal
```

预期输出：

```
      命中续展证明记录 fileType=XZHZ
[续展] B1021TMXZ20250000008040XZHZ0100014 | 第 33 类 | 续展至 2035-12-20 | 小字 *TMXZ20250000008040XZHZ01*
[续展] 输出 assets\cert\out_15615867_xz.jpg
```

### 3. 自检条码

```powershell
python -c "import zxingcpp,numpy as np;from PIL import Image;im=Image.open(r'assets\cert\out_42500948.jpg').convert('L');c=im.crop((26,66,380,112)).resize((708,92));print([r.text for r in zxingcpp.read_barcodes(np.array(c))])"
```

能打印出 `B1021TMZC...` 就说明条码是对的。

---

## 三、接入 Aurabot（QQ 机器人）

### 1. 放模块

把包里的 `aurabot/trademark-cert/` 整个目录复制到：

```
C:\Users\<你的用户名>\.dsh\plugins-data\aurabot\modules\trademark-cert\
```

### 2. 检查模块里的路径

打开 `index.mjs`，找到 `settings` 部分，确认这三项指向本机实际位置：

```js
pythonPath: 'C:\\...\\python.exe',      // Python 解释器绝对路径
scriptPath: WS + '\\tools\\cert_from_regno.py',
workdir:    'C:\\...\\<解压目录>',       // 必须是工作区根目录
```

> `workdir` 尤其关键。脚本内部用相对路径找 `assets/cert/template.jpg`，`cwd` 不对会报 `[Errno 2] No such file or directory`。

### 3. 命令

| 命令 | 别名 | 行为 |
|---|---|---|
| `/证书 15615867` | 商标证、商标证书 | 自动分派 |
| `/续展 15615867` | 商标续展、续展证明 | 强制出续展证明 |
| 直接发 8 位号 | — | 同 `/证书` |

非主人要用的话，模块设置里开 `onlyOwner: false`，或用 `whitelist` 加白名单。

### 4. 重载

保存 `index.mjs` 后 Aurabot 会自动热重载，日志里出现：

```
[modules] 模块「trademark-cert」文件改了，重新加载
[trademark-cert] 模块已加载，输出目录 ...
```

---

## 四、可选：开机自启

如果不想每次手动开，注册一个登录时触发的计划任务：

```powershell
schtasks /Create /TN "DSH-Cert-AutoStart" /SC ONLOGON /RL HIGHEST ^
  /TR "powershell -WindowStyle Hidden -File \"<解压目录>\tools\autostart.ps1\""
```

`autostart.ps1` 是幂等的——已经跑着的不会重复启动。

> **注意**：这个 .ps1 必须存成 **UTF-8 with BOM**。Windows PowerShell 5.1 读无 BOM 的 UTF-8 会按 ANSI 解码，含中文的路径会变成乱码，导致 `NOT FOUND`。

---

## 五、故障排查

### `浏览器调试端口连不上（Edge 没起？）`

正常情况下脚本会自己拉起 Edge。如果仍然报这个，说明 Edge 启动失败：

```powershell
# 手动确认 Edge 能否以无头模式起来
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless=new --remote-debugging-port=9333 --user-data-dir="$env:LOCALAPPDATA\dsh-cert\edgeprof" about:blank

# 另开一个窗口验证
curl http://127.0.0.1:9333/json/version
```

能返回 JSON 就说明 Edge 没问题。返回不了，检查杀软是否拦截了 `msedge.exe` 的调试端口。

### `枚举失败: ... 仍在挑战页`

瑞数 WAF 没通过。通常是 Edge 被降级或缺失某些组件。换台机器试试，或把 Edge 升到最新。

### `WebSocket is not defined`

Node 版本太低，升到 22+。

### `[Errno 2] No such file or directory: 'assets/cert/template.jpg'`

`cwd` 不对。脚本必须从**解压目录根**执行，或给 Aurabot 配好 `workdir`。

### 生成的数字字形和票面不一样

字体没装。按第一节把 `AdobeSongStd-Light.otf` 复制到 `C:\Windows\Fonts\`。

### `注册日期 xxx 不在签发局长申长雨任期内`

模板上的签名固定是申长雨，注册日期在 2018-05-01 ~ 2026-10 之外的号脚本会主动拒绝——硬出会一眼假。要出老证得换对应年代的老版式模板。

### `申请人/地址在公开数据里带星号`

公开库对个人和小微企业做了脱敏，接口返回就是 `*******`。这类需要人工补全后再出图。

---

## 六、数据来源

| 用途 | 地址 |
|---|---|
| 旧库（注册证主数据） | `wsgs.sbj.cnipa.gov.cn:9443/tmpu/puInfoView/puMobileRe.html?regNum=<号>` |
| 新门户（2025+ 新证） | `pub.sbj.cnipa.gov.cn/.../pubInfo/mobile/getPubInfo/<号>/<条码串>` |
| 商标图样 | `sbgswj.sbj.cnipa.gov.cn:9443/tmann` + `imageUrl` |

两个库都要过瑞数 WAF，纯 HTTP 请求一律 406，必须走浏览器。

---

## 七、编码规则速查

**注册证**

```
条码串  B{版式码}TMZC{注册号左补零到12位}ZCZC01000{序号}
小字    *TMZC{注册号}D01T{出证日期YYMMDD}*
```

**续展证明**

```
条码串  B1021TMXZ{15位申请号}XZHZ01{序号}
小字    *TMXZ{15位申请号}XZHZ01*
```

续展的 15 位申请号来自接口 `appNum` 字段，**不是**注册号补零，无法从注册号推算。
