from PIL import Image, ImageDraw

ICON_SIZE = 64


def _make_icon(color, border_color=None):
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    if border_color:
        draw.ellipse(
            [margin, margin, ICON_SIZE - margin, ICON_SIZE - margin],
            fill=border_color,
        )
        inner = margin + 3
        draw.ellipse(
            [inner, inner, ICON_SIZE - inner, ICON_SIZE - inner],
            fill=color,
        )
    else:
        draw.ellipse(
            [margin, margin, ICON_SIZE - margin, ICON_SIZE - margin],
            fill=color,
        )
    try:
        cx, cy = ICON_SIZE // 2, ICON_SIZE // 2
        draw.text((cx - 6, cy - 8), "K", fill="white")
    except Exception:
        pass
    return img


def icon_ok():
    return _make_icon((76, 175, 80), (56, 142, 60))


def icon_syncing():
    return _make_icon((255, 167, 38), (245, 124, 0))


def icon_error():
    return _make_icon((229, 57, 53), (198, 40, 40))
