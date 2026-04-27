import network, socket, ujson, ntptime, time, gc
from machine import Pin, SoftSPI, reset
import framebuf

VERSION = "1.7"
BETA    = True

# ── staged OTA apply (runs before anything else) ──────────────────────────────
def _apply_staged():
    import os as _os
    try:
        _os.stat("update.py")
    except OSError:
        return  # no staged update waiting
    print("applying staged OTA update...")
    try:
        with open("update.py", "rb") as f:
            data = f.read()
        with open("main.py", "wb") as f:
            f.write(data)
        _os.remove("update.py")
        print("done, rebooting")
        time.sleep(2)
        reset()
    except Exception as e:
        print("staged apply error:", e)
        try:
            import os; os.remove("update.py")
        except:
            pass

_apply_staged()

# Write our version to disk — OTA compares against this file, not the constant
try:
    with open("version.txt", "w") as f:
        f.write(VERSION)
except:
    pass

try:
    import ssl
    HAS_SSL = True
except ImportError:
    HAS_SSL = False

WIDTH  = 176
HEIGHT = 264

WMO = {
    0:"Clear Sky",   1:"Mainly Clear", 2:"Part. Cloudy",
    3:"Overcast",   45:"Fog",         48:"Icy Fog",
   51:"Lt Drizzle",53:"Drizzle",     55:"Hvy Drizzle",
   61:"Lt Rain",   63:"Rain",        65:"Hvy Rain",
   71:"Lt Snow",   73:"Snow",        75:"Hvy Snow",
   80:"Showers",   82:"Hvy Showers", 95:"Thunderstorm",
}

# ── config ────────────────────────────────────────────────────────────────────

def _load_cfg():
    try:
        with open("config.json") as f:
            return ujson.load(f)
    except Exception as e:
        print("config error:", e)
        return {}

_cfg           = _load_cfg()
SSID           = _cfg.get("ssid", "")
PASSWORD       = _cfg.get("password", "")
TZ_OFFSET      = _cfg.get("tz_offset", 0)
WEATHER_LAT    = _cfg.get("lat", 0.0)
WEATHER_LON    = _cfg.get("lon", 0.0)
WEATHER_TZ     = _cfg.get("tz", "UTC")
LOCATION_LABEL = _cfg.get("label", "PicoDeck")

_eur_usd_rate  = None  # cached EUR/USD rate, updated when stocks are fetched


# ── display driver ────────────────────────────────────────────────────────────

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
        """Fast update — no full clear. ~0.5s. May ghost slightly over time."""
        self._write(0x24, self.buf)
        self._turn_on(0xFF)


# ── scaled text ───────────────────────────────────────────────────────────────

def draw_big(fb, text, x, y, scale, color):
    for ci, ch in enumerate(text):
        cbuf = bytearray(8)
        cfb = framebuf.FrameBuffer(cbuf, 8, 8, framebuf.MONO_HLSB)
        cfb.fill(1)
        cfb.text(ch, 0, 0, 0)
        for row in range(8):
            for col in range(8):
                if cfb.pixel(col, row) == 0:
                    fb.fill_rect(x + ci * 8 * scale + col * scale,
                                 y + row * scale, scale, scale, color)


# ── € glyph (not in built-in 8x8 font) ──────────────────────────────────────
# Custom 8x8 bitmap: 0=black, 1=white (MONO_HLSB)
_EUR_BLACK = bytearray(b'\x83\x7D\x7F\x03\x7F\x03\x7D\x83')
_EUR_WHITE = bytearray(b'\x7C\x82\x80\xFC\x80\xFC\x82\x7C')

def fb_text(fb, s, x, y, c):
    """Like fb.text() but renders the € character correctly."""
    cx = x
    glyph = _EUR_BLACK if c == 0 else _EUR_WHITE
    key   = 1 - c
    for ch in s:
        if ch == '€':  # €
            gfb = framebuf.FrameBuffer(glyph, 8, 8, framebuf.MONO_HLSB)
            fb.blit(gfb, cx, y, key)
        else:
            fb.text(ch, cx, y, c)
        cx += 8


# ── weather icons ─────────────────────────────────────────────────────────────

