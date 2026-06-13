"""
PicoDeck V2 — Graphics Test Screen
Branch: testing — never merged to main.

Layout (176 x 264):
  y=  0-13   Header bar
  y= 14-69   Greyscale / dither bands  (4 x 14 px)
  y= 70      Separator
  y= 71-125  Shapes                    (55 px)
  y=126      Separator
  y=127-263  Text sizes                (137 px)
"""
from machine import Pin, SoftSPI
import framebuf, time

WIDTH  = 176
HEIGHT = 264


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


# ── screen ────────────────────────────────────────────────────────────────────

def draw(epd):
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
    fb.rect(4, sy + 4, 28, 20, 0)
    fb.text("rect", 4, sy + 28, 0)

    fb.fill_rect(40, sy + 4, 28, 20, 0)
    fb.text("fill", 40, sy + 28, 0)

    fb.line(76, sy + 4, 100, sy + 24, 0)
    fb.line(76, sy + 24, 100, sy + 4, 0)
    fb.text("line", 76, sy + 28, 0)

    circle(fb, 124, sy + 14, 12, 0, fill=False)
    fb.text("circ", 113, sy + 28, 0)

    circle(fb, 160, sy + 14, 10, 0, fill=True)
    fb.text("fill", 149, sy + 28, 0)

    triangle(fb, 4, sy + 52, 28, sy + 52, 16, sy + 38, 0)
    fb.text("tri", 4, sy + 54, 0)

    # separator
    fb.hline(0, 126, WIDTH, 0)

    # text sizes (y=127+)
    fb.text("1x: AaBbCc 0123456", 2, 130, 0)
    fb.hline(0, 140, WIDTH, 0)
    draw_big(fb, "2x Hello!", 2, 143, 2, 0)
    fb.hline(0, 161, WIDTH, 0)
    draw_big(fb, "3x Hi!", 2, 164, 3, 0)


# ── main ──────────────────────────────────────────────────────────────────────

print("V2 graphics test — init")
epd = EPD()
draw(epd)
epd.show()
print("done")
