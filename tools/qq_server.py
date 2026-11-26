# -*- coding: utf-8 -*-
"""
qq_server.py —— NapCat (OneBot 11) 接入层

链路：
  QQ 里发一个注册号
    → NapCat 上报到 http://127.0.0.1:8080/qq
    → 本服务解析号码，丢进单线程队列
    → 调 cert_from_regno.py 出图
    → 通过 NapCat HTTP 接口把图片发回原会话

特点：
  · 单 worker 串行，不会同时开多个浏览器实例
  · 同号 10 分钟内命中的结果直接复用，不重复跑
  · 群聊要 @ 机器人才响应（可关）
  · 全程本机，不需要公网

用法：
  1. 配 cert/napcat.json（NapCat 的地址、token、端口）
  2. python tools/qq_server.py
  3. NapCat 里把「HTTP 客户端(上报)」指向 http://127.0.0.1:8080/qq
"""
import base64
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request

from flask import Flask, request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(BASE, "cert", "napcat.json")
PY = sys.executable
GEN = os.path.join(BASE, "tools", "cert_from_regno.py")
OUT_DIR = os.path.join(BASE, "assets", "cert")

DEFAULT_CFG = {
    "napcat_url": "http://127.0.0.1:3000",
    "token": "",
    "listen_host": "127.0.0.1",
    "listen_port": 8080,
    "whitelist": [],
    "reply_groups": True,
    "need_at": True,
    "reply_hint": True,
    "cache_minutes": 10,
}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if os.path.exists(CFG_PATH):
        try:
            cfg.update(json.load(open(CFG_PATH, encoding="utf-8")))
        except Exception as e:
            print("[warn] 配置读取失败，用默认值: %s" % e)
    else:
        os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
        json.dump(cfg, open(CFG_PATH, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("[init] 已生成默认配置 %s，按需修改后重启" % CFG_PATH)
    return cfg


CFG = load_cfg()
app = Flask(__name__)
jobs = queue.Queue()
_done = {}
_inflight = set()          # regno -> (timestamp, path)
_busy = threading.Lock()


# ---------------- 出站 ----------------
def _post(path, payload):
    url = CFG["napcat_url"].rstrip("/") + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if CFG.get("token"):
        req.add_header("Authorization", "Bearer " + CFG["token"])
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except Exception as e:
        print("[send-fail] %s -> %s" % (path, str(e)[:120]))
        return {}


def send_text(ev, text):
    if ev["kind"] == "group":
        _post("/send_group_msg", {"group_id": ev["group_id"],
                                  "message": [{"type": "text", "data": {"text": text}}]})
    else:
        _post("/send_private_msg", {"user_id": ev["user_id"],
                                    "message": [{"type": "text", "data": {"text": text}}]})


def send_image(ev, path):
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    seg = [{"type": "image", "data": {"file": "base64://" + b64}}]
    if ev["kind"] == "group":
        _post("/send_group_msg", {"group_id": ev["group_id"], "message": seg})
    else:
        _post("/send_private_msg", {"user_id": ev["user_id"], "message": seg})


# ---------------- 业务 ----------------
def gen(regno):
    """返回图片路径；失败返回 (None, 原因)"""
    out = os.path.join(OUT_DIR, "out_%s.jpg" % regno)
    t0 = time.time()
    if os.path.exists(out):
        try:
            os.remove(out)          # 先删旧图，否则失败时会误判成功
        except Exception:
            pass
    p = subprocess.run([PY, GEN, regno, "--out", out],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300, cwd=BASE)
    log = (p.stdout or "") + (p.stderr or "")
    if os.path.exists(out) and os.path.getsize(out) > 20000 and os.path.getmtime(out) >= t0 - 2:
        print("[gen-ok] %s %.1fs %s" % (regno, time.time() - t0, out), flush=True)
        return out, log
    print("[gen-fail] %s\n%s" % (regno, log[-600:]), flush=True)
    return None, log


def worker():
    while True:
        ev = jobs.get()
        try:
            regno = ev["regno"]
            hit = _done.get(regno)
            if hit and time.time() - hit[0] < CFG["cache_minutes"] * 60 and os.path.exists(hit[1]):
                send_image(ev, hit[1])
                continue          # task_done 交给 finally，别再调一次
            path, log = gen(regno)
            if path:
                _done[regno] = (time.time(), path)
                send_image(ev, path)
            else:
                if "枚举" in log or "无命中" in log:
                    send_text(ev, "%s 在旧库和新门户都没查到记录，核对下号码" % regno)
                else:
                    send_text(ev, "%s 处理失败，稍后再试或看服务端日志" % regno)
        except Exception as e:
            print("[worker-err] %s" % e, flush=True)
            try:
                send_text(ev, "出错了：%s" % str(e)[:80])
            except Exception:
                pass
        finally:
            try:
                _inflight.discard(ev["regno"])
            except Exception:
                pass
            jobs.task_done()


# ---------------- 入站 ----------------
def parse(ev):
    if ev.get("post_type") not in ("message", "message_sent"):
        return None
    mtype = ev.get("message_type")
    segs = ev.get("message") or []
    if isinstance(segs, str):
        segs = [{"type": "text", "data": {"text": segs}}]

    text, at_me = "", False
    for s in segs:
        if s.get("type") == "text":
            text += s.get("data", {}).get("text", "")
        elif s.get("type") == "at":
            if str(s.get("data", {}).get("qq")) == str(ev.get("self_id")):
                at_me = True

    if mtype == "group":
        if not CFG.get("reply_groups", True):
            return None
        if CFG.get("need_at", True) and not at_me:
            return None
        kind, gid = "group", ev.get("group_id")
    elif mtype == "private":
        kind, gid = "private", None
    else:
        return None

    uid = ev.get("user_id")
    wl = CFG.get("whitelist") or []
    if wl and str(uid) not in [str(x) for x in wl]:
        print("[skip] %s 不在白名单" % uid)
        return None

    regno = re.sub(r"\D", "", text or "")
    if not re.fullmatch(r"\d{7,15}", regno):
        if regno:
            send_text({"kind": kind, "user_id": uid, "group_id": gid}, "发 8 位注册号就行")
        return None
    return {"kind": kind, "user_id": uid, "group_id": gid, "regno": regno}


@app.route("/qq", methods=["POST"])
def qq():
    try:
        ev = request.get_json(force=True, silent=True) or {}
    except Exception:
        return "bad"
    job = parse(ev)
    if not job:
        print("[skip] post_type=%s message_type=%s user=%s" % (
            ev.get("post_type"), ev.get("message_type"), ev.get("user_id")), flush=True)
    if job and job["regno"] in _inflight:
        print("[dup] %s 正在处理，跳过" % job["regno"], flush=True)
        return "ok"
    if job:
        _inflight.add(job["regno"])
        print("[in] %s %s -> %s" % (job["kind"], job["user_id"], job["regno"]), flush=True)
        if CFG.get("reply_hint", True):
            send_text(job, "收到，查一下，约半分钟")
        jobs.put(job)
    return "ok"


@app.route("/health")
def health():
    return {"ok": True, "queued": jobs.qsize(), "cached": len(_done)}


def main():
    threading.Thread(target=worker, daemon=True).start()
    print("QQ 接入已启动 → http://%s:%s/qq" % (CFG["listen_host"], CFG["listen_port"]))
    print("NapCat 的「HTTP 客户端(上报)」填上面这个地址")
    app.run(CFG["listen_host"], int(CFG["listen_port"]), threaded=True)


if __name__ == "__main__":
    main()
