"""
PicoDeck V2 — Graphics Test Screen
One full refresh draws everything static; partial refresh loops the bottom
animation zone. Branch: testing — never merged to main.

Layout (176 x 264):
  y=  0-13   Header bar
  y= 14-69   Greyscale / dither bands  (4 x 14 px)
  y= 70      Separator
  y= 71-125  Shapes                    (55 px)
  y=126      Separator
  y=127-154  Text sizes                (28 px)
  y=155      Separator
  y=156-263  Animation zone            (108 px, partial refresh)
"""
from machine import Pin, SoftSPI
import framebuf, time, gc

WIDTH  = 176
HEIGHT = 264

ANIM_Y = 156
ANIM_H = HEIGHT - ANIM_Y   # 108

FULL_REFRESH_EVERY = 40    # re-init + full refresh to clear ghost after N frames


# ── EPD driver ────────────────────────────────────────────────────────────────

class EPD:
    def __init__(self):
        self.spi  = SoftSPI(baudrate=2_000_000, polarity=0, phase=0,
                            sck=Pin(1), mosi=Pin(0), miso=Pin(6))
        self.cs   = Pin(2, Pin.OUT, value=1)
        self.dc   = Pin(3, Pin.OUT)
        self.rst  = Pin(4, Pin.OUT)
        self.busy = Pin(5, Pin.IN, Pin.PULL_DOWN)
        self.buf  = bytearray(WIDTH * HEIGHT // 8)
        self.fb   = framebuf.FrameBuffer(self.buf, WIDTH, HEIGHT, framebuf.MONO_HLSB)
        self._init()

    def _cmd(self, cmd):
        self.dc.value(0); self.cs.value(0)
        self.spi.write(bytes([cmd])); self.cs.value(1)

    def _data(self, *vals):
        self.dc.value(1); self.cs.value(0)
        self.spi.write(bytes(vals)); self.cs.value(1)

    def _bulk(self, buf):
        self.dc.value(1); self.cs.value(0)
        self.spi.write(buf); self.cs.value(1)

    def _wait(self, timeout_ms=10000):
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while self.busy.value() == 1:
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                return
            time.sleep_ms(2)
        time.sleep_ms(200)

    def _reset(self):
        self.rst.value(1); time.sleep_ms(200)
        self.rst.value(0); time.sleep_ms(2)
        self.rst.value(1); time.sleep_ms(200)

    def _init(self):
        self._reset(); self._wait()
        self._cmd(0x12); self._wait()
        self._cmd(0x45); self._data(0x00, 0x00, 0x07, 0x01)
        self._cmd(0x4F); self._data(0x00, 0x00)
        self._cmd(0x11); self._data(0x03)

    def _turn_on(self, mode):
        self._cmd(0x22); self._data(mode)
        self._cmd(0x20); self._wait()

    def _write(self, cmd, data):
        self._cmd(0x4F); self._data(0x00, 0x00)
        self._cmd(cmd); self._bulk(data)

    def show(self):
        self._write(0x26, bytes([0xFF] * (WIDTH * HEIGHT // 8)))
        self._write(0x24, self.buf)
        self._turn_on(0xF7)

    def show_partial(self):
        self._write(0x24, self.buf)
        self._turn_on(0xFF)


# ── drawing primitives ────────────────────────────────────────────────────────

def fill_dither(fb, x, y, w, h, level):
    """Fill rect with dithered grey. level: 0=black 1=dk-grey 2=lt-grey 3=white"""
    if level == 0:
        fb.fill_rect(x, y, w, h, 0)
    elif level == 3:
        fb.fill_rect(x, y, w, h, 1)
    elif level == 2:
        fb.fill_rect(x, y, w, h, 1)
        for dy in range(h):
            for dx in range(w):
                if (dx + dy) % 2 == 0:
                    fb.pixel(x + dx, y + dy, 0)
    else:
        fb.fill_rect(x, y, w, h, 0)
        for dy in range(h):
            for dx in range(w):
                if dx % 2 == 1 and dy % 2 == 1:
                    fb.pixel(x + dx, y + dy, 1)


def circle(fb, cx, cy, r, c, fill=False):
    x, y, err = r, 0, 0
    while x >= y:
        if fill:
            fb.hline(cx - x, cy + y, 2 * x + 1, c)
            fb.hline(cx - x, cy - y, 2 * x + 1, c)
            fb.hline(cx - y, cy + x, 2 * y + 1, c)
            fb.hline(cx - y, cy - x, 2 * y + 1, c)
        else:
            for px, py in ((cx+x,cy+y),(cx-x,cy+y),(cx+x,cy-y),(cx-x,cy-y),
                           (cx+y,cy+x),(cx-y,cy+x),(cx+y,cy-x),(cx-y,cy-x)):
                if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                    fb.pixel(px, py, c)
        y += 1
        if err <= 0:
            err += 2 * y + 1
        if err > 0:
            x -= 1
            err -= 2 * x + 1


def draw_big(fb, text, x, y, scale, color):
    for ci, ch in enumerate(text):
        cbuf = bytearray(8)
        cfb = framebuf.FrameBuffer(cbuf, 8, 8, framebuf.MONO_HLSB)
        cfb.fill(1); cfb.text(ch, 0, 0, 0)
        for row in range(8):
            for col in range(8):
                if cfb.pixel(col, row) == 0:
                    fb.fill_rect(x + ci*8*scale + col*scale,
                                 y + row*scale, scale, scale, color)


def triangle(fb, x0, y0, x1, y1, x2, y2, c):
    fb.line(x0, y0, x1, y1, c)
    fb.line(x1, y1, x2, y2, c)
    fb.line(x2, y2, x0, y0, c)


# ── static screen (full refresh) ─────────────────────────────────────────────

def draw_static(epd):
    fb = epd.fb
    fb.fill(1)

    # header
    fb.fill_rect(0, 0, WIDTH, 14, 0)
    hdr = "V2 GRAPHICS TEST"
    fb.text(hdr, (WIDTH - len(hdr) * 8) // 2, 3, 1)

    # greyscale / dither bands
    band_labels = [("WHITE",   3, 0),
                   ("LT GREY", 2, 0),
                   ("DK GREY", 1, 1),
                   ("BLACK",   0, 1)]
    for i, (lbl, level, tc) in enumerate(band_labels):
        y = 14 + i * 14
        fill_dither(fb, 0, y, WIDTH, 14, level)
        fb.text(lbl, (WIDTH - len(lbl) * 8) // 2, y + 3, tc)

    # separator
    fb.hline(0, 70, WIDTH, 0)

    # shapes (y=71 to y=125)
    sy = 71
    # outline rect
    fb.rect(4, sy + 4, 28, 20, 0)
    fb.text("rect", 4, sy + 28, 0)

    # filled rect
    fb.fill_rect(40, sy + 4, 28, 20, 0)
    fb.text("fill", 40, sy + 28, 0)

    # X cross lines
    fb.line(76, sy + 4, 100, sy + 24, 0)
    fb.line(76, sy + 24, 100, sy + 4, 0)
    fb.text("line", 76, sy + 28, 0)

    # circle
    circle(fb, 124, sy + 14, 12, 0, fill=False)
    fb.text("circ", 113, sy + 28, 0)

    # filled circle
    circle(fb, 160, sy + 14, 10, 0, fill=True)
    fb.text("fill", 149, sy + 28, 0)

    # triangle
    triangle(fb, 4, sy + 44, 20, sy + 44, 12, sy + 36, 0)
    fb.text("tri", 4, sy + 46, 0)

    # separator
    fb.hline(0, 126, WIDTH, 0)

    # text sizes (y=127 to y=154)
    fb.text("1x: AaBbCc 0123456", 2, 129, 0)
    draw_big(fb, "2x: Hello!", 2, 139, 2, 0)

    # separator
    fb.hline(0, 155, WIDTH, 0)

    # animation zone border + label
    fb.rect(0, ANIM_Y, WIDTH, ANIM_H, 0)
    lbl = "PARTIAL REFRESH"
    fb.text(lbl, (WIDTH - len(lbl) * 8) // 2, ANIM_Y + 3, 0)


# ── animation zone (partial refresh) ─────────────────────────────────────────

def draw_anim(epd, bx, by, vx, vy, frame):
    fb = epd.fb
    inner_y = ANIM_Y + 14
    inner_h = ANIM_H - 15
    fb.fill_rect(1, inner_y, WIDTH - 2, inner_h, 1)

    # velocity arrows showing direction
    ax = WIDTH // 2 + (vx * 10)
    ay = ANIM_Y + ANIM_H - 22
    fb.line(WIDTH // 2, ay, ax, ay, 0)
    fb.line(WIDTH // 2, ay, WIDTH // 2, ay - (vy * 5), 0)

    # bouncing ball
    circle(fb, bx, by, 9, 0, fill=True)

    # frame counter bottom-right
    s = "f:%d" % frame
    fb.text(s, WIDTH - len(s) * 8 - 3, ANIM_Y + ANIM_H - 11, 0)

    epd.show_partial()


# ── main ──────────────────────────────────────────────────────────────────────

print("V2 graphics test — init")
epd = EPD()
draw_static(epd)
epd.show()
print("static drawn, starting animation loop")

bx, by = WIDTH // 2, ANIM_Y + ANIM_H // 2
vx, vy = 4, 3
frame  = 0

while True:
    gc.collect()

    bx += vx; by += vy

    if bx - 9 <= 1:             bx = 10;            vx = abs(vx)
    if bx + 9 >= WIDTH - 2:     bx = WIDTH - 11;    vx = -abs(vx)
    if by - 9 <= ANIM_Y + 14:   by = ANIM_Y + 23;   vy = abs(vy)
    if by + 9 >= HEIGHT - 2:    by = HEIGHT - 11;    vy = -abs(vy)

    frame += 1
    draw_anim(epd, bx, by, vx, vy, frame)

    if frame % FULL_REFRESH_EVERY == 0:
        print("full refresh to clear ghost (frame %d)" % frame)
        epd._init()
        draw_static(epd)
        draw_anim(epd, bx, by, vx, vy, frame)
        epd.show()

    time.sleep_ms(150)
