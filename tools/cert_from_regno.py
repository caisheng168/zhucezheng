# -*- coding: utf-8 -*-
"""
cert_from_regno.py —— 给注册号，自动反查要素并填入固定模板（v2，接新门户）

数据源（按技能文档 v2）
  旧库 : wsgs.sbj.cnipa.gov.cn:9443 的 puMobileRe 页面（#fl）
  新门户: pub.sbj.cnipa.gov.cn 的同源接口 getPubInfo/{regNo}/{barCode}
          —— 2026 年前后的新证旧库没有，必须靠枚举 barCode 抓

流程
  1. 旧库查一次，拿到 fileBarcode 就送新门户验证一次取最新字段
  2. 旧库空/字段不全 → 枚举 barCode（版式码 1021/1023/1011 × 版本 × 序号）
  3. 小字日期按三档规则推算（2022-2024 用 printDate；≤2021 用注册日+21；≥2025 用 printDate-1）
  4. 图样 imageUrl 从 getFdfsUrl 前缀拼出来下载
  5. 套 cert/42500948.json 的坐标出图
"""
import argparse
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import datetime as dt
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request

QUIET = False


def _say(*a, **kw):
    """静默模式下不打印过程日志；--quiet 时仅在结尾输出图片路径。"""
    if not QUIET:
        print(*a, **kw)


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENLOG = os.path.join(BASE, "logs", "gen.log")


