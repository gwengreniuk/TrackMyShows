"""Generate the app icons (phone PWA + Kodi add-on). Run: python tools/make_icons.py"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BG = (15, 23, 42)
ACCENT = (45, 212, 191)
WHITE = (241, 245, 249)


def draw(size, maskable=False):
    scale = 4  # supersample for smooth edges
    s = size * scale
    img = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if maskable:
        d.rectangle([0, 0, s, s], fill=BG)
        inset = s * 0.2  # keep artwork inside the maskable safe zone
    else:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=s * 0.22, fill=BG)
        inset = s * 0.14
    w = s - 2 * inset
    # TV body
    tv = [inset, inset + w * 0.12, s - inset, inset + w * 0.80]
    d.rounded_rectangle(tv, radius=w * 0.10, outline=WHITE, width=int(w * 0.07))
    # stand
    d.line([(s / 2 - w * 0.18, inset + w * 0.95), (s / 2 + w * 0.18, inset + w * 0.95)], fill=WHITE, width=int(w * 0.07))
    # check mark
    pts = [(inset + w * 0.28, inset + w * 0.47), (inset + w * 0.44, inset + w * 0.62), (inset + w * 0.74, inset + w * 0.32)]
    d.line(pts, fill=ACCENT, width=int(w * 0.10), joint='curve')
    for x, y in (pts[0], pts[2]):
        r = w * 0.05
        d.ellipse([x - r, y - r, x + r, y + r], fill=ACCENT)
    return img.resize((size, size), Image.LANCZOS)


def save(img, *parts):
    path = os.path.join(ROOT, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    print('wrote', os.path.relpath(path, ROOT))


if __name__ == '__main__':
    for size in (180, 192, 512):
        save(draw(size), 'phone', 'icons', 'icon-%d.png' % size)
    save(draw(512, maskable=True), 'phone', 'icons', 'maskable-512.png')
    save(draw(256), 'plugin.video.trackmyshows', 'resources', 'icon.png')