def weather_icon(fb, x, y, code):
    """Draw a 16x16 weather icon at (x, y)."""
    c = 0
    if code == 0:  # clear sky — sun with rays
        fb.fill_rect(x+5, y+3, 6, 10, c)
        fb.fill_rect(x+3, y+5, 10, 6, c)
        fb.pixel(x+4,  y+4,  c); fb.pixel(x+11, y+4,  c)
        fb.pixel(x+4,  y+11, c); fb.pixel(x+11, y+11, c)
        fb.pixel(x+7,  y+0,  c); fb.pixel(x+8,  y+0,  c)
        fb.pixel(x+7,  y+15, c); fb.pixel(x+8,  y+15, c)
        fb.pixel(x+0,  y+7,  c); fb.pixel(x+0,  y+8,  c)
        fb.pixel(x+15, y+7,  c); fb.pixel(x+15, y+8,  c)
        fb.pixel(x+2,  y+2,  c); fb.pixel(x+13, y+2,  c)
        fb.pixel(x+2,  y+13, c); fb.pixel(x+13, y+13, c)
    elif code in (1, 2):  # partly cloudy — small sun + cloud
        fb.fill_rect(x+9, y+0, 4,  7,  c)
        fb.fill_rect(x+7, y+2, 8,  3,  c)
        fb.pixel(x+6,  y+1,  c); fb.pixel(x+13, y+1,  c)
        fb.pixel(x+6,  y+7,  c); fb.pixel(x+13, y+7,  c)
        fb.fill_rect(x+1, y+8, 12, 6,  c)
        fb.fill_rect(x+3, y+6, 6,  3,  c)
        fb.fill_rect(x+7, y+5, 4,  4,  c)
    elif code == 3:  # overcast — full cloud
        fb.fill_rect(x+1, y+7, 14, 7, c)
        fb.fill_rect(x+3, y+5, 8,  4, c)
        fb.fill_rect(x+7, y+3, 5,  5, c)
    elif code in (45, 48):  # fog — horizontal lines
        fb.hline(x+1, y+3,  14, c)
        fb.hline(x+3, y+7,  10, c)
        fb.hline(x+1, y+11, 14, c)
    elif code in (51, 53, 55):  # drizzle — cloud + light dots
        fb.fill_rect(x+1, y+2, 14, 6, c)
        fb.fill_rect(x+3, y+0, 8,  4, c)
        fb.pixel(x+3,  y+10, c); fb.pixel(x+7,  y+12, c)
        fb.pixel(x+11, y+10, c); fb.pixel(x+5,  y+14, c)
        fb.pixel(x+9,  y+14, c)
    elif code in (61, 63, 65, 80, 81, 82):  # rain — cloud + lines
        fb.fill_rect(x+1, y+2, 14, 6, c)
        fb.fill_rect(x+3, y+0, 8,  4, c)
        fb.vline(x+3,  y+10, 5, c)
        fb.vline(x+8,  y+10, 5, c)
        fb.vline(x+13, y+10, 5, c)
    elif code in (71, 73, 75):  # snow — cloud + asterisk dots
        fb.fill_rect(x+1, y+2, 14, 6, c)
        fb.fill_rect(x+3, y+0, 8,  4, c)
        for sx, sy in [(3,11),(3,13),(8,10),(8,12),(8,14),(13,11),(13,13)]:
            fb.pixel(x+sx, y+sy, c)
    elif code == 95:  # thunderstorm — cloud + bolt
        fb.fill_rect(x+1, y+1, 14, 6, c)
        fb.fill_rect(x+3, y+0, 8,  3, c)
        fb.line(x+10, y+8,  x+6,  y+12, c)
        fb.hline(x+6,  y+12, 5,       c)
        fb.line(x+10, y+12, x+6,  y+15, c)
    else:  # unknown
        fb.rect(x+3, y+3, 10, 10, c)
        fb.text("?", x+4, y+4, c)


# ── networking ────────────────────────────────────────────────────────────────

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return True
    wlan.connect(SSID, PASSWORD)
    for _ in range(30):
        if wlan.isconnected():
            return True
        time.sleep(1)
    return False


def get_time():
    try:
        t = time.localtime(time.time() + TZ_OFFSET)
        if t[0] < 2024:
            return "--:--"
        return "%02d:%02d" % (t[3], t[4])
    except:
        return "--:--"