def _genlog(line):
    """每次生成追加一行流水，成功失败都记。"""
    try:
        os.makedirs(os.path.dirname(GENLOG), exist_ok=True)
        with open(GENLOG, "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
    except Exception:
        pass
NODE = r"C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
CONFIG = os.path.join(BASE, "cert", "42500948.json")
OUT = os.path.join(BASE, "assets", "cert", "out_v3.jpg")
LOGO_CACHE = os.path.join(BASE, "cert", "logo_cache")

FDFS_PREFIX = "https://sbgswj.sbj.cnipa.gov.cn:9443/tmann"

# 签发局长「申长雨」任期：只有任期内的注册日期才出图
# 续展证明版式适用期（按出证日 printDate 判断）
EXT_START = dt.date(2019, 4, 1)
EXT_END = dt.date(2026, 11, 1)
EXT_TEMPLATE = os.path.join(BASE, "cert", "ext_template.json")

TENURE_START = dt.date(2018, 5, 1)
TENURE_END = dt.date(2026, 10, 31)
QR_HOST_NEW = "https://pub.sbj.cnipa.gov.cn/toas-pub-prod/portalui-pub-prod/registerNoticeMobile"
UA_MOBILE = ("Mozilla/5.0 (Linux; Android 14; 23013RK75C) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36")


def _node(script, *args, timeout=300):
    p = subprocess.run([NODE, script] + [str(a) for a in args],
                       capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    return (p.stdout or "").strip(), (p.stderr or "").strip()


# ---------------- 旧库 ----------------
EDGE_EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EDGE_PROF = os.path.join(os.path.expanduser("~"), "AppData", "Local", "dsh-cert", "edgeprof")


def _edge_alive(timeout=2):
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:9333/json/version", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_edge(quiet=False):
    """CDP 9333 不通时自动拉起 headless Edge，最多等 20 秒。"""
    if _edge_alive():
        return True
    if not os.path.exists(EDGE_EXE):
        raise RuntimeError("找不到 msedge.exe：%s" % EDGE_EXE)
    os.makedirs(EDGE_PROF, exist_ok=True)
    import subprocess as _sp
    flags = 0
    if os.name == "nt":
        flags = getattr(_sp, "CREATE_NO_WINDOW", 0) | getattr(_sp, "DETACHED_PROCESS", 0)
    try:
        _sp.Popen(
            [EDGE_EXE, "--headless=new", "--remote-debugging-port=9333",
             "--user-data-dir=%s" % EDGE_PROF, "--no-first-run", "--disable-gpu",
             "--disable-blink-features=AutomationControlled", "about:blank"],
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            creationflags=flags, close_fds=True)
    except Exception as e:
        raise RuntimeError("启动 Edge 失败：%s" % e)
    import time as _t
    for _ in range(40):
        _t.sleep(0.5)
        if _edge_alive(1):
            if not quiet:
                _say("      已自动拉起 Edge（CDP 9333）")
            return True
    raise RuntimeError("Edge 已启动但 9333 一直没就绪，可能是权限或杀软拦截")


def fetch_old(regno):
    url = "https://wsgs.sbj.cnipa.gov.cn:9443/tmpu/puInfoView/puMobileRe.html?regNum=%s" % regno
    ensure_edge(QUIET)
    out, err = _node(os.path.join(BASE, "tools", "cdp_probe.js"), url, 10000)
    if not out:
        if "ERR" in err or "fetch failed" in err or "ECONN" in err:
            raise RuntimeError("浏览器调试端口连不上（Edge 没起？）: %s" % err[:120])
        return []
    try:
        obj = json.loads(out)
        payload = json.loads(obj.get("flValue") or "{}")
        return payload.get("rows") or []
    except Exception:
        return []


def pick_old(rows):
    def score(r):
        s = 0
        if r.get("flowType") == "TMZC":
            s += 10
        if r.get("fileType") == "ZCZC":
            s += 10
        if r.get("fileType") in ("BGZM", "ZRHZ", "XZHZ", "BFZC", "BHFS"):
            s -= 20
        return s
    return sorted(rows, key=score, reverse=True)[0] if rows else None


# ---------------- 新门户 ----------------
def enumerate_pub(regno, max_v=2):
    """枚举 barCode；命中会写 _pub_{regno}.json"""
    cache0 = os.path.join(BASE, "assets", "cert", "_pub_%s.json" % regno)
    if os.path.exists(cache0):
        try:
            raw0 = json.load(open(cache0, encoding="utf-8"))
            h0 = raw0["hits"][0]
            d0 = h0.get("data")
            if d0 is None and h0.get("body"):
                try:
                    d0 = json.loads(h0["body"]).get("data")
                except Exception:
                    pass
            if d0:
                return h0.get("bc"), d0
        except Exception:
            pass
    ensure_edge(QUIET)
    out, err = _node(os.path.join(BASE, "tools", "cdp_pub.js"), regno, max_v)
    if not out:
        raise RuntimeError("枚举失败: %s" % err[:200])
    data, _ = json.JSONDecoder().raw_decode(out)   # 后面可能跟着 FIELDS 行
    cache = os.path.join(BASE, "assets", "cert", "_pub_%s.json" % regno)
    if not data.get("hits"):
        return None, None
    if os.path.exists(cache):
        raw = json.load(open(cache, encoding="utf-8"))
        return raw["hits"][0].get("bc"), raw["hits"][0].get("data")
    return data["hits"][0], None


def fetch_pub_one(regno, barcode):
    """已知 barCode 时单点验证（走同一个浏览器脚本，maxV 设小但仍会枚举，故直接用枚举结果）"""
    return None


# ---------------- 日期 / 文本 ----------------
def small_print_date(reg_d, print_d, pending_note=None):
    """
    三档规则（技能文档 v2 第六节）
      ≤2021 : 注册日 +21 天，周六→周五、周日→周一
      2022-2024 : printDate 直接当票面日
      ≥2025 : printDate −1 天（主路），周一特例记 pending
    """
    y = reg_d.year
    if y <= 2021:
        d = reg_d + dt.timedelta(days=21)
        wd = d.weekday()
        if wd == 5:
            d -= dt.timedelta(days=1)
        elif wd == 6:
            d += dt.timedelta(days=1)
        return d, None
    if 2022 <= y <= 2024:
        return print_d, None
    d1 = print_d - dt.timedelta(days=1)
    note = None
    if print_d.weekday() == 0:      # printDate 落在周一，−1 与 −3 都出现过
        d3 = print_d - dt.timedelta(days=3)
        note = ("printDate %s 是周一：默认 -1 给 %s（周日），另一可能是 -3 的 %s（周五）"
                % (print_d.strftime("%Y-%m-%d"), d1.strftime("%y%m%d"), d3.strftime("%y%m%d")))
    return d1, note


def normalize_goods(goods, cls):
    g = " ".join(str(goods or "").split())
    g = re.sub(r"^第\s*\d+\s*类\s*[:：]\s*", "", g)
    g = re.sub(r"\s+([；，、（）])", r"\1", g)
    g = g.strip()
    # 票面商品列表末尾固定带「（截止）」，有的数据源不返回，这里统一补
    if g and "截止" not in g:
        g = g.rstrip("；;，,、。 ") + "（截止）"
    return "第 %s 类：%s" % (cls, g)


def _parse_date(s):
    return dt.date.fromisoformat(str(s)[:10]) if s else None


# ---------------- 图样 ----------------
def _valid_image(path):
    if not os.path.exists(path) or os.path.getsize(path) < 1500:
        return False
    h = open(path, "rb").read(4)
    return h[:2] == b"\xff\xd8" or h[:4] == b"\x89PNG"


def resolve_logo(regno, image_url):
    os.makedirs(LOGO_CACHE, exist_ok=True)
    cached = os.path.join(LOGO_CACHE, "%s.jpg" % regno)
    if _valid_image(cached):
        return cached
    if not image_url:
        return None
    url = FDFS_PREFIX.rstrip("/") + image_url
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            "User-Agent": UA_MOBILE,
            "Referer": "https://wsgs.sbj.cnipa.gov.cn:9443/"})
        data = urllib.request.urlopen(req, context=ctx, timeout=30).read()
        if len(data) > 1500:
            open(cached, "wb").write(data)
            if _valid_image(cached):
                return cached
    except Exception as e:
        _say("      图样下载失败: %s" % str(e)[:80])
    return None


