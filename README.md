# PicoDeck

A live weather and ticker display for the **Waveshare 2.7" e-Paper HAT Rev 2.2** driven by a **Raspberry Pi Pico 2 W**, written in MicroPython.

![PicoDeck display showing weather and crypto ticker]

## Features

- Live weather from [Open-Meteo](https://open-meteo.com/) (no API key needed)
  - Temperature, feels like, humidity, wind speed, weather condition
- Real-time clock via NTP, refreshes every clock minute
- Three rotating ticker groups (cycles each minute):
  - **CRYPTO** — BTC, LTC, ETH (via CoinGecko)
  - **STOCKS** — NVDA, GOOGL, AAPL (via Yahoo Finance)
  - **MARKETS** — NASDAQ, EUR/USD rates (via Yahoo Finance + frankfurter.app)
- Top bar shows current time and WiFi SSID

## Hardware

| Component | Details |
|-----------|---------|
| Display | Waveshare 2.7" e-Paper HAT Rev 2.2 (176×264, SSD1680) |
| MCU | Raspberry Pi Pico 2 W (RP2350) |
| MicroPython | v1.28.0 |

## Wiring

| Display Pin | Pico 2 W Pin |
|-------------|--------------|
| VCC | 3V3 (pin 36) |
| GND | GND (pin 38) |
| DIN | GP0 (pin 1) |
| CLK | GP1 (pin 2) |
| CS | GP2 (pin 4) |
| DC | GP3 (pin 5) |
| RST | GP4 (pin 6) |
| BUSY | GP5 (pin 7) |

> Uses `machine.SoftSPI` on GP0/GP1 — hardware SPI is not required.

## Setup

1. Flash MicroPython v1.28.0 onto your Pico 2 W.

2. Copy `main_template.py` to your machine and rename it `main.py`.

3. Edit the configuration section at the top of `main.py`:

```python
SSID      = 'YOUR_WIFI_SSID'
PASSWORD  = 'YOUR_WIFI_PASSWORD'
TZ_OFFSET = 0          # seconds: 3600 = UTC+1, 0 = UTC, -18000 = UTC-5, etc.

WEATHER_LAT   = 0.0    # your latitude  (find at open-meteo.com)
WEATHER_LON   = 0.0    # your longitude
WEATHER_TZ    = "Europe%2FLondon"   # URL-encoded timezone string
LOCATION_LABEL = "Your Location"    # shown on screen
```

4. Flash to the Pico using [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html):

```bash
python -m mpremote connect COM3 cp main.py :main.py
```

Replace `COM3` with your actual serial port (Windows: check Device Manager; Linux/Mac: `/dev/ttyACM0` or similar).

5. Run it:

```bash
python -m mpremote connect COM3 run main.py
```

Or reset the Pico — `main.py` runs automatically on boot.

## Battery Power

Power via the **VSYS** pin (pin 39) accepts 1.8V–5.5V. A single LiPo cell (3.7V) works directly. Use GND (pin 38, right next to VSYS) for the negative connection.

## Timezone Reference

| Region | TZ_OFFSET | WEATHER_TZ |
|--------|-----------|------------|
| Ireland/UK (GMT) | `0` | `Europe%2FLondon` |
| Ireland/UK (BST, summer) | `3600` | `Europe%2FDublin` |
| Central Europe (CET) | `3600` | `Europe%2FBerlin` |
| Eastern US (EST) | `-18000` | `America%2FNew_York` |
| Pacific US (PST) | `-28800` | `America%2FLos_Angeles` |

## License

Apache 2.0 — see [LICENSE](LICENSE).
