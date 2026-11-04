"""离线生成 TALKMAN COLOPLAY 图标与共享母版；运行应用不依赖 Pillow。"""
from __future__ import annotations

import argparse
import io
import math
from pathlib import Path
import struct
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / 'TalkMan-assets'
GREEN, BLACK, WHITE = '#63e994', '#111111', '#f5f5f5'
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256, 512, 1024)


class Artwork:
    """相同几何同时输出矢量与抗锯齿位图，避免两端各画一套。"""
    def __init__(self, size):
        self.size = size
        self.factor = min(8, max(2, 2048 // size))
        self.scale = size * self.factor / 100
        self.image = Image.new('RGBA', (size*self.factor, size*self.factor))
        self.draw = ImageDraw.Draw(self.image)
        self.nodes = []

    def coords(self, values):
        return tuple(round(value*self.scale) for value in values)

    def rect(self, x, y, width, height, radius, fill):
        self.draw.rounded_rectangle(self.coords((x, y, x+width, y+height)),
                                    radius=round(radius*self.scale), fill=fill)
        self.nodes.append(f'<rect x="{x:g}" y="{y:g}" width="{width:g}" height="{height:g}" rx="{radius:g}" fill="{fill}"/>')

    def line(self, points, color, width, *, svg_path=None):
        self.draw.line([self.coords(point) for point in points], fill=color,
                       width=max(1, round(width*self.scale)), joint='curve')
        if svg_path is not None:
            self.nodes.append(f'<path d="{svg_path}" fill="none" stroke="{color}" stroke-width="{width:g}"/>')
        else:
            value = ' '.join(f'{x:g},{y:g}' for x, y in points)
            self.nodes.append(f'<polyline points="{value}" fill="none" stroke="{color}" stroke-width="{width:g}"/>')

    def arc(self, cx, cy, rx, ry, start, end, color, width, *, closed=False):
        points = [(cx+rx*math.cos(math.radians(degree)), cy+ry*math.sin(math.radians(degree)))
                  for degree in (start+(end-start)*i/180 for i in range(181))]
        if closed:
            points.append(points[0])
            self.line(points, color, width)
            self.nodes[-1] = f'<ellipse cx="{cx:g}" cy="{cy:g}" rx="{rx:g}" ry="{ry:g}" fill="none" stroke="{color}" stroke-width="{width:g}"/>'
        else:
            x0, y0 = points[0]; x1, y1 = points[-1]
            path = f'M{x0:g} {y0:g} A{rx:g} {ry:g} 0 0 1 {x1:g} {y1:g}'
            self.line(points, color, width, svg_path=path)

    def png(self):
        return self.image.resize((self.size, self.size), Image.Resampling.LANCZOS)

    def svg(self, title):
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                f'<title>{escape(title)}</title>'+''.join(self.nodes)+'</svg>\n')


def draw_mark(size, *, ink=GREEN, tile='desktop', small=None, device_only=False):
    """100 单位网格：圆屏中心50/50、半径29；四键位于17/74。"""
    art = Artwork(size)
    if tile == 'ios':
        art.rect(0, 0, 100, 100, 0, BLACK)
    elif tile == 'desktop':
        art.rect(4, 4, 92, 92, 21, '#505050')
        art.rect(5, 5, 90, 90, 20, BLACK)
    small = size <= 24 if small is None else small
    for x in (17, 74):
        for y in (17, 74):
            art.rect(x, y, 9, 9, 2, ink)
    art.arc(50, 50, 29, 29, 0, 360, ink, 4 if small else 3, closed=True)
    if device_only:
        return art
    # 沿用 Desktop 的麦克风与 I 形文字光标；小尺寸去掉 I，麦克风改实心。
    scale = .66
    cx = 50 if small else 43
    dx, dy = (17 if small else 15.35), 17.33
    pos = lambda x, y: (dx+x*scale, dy+y*scale)
    line = (7 if small else 5)*scale
    x, y = pos(cx-10, 22)
    art.rect(x, y, 20*scale, 33*scale, 10*scale, ink)
    if not small:
        # 单色透明版也使用真正的中空轮廓，不填入假背景。
        # 擦除内孔仅影响这一尚未与其他图形相交的局部。
        hole = (x+line, y+line, x+20*scale-line, y+33*scale-line)
        art.draw.rounded_rectangle(art.coords(hole), radius=round(5*scale*art.scale),
                                   fill=BLACK if tile in ('desktop', 'ios') else (0, 0, 0, 0))
        art.nodes.pop()
        art.nodes.append(f'<rect x="{x+line/2:g}" y="{y+line/2:g}" width="{20*scale-line:g}" height="{33*scale-line:g}" rx="{10*scale-line/2:g}" fill="none" stroke="{ink}" stroke-width="{line:g}"/>')
    mx, my = pos(cx, 49)
    art.arc(mx, my, 19*scale, 18*scale, 0, 180, ink, line)
    for points in (((cx-19,42),(cx-19,49)), ((cx+19,42),(cx+19,49)),
                   ((cx,65),(cx,77)), ((cx-10,77),(cx+10,77))):
        art.line([pos(*p) for p in points], ink, line)
    if not small:
        for points in (((75,26),(75,73)), ((69,26),(81,26)), ((69,73),(81,73))):
            art.line([pos(*p) for p in points], ink, line)
    return art


def font(size):
    path = Path('C:/Windows/Fonts/msyh.ttc')
    return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default(size=size)


def save_icns(path):
    # 显式提供 16/32/64 的 1× 表示。Pillow 默认只输出这些尺寸的 Retina 表示。
    # 16pt 的 2× 图也用简化几何，不因物理像素翻倍而重新添入细小光标。
    entries = []
    for kind, logical, density in (
        (b'icp4',16,1), (b'icp5',32,1), (b'icp6',64,1),
        (b'ic07',128,1), (b'ic08',256,1), (b'ic09',512,1),
        (b'ic10',512,2), (b'ic11',16,2), (b'ic12',32,2),
        (b'ic13',128,2), (b'ic14',256,2),
    ):
        stream=io.BytesIO()
        draw_mark(logical*density,small=logical<=24).png().save(stream,format='PNG')
        data=stream.getvalue()
        entries.append(kind+struct.pack('>I',8+len(data))+data)
    data=b''.join(entries)
    path.write_bytes(b'icns'+struct.pack('>I',8+len(data))+data)


def preview(shared, images):
    sheet = Image.new('RGB', (1280, 850), BLACK)
    d = ImageDraw.Draw(sheet)
    d.text((48, 32), 'TALKMAN / COLOPLAY', font=font(32), fill=WHITE)
    d.text((48, 82), '统一图标 · 本地待审阅', font=font(18), fill='#9dada3')
    reference=ROOT.parent/'TalkMan-发布授权/TalkMan-assets/app/TalkMan-256.png'
    desktop=Image.new('RGBA',(256,256))
    if reference.is_file():
        with Image.open(reference) as original:
            desktop=original.convert('RGBA')
    for left, title, note, icon in (
        (48, 'Desktop · 已有基础', '麦克风＋文字光标' if reference.is_file() else '本机未提供参考图', desktop),
        (450, 'COLOPLAY · 衍生方案', '圆屏＋四角按键，保留内部语音图形', images[256]),
        (852, 'iOS · 共享母版', '1024 方形不透明底，由系统裁角', draw_mark(256, tile='ios').png())):
        sheet.paste(icon, (left, 140), icon)
        d.text((left, 423), title, font=font(22), fill=WHITE)
        d.text((left, 464), note, font=font(16), fill='#9dada3')
    for row, (bg, ink) in enumerate((('#f2f2f0', '#111111'), ('#111111', '#f5f5f5'))):
        y = 530+row*156
        d.rectangle((0,y,1280,y+156),fill=bg)
        d.text((48,y+19), '小尺寸 / 单色 / 共用设备符号',font=font(18),fill=ink)
        for x, size in zip((48,165,265,353,435,510),(64,48,32,24,20,16)):
            icon = draw_mark(size, ink=ink, tile=None).png()
            sheet.paste(icon,(x,y+60),icon)
            d.text((x,y+128),str(size),font=font(12),fill=ink)
        symbol=draw_mark(64,ink=ink,tile=None,device_only=True).png()
        sheet.paste(symbol,(680,y+60),symbol)
        for x,size in ((838,18),(920,36)):
            menu=draw_mark(size,ink=ink,tile=None,small=True).png()
            sheet.paste(menu,(x,y+62),menu)
        d.text((812,y+112),'菜单栏透明模板',font=font(14),fill=ink)
    sheet.save(shared/'TalkMan-COLOPLAY图标预览.png')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shared', type=Path, default=ROOT.parent/'TalkMan-品牌素材')
    args=parser.parse_args()
    shared=args.shared
    shared.mkdir(parents=True,exist_ok=True)
    dest=ASSETS/'app';dest.mkdir(exist_ok=True)
    images={size:draw_mark(size).png() for size in SIZES}
    for size, image in images.items():
        image.save(dest/f'TalkMan-{size}.png')
    # PNG 格式 ICO 条目保留各尺寸的独立小图几何，不能让大图自动缩小覆盖。
    images[256].save(dest/'TalkMan.ico',sizes=[(s,s) for s in SIZES if s<=256],
                     append_images=[images[s] for s in SIZES if s<256])
    (ASSETS/'TalkMan-COLOPLAY.ico').write_bytes((dest/'TalkMan.ico').read_bytes())
    save_icns(dest/'TalkMan.icns')
    for size in (18,36):
        draw_mark(size,ink='#000000',tile=None,small=True).png().save(dest/f'TalkMan-menu-{size}.png')
    svg=draw_mark(1024).svg('TALKMAN COLOPLAY：语音、文字与圆屏四键')
    (dest/'TalkMan.svg').write_text(svg,encoding='utf-8')
    (shared/'TalkMan-COLOPLAY图标.svg').write_text(svg,encoding='utf-8')
    images[1024].save(shared/'TalkMan-COLOPLAY图标-1024.png')
    ios=draw_mark(1024,tile='ios')
    (shared/'TalkMan-COLOPLAY-iOS母版.svg').write_text(ios.svg('TALKMAN COLOPLAY：iOS 方形母版'),encoding='utf-8')
    ios.png().convert('RGB').save(shared/'TalkMan-COLOPLAY-iOS-1024.png')
    for name,ink in (('黑',BLACK),('白',WHITE),('绿',GREEN)):
        art=draw_mark(1024,ink=ink,tile=None)
        (shared/f'TalkMan-COLOPLAY单色-{name}.svg').write_text(art.svg('TALKMAN COLOPLAY 单色图形'),encoding='utf-8')
    for name,ink in (('黑',BLACK),('白',WHITE),('绿',GREEN)):
        art=draw_mark(1024,ink=ink,tile=None,device_only=True)
        (shared/f'TalkMan-COLOPLAY设备符号-{name}.svg').write_text(art.svg('COLOPLAY 圆屏与四角按键'),encoding='utf-8')
    (shared/'TalkMan-COLOPLAY小尺寸.svg').write_text(
        draw_mark(24,tile=None).svg('TALKMAN COLOPLAY 小尺寸简化图形'),encoding='utf-8')
    preview(shared,images)
    print(f'已生成本端图标与共享素材：{shared}')


if __name__=='__main__':
    main()