# ---------------- 主流程 ----------------
def _pick_xz(rows):
    for r in rows or []:
        if r.get("fileType") == "XZHZ":
            return r
    return None


def _cls_from_xz(xz, reg_rec):
    for src in (xz, reg_rec):
        raw = str((src or {}).get("intlCls") or (src or {}).get("intCls") or "").strip()
        m = re.search(r"\d+", raw)
        if m:
            return m.group(0)
    body = str((xz or {}).get("extenItems") or "")
    m = re.search(r"第\s*(\d+)\s*类", body)
    return m.group(1) if m else ""


def gen_renewal(regno, xz, reg_rec, out, quiet=False):
    """续展证明：B1021TMXZ{15位申请号}XZHZ01{序号} → 正文用注册号/类别/续展止日"""
    app_num = str(xz.get("appNum") or "").strip()
    barcode = str(xz.get("fileBarcode") or "").strip()
    if not (app_num and barcode):
        raise RuntimeError("续展记录缺少 appNum/fileBarcode，无法生成")
    print_d = _parse_date(xz.get("printDate"))
    if print_d and not (EXT_START <= print_d <= EXT_END):
        raise RuntimeError(
            "续展证明出证日 %s 不在版式适用期（%s 至 %s），不予生成"
            % (print_d.strftime("%Y-%m-%d"),
               EXT_START.strftime("%Y-%m-%d"), EXT_END.strftime("%Y-%m-%d")))
    end_d = _parse_date(xz.get("endDate") or xz.get("extenEndDate"))
    if not end_d:
        raise RuntimeError("续展记录缺少 endDate，无法生成")
    cls = _cls_from_xz(xz, reg_rec) or ""
    if not cls:
        raise RuntimeError("续展记录里找不到类别号")
    code_text = "*TMXZ%sXZHZ01*" % app_num

    d = 18 if len(str(cls)) >= 2 else 0
    cfg = json.load(open(EXT_TEMPLATE, encoding="utf-8"))
    cfg["data"].update({
        "pdf417": barcode,
        "code_text": code_text,
        "reg_no": regno,
        "cls": cls,
        "date_y": end_d.strftime("%Y"),
        "date_m": end_d.strftime("%m"),
        "date_d": end_d.strftime("%d"),
    })
    for f in cfg["fields"]:
        if f["key"] == "cls":
            f["center_box"] = [691, 736 + d]
        if f["key"] == "cls_tail":
            f["dx"] = d
    if d == 0:
        cfg["fields"] = [f for f in cfg["fields"] if f["key"] != "cls_tail"]
    cfg["output"] = out
    sys.path.insert(0, os.path.join(BASE, "tools"))
    import cert_gen
    cert_gen.build(cfg, out)
    _say("[续展] %s | 第 %s 类 | 续展至 %s | 小字 %s" % (
        barcode, cls, end_d.isoformat(), code_text))
    _say("[续展] 输出 %s" % out)
    if quiet:
        _sys.stdout.write(os.path.abspath(out) + "\n")
        _sys.stdout.flush()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("regno")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--quiet", action="store_true", help="只输出最终图片路径")
    ap.add_argument("--logo-scale", type=float, default=None)
    ap.add_argument("--json", default=None, help="直接用已抓好的 getPubInfo 字段 JSON")
    ap.add_argument("--renewal", action="store_true",
                    help="强制出续展证明；名下没有续展记录时报错，不降级到注册证")
    a = ap.parse_args()
    global QUIET
    QUIET = bool(getattr(a, "quiet", False))
    import time as _time
    _t0 = _time.time()

    if a.json:
        rec = json.load(open(a.json, encoding="utf-8"))
        barcode = rec.get("barCode")
        _say("[1/4] 直接读取 %s" % a.json)
    else:
        _say("[1/4] 旧库查询 %s …" % a.regno)
        rows = fetch_old(a.regno)
        # 名下有续展证明（XZHZ）时优先出续展证明
        _xz = _pick_xz(rows)
        if _xz:
            _say("      命中续展证明记录 fileType=XZHZ")
            _zz = next((r for r in rows if r.get("fileType") == "ZCZC"), None)
            out_xz = a.out if a.out != OUT else os.path.join(
                BASE, "assets", "cert", "out_%s_xz.jpg" % a.regno)
            gen_renewal(a.regno, _xz, _zz, out_xz, quiet=QUIET)
            _genlog("%s  %-10s ok-xz %5.1fs  %8dB  %s" % (
                dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), a.regno,
                _time.time() - _t0,
                os.path.getsize(out_xz) if os.path.exists(out_xz) else 0,
                os.path.basename(out_xz)))
            return 0
        if a.renewal:
            raise RuntimeError("该注册号名下没有续展证明记录（fileType=XZHZ 未命中），无法生成续展证明")
        rec = pick_old(rows) if rows else None
        if rec and rec.get("fileBarcode"):
            barcode = rec["fileBarcode"]
        else:
            _say("      旧库无记录（新证常态），转新门户枚举 barCode …")
            rec, barcode = None, None

        if barcode is None:
            hit, fields = enumerate_pub(a.regno)
            if not hit:
                _say("      枚举 1021/1023/1011 × 版本01-02 × 序号01-30 全部无命中")
                _say("      可按附录 A 把版本扩到 09 再试一轮")
                return 2
            barcode = hit
            rec = fields or {}

    # 分类号可能带分号/空格/中文冒号等脏字符（如 33;），只保留数字
    cls_raw = str(rec.get("intlCls") or rec.get("intCls") or "").strip()
    _m = re.search(r"\d+", cls_raw)
    cls = _m.group(0) if _m else cls_raw.strip(";； ")
    tm = rec.get("tmName", "")
    reg_d = _parse_date(rec.get("regDate"))

    # 任期校验：签发局长申长雨（2018-05-01 ~ 2026-10），期外直接报错不出图
    if reg_d and not (TENURE_START <= reg_d <= TENURE_END):
        raise RuntimeError(
            "注册日期 %s 不在签发局长申长雨任期内（%s 至 %s），不予生成"
            % (reg_d.strftime("%Y-%m-%d"),
               TENURE_START.strftime("%Y-%m-%d"),
               TENURE_END.strftime("%Y-%m")))
    end_d = _parse_date(rec.get("endDate") or rec.get("extenEndDate") or rec.get("extenEnddate"))
    print_d = _parse_date(rec.get("printDate")) or _parse_date(
        str(rec.get("certValidInfo", ""))[-10:])
    if not (reg_d and print_d):
        _say("      缺少 regDate/printDate，无法推算小字")
        return 3

    sp, note = small_print_date(reg_d, print_d)
    ver = "01"
    m = re.search(r"ZCZC(\d{2})", barcode or "")
    if m:
        ver = m.group(1)
    biz = "TMZC"
    m2 = re.search(r"B\d{4}([A-Z]{4})", barcode or "")
    if m2:
        biz = m2.group(1)
    code_text = "*%s%sD0%dT%s*" % (biz, a.regno, int(ver), sp.strftime("%y%m%d"))

    holder = rec.get("applyCnName") or rec.get("appPerson") or ""
    addr = rec.get("applyCnAddr") or rec.get("appAddr") or ""
    img = rec.get("imageUrl") or rec.get("tmUrl") or ""
    masked = "*" in holder or "*" in addr

    data = {
        "pdf417": barcode,
        "code_text": code_text,
        "cert_no": a.regno,
        "class_no": cls,
        "goods": normalize_goods(rec.get("goods"), cls),
        "holder": holder,
        "addr": addr,
        "date_from": reg_d.strftime("%Y年%m月%d日"),
        "date_to": (end_d or reg_d.replace(year=reg_d.year + 10)).strftime("%Y年%m月%d日"),
        "qr": "%s?regNum=%s&&barCode=%s" % (QR_HOST_NEW, a.regno, barcode),
        "_meta": {"tmName": tm, "intlCls": cls, "printDate": print_d.isoformat(),
                  "smallPrintDate": sp.isoformat(), "barcode": barcode, "masked": masked},
    }

    _say("[2/4] %s | 第 %s 类 | %s | 注册 %s | 小字 %s" %
          (barcode, cls, tm, reg_d.isoformat(), code_text))
    if note:
        _say("      !! " + note)

    logo = resolve_logo(a.regno, img)
    if logo:
        data["logo"] = logo
        _say("[3/4] 图样 %s" % os.path.basename(logo))
    if masked:
        _say("      !! 申请人/地址在公开数据里带星号（个人或部分企业被脱敏），需手工补全")

    cfg = json.load(open(CONFIG, encoding="utf-8"))
    cfg["data"] = data
    cfg["output"] = a.out
    if a.logo_scale:
        for f in cfg["fields"]:
            if f["key"] == "logo":
                f["scale"] = float(a.logo_scale)
    for f in cfg["fields"]:
        if f["key"] == "goods":
            f["prefix"] = "第 %s 类：" % cls
            # 数据里 goods 自带的前缀可能也带脏字符，统一重拼
            _g = str(cfg["data"].get("goods") or "")
            _g = re.sub(r"^\s*第\s*\d+\s*[;；:：]?\s*类\s*[;；:：]?\s*", "", _g)
            cfg["data"]["goods"] = "第 %s 类：%s" % (cls, _g)

    sys.path.insert(0, os.path.join(BASE, "tools"))
    import cert_gen
    cert_gen.build(cfg, a.out)
    _say("[4/4] 输出 %s" % a.out)
    _sz = os.path.getsize(a.out) if os.path.exists(a.out) else 0
    _genlog("%s  %-10s ok    %5.1fs  %8dB  %s" % (
        dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), a.regno,
        _time.time() - _t0, _sz, os.path.basename(a.out)))
    if QUIET:
        # 只吐路径，方便被插件用 subprocess 抓取
        _sys.stdout.write(os.path.abspath(a.out) + "\n")
        _sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as _e:
        # 失败时只吐一行机器可读的错误，不抛 traceback，方便插件判断
        _rg = "?"
        try:
            _rg = [x for x in _sys.argv[1:] if not x.startswith("-")][0]
        except Exception:
            pass
        _genlog("%s  %-10s fail         --  ERROR: %s" % (
            dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), _rg,
            str(_e).replace("\n", " ")[:160]))
        _sys.stderr.write("ERROR: %s\n" % str(_e).replace("\n", " ")[:300])
        _sys.stderr.flush()
        sys.exit(2)
