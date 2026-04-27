"""
BLE HID keyboard central for Pico 2W.
Scans for the first device advertising the HID service (UUID 0x1812)
and connects automatically. Reconnects on disconnect.

Usage:
    import ble_keyboard
    ble_keyboard.init()          # start scanning (call once)
    ch = ble_keyboard.read_char()  # returns next char or None
    ble_keyboard.status()        # "scanning" / "connecting" / "ready" / "idle"
"""
import bluetooth

_HID_SVC = bluetooth.UUID(0x1812)
_HID_RPT = bluetooth.UUID(0x2A4D)

_IRQ_SCAN_RESULT          = 5
_IRQ_SCAN_DONE            = 6
_IRQ_PERIPHERAL_CONNECT   = 7
_IRQ_PERIPHERAL_DISCONNECT = 8
_IRQ_GATTC_SERVICE_RESULT = 9
_IRQ_GATTC_SERVICE_DONE   = 10
_IRQ_GATTC_CHAR_RESULT    = 11
_IRQ_GATTC_CHAR_DONE      = 12
_IRQ_GATTC_NOTIFY         = 18

# HID keycode → character (unshifted)
_MAP = {
     4:'a', 5:'b', 6:'c', 7:'d', 8:'e', 9:'f',10:'g',11:'h',
    12:'i',13:'j',14:'k',15:'l',16:'m',17:'n',18:'o',19:'p',
    20:'q',21:'r',22:'s',23:'t',24:'u',25:'v',26:'w',27:'x',
    28:'y',29:'z',
    30:'1',31:'2',32:'3',33:'4',34:'5',35:'6',36:'7',37:'8',38:'9',39:'0',
    40:'\r',42:'\x08',43:'\t',44:' ',
    45:'-',46:'=',47:'[',48:']',49:'\\',51:';',52:"'",53:'`',54:',',55:'.',56:'/',
}
# HID keycode → character (shifted)
_SMAP = {
     4:'A', 5:'B', 6:'C', 7:'D', 8:'E', 9:'F',10:'G',11:'H',
    12:'I',13:'J',14:'K',15:'L',16:'M',17:'N',18:'O',19:'P',
    20:'Q',21:'R',22:'S',23:'T',24:'U',25:'V',26:'W',27:'X',
    28:'Y',29:'Z',
    30:'!',31:'@',32:'#',33:'$',34:'%',35:'^',36:'&',37:'*',38:'(',39:')',
    40:'\r',42:'\x08',43:'\t',44:' ',
    45:'_',46:'+',47:'{',48:'}',49:'|',51:':',52:'"',53:'~',54:'<',55:'>',56:'?',
}

_buf       = []        # buffered characters for read_char()
_prev_keys = set()     # last seen keycodes (key-repeat suppression)
_state     = "idle"
_conn_h    = None
_svc_range = [None, None]  # [start_handle, end_handle]
_char_hs   = []        # Report characteristic value handles
_target    = None      # (addr_type, addr_bytes) of keyboard to connect to
_ble       = None


def _has_hid(adv_data):
    """Return True if advertisement data contains 16-bit UUID 0x1812 (HID)."""
    i, n = 0, len(adv_data)
    while i < n:
        ln = adv_data[i]
        if ln == 0 or i + ln >= n:
            break
        t = adv_data[i + 1]
        if t in (0x02, 0x03):  # incomplete/complete 16-bit UUID list
            for j in range(i + 2, i + 1 + ln, 2):
                if j + 1 < n and (adv_data[j] | (adv_data[j + 1] << 8)) == 0x1812:
                    return True
        i += ln + 1
    return False


def _irq(event, data):
    global _state, _conn_h, _target, _prev_keys

    if event == _IRQ_SCAN_RESULT:
        addr_type, addr, adv_type, rssi, adv_data = data
        if _state == "scanning" and _has_hid(bytes(adv_data)):
            _target = (addr_type, bytes(addr))
            _state  = "found"
            _ble.gap_scan(None)  # stop scan immediately

    elif event == _IRQ_SCAN_DONE:
        if _state == "found" and _target:
            _state = "connecting"
            print("BLE kbd: connecting...")
            _ble.gap_connect(_target[0], _target[1])
        elif _state not in ("connecting", "ready"):
            _state = "idle"

    elif event == _IRQ_PERIPHERAL_CONNECT:
        conn_h, addr_type, addr = data
        _conn_h = conn_h
        _svc_range[0] = _svc_range[1] = None
        _char_hs.clear()
        _state = "discovering"
        _ble.gattc_discover_services(conn_h)

    elif event == _IRQ_GATTC_SERVICE_RESULT:
        conn_h, start, end, uuid = data
        if uuid == _HID_SVC:
            _svc_range[0], _svc_range[1] = start, end

    elif event == _IRQ_GATTC_SERVICE_DONE:
        conn_h, status = data
        if _svc_range[0] is not None:
            _ble.gattc_discover_characteristics(conn_h, _svc_range[0], _svc_range[1])

    elif event == _IRQ_GATTC_CHAR_RESULT:
        conn_h, def_h, val_h, props, uuid = data
        if uuid == _HID_RPT:
            _char_hs.append(val_h)

    elif event == _IRQ_GATTC_CHAR_DONE:
        conn_h, status = data
        if _char_hs:
            for vh in _char_hs:
                # Write 0x0001 to CCCD (val_h + 1) to enable notifications
                _ble.gattc_write(conn_h, vh + 1, b'\x01\x00', 1)
            _state = "ready"
            print("BLE kbd: ready")

    elif event == _IRQ_GATTC_NOTIFY:
        conn_h, val_h, rpt = data
        r = bytes(rpt)
        if len(r) < 3:
            return
        shift    = bool(r[0] & 0x22)          # left/right shift modifier bits
        cur_keys = set(r[2:8]) - {0}
        new_keys = cur_keys - _prev_keys       # only fire on key-down
        _prev_keys.clear()
        _prev_keys.update(cur_keys)
        keymap = _SMAP if shift else _MAP
        for kc in new_keys:
            ch = keymap.get(kc)
            if ch:
                _buf.append(ch)

    elif event == _IRQ_PERIPHERAL_DISCONNECT:
        _conn_h = None
        _char_hs.clear()
        _svc_range[0] = _svc_range[1] = None
        _prev_keys.clear()
        print("BLE kbd: disconnected, rescanning")
        _state = "idle"
        scan()


def init():
    global _ble
    _ble = bluetooth.BLE()
    _ble.active(True)
    _ble.irq(_irq)
    scan()


def scan():
    global _state
    if _state != "idle":
        return
    _state = "scanning"
    print("BLE kbd: scanning...")
    # duration=0 = indefinite, interval/window in µs, active=True for scan requests
    _ble.gap_scan(0, 30000, 30000, True)


def read_char():
    """Return the next buffered character, or None if buffer is empty."""
    return _buf.pop(0) if _buf else None


def status():
    return _state
