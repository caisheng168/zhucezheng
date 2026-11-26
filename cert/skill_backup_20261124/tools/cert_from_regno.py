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
import datetime as dt
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = r"C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
CONFIG = os.path.join(BASE, "cert", "42500948.json")
OUT = os.path.join(BASE, "assets", "cert", "out_v3.jpg")
LOGO_CACHE = os.path.join(BASE, "cert", "logo_cache")

FDFS_PREFIX = "https://sbgswj.sbj.cnipa.gov.cn:9443/tmann"
QR_HOST_NEW = "https://pub.sbj.cnipa.gov.cn/toas-pub-prod/portalui-pub-prod/registerNoticeMobile"
UA_MOBILE = ("Mozilla/5.0 (Linux; Android 14; 23013RK75C) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36")


def _node(script, *args, timeout=300):
    p = subprocess.run([NODE, script] + [str(a) for a in args],
                       capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    return (p.stdout or "").strip(), (p.stderr or "").strip()


# ---------------- 旧库 ----------------
def fetch_old(regno):
    url = "https://wsgs.sbj.cnipa.gov.cn:9443/tmpu/puInfoView/puMobileRe.html?regNum=%s" % regno
    out, err = _node(os.path.join(BASE, "tools", "cdp_probe.js"), url, 10000)
    if not out:
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
        print("      图样下载失败: %s" % str(e)[:80])
    return None


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("regno")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--logo-scale", type=float, default=None)
    ap.add_argument("--json", default=None, help="直接用已抓好的 getPubInfo 字段 JSON")
    a = ap.parse_args()

    if a.json:
        rec = json.load(open(a.json, encoding="utf-8"))
        barcode = rec.get("barCode")
        print("[1/4] 直接读取 %s" % a.json)
    else:
        print("[1/4] 旧库查询 %s …" % a.regno)
        rows = fetch_old(a.regno)
        rec = pick_old(rows) if rows else None
        if rec and rec.get("fileBarcode"):
            barcode = rec["fileBarcode"]
        else:
            print("      旧库无记录（新证常态），转新门户枚举 barCode …")
            rec, barcode = None, None

        if barcode is None:
            hit, fields = enumerate_pub(a.regno)
            if not hit:
                print("      枚举 1021/1023/1011 × 版本01-02 × 序号01-30 全部无命中")
                print("      可按附录 A 把版本扩到 09 再试一轮")
                return 2
            barcode = hit
            rec = fields or {}

    cls = str(rec.get("intlCls") or rec.get("intCls") or "").strip()
    tm = rec.get("tmName", "")
    reg_d = _parse_date(rec.get("regDate"))
    end_d = _parse_date(rec.get("endDate") or rec.get("extenEndDate") or rec.get("extenEnddate"))
    print_d = _parse_date(rec.get("printDate")) or _parse_date(
        str(rec.get("certValidInfo", ""))[-10:])
    if not (reg_d and print_d):
        print("      缺少 regDate/printDate，无法推算小字")
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

    print("[2/4] %s | 第 %s 类 | %s | 注册 %s | 小字 %s" %
          (barcode, cls, tm, reg_d.isoformat(), code_text))
    if note:
        print("      !! " + note)

    logo = resolve_logo(a.regno, img)
    if logo:
        data["logo"] = logo
        print("[3/4] 图样 %s" % os.path.basename(logo))
    if masked:
        print("      !! 申请人/地址在公开数据里带星号（个人或部分企业被脱敏），需手工补全")

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

    sys.path.insert(0, os.path.join(BASE, "tools"))
    import cert_gen
    cert_gen.build(cfg, a.out)
    print("[4/4] 输出 %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