def http_get(host, path):
    try:
        addr = socket.getaddrinfo(host, 80)[0][-1]
        s = socket.socket()
        s.settimeout(15)
        s.connect(addr)
        s.send(("GET %s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\n\r\n"
                % (path, host)).encode())
        chunks = []
        while True:
            c = s.recv(512)
            if not c:
                break
            chunks.append(c)
        s.close()
        data = b"".join(chunks)
        return data[data.find(b"\r\n\r\n") + 4:]
    except Exception as e:
        print("http error:", e)
        return None


def https_raw(host, path, cookie=None):
    if not HAS_SSL:
        return None
    try:
        addr = socket.getaddrinfo(host, 443)[0][-1]
        sock = socket.socket()
        sock.settimeout(15)
        sock.connect(addr)
        s = ssl.wrap_socket(sock, server_hostname=host)
        hdrs = ("GET %s HTTP/1.0\r\nHost: %s\r\n"
                "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120\r\n"
                "Accept: */*\r\nAccept-Encoding: identity\r\n") % (path, host)
        if cookie:
            hdrs += "Cookie: %s\r\n" % cookie
        hdrs += "Connection: close\r\n\r\n"
        s.write(hdrs.encode())
        chunks = []
        while True:
            c = s.read(512)
            if not c:
                break
            chunks.append(c)
        s.close()
        return b"".join(chunks)
    except Exception as e:
        print("https error %s:" % host, e)
        return None


def https_body(host, path, cookie=None):
    raw = https_raw(host, path, cookie)
    if not raw:
        return None
    body = raw[raw.find(b"\r\n\r\n") + 4:]
    if body[:5] in (b"<html", b"<!DOC"):
        print("html response from", host)
        return None
    return body


# ── OTA update ────────────────────────────────────────────────────────────────

_OTA_HOST = "raw.githubusercontent.com"
_OTA_BASE = "/Ilikehomeassistant/picodeck/main"

_BAR_X = 10
_BAR_Y = 80
_BAR_W = WIDTH - 20
_BAR_H = 12


