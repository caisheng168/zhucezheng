#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
img_text_replace.py —— 模板图改字（v2）

能力：
  1) 自动定位文字行（--auto），不用手量坐标；
  2) 用扩散 inpaint（Laplacian 迭代）擦掉旧文字，保留渐变/底纹过渡；
  3) 按新文字自适应字号、居中/左/右对齐、自动继承原文字颜色、可选描边；
  4) 批量改字（--jobs 多行指令）。

单条用法:
  python img_text_replace.py --image in.png --out out.png ^
    --box 120,80,520,160 --text "秋季限时特惠" --font C:\Windows\Fonts\msyhbd.ttc

自动定位 + 改第 N 行:
  python img_text_replace.py --image in.png --out out.png --auto --line 0 --text "新标题"

批量:
  --jobs "0=秋季限时特惠|1=时间：11 月 9 日"    # 序号=新文字，配合 --auto
"""
import argparse
import json
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


# ============ 背景修复：扩散 inpaint ============
def _edge_interp_init(arr, mask, box):
    """给 mask 区域一个初始值：四方向边缘插值平均，避免迭代收敛慢。"""
    y1, x1, y2, x2 = box
    h, w = mask.shape
    out = arr.copy()
    rh, rw = y2 - y1, x2 - x1

    def avg_slice(sl):
        s = arr[sl].reshape(-1, 3)
        return s.mean(0)

    top = avg_slice((slice(max(0, y1 - 1), y1), slice(x1, x2))) if y1 > 0 else None
    bot = avg_slice((slice(y2, min(h, y2 + 1)), slice(x1, x2))) if y2 < h else None
    lef = avg_slice((slice(y1, y2), slice(max(0, x1 - 1), x1))) if x1 > 0 else None
    rig = avg_slice((slice(y1, y2), slice(x2, min(w, x2 + 1)))) if x2 < w else None
    cands = [c for c in (top, bot, lef, rig) if c is not None]
    base = np.mean(cands, 0) if cands else np.zeros(3)
    if top is not None and bot is not None:
        t = np.linspace(0, 1, rh)[:, None, None]
        base = top[None, None, :] * (1 - t) + bot[None, None, :] * t
    out[y1:y2, x1:x2] = base
    return out


def diffusion_inpaint(img: Image.Image, box, iters=300, grain=0) -> Image.Image:
    """Laplacian 扩散填充：掩码内像素迭代为四邻域均值，边界像素保持不动。"""
    x1, y1, x2, y2 = box
    W, H = img.size
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(W, x2), min(H, y2)
    if x2 - x1 < 1 or y2 - y1 < 1:
        raise ValueError("选框无效: %s" % (box,))

    arr = np.array(img.convert("RGB")).astype(np.float32)
    mask = np.zeros((H, W), bool)
    mask[y1:y2, x1:x2] = True
    arr = _edge_interp_init(arr, mask, (y1, x1, y2, x2))

    # 只在与选框相关的子区域上迭代，速度快
    py, px = 2, 2
    sy1, sx1 = max(0, y1 - py), max(0, x1 - px)
    sy2, sx2 = min(H, y2 + py), min(W, x2 + px)
    sub = arr[sy1:sy2, sx1:sx2].copy()
    m = mask[sy1:sy2, sx1:sx2]

    for _ in range(iters):
        up = np.empty_like(sub); up[1:] = sub[:-1]; up[0] = sub[0]
        dn = np.empty_like(sub); dn[:-1] = sub[1:]; dn[-1] = sub[-1]
        lf = np.empty_like(sub); lf[:, 1:] = sub[:, :-1]; lf[:, 0] = sub[:, 0]
        rt = np.empty_like(sub); rt[:, :-1] = sub[:, 1:]; rt[:, -1] = sub[:, -1]
        new = (up + dn + lf + rt) * 0.25
        sub[m] = new[m]

    if grain > 0:
        noise = np.random.default_rng(3).normal(0, grain, sub.shape).astype(np.float32)
        sub[m] += noise[m] * 0.5

    arr[sy1:sy2, sx1:sx2] = sub
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


# ============ 文字行自动定位（投影法） ============
def detect_text_lines(img: Image.Image, min_h=12, gap=6):
    """返回可疑文字行框列表 [(x1,y1,x2,y2), ...]，需配合 --line 选择。"""
    g = np.array(img.convert("L")).astype(np.float32)
    g = np.array(Image.fromarray(g.astype(np.uint8)).filter(ImageFilter.MedianFilter(3))).astype(np.float32)
    # 用 局部中值 作为背景估计，差值大的视为文字
    bg = np.array(Image.fromarray(g.astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(max(3, g.shape[1] // 60)))).astype(np.float32)
    diff = np.abs(g - bg)
    thr = max(18.0, diff.mean() + 2.0 * diff.std())
    m = diff > thr

    rows = m.sum(1)
    colthr = max(3, m.shape[1] * 0.004)
    bands, cur = [], None
    for y, v in enumerate(rows):
        if v > colthr:
            cur = [y, y] if cur is None else [cur[0], y]
        else:
            if cur is not None:
                bands.append(cur); cur = None
    if cur:
        bands.append(cur)

    lines = []
    for a, b in bands:
        if b - a + 1 < min_h:
            continue
        cols = np.where(m[a:b + 1].sum(0) > 0)[0]
        if len(cols) == 0:
            continue
        lines.append((int(cols[0]), int(a), int(cols[-1]) + 1, int(b) + 1))
    # 合并过近的相邻行
    merged = []
    for ln in lines:
        if merged and ln[1] - merged[-1][3] <= gap:
            p = merged[-1]
            merged[-1] = (min(p[0], ln[0]), p[1], max(p[2], ln[2]), ln[3])
        else:
            merged.append(ln)
    return merged


# ============ 颜色 / 字号 ============
def sample_text_color(img, box, mode="auto"):
    """mode: auto=取框内最突出色; light=最亮色; dark=最暗色"""
    x1, y1, x2, y2 = box
    region = np.array(img.convert("RGB"))[y1:y2, x1:x2].reshape(-1, 3).astype(np.float32)
    if region.size == 0:
        return (0, 0, 0)
    lum = region @ np.array([0.299, 0.587, 0.114], np.float32)
    if mode == "light":
        idx = np.argsort(lum)[::-1][: max(1, len(lum) // 25)]
    elif mode == "dark":
        idx = np.argsort(lum)[: max(1, len(lum) // 25)]
    else:
        med = np.median(lum)
        idx = np.argsort(np.abs(lum - med))[::-1][: max(1, len(lum) // 25)]
    return tuple(int(v) for v in np.median(region[idx], axis=0))


def hex2rgb(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def fit_font(path, text, max_w, max_h, size=0, spacing=1.0):
    if size and size > 0:
        return ImageFont.truetype(path, size)
    lo, hi = 6, max(8, int(max_h * 2.2))
    best = ImageFont.truetype(path, lo)
    while lo <= hi:
        mid = (lo + hi) // 2
        f = ImageFont.truetype(path, mid)
        l, t, r, b = f.getbbox(text)
        if (r - l) <= max_w and (b - t) * spacing <= max_h:
            best, lo = f, mid + 1
        else:
            hi = mid - 1
    return best


# ============ 主流程 ============
def color_from_diff(orig: Image.Image, cleaned: Image.Image, box):
    """最可靠的取色：擦除前后差异最大的像素 = 旧文字笔画，取其中位色。"""
    x1, y1, x2, y2 = box
    a = np.array(orig.convert("RGB"))[y1:y2, x1:x2].astype(np.float32)
    b = np.array(cleaned.convert("RGB"))[y1:y2, x1:x2].astype(np.float32)
    if a.size == 0:
        return (0, 0, 0)
    d = np.abs(a - b).sum(2).reshape(-1)
    k = max(1, int(len(d) * 0.04))
    idx = np.argsort(d)[::-1][:k]
    return tuple(int(v) for v in np.median(a.reshape(-1, 3)[idx], axis=0))


def texture_transfer(cleaned: Image.Image, orig: Image.Image, box, strength=0.6):
    """把框外的残差纹理镜像延拓进框内，让斜纹/颗粒/布纹等质感延续。"""
    if strength <= 0:
        return cleaned
    x1, y1, x2, y2 = box
    o = np.array(orig.convert("RGB")).astype(np.float32)
    out = np.array(cleaned.convert("RGB")).astype(np.float32)
    bl = np.array(Image.fromarray(o.astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(3))).astype(np.float32)
    detail = o - bl
    H, W = detail.shape[:2]
    bw, bh = x2 - x1, y2 - y1
    band_h = min(y1, bh)
    if band_h < 4:
        band_h = min(H - y2, bh)
        if band_h < 4:
            return cleaned
        src = detail[y2:y2 + band_h, x1:x2][::-1]
    else:
        src = detail[y1 - band_h:y1, x1:x2][::-1]
    reps = int(np.ceil(bh / band_h))
    tile = np.tile(src, (reps, 1, 1))[:bh]
    tile = np.array(Image.fromarray(
        np.clip(tile + 128, 0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(0.8))).astype(np.float32) - 128
    fade = np.linspace(1.0, 0.35, bh)[:, None, None]
    out[y1:y2, x1:x2] += tile * fade * strength
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def replace_text(img, box, text, args, color=None):
    x1, y1, x2, y2 = box
    out = diffusion_inpaint(img, box, iters=args.iters,
                            grain=args.grain)
    if args.texture > 0:
        out = texture_transfer(out, img, box, args.texture)
    if color is None:
        color = color_from_diff(img, out, box) if args.color == "auto" else hex2rgb(args.color)
    if not text:
        return out, color, 0

    pad = args.pad
    box_w, box_h = x2 - x1, y2 - y1
    SS = 4  # 超采样，字形边缘更平滑
    tile = out.crop((x1, y1, x2, y2)).resize((box_w * SS, box_h * SS), Image.LANCZOS)

    iw, ih = box_w * SS - 2 * pad * SS, box_h * SS - 2 * pad * SS
    font = fit_font(args.font, text, iw, ih, args.size * SS if args.size else 0)
    l, t, r, b = font.getbbox(text)
    tw, th = r - l, b - t
    if args.align == "left":
        tx = pad * SS - l
    elif args.align == "right":
        tx = box_w * SS - pad * SS - tw - l
    else:
        tx = box_w * SS // 2 - tw // 2 - l
    ty = box_h * SS // 2 - th // 2 - t

    d = ImageDraw.Draw(tile)
    sc = hex2rgb(args.stroke_color) if args.stroke_color else None
    d.text((tx, ty), text, font=font, fill=color,
           stroke_width=args.stroke * SS, stroke_fill=sc)
    out.paste(tile.resize((box_w, box_h), Image.LANCZOS), (x1, y1))
    return out, color, max(1, round(font.size / SS))


def parse_box(s):
    return tuple(int(float(v)) for v in s.split(","))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--box", default="")
    ap.add_argument("--auto", action="store_true", help="自动定位文字行")
    ap.add_argument("--line", type=int, default=0, help="配合 --auto 选第几行")
    ap.add_argument("--list-lines", action="store_true", help="只输出检测到的行框")
    ap.add_argument("--text", default="")
    ap.add_argument("--jobs", default="", help='批量: "0=新字A|1=新字B"')
    ap.add_argument("--font", default=r"C:\Windows\Fonts\msyh.ttc")
    ap.add_argument("--color", default="auto")
    ap.add_argument("--color-from", default="auto", choices=["auto", "light", "dark"])
    ap.add_argument("--align", default="center", choices=["left", "center", "right"])
    ap.add_argument("--size", type=int, default=0)
    ap.add_argument("--pad", type=int, default=2)
    ap.add_argument("--expand", type=int, default=2, help="选框外扩像素，抹掉抗锯齿边缘")
    ap.add_argument("--iters", type=int, default=300)
    ap.add_argument("--grain", type=float, default=2.0, help="填充后的颗粒噪声强度")
    ap.add_argument("--texture", type=float, default=0.6, help="0~1 把框外纹理延拓进框内，0 关闭")
    ap.add_argument("--stroke", type=int, default=0)
    ap.add_argument("--stroke-color", default="")
    ap.add_argument("--erase-only", action="store_true")
    ap.add_argument("--scale", type=float, default=1.0, help="输出放大倍数（2=2x 更清晰）")
    ap.add_argument("--show-box", action="store_true")
    a = ap.parse_args()

    img = Image.open(a.image).convert("RGB")
    W, H = img.size
    print("原图: %dx%d" % (W, H))

    lines = detect_text_lines(img) if (a.auto or a.list_lines) else []
    if a.list_lines:
        for i, ln in enumerate(lines):
            print("line %d: %s  宽%d 高%d" % (i, ln, ln[2] - ln[0], ln[3] - ln[1]))
        print(json.dumps(lines))
        return

    def grow(box, k):
        return (max(0, box[0] - k), max(0, box[1] - k),
                min(W, box[2] + k), min(H, box[3] + k))

    out = img
    report = []
    if a.jobs:
        for item in a.jobs.split("|"):
            if "=" not in item:
                continue
            k, txt = item.split("=", 1)
            idx = int(k)
            if a.auto:
                box = lines[idx] if idx < len(lines) else None
            else:
                box = parse_box(a.box)
            if box is None:
                print("跳过第 %d 行（超出检测范围）" % idx); continue
            out, color, size = replace_text(out, grow(box, a.expand), txt, a, None)
            report.append({"box": box, "text": txt, "color": color, "size": size})
            print("第%d行 %s -> %s 字号%d 颜色%s" % (idx, box, txt, size, color))
    else:
        box = lines[a.line] if a.auto else parse_box(a.box)
        print("目标框:", box)
        out, color, size = replace_text(out, grow(box, a.expand), "" if a.erase_only else a.text, a, None)
        report.append({"box": box, "text": a.text, "color": color, "size": size})
        print("字号:", size, "文字色:", color)

    if a.show_box:
        d = ImageDraw.Draw(out)
        for r in report:
            d.rectangle(r["box"], outline=(255, 0, 0), width=2)

    if a.scale != 1.0:
        out = out.resize((int(W * a.scale), int(H * a.scale)), Image.LANCZOS)

    out.save(a.out)
    print("输出 ->", a.out, out.size)


if __name__ == "__main__":
    sys.exit(main())
