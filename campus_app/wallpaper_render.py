"""Render data-only classroom wallpaper in a short-lived process."""
import json
import math
import os
from pathlib import Path


def render(payload, width, height, output):
    from PIL import Image, ImageDraw, ImageFont

    width, height = int(width), int(height)
    if not 320 <= width <= 16384 or not 240 <= height <= 16384:
        raise ValueError("显示器尺寸超出支持范围")
    ratio = min(1, 4096/max(width, height))
    width, height = round(width*ratio), round(height*ratio)
    canvas = Image.new("RGB", (width, height), "#f4f3ed")
    draw = ImageDraw.Draw(canvas)
    font_root = Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts"
    fonts = {}

    def font(size, bold=False):
        key = (size, bold)
        if key not in fonts:
            candidates = (["msyhbd.ttc", "msyh.ttc"] if bold else ["msyh.ttc"]) + ["simsun.ttc", "simhei.ttf", "Deng.ttf"]
            path = next((font_root/name for name in candidates if (font_root/name).is_file()), None)
            if path is None:
                raise OSError("系统缺少中文字体，请安装微软雅黑或宋体")
            fonts[key] = ImageFont.truetype(str(path), max(10, round(size)))
        return fonts[key]

    def trim(value, face, limit):
        value = str(value)
        if draw.textlength(value, font=face) <= limit:
            return value
        while value and draw.textlength(value+"…", font=face) > limit:
            value = value[:-1]
        return value+"…"

    scale = max(.65, min(width/1600, height/900))
    if height > width:
        scale = max(1, scale)
    margin = max(20, round(38*scale))
    left = round(width*.14) if width >= height else margin
    right, top = width-margin, max(25, round(height*.065))
    accent, ink, muted = "#38674f", "#26392f", "#7d867c"
    title_font, small_font = font(max(25, 43*scale), True), font(max(12, 16*scale))
    draw.text((left, top), "今日值日安排", font=title_font, fill=ink)
    title_height = draw.textbbox((0, 0), "今日值日安排", font=title_font)[3]
    subtitle = f'{payload["today"]["date"]}  ·  {payload["class_name"]}'
    subtitle_top = top+title_height+round(16*scale)
    draw.text((left, subtitle_top), trim(subtitle, small_font, right-left), font=small_font, fill=muted)
    content_top = subtitle_top+round(51*scale)
    bottom = height-max(44, round(height*.085))
    areas = payload["today"]["areas"]
    gap = max(9, round(20*scale))
    truncated = False
    layout = []
    if areas:
        def score(columns):
            rows = math.ceil(len(areas)/columns)
            return min((right-left-gap*(columns-1))/columns/360,
                       (bottom-content_top-gap*(rows-1))/rows/180)
        columns = max(range(1, min(4, len(areas))+1), key=score)
        rows = math.ceil(len(areas)/columns)
        card_width = (right-left-gap*(columns-1))/columns
        card_height = (bottom-content_top-gap*(rows-1))/rows
        for index, area in enumerate(areas):
            x = round(left+(index % columns)*(card_width+gap))
            y = round(content_top+(index//columns)*(card_height+gap))
            x2, y2 = round(x+card_width), round(y+card_height)
            layout.append([x, y, x2, y2])
            draw.rounded_rectangle((x, y, x2, y2), radius=max(5, round(min(18*scale,card_height*.12))),
                                   fill="#eaf0e5" if index == 0 else "#fffefa", outline="#dce3d5", width=1)
            pad = max(3, round(min(24*scale, card_height*.10)))
            label_face = font(min(max(13, card_height*.16), 25*scale), True)
            label = area["area_name"]+(" · "+area["group_name"] if area.get("group_name") else "")
            label = trim(label, label_face, card_width-2*pad)
            draw.text((x+pad, y+pad), label, font=label_face, fill=accent)
            label_height = draw.textbbox((0, 0), label, font=label_face)[3]
            names_top = y+pad+label_height+max(5, round(14*scale))
            name_face = font(min(max(12, card_height*.14), 25*scale))
            line_height = max(15, draw.textbbox((0,0), "林予安", font=name_face)[3]+round(7*scale))
            max_lines = max(0, int((y2-pad-names_top)/line_height))
            names = "、".join(s["name"]+("（请假）" if s.get("absent") else "") for s in area["students"]) or "暂无出勤人员"
            lines, line = [], ""
            for char in names:
                if line and draw.textlength(line+char, font=name_face) > card_width-2*pad:
                    lines.append(line)
                    line = char
                else:
                    line += char
            if line:
                lines.append(line)
            if max_lines == 0:
                truncated = True
                continue
            if len(lines) > max_lines:
                truncated = True
                lines = lines[:max_lines]
                lines[-1] = trim(lines[-1]+"…", name_face, card_width-2*pad)
            for line_index, line in enumerate(lines):
                draw.text((x+pad, names_top+line_index*line_height), line, font=name_face, fill=ink)
    else:
        draw.text((left, content_top+25), "这一天尚未开始排班", font=small_font, fill=muted)
    footer = "名单较长，完整安排请打开校园值日管家" if truncated else "每日自动更新  ·  校园值日管家"
    draw.text((left, height-max(28, round(45*scale))), trim(footer, small_font, right-left), font=small_font, fill=muted)
    output = Path(output)
    temporary = output.with_suffix(".tmp.png")
    canvas.save(temporary, "PNG")
    canvas.close()
    temporary.replace(output)
    return {"size": [width, height], "areas": len(areas), "truncated": truncated, "cards": layout}


def render_request(path):
    path = Path(path).resolve()
    if path.stat().st_size > 1048576:
        raise ValueError("壁纸数据过大")
    request = json.loads(path.read_text(encoding="utf-8"))
    for job in request["jobs"]:
        output = Path(job["path"]).resolve()
        if output.parent != path.parent or not output.name.startswith("duty-") or output.suffix != ".png":
            raise ValueError("壁纸输出目录有误")
        render(request["payload"], job["width"], job["height"], output)