def _ota_stream(epd, host, path):
    """Stream HTTPS download with live progress bar. Returns bytes or None."""
    if not HAS_SSL:
        return None
    try:
        addr = socket.getaddrinfo(host, 443)[0][-1]
        sock = socket.socket()
        sock.settimeout(30)
        sock.connect(addr)
        s = ssl.wrap_socket(sock, server_hostname=host)
        s.write(("GET %s HTTP/1.0\r\nHost: %s\r\n"
                 "User-Agent: Mozilla/5.0\r\n"
                 "Accept: */*\r\nAccept-Encoding: identity\r\n"
                 "Connection: close\r\n\r\n" % (path, host)).encode())

        # Read headers
        hbuf = b""
        while b"\r\n\r\n" not in hbuf:
            c = s.read(256)
            if not c:
                break
            hbuf += c

        total = 0
        for line in hbuf.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                total = int(line.split(b":")[1].strip())
                break

        sep    = hbuf.find(b"\r\n\r\n") + 4
        chunks = [hbuf[sep:]]
        done   = len(chunks[0])
        last_pct = -1

        while True:
            c = s.read(512)
            if not c:
                break
            chunks.append(c)
            done += len(c)
            if total > 0:
                pct = min(done * 100 // total, 100)
                if pct != last_pct:
                    last_pct = pct
                    filled = (pct * (_BAR_W - 2)) // 100
                    epd.fb.fill_rect(_BAR_X + 1, _BAR_Y + 1, _BAR_W - 2, _BAR_H - 2, 1)
                    if filled > 0:
                        epd.fb.fill_rect(_BAR_X + 1, _BAR_Y + 1, filled, _BAR_H - 2, 0)
                    pct_str = "%d%%" % pct
                    epd.fb.fill_rect(0, _BAR_Y + _BAR_H + 2, WIDTH, 10, 1)
                    epd.fb.text(pct_str, (WIDTH - len(pct_str) * 8) // 2,
                                _BAR_Y + _BAR_H + 2, 0)
                    epd.show_partial()

        s.close()
        body = b"".join(chunks)
        if body[:5] in (b"<html", b"<!DOC"):
            return None
        return body
    except Exception as e:
        print("OTA stream error:", e)
        return None


def ota_check(epd):
    try:
        # Read local version from file — more reliable than the hardcoded constant
        local_ver = VERSION
        try:
            with open("version.txt") as f:
                local_ver = f.read().strip()
        except:
            pass  # no version.txt yet, fall back to code constant

        ver_body = https_body(_OTA_HOST, _OTA_BASE + "/version.txt")
        if not ver_body:
            print("OTA: could not fetch version")
            return
        remote = ver_body.strip().decode()
        if remote == local_ver:
            print("OTA: up to date (%s)" % local_ver)
            return

        print("OTA: %s -> %s" % (local_ver, remote))
        epd.fb.fill(1)
        epd.fb.fill_rect(0, 0, WIDTH, 16, 0)
        hdr = "Updating PicoDeck"
        epd.fb.text(hdr, (WIDTH - len(hdr) * 8) // 2, 4, 1)
        ver_str = "v%s  ->  v%s" % (local_ver, remote)
        epd.fb.text(ver_str, (WIDTH - len(ver_str) * 8) // 2, 28, 0)
        epd.fb.rect(_BAR_X, _BAR_Y, _BAR_W, _BAR_H, 0)
        epd.fb.text("0%", (WIDTH - 16) // 2, _BAR_Y + _BAR_H + 2, 0)
        epd.show()

        gc.collect()
        new_code = _ota_stream(epd, _OTA_HOST, _OTA_BASE + "/main.py")
        if not new_code:
            print("OTA: download failed")
            return
        # Write to staging file — main.py is replaced safely on next boot
        with open("update.py", "wb") as f:
            f.write(new_code)

        epd.fb.fill(1)
        msg = "Updated!  Rebooting..."
        epd.fb.text(msg, (WIDTH - len(msg) * 8) // 2, HEIGHT // 2 - 4, 0)
        epd.show()
        print("OTA done, rebooting")
        time.sleep(3)
        reset()
    except Exception as e:
        print("OTA error:", e)


# ── Yahoo Finance crumb auth ──────────────────────────────────────────────────

_yf = {"cookie": None, "crumb": None}


def _yf_refresh():
    print("getting yahoo auth...")
    try:
        addr = socket.getaddrinfo("fc.yahoo.com", 443)[0][-1]
        sock = socket.socket()
        sock.settimeout(10)
        sock.connect(addr)
        s = ssl.wrap_socket(sock, server_hostname="fc.yahoo.com")
        s.write(b"GET / HTTP/1.0\r\nHost: fc.yahoo.com\r\n"
                b"User-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n")
        buf = b""
        while len(buf) < 8192:
            c = s.read(256)
            if not c or b"\r\n\r\n" in buf:
                break
            buf += c
        s.close()
        idx = buf.find(b"Set-Cookie: ")
        if idx < 0:
            print("no cookie in fc.yahoo.com response")
            return False
        end = buf.find(b";", idx + 12)
        cookie = buf[idx + 12:end].decode()
        raw = https_raw("query2.finance.yahoo.com", "/v1/test/getcrumb", cookie)
        if not raw:
            return False
        crumb = raw[raw.find(b"\r\n\r\n") + 4:].strip().decode()
        if not crumb or crumb.startswith("<"):
            print("bad crumb:", crumb[:30])
            return False
        _yf["cookie"] = cookie
        _yf["crumb"]  = crumb
        print("yahoo auth ok")
        return True
    except Exception as e:
        print("yf auth error:", e)
        return False


def fetch_yahoo(symbols, names):
    if not _yf["crumb"] and not _yf_refresh():
        return None
    try:
        url_syms = ",".join(s.replace("^", "%5E").replace("=", "%3D")
                            for s in symbols)
        crumb = (_yf["crumb"].replace("&", "%26")
                             .replace("/", "%2F")
                             .replace("+", "%2B"))
        path = ("/v7/finance/quote?symbols=%s"
                "&fields=regularMarketPrice,regularMarketChangePercent"
                "&crumb=%s") % (url_syms, crumb)
        body = https_body("query1.finance.yahoo.com", path, _yf["cookie"])
        if not body:
            _yf["cookie"] = None
            _yf["crumb"]  = None
            return None
        results = ujson.loads(body)["quoteResponse"]["result"]
        name_map = dict(zip(symbols, names))
        return [(name_map.get(r["symbol"], r["symbol"][:5]),
                 r["regularMarketPrice"],
                 r["regularMarketChangePercent"])
                for r in results]
    except Exception as e:
        print("yahoo error:", e)
        _yf["cookie"] = None
        _yf["crumb"]  = None
        return None


# ── data fetching ─────────────────────────────────────────────────────────────

def fetch_weather():
    try:
        body = https_body("api.open-meteo.com",
            "/v1/forecast?latitude=%s&longitude=%s"
            "&current=temperature_2m,weather_code,windspeed_10m,"
            "relative_humidity_2m,apparent_temperature"
            "&forecast_days=1&timezone=%s" % (WEATHER_LAT, WEATHER_LON, WEATHER_TZ))
        if not body:
            return None
        d = ujson.loads(body)["current"]
        return {
            "temp":     round(d["temperature_2m"]),
            "feels":    round(d["apparent_temperature"]),
            "humidity": round(d["relative_humidity_2m"]),
            "wind":     round(d["windspeed_10m"]),
            "code":     d["weather_code"],
        }
    except Exception as e:
        print("weather error:", e)
        return None


def fetch_crypto():
    try:
        body = https_body("api.coingecko.com",
            "/api/v3/simple/price?ids=bitcoin,litecoin,ethereum"
            "&vs_currencies=eur&include_24hr_change=true")
        if not body:
            return None
        d = ujson.loads(body)
        return [
            ("BTC", d["bitcoin"]["eur"],  d["bitcoin"]["eur_24h_change"]),
            ("LTC", d["litecoin"]["eur"], d["litecoin"]["eur_24h_change"]),
            ("ETH", d["ethereum"]["eur"], d["ethereum"]["eur_24h_change"]),
        ]
    except Exception as e:
        print("crypto error:", e)
        return None


def fetch_stocks():
    global _eur_usd_rate
    # Batch EURUSD=X with stocks to get conversion rate in one request
    result = fetch_yahoo(
        ["EURUSD=X", "NVDA", "GOOGL", "AAPL"],
        ["EURUSD",   "NVDA", "GOOGL", "AAPL"]
    )
    if not result:
        return None
    for sym, price, _ in result:
        if sym == "EURUSD" and price > 0:
            _eur_usd_rate = price
            break
    return [(s, p, c) for s, p, c in result if s != "EURUSD"]


GROUPS = [
    ("CRYPTO", fetch_crypto),
    ("STOCKS", fetch_stocks),
]


# ── display ───────────────────────────────────────────────────────────────────

def fmt_price(val, sym):
    if sym == "BTC":
        return "€%d" % round(val)
    elif sym in ("LTC", "ETH"):
        return "€%.2f" % val
    elif sym in ("NVDA", "GOOGL", "AAPL"):
        if _eur_usd_rate and _eur_usd_rate > 0:
            eur = val / _eur_usd_rate
            return "€%d" % round(eur) if eur >= 100 else "€%.2f" % eur
        return "$%.2f" % val
    else:
        return "%.4f" % val


def fmt_change(pct):
    sign = "+" if pct >= 0 else ""
    return "%s%.1f%%" % (sign, pct)


def draw(epd, weather, ts, group_name, tickers):
    fb = epd.fb
    fb.fill(1)

    # top bar
    fb.fill_rect(0, 0, WIDTH, 16, 0)
    fb.text(ts, 4, 4, 1)
    fb.text(SSID, WIDTH - len(SSID) * 8 - 4, 4, 1)

    # location
    loc = LOCATION_LABEL
    fb.text(loc, (WIDTH - len(loc) * 8) // 2, 20, 0)
    fb.hline(4, 32, WIDTH - 8, 0)

    if weather:
        cond = WMO.get(weather["code"], "Code %d" % weather["code"])
        temp = str(weather["temp"]) + " C"
        weather_icon(fb, 2, 35, weather["code"])
        fb.text(cond, 22, 40, 0)
        tx = (WIDTH - len(temp) * 8) // 2
        for dx in range(2):
            for dy in range(2):
                fb.text(temp, tx + dx, 54 + dy, 0)

    fb.hline(4, 72, WIDTH - 8, 0)
    if weather:
        fb.text("Humidity:",   4, 80,  0)
        fb.text("%d%%" % weather["humidity"], 104, 80,  0)
        fb.text("Wind:",       4, 94,  0)
        fb.text("%d km/h" % weather["wind"],  104, 94,  0)
        fb.text("Feels like:", 4, 108, 0)
        fb.text("%d C" % weather["feels"],    104, 108, 0)

    fb.hline(4, 120, WIDTH - 8, 0)
    upd = "Updated " + ts
    fb.text(upd, (WIDTH - len(upd) * 8) // 2, 128, 0)

    # ticker section
    fb.hline(0, 141, WIDTH, 0)
    fb.hline(0, 142, WIDTH, 0)
    fb.fill_rect(0, 143, WIDTH, 14, 0)
    hdr = "-- " + group_name + " --"
    fb.text(hdr, (WIDTH - len(hdr) * 8) // 2, 145, 1)

    if tickers:
        for i, (sym, price, change) in enumerate(tickers[:3]):
            y = 163 + i * 17
            p_str = fmt_price(price, sym)
            c_str = fmt_change(change)
            fb.text(sym,       4, y, 0)
            fb_text(fb, p_str, 52, y, 0)
            fb.text(c_str, WIDTH - len(c_str) * 8 - 4, y, 0)
    else:
        fb.text("No data", (WIDTH - 7 * 8) // 2, 175, 0)


# ── startup ───────────────────────────────────────────────────────────────────
print("init... v" + VERSION)
epd = EPD()

# Connecting screen — full refresh base
epd.fb.fill(1)
label = "Connecting"
lx = (WIDTH - len(label) * 8) // 2
ly = HEIGHT // 2 - 8
epd.fb.text(label, lx, ly, 0)
epd.show()

# Animated dots while connecting
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
if not wlan.isconnected():
    wlan.connect(SSID, PASSWORD)

dot_states = ["   ", ".  ", ".. ", "..."]
dot_x = lx + len(label) * 8 + 2
ds = 0
for _ in range(30):
    ds = (ds + 1) % len(dot_states)
    epd.fb.fill_rect(dot_x, ly, 26, 8, 1)
    epd.fb.text(dot_states[ds], dot_x, ly, 0)
    epd.show_partial()
    if wlan.isconnected():
        break
    time.sleep(1)

# Reset display state after partial refreshes before full refresh
epd._init()

# PicoDeck splash screen
epd.fb.fill(1)
pw = 4 * 8 * 3
draw_big(epd.fb, "Pico", (WIDTH - pw) // 2, 86, 3, 0)
draw_big(epd.fb, "Deck", (WIDTH - pw) // 2, 86 + 24 + 4, 3, 0)
ver_str = "version " + VERSION
epd.fb.text(ver_str, (WIDTH - len(ver_str) * 8) // 2, 150, 0)
if BETA:
    warn = "Warning! Beta build!"
    epd.fb.fill_rect(0, HEIGHT - 16, WIDTH, 16, 0)
    epd.fb.text(warn, (WIDTH - len(warn) * 8) // 2, HEIGHT - 12, 1)
epd.show()

# NTP + OTA while splash is visible
try:
    ntptime.settime()
    print("ntp ok")
except Exception as e:
    print("ntp fail:", e)

ota_check(epd)
time.sleep(2)

# ── main loop ─────────────────────────────────────────────────────────────────
weather      = None
ticker_cache = [None, None]
group_idx    = 0
last_min     = -1

while True:
    gc.collect()
    t = time.localtime(time.time() + TZ_OFFSET)
    cur_min = t[4]

    if cur_min != last_min:
        last_min = cur_min
        connect_wifi()

        new_w = fetch_weather()
        if new_w:
            weather = new_w

        gname, gfetch = GROUPS[group_idx]
        new_t = gfetch()
        if new_t:
            ticker_cache[group_idx] = new_t

        ts = get_time()
        print("tick:", ts, gname, ticker_cache[group_idx])
        draw(epd, weather, ts, gname, ticker_cache[group_idx])
        epd.show()

        group_idx = (group_idx + 1) % len(GROUPS)

    time.sleep_ms(500)
