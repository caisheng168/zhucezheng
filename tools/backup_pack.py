"""打包备份：README/DEPLOY + 引擎 + 配置 + 模板 + 字体 + Aurabot 模块。"""
import os
import glob
import shutil
import zipfile
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)

# 1) 清理调试产物
pats = [
    "assets/cert/_*.png", "assets/cert/_*.jpg", "assets/cert/proof_*.jpg",
    "assets/cert/ext/_*.png", "assets/cert/ext/_*.jpg",
    "cert/_*.json", "cert/*.bak",
]
removed = 0
for p in pats:
    for f in glob.glob(p):
        try:
            os.remove(f)
            removed += 1
        except Exception:
            pass

# 2) 字体与 Aurabot 模块：复制进包内暂存区
FONT_SRC = r"C:\Windows\Fonts\AdobeSongStd-Light.otf"
FONT_DST = os.path.join("assets", "fonts", "AdobeSongStd-Light.otf")
os.makedirs(os.path.dirname(FONT_DST), exist_ok=True)
if os.path.exists(FONT_SRC):
    shutil.copy2(FONT_SRC, FONT_DST)

MOD_SRC = os.path.join(os.path.expanduser("~"), ".dsh", "plugins-data", "aurabot",
                       "modules", "trademark-cert", "index.mjs")
MOD_DST = os.path.join("aurabot", "trademark-cert", "index.mjs")
os.makedirs(os.path.dirname(MOD_DST), exist_ok=True)
if os.path.exists(MOD_SRC):
    shutil.copy2(MOD_SRC, MOD_DST)

# 3) 收集
include_dirs = ["tools", "cert", "assets/cert", "assets/fonts", "aurabot", "logs"]
files = ["README.md", "DEPLOY.md"]
for d in include_dirs:
    for root, dirs, names in os.walk(d):
        dirs[:] = [x for x in dirs if x not in ("__pycache__", ".aurabot-cache")]
        for n in names:
            if n.endswith((".bak", ".pyc")):
                continue
            fp = os.path.join(root, n)
            if os.path.getsize(fp) > 30 * 1024 * 1024:
                continue
            files.append(fp.replace("\\", "/"))

files = [f for f in files if os.path.exists(f)]

# 4) 打包
os.makedirs("backup", exist_ok=True)
for old in glob.glob("backup/*.zip"):
    os.remove(old)
zpath = os.path.join("backup", "商标注册证与续展证明技能_%s.zip"
                     % datetime.datetime.now().strftime("%Y%m%d"))
with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
    for fp in files:
        z.write(fp, fp)

print("清理临时文件 %d 个" % removed)
print("打包 %d 个文件 -> %s (%.1f MB)"
      % (len(files), zpath, os.path.getsize(zpath) / 1048576.0))
print("--- 关键文件 ---")
for fp in sorted(files):
    if fp in ("README.md", "DEPLOY.md") or fp.startswith(("aurabot/", "assets/fonts/")) \
       or fp.endswith(("cert_gen.py", "cert_from_regno.py", "cdp_probe.js", "cdp_pub.js",
                       "ext_template.json", "42500948.json", "SKILL.md")):
        print("  " + fp)
print("--- 顶层结构 ---")
top = sorted({f.split("/")[0] for f in files})
for t in top:
    print("  " + t + "/")
