# -*- coding: utf-8 -*-
# ============================================================
#  PORT BOT CHILIAD v3.0
#  Автоматизация работы «Порт» на СОБСТВЕННОМ / тестовом сервере.
#  Работает через захват экрана (OpenCV) и ввод клавиш/мыши (SendInput).
#  Нет инжекта в процесс, нет чтения памяти, нет обхода античита.
# ============================================================

import json, os, sys, re, threading, time, queue, csv, traceback, ctypes, random, math
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

IS_WIN = sys.platform == "win32"

try:
    import cv2
    import numpy as np
    import mss
    from PIL import Image, ImageTk
    IMPORT_ERROR = None
except Exception as _e:                      # noqa
    cv2 = np = mss = Image = ImageTk = None
    IMPORT_ERROR = str(_e)

# ── пути (рядом с EXE, а не во временной папке PyInstaller) ──
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "session_log.csv")
TPL_DIR  = os.path.join(APP_DIR, "templates")

# ╔══════════════════════════════════════════════════════════╗
# ║                        КОНФИГ                           ║
# ╚══════════════════════════════════════════════════════════╝
DEFAULTS = {
    "screen": {"left": 0, "top": 0, "width": 1920, "height": 1080},
    "game_window_title": "",          # подстрока заголовка окна игры; пусто = не проверять

    # красный маркер (два диапазона H, т.к. красный на границе круга)
    "marker_hsv_low":   [0,   120,  80],
    "marker_hsv_high":  [12,  255, 255],
    "marker_hsv_low2":  [168, 120,  80],
    "marker_hsv_high2": [180, 255, 255],
    "marker_min_area": 150,
    "close_area":      3000,
    "arrive_on_vanish": True,         # метка пропала вблизи = дошли
    "vanish_ratio":     0.5,

    # движение
    "steer_mode": "camera",           # camera = поворот мышью, keys = A/D
    "camera_gain": 0.06,
    "camera_max_step": 40,
    "horizontal_deadzone": 40,
    "camera_search": True,            # крутить камеру, если метки не видно
    "search_turn_px": 35,
    "search_timeout": 14.0,
    "approach_key": "w", "sprint_key": "shift",
    "left_key": "a", "right_key": "d", "back_key": "s",
    "jump_key": "space", "interact_key": "e",

    # анти-застревание
    "stuck": {"enabled": True, "check_interval": 0.4, "diff_threshold": 1.0,
              "seconds": 1.6, "max_consecutive": 6},

    # мини-игра
    "minigame": {
        "enabled": True,
        "roi": [580, 340, 760, 280],
        "green_low":  [35, 60, 60],   "green_high": [90, 255, 255],
        "white_low":  [0, 0, 200],    "white_high": [180, 40, 255],
        "zone_close_px": 25,          # склейка зоны, если ползунок её перекрыл
        "min_zone_px": 8,
        "min_zone_area": 30,
        "min_slider_area": 6,
        "confirm_frames": 2,
        "action_key": "e",
        "timeout_seconds": 18
    },
    "predict_latency_ms": 30,
    "minigame_start_timeout": 4.0,
    "minigame_start_retries": 2,
    "pickup_retries": 3,

    # тайминги
    "pickup_anim_wait": 2.5, "delivery_anim_wait": 1.8,
    "interaction_cooldown": 1.8,
    "start_delay_seconds": 5,         # время переключиться в окно игры
    "max_run_minutes": 120,
    "max_fail_streak": 5,
    "idle_watchdog_minutes": 4,

    # автостарт смены (шаги: key / wait / click_template / click_xy)
    "start_shift": {"enabled": False, "steps": [
        {"type": "key", "key": "e"},
        {"type": "wait", "sec": 1.2},
        {"type": "click_template", "name": "start_shift.png", "timeout": 6, "required": False}
    ]},
    # слежение за концом смены по шаблону templates/shift_end.png
    "watch": {"shift_end_template": "shift_end.png", "check_interval": 3.0},
    "template_threshold": 0.82,

    "pay_per_box": 11.0,
    "hotkey_start": "f6", "hotkey_stop": "f7",
    "csv_log": True
}

def _deep_merge(dst, src):
    for k, v in src.items():
        if k not in dst:
            dst[k] = json.loads(json.dumps(v))
        elif isinstance(v, dict) and isinstance(dst[k], dict):
            _deep_merge(dst[k], v)

def save_config(cfg):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def load_config():
    data = None
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            try: os.replace(CFG_PATH, CFG_PATH + ".broken")
            except Exception: pass
    if data is None:
        data = {}
    _deep_merge(data, DEFAULTS)
    if not os.path.exists(CFG_PATH):
        save_config(data)
    return data

def cfg_get(cfg, path):
    cur = cfg
    for p in path.split("."):
        cur = cur[p]
    return cur

def cfg_set(cfg, path, value):
    parts = path.split(".")
    cur = cfg
    for p in parts[:-1]:
        cur = cur[p]
    cur[parts[-1]] = value

# ╔══════════════════════════════════════════════════════════╗
# ║           ВВОД: SendInput (скан-коды) + мышь            ║
# ╚══════════════════════════════════════════════════════════╝
if IS_WIN:
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    try: user32.SetProcessDPIAware()
    except Exception: pass
    ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]
    class _IU(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _IU)]

KEYEVENTF_EXT, KEYEVENTF_UP, KEYEVENTF_SCAN = 0x0001, 0x0002, 0x0008
MOUSEEVENTF_MOVE, MOUSEEVENTF_LDOWN, MOUSEEVENTF_LUP = 0x0001, 0x0002, 0x0004

def _build_scancodes():
    sc = {}
    for i, ch in enumerate("1234567890"): sc[ch] = 0x02 + i
    for i, ch in enumerate("qwertyuiop"): sc[ch] = 0x10 + i
    for i, ch in enumerate("asdfghjkl"):  sc[ch] = 0x1E + i
    for i, ch in enumerate("zxcvbnm"):    sc[ch] = 0x2C + i
    sc.update({"esc": 0x01, "backspace": 0x0E, "tab": 0x0F, "enter": 0x1C,
               "ctrl": 0x1D, "shift": 0x2A, "alt": 0x38, "space": 0x39,
               "up": 0x48, "left": 0x4B, "right": 0x4D, "down": 0x50})
    for i in range(10): sc["f%d" % (i + 1)] = 0x3B + i
    sc["f11"], sc["f12"] = 0x57, 0x58
    return sc
SCANCODES = _build_scancodes()
EXTENDED  = {"up", "left", "right", "down"}

def vk_for(name):
    m = re.fullmatch(r"f(\d{1,2})", str(name).strip().lower())
    if m and 1 <= int(m.group(1)) <= 12:
        return 0x70 + int(m.group(1)) - 1
    return None

def key_pressed(vk):
    if not IS_WIN or not vk:
        return False
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)

def foreground_title():
    if not IS_WIN:
        return ""
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value

class Keys:
    """Ввод через SendInput со скан-кодами (игры игнорируют VK-события)."""
    def __init__(self):
        self.held = set()
        self.sent = []            # журнал для тестов на не-Windows

    def _send(self, kind, *args):
        if not IS_WIN:
            self.sent.append((kind,) + args)
            return
        if kind == "key":
            key, up = args
            inp = INPUT(type=1)
            flags = KEYEVENTF_SCAN | (KEYEVENTF_UP if up else 0)
            if key in EXTENDED: flags |= KEYEVENTF_EXT
            inp.u.ki = KEYBDINPUT(0, SCANCODES[key], flags, 0, 0)
        else:
            dx, dy, flags = args
            inp = INPUT(type=0)
            inp.u.mi = MOUSEINPUT(dx, dy, 0, flags, 0, 0)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def _norm(self, key):
        k = str(key).strip().lower()
        if k not in SCANCODES:
            raise ValueError("Неизвестная клавиша: %r" % key)
        return k

    def down(self, key):
        k = self._norm(key)
        if k not in self.held:
            self._send("key", k, False); self.held.add(k)

    def up(self, key):
        k = self._norm(key)
        if k in self.held:
            self._send("key", k, True); self.held.discard(k)

    def tap(self, key, hold=0.07):
        k = self._norm(key)
        self._send("key", k, False); time.sleep(hold); self._send("key", k, True)
        self.held.discard(k); time.sleep(0.04)

    def release_all(self):
        for k in list(self.held):
            try: self._send("key", k, True)
            except Exception: pass
            self.held.discard(k)

    def mouse_rel(self, dx, dy=0):
        self._send("mouse", int(dx), int(dy), MOUSEEVENTF_MOVE)

    def click(self, x, y):
        if IS_WIN:
            user32.SetCursorPos(int(x), int(y)); time.sleep(0.05)
        self._send("mouse", 0, 0, MOUSEEVENTF_LDOWN); time.sleep(0.05)
        self._send("mouse", 0, 0, MOUSEEVENTF_LUP)

# ╔══════════════════════════════════════════════════════════╗
# ║               КОМПЬЮТЕРНОЕ ЗРЕНИЕ (функции)             ║
# ╚══════════════════════════════════════════════════════════╝
def capture_screen(cfg):
    s = cfg["screen"]
    with mss.mss() as sct:
        shot = sct.grab({"left": int(s["left"]), "top": int(s["top"]),
                         "width": int(s["width"]), "height": int(s["height"])})
    return np.ascontiguousarray(np.asarray(shot)[:, :, :3])

def find_marker(frame, cfg):
    """(cx, cy, area) крупнейшего красного пятна или None."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, np.array(cfg["marker_hsv_low"]),  np.array(cfg["marker_hsv_high"]))
    m2 = cv2.inRange(hsv, np.array(cfg["marker_hsv_low2"]), np.array(cfg["marker_hsv_high2"]))
    mask = cv2.bitwise_or(m1, m2)
    k = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = [c for c in cnts if cv2.contourArea(c) >= float(cfg["marker_min_area"])]
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    M = cv2.moments(c)
    if M["m00"] == 0:
        return None
    return int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"]), float(cv2.contourArea(c))

def _largest(mask, min_area):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = [c for c in cnts if cv2.contourArea(c) >= min_area]
    return max(cnts, key=cv2.contourArea) if cnts else None

def mg_analyze(roi, mg):
    """(slider_x, zone_lo, zone_hi) или None, если UI мини-игры не найден."""
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    g = cv2.inRange(hsv, np.array(mg["green_low"]), np.array(mg["green_high"]))
    w = cv2.inRange(hsv, np.array(mg["white_low"]), np.array(mg["white_high"]))
    cw = max(1, int(mg.get("zone_close_px", 25)))
    w = cv2.morphologyEx(w, cv2.MORPH_CLOSE, np.ones((3, cw), np.uint8))
    gc = _largest(g, float(mg.get("min_slider_area", 6)))
    wc = _largest(w, float(mg.get("min_zone_area", 30)))
    if gc is None or wc is None:
        return None
    gx, _, gw, _ = cv2.boundingRect(gc)
    wx, _, ww, _ = cv2.boundingRect(wc)
    if ww < int(mg.get("min_zone_px", 8)):
        return None
    return gx + gw / 2.0, float(wx), float(wx + ww)

_TPL_CACHE = {}
def load_template(name):
    if name in _TPL_CACHE:
        return _TPL_CACHE[name]
    path = os.path.join(TPL_DIR, name)
    img = None
    if os.path.exists(path):
        try: img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_GRAYSCALE)
        except Exception: img = None
    _TPL_CACHE[name] = img
    return img

def save_template(img_bgr, name):
    os.makedirs(TPL_DIR, exist_ok=True)
    ok, buf = cv2.imencode(".png", img_bgr)
    if not ok:
        raise RuntimeError("не удалось закодировать PNG")
    buf.tofile(os.path.join(TPL_DIR, name))
    _TPL_CACHE.pop(name, None)

def find_template(frame, name, thr):
    tpl = load_template(name)
    if tpl is None:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if tpl.shape[0] > gray.shape[0] or tpl.shape[1] > gray.shape[1]:
        return None
    res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    if mx >= thr:
        return loc[0] + tpl.shape[1] // 2, loc[1] + tpl.shape[0] // 2, float(mx)
    return None

# ── калибровка цвета по точке ──
def sample_hsv(frame, x, y, r=3):
    h, w = frame.shape[:2]
    patch = frame[max(0, y - r):min(h, y + r + 1), max(0, x - r):min(w, x + r + 1)]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(int)
    hs = hsv[:, 0]
    if (hs > 150).any() and (hs < 30).any():          # красный на границе круга
        hs = np.where(hs > 90, hs - 180, hs)
    return [int(np.median(hs)) % 180, int(np.median(hsv[:, 1])), int(np.median(hsv[:, 2]))]

def marker_ranges(h, s, v):
    smin, vmin = max(60, s - 90), max(50, v - 110)
    if h <= 12 or h >= 168:
        return [0, smin, vmin], [12, 255, 255], [168, smin, vmin], [180, 255, 255]
    return [max(0, h - 10), smin, vmin], [min(179, h + 10), 255, 255], [181, 0, 0], [181, 0, 0]

def hue_range(h, s, v, tol=14):
    return [max(0, h - tol), max(40, s - 90), max(40, v - 100)], [min(179, h + tol), 255, 255]

def zone_range(h, s, v):
    if s < 60:                                         # белая/светлая зона
        return [0, 0, max(120, v - 60)], [179, min(255, s + 50), 255]
    return hue_range(h, s, v)

# ╔══════════════════════════════════════════════════════════╗
# ║                       CSV-ЛОГ                           ║
# ╚══════════════════════════════════════════════════════════╝
class CsvLogger:
    HEADER = ["timestamp", "event", "boxes", "earned", "errors", "detail"]
    def __init__(self, enabled):
        self.enabled = enabled
        self._lock = threading.Lock()
        if enabled and not os.path.exists(LOG_PATH):
            try:
                with open(LOG_PATH, "w", newline="", encoding="utf-8-sig") as f:
                    csv.writer(f).writerow(self.HEADER)
            except Exception:
                self.enabled = False

    def write(self, event, boxes, earned, errors, detail=""):
        if not self.enabled:
            return
        row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), event, boxes,
               "%.2f" % earned, errors, detail]
        try:
            with self._lock, open(LOG_PATH, "a", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(row)
        except Exception:
            pass

# ╔══════════════════════════════════════════════════════════╗
# ║                      ЯДРО БОТА (FSM)                    ║
# ╚══════════════════════════════════════════════════════════╝
class PortBot:
    """
    FIND_PICKUP → APPROACH_PICKUP → WAIT_PICKUP_ANIM → FIND_DELIVERY →
    APPROACH_DELIVERY → WAIT_MINIGAME_START → MINIGAME → WAIT_DELIVERY_ANIM → …
    Любая ошибка → CYCLE_FAILED → FIND_PICKUP (без остановки программы).
    """
    STATES = {
        "IDLE": "⏸ Ожидание", "FIND_PICKUP": "🔍 Поиск метки подбора",
        "APPROACH_PICKUP": "🏃 Иду к коробке", "WAIT_PICKUP_ANIM": "📦 Подбор",
        "FIND_DELIVERY": "🔍 Поиск точки сдачи", "APPROACH_DELIVERY": "🏃 Иду к точке сдачи",
        "WAIT_MINIGAME_START": "⏳ Жду мини-игру", "MINIGAME": "🎯 Мини-игра",
        "WAIT_DELIVERY_ANIM": "✅ Сдача", "CYCLE_FAILED": "⚠️ Сбой — повтор",
    }

    def __init__(self, emit):
        self.emit = emit
        self.stop_ev = threading.Event()
        self._thread = None
        self.running = False
        self.boxes = self.errors = self.stuck_events = 0
        self.started = None
        self.cfg = load_config()
        self.logger = CsvLogger(self.cfg.get("csv_log", True))
        self.inp = Keys()
        self._state = "IDLE"
        self._sct = None
        self._vk_stop = None
        self._reset_runtime()
        self._handlers = {
            "FIND_PICKUP": self._st_find_pickup, "APPROACH_PICKUP": self._st_approach_pickup,
            "WAIT_PICKUP_ANIM": self._st_wait_pickup_anim, "FIND_DELIVERY": self._st_find_delivery,
            "APPROACH_DELIVERY": self._st_approach_delivery,
            "WAIT_MINIGAME_START": self._st_wait_minigame_start, "MINIGAME": self._st_minigame,
            "WAIT_DELIVERY_ANIM": self._st_wait_delivery_anim, "CYCLE_FAILED": self._st_cycle_failed,
        }

    # ── служебное ──
    def _reset_runtime(self):
        now = time.time()
        self._state_t0 = now; self._last_seen = 0.0; self._last_area = 0.0
        self._lost_t0 = None; self._prev_small = None; self._stuck_since = None
        self._stuck_check_t = 0.0; self._stuck_streak = 0; self._fail_streak = 0
        self._tries = 0; self._last_interact = 0.0; self._last_steer = 0.0
        self._last_progress = now; self._watchdog_hits = 0; self._watch_t = 0.0
        self._wrong_window = False

    def _say(self, msg):
        self.emit(("log", "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)))

    def _earned(self):
        return self.boxes * float(self.cfg["pay_per_box"])

    def _emit_stats(self):
        self.emit(("stats", self.boxes, self._earned(), self.errors, self.stuck_events, self.started))

    def _enter(self, s):
        self._state = s
        self._state_t0 = time.time()
        self._prev_small = None; self._stuck_since = None
        self._lost_t0 = None; self._last_area = 0.0
        self.emit(("state", self.STATES.get(s, s)))

    def _emergency(self):
        if self.stop_ev.is_set():
            return True
        if self._vk_stop and key_pressed(self._vk_stop):
            self._say("🛑 Аварийная остановка клавишей")
            self.stop_ev.set()
            return True
        return False

    def _wait(self, seconds, step=0.03):
        end = time.time() + seconds
        while time.time() < end:
            if self._emergency():
                return
            time.sleep(step)

    def _grab(self, region=None):
        if region is None:
            s = self.cfg["screen"]
            region = {"left": int(s["left"]), "top": int(s["top"]),
                      "width": int(s["width"]), "height": int(s["height"])}
        return np.ascontiguousarray(np.asarray(self._sct.grab(region))[:, :, :3])

    def _interact(self):
        self.inp.release_all()
        self.inp.tap(self.cfg["interact_key"])
        self._last_interact = time.time()

    def _fail(self, reason):
        self.errors += 1; self._fail_streak += 1
        self.inp.release_all()
        self._emit_stats()
        self.logger.write("FAIL", self.boxes, self._earned(), self.errors, reason)
        self._say("⚠️ %s (ошибок: %d)" % (reason, self.errors))
        self._enter("CYCLE_FAILED")

    # ── шаги автостарта / шаблоны ──
    def _click_template(self, name, timeout, required):
        thr = float(self.cfg["template_threshold"])
        s = self.cfg["screen"]
        end = time.time() + timeout
        while time.time() < end and not self._emergency():
            m = find_template(self._grab(), name, thr)
            if m:
                self.inp.click(int(s["left"]) + m[0], int(s["top"]) + m[1])
                self._say("🖱 Нажал кнопку по шаблону %s" % name)
                return True
            time.sleep(0.25)
        if required:
            self._say("⚠️ Шаблон %s не найден за %.0f с" % (name, timeout))
        return False

    def _run_steps(self, steps):
        for st in steps:
            if self._emergency():
                return False
            t = st.get("type")
            try:
                if t == "key":
                    self.inp.tap(st["key"], float(st.get("hold", 0.08)))
                elif t == "wait":
                    self._wait(float(st.get("sec", 1.0)))
                elif t == "click_template":
                    ok = self._click_template(st["name"], float(st.get("timeout", 8)), bool(st.get("required", True)))
                    if not ok and st.get("required", True):
                        return False
                elif t == "click_xy":
                    s = self.cfg["screen"]
                    self.inp.click(int(s["left"]) + int(st["x"]), int(s["top"]) + int(st["y"]))
                else:
                    self._say("⚠️ Неизвестный шаг автостарта: %r" % (t,))
            except Exception as e:
                self._say("⚠️ Шаг автостарта не выполнен: %s" % e)
                return False
        return True

    def _check_watch(self):
        w = self.cfg.get("watch", {})
        name = w.get("shift_end_template")
        now = time.time()
        if not name or now - self._watch_t < float(w.get("check_interval", 3.0)):
            return
        self._watch_t = now
        if load_template(name) is None:
            return
        if find_template(self._grab(), name, float(self.cfg["template_threshold"])):
            self.inp.release_all()
            self._say("🏁 Смена закончилась (шаблон найден)")
            self.logger.write("SHIFT_END", self.boxes, self._earned(), self.errors)
            ss = self.cfg["start_shift"]
            if ss.get("enabled") and self._run_steps(ss["steps"]):
                self._say("🚀 Новая смена начата автоматически")
                self._last_progress = time.time()
                self._enter("FIND_PICKUP")
            else:
                self.stop_ev.set()

    # ── движение ──
    def _search_turn(self):
        self.inp.release_all()
        if self.cfg.get("camera_search", True):
            self.inp.mouse_rel(int(self.cfg["search_turn_px"]), 0)
        self._wait(0.06)

    def _steer_and_move(self, frame, marker):
        c, inp = self.cfg, self.inp
        cx, _, area = marker
        dx = cx - frame.shape[1] / 2.0
        dead = float(c["horizontal_deadzone"])
        inp.down(c["sprint_key"]); inp.down(c["approach_key"])
        if c.get("steer_mode", "camera") == "camera":
            inp.up(c["left_key"]); inp.up(c["right_key"])
            now = time.time()
            if abs(dx) > dead and now - self._last_steer >= 0.04:
                m = int(c["camera_max_step"])
                step = int(max(-m, min(m, dx * float(c["camera_gain"]))))
                if step == 0:
                    step = 1 if dx > 0 else -1
                inp.mouse_rel(step, 0)
                self._last_steer = now
        else:
            if dx < -dead:   inp.up(c["right_key"]); inp.down(c["left_key"])
            elif dx > dead:  inp.up(c["left_key"]);  inp.down(c["right_key"])
            else:            inp.up(c["left_key"]);  inp.up(c["right_key"])
        return area >= float(c["close_area"])

    def _check_stuck(self, frame):
        s = self.cfg["stuck"]
        if not s.get("enabled", True):
            return False
        now = time.time()
        if now - self._stuck_check_t < float(s["check_interval"]):
            return False
        self._stuck_check_t = now
        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 36),
                           interpolation=cv2.INTER_AREA).astype(np.int16)
        prev, self._prev_small = self._prev_small, small
        if prev is None:
            return False
        if float(np.mean(np.abs(small - prev))) < float(s["diff_threshold"]):
            if self._stuck_since is None:
                self._stuck_since = now
            elif now - self._stuck_since >= float(s["seconds"]):
                self._stuck_since = None
                return True
        else:
            self._stuck_since = None
        return False

    def _unstick(self):
        c, inp = self.cfg, self.inp
        self.stuck_events += 1; self._stuck_streak += 1
        self._emit_stats()
        self._say("🧱 Застрял — манёвр обхода #%d" % self._stuck_streak)
        self.logger.write("STUCK", self.boxes, self._earned(), self.errors, str(self._stuck_streak))
        if self._stuck_streak > int(c["stuck"]["max_consecutive"]):
            self._fail("не могу обойти препятствие")
            return
        inp.release_all()
        side = random.choice([c["left_key"], c["right_key"]])
        inp.down(c["back_key"]); self._wait(0.45); inp.up(c["back_key"])
        if c.get("steer_mode", "camera") == "camera":
            inp.mouse_rel(random.choice([-130, 130]), 0)
        inp.down(side); inp.down(c["approach_key"]); inp.down(c["sprint_key"])
        self._wait(0.35); inp.tap(c["jump_key"], 0.08); self._wait(0.5)
        inp.release_all()
        self._prev_small = None; self._stuck_since = None

    def _approach(self, frame, marker, on_arrive):
        c, now = self.cfg, time.time()
        if marker is None:
            if self._lost_t0 is None:
                self._lost_t0 = now
            lost = now - self._lost_t0
            if (c.get("arrive_on_vanish", True) and lost >= 0.35 and now - self._last_seen < 1.5
                    and self._last_area >= float(c["close_area"]) * float(c["vanish_ratio"])):
                self._say("📍 Метка исчезла вблизи — считаю, что дошёл")
                self._last_area = 0.0
                on_arrive(); return
            if lost < 0.35:
                return                                   # метка мигает — держим курс
            if lost > float(c["search_timeout"]):
                self._fail("метка потеряна"); return
            self._search_turn(); return
        self._lost_t0 = None
        self._last_seen = now
        self._last_area = marker[2]
        arrived = self._steer_and_move(frame, marker)
        if arrived and now - self._last_interact > float(c["interaction_cooldown"]):
            on_arrive(); return
        if self._check_stuck(frame):
            self._unstick()

    # ── мини-игра ──
    def _mg_region(self):
        s = self.cfg["screen"]
        x, y, w, h = [int(v) for v in self.cfg["minigame"]["roi"]]
        return {"left": int(s["left"]) + x, "top": int(s["top"]) + y, "width": w, "height": h}

    def _minigame_visible(self):
        return mg_analyze(self._grab(self._mg_region()), self.cfg["minigame"]) is not None

    def _play_minigame(self):
        c = self.cfg
        mg = c["minigame"]
        if not mg["enabled"]:
            self._interact(); return True
        region = self._mg_region()
        need = max(1, int(mg.get("confirm_frames", 2)))
        key = mg["action_key"]
        t_out = float(mg["timeout_seconds"])
        lat = float(c.get("predict_latency_ms", 30)) / 1000.0
        t0 = time.time(); streak = 0; hist = []; gone = None; seen = False
        while not self._emergency():
            now = time.time()
            if now - t0 > t_out:
                self._say("⏱ Мини-игра: тайм-аут"); return False
            r = mg_analyze(self._grab(region), mg)
            if r is None:
                streak = 0; hist = []
                if seen:
                    gone = gone or now
                    if now - gone > 0.8:
                        self._say("Мини-игра закрылась без моего нажатия"); return False
                time.sleep(0.01); continue
            seen = True; gone = None
            sx, lo, hi = r
            hist = (hist + [(now, sx)])[-4:]
            px = sx
            if len(hist) >= 2 and hist[-1][0] > hist[0][0]:
                v = (hist[-1][1] - hist[0][1]) / (hist[-1][0] - hist[0][0])
                px = sx + v * lat
            if lo <= px <= hi:
                streak += 1
                if streak >= need:
                    self.inp.release_all()
                    self.inp.tap(key, 0.05)
                    time.sleep(0.4)
                    return True
            else:
                streak = 0
            time.sleep(0.004)
        return False

    # ── обработчики состояний ──
    def _find(self, marker, nxt):
        if marker is not None:
            self.inp.release_all()
            self._stuck_streak = 0
            self._enter(nxt); return
        if time.time() - self._state_t0 > float(self.cfg["search_timeout"]):
            self._fail("метка не найдена"); return
        self._search_turn()

    def _st_find_pickup(self, frame, marker):   self._find(marker, "APPROACH_PICKUP")
    def _st_find_delivery(self, frame, marker): self._find(marker, "APPROACH_DELIVERY")

    def _arrive_pickup(self):
        self._interact(); self._tries = 1
        self._say("📦 Беру коробку…")
        self._enter("WAIT_PICKUP_ANIM")

    def _arrive_delivery(self):
        self._interact(); self._tries = 1
        self._say("📍 У точки сдачи, жду мини-игру…")
        self._enter("WAIT_MINIGAME_START")

    def _st_approach_pickup(self, frame, marker):
        self._approach(frame, marker, self._arrive_pickup)

    def _st_approach_delivery(self, frame, marker):
        self._approach(frame, marker, self._arrive_delivery)

    def _st_wait_pickup_anim(self, frame, marker):
        c = self.cfg
        self.inp.release_all()
        self._wait(float(c["pickup_anim_wait"]))
        if self.stop_ev.is_set():
            return
        m = find_marker(self._grab(), c)
        if m and m[2] >= float(c["close_area"]) * 0.6:           # метка на месте = не взял
            if self._tries < int(c["pickup_retries"]):
                self._tries += 1
                self._say("🔁 Коробка не взята, повтор %d/%s" % (self._tries, c["pickup_retries"]))
                self._interact(); return
            self._fail("не удалось подобрать коробку"); return
        self._say("🔍 Ищу точку сдачи…")
        self._enter("FIND_DELIVERY")

    def _st_wait_minigame_start(self, frame, marker):
        c = self.cfg
        if not c["minigame"]["enabled"] or self._minigame_visible():
            self._enter("MINIGAME"); return
        if time.time() - self._state_t0 > float(c["minigame_start_timeout"]):
            if self._tries <= int(c["minigame_start_retries"]):
                self._tries += 1
                self._say("🔁 Мини-игра не появилась, повторяю E (%d)" % self._tries)
                self._interact(); self._state_t0 = time.time(); return
            self._fail("мини-игра не появилась"); return
        self._wait(0.05)

    def _st_minigame(self, frame, marker):
        ok = self._play_minigame()
        if self.stop_ev.is_set():
            return
        if ok:
            self.boxes += 1; self._fail_streak = 0; self._stuck_streak = 0
            self._watchdog_hits = 0; self._last_progress = time.time()
            self._emit_stats()
            self.logger.write("BOX_OK", self.boxes, self._earned(), self.errors)
            self._say("✅ Цикл #%d. +$%.0f | итого $%.2f" % (self.boxes, float(self.cfg["pay_per_box"]), self._earned()))
            self._enter("WAIT_DELIVERY_ANIM")
        else:
            self._fail("мини-игра не пройдена")

    def _st_wait_delivery_anim(self, frame, marker):
        self.inp.release_all()
        self._wait(float(self.cfg["delivery_anim_wait"]))
        if not self.stop_ev.is_set():
            self._enter("FIND_PICKUP")

    def _st_cycle_failed(self, frame, marker):
        self.inp.release_all()
        if self._fail_streak >= int(self.cfg["max_fail_streak"]):
            self._say("🛑 %d сбоев подряд — останавливаюсь" % self._fail_streak)
            self.logger.write("AUTO_STOP", self.boxes, self._earned(), self.errors, "fail streak")
            self.stop_ev.set(); return
        self._wait(2.0)
        if not self.stop_ev.is_set():
            self._enter("FIND_PICKUP")

    def _watchdog(self):
        c = self.cfg
        self._watchdog_hits += 1
        self.inp.release_all()
        self._say("🐕 Нет прогресса %.0f мин — перезапуск цикла (%d/3)" % (float(c["idle_watchdog_minutes"]), self._watchdog_hits))
        self.logger.write("WATCHDOG", self.boxes, self._earned(), self.errors, str(self._watchdog_hits))
        self._last_progress = time.time()
        if self._watchdog_hits >= 3:
            self._say("🛑 Watchdog сработал 3 раза — остановка")
            self.stop_ev.set(); return
        self._enter("FIND_PICKUP")

    # ── главный цикл ──
    def _run_loop(self):
        self.running = True
        self.started = time.time()
        self.boxes = self.errors = self.stuck_events = 0
        self._reset_runtime()
        c = self.cfg
        self._vk_stop = vk_for(c.get("hotkey_stop", "f7"))
        try:
            self._sct = mss.mss()
            self.logger.write("SESSION_START", 0, 0.0, 0)
            self._emit_stats()
            delay = int(c.get("start_delay_seconds", 5))
            if delay > 0:
                self._say("🟢 Старт через %d с — переключись в окно игры" % delay)
                self._wait(delay)
            ss = c["start_shift"]
            if ss.get("enabled") and not self.stop_ev.is_set():
                self._say("🚀 Автостарт смены…")
                if not self._run_steps(ss["steps"]):
                    self._say("⚠️ Автостарт не завершён — продолжаю как есть")
            self._enter("FIND_PICKUP")
            title = str(c.get("game_window_title", "")).strip().lower()
            while not self._emergency():
                if (time.time() - self.started) / 60.0 >= float(c["max_run_minutes"]):
                    self._say("⏱ Достигнут лимит %s мин" % c["max_run_minutes"]); break
                if title:
                    wrong = title not in foreground_title().lower()
                    if wrong:
                        self.inp.release_all()
                        if not self._wrong_window:
                            self._say("⏸ Окно игры не в фокусе — пауза"); self._wrong_window = True
                        time.sleep(0.3); continue
                    if self._wrong_window:
                        self._say("▶ Окно игры в фокусе — продолжаю"); self._wrong_window = False
                        self._last_progress = time.time()
                if time.time() - self._last_progress > float(c["idle_watchdog_minutes"]) * 60:
                    self._watchdog(); continue
                self._check_watch()
                frame = self._grab()
                marker = find_marker(frame, c)
                handler = self._handlers.get(self._state)
                if handler:
                    handler(frame, marker)
                time.sleep(0.02)
        except Exception as e:
            self.errors += 1
            self._say("💥 Ошибка: %s\n%s" % (e, traceback.format_exc()))
            self.logger.write("CRASH", self.boxes, self._earned(), self.errors, str(e))
        finally:
            self.inp.release_all()
            try:
                if self._sct: self._sct.close()
            except Exception: pass
            self.running = False
            self.logger.write("SESSION_END", self.boxes, self._earned(), self.errors,
                              "elapsed=%ds" % int(time.time() - (self.started or time.time())))
            self._emit_stats()
            self._state = "IDLE"
            self.emit(("state", self.STATES["IDLE"]))
            self.emit(("stopped",))
            self._say("🔴 Остановлен. Коробок: %d, $%.2f, ошибок: %d, застреваний: %d"
                      % (self.boxes, self._earned(), self.errors, self.stuck_events))

    def start(self):
        if self.running:
            return
        self.cfg = load_config()
        self.logger = CsvLogger(self.cfg.get("csv_log", True))
        self.stop_ev.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.stop_ev.set()
        self.inp.release_all()

# ╔══════════════════════════════════════════════════════════╗
# ║          ВЫБОР ОБЛАСТИ / ЦВЕТА НА СНИМКЕ ЭКРАНА         ║
# ╚══════════════════════════════════════════════════════════╝
class ScreenPicker(tk.Toplevel):
    """mode: 'rect' — выделить рамку, 'point' — клик, 'view' — просто показать."""
    def __init__(self, master, img_bgr, mode, prompt, on_done):
        super().__init__(master)
        self.mode, self.on_done = mode, on_done
        h, w = img_bgr.shape[:2]
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.scale = min(1.0, sw / float(w), sh / float(h))
        dw, dh = int(w * self.scale), int(h * self.scale)
        self.overrideredirect(True)
        self.geometry("%dx%d+0+0" % (dw, dh))
        self.attributes("-topmost", True)
        pil = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
        if self.scale != 1.0:
            pil = pil.resize((dw, dh))
        self.photo = ImageTk.PhotoImage(pil)
        self.cv = tk.Canvas(self, width=dw, height=dh, highlightthickness=0,
                            cursor="arrow" if mode == "view" else "crosshair")
        self.cv.pack()
        self.cv.create_image(0, 0, image=self.photo, anchor="nw")
        self.cv.create_rectangle(0, 0, dw, 34, fill="#101018", outline="")
        self.cv.create_text(10, 17, anchor="w", fill="#ffffff", font=("Segoe UI", 12, "bold"),
                            text=prompt + "   [Esc / ПКМ — отмена]")
        self._p0 = None; self._rect = None; self._done = False
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Button-3>", lambda e: self._finish(None))
        self.bind("<Escape>", lambda e: self._finish(None))
        self.focus_force()

    def _img(self, x, y):
        return int(x / self.scale), int(y / self.scale)

    def _press(self, e):
        self._p0 = (e.x, e.y)
        if self.mode == "view":
            self._finish(None)

    def _drag(self, e):
        if self.mode != "rect" or not self._p0:
            return
        if self._rect:
            self.cv.delete(self._rect)
        self._rect = self.cv.create_rectangle(self._p0[0], self._p0[1], e.x, e.y, outline="#00ff66", width=2)

    def _release(self, e):
        if self.mode == "point":
            self._finish(self._img(e.x, e.y))
        elif self.mode == "rect" and self._p0:
            x0, y0 = self._img(min(self._p0[0], e.x), min(self._p0[1], e.y))
            x1, y1 = self._img(max(self._p0[0], e.x), max(self._p0[1], e.y))
            if x1 - x0 >= 4 and y1 - y0 >= 4:
                self._finish((x0, y0, x1 - x0, y1 - y0))

    def _finish(self, result):
        if self._done:
            return
        self._done = True
        try: self.destroy()
        except Exception: pass
        self.on_done(result)

# ╔══════════════════════════════════════════════════════════╗
# ║                   МАСТЕР НАСТРОЕК (таблица)             ║
# ╚══════════════════════════════════════════════════════════╝
WIZARD_SPEC = {
    "📐 Экран": [
        ("Left", "screen.left", "int"), ("Top", "screen.top", "int"),
        ("Width", "screen.width", "int"), ("Height", "screen.height", "int"),
        ("Заголовок окна игры (пусто = не проверять)", "game_window_title", "str")],
    "🔴 Маркер": [
        ("HSV low", "marker_hsv_low", "list"), ("HSV high", "marker_hsv_high", "list"),
        ("HSV low 2", "marker_hsv_low2", "list"), ("HSV high 2", "marker_hsv_high2", "list"),
        ("Мин. площадь", "marker_min_area", "float"), ("Площадь «рядом»", "close_area", "float"),
        ("Дошёл, если метка исчезла", "arrive_on_vanish", "bool")],
    "🎯 Мини-игра": [
        ("Включена", "minigame.enabled", "bool"), ("ROI [x,y,w,h]", "minigame.roi", "list"),
        ("Ползунок HSV low", "minigame.green_low", "list"), ("Ползунок HSV high", "minigame.green_high", "list"),
        ("Зона HSV low", "minigame.white_low", "list"), ("Зона HSV high", "minigame.white_high", "list"),
        ("Кадров подтверждения", "minigame.confirm_frames", "int"),
        ("Упреждение (мс)", "predict_latency_ms", "float"),
        ("Клавиша", "minigame.action_key", "str"), ("Тайм-аут (с)", "minigame.timeout_seconds", "float")],
    "🏃 Движение": [
        ("Руление: camera / keys", "steer_mode", "str"), ("Усиление камеры", "camera_gain", "float"),
        ("Макс. шаг камеры (px)", "camera_max_step", "int"), ("Мёртвая зона (px)", "horizontal_deadzone", "int"),
        ("Поиск метки камерой", "camera_search", "bool"), ("Шаг поиска (px)", "search_turn_px", "int"),
        ("Тайм-аут поиска (с)", "search_timeout", "float"),
        ("Анти-застревание", "stuck.enabled", "bool"), ("Застрял через (с)", "stuck.seconds", "float"),
        ("Порог разницы кадров", "stuck.diff_threshold", "float")],
    "⏱ Тайминги": [
        ("Анимация подбора (с)", "pickup_anim_wait", "float"), ("Пауза после сдачи (с)", "delivery_anim_wait", "float"),
        ("Кулдаун E (с)", "interaction_cooldown", "float"), ("Задержка старта (с)", "start_delay_seconds", "int"),
        ("Лимит работы (мин)", "max_run_minutes", "float"), ("Сбоев подряд до стопа", "max_fail_streak", "int"),
        ("Watchdog (мин без прогресса)", "idle_watchdog_minutes", "float"), ("Оплата за коробку ($)", "pay_per_box", "float"),
        ("Автостарт смены (шаги в config.json)", "start_shift.enabled", "bool")],
    "⌨ Клавиши": [
        ("Вперёд", "approach_key", "str"), ("Бег", "sprint_key", "str"), ("Влево", "left_key", "str"),
        ("Вправо", "right_key", "str"), ("Назад", "back_key", "str"), ("Прыжок", "jump_key", "str"),
        ("Взаимодействие", "interact_key", "str"), ("Горячая клавиша СТАРТ", "hotkey_start", "str"),
        ("Горячая клавиша СТОП", "hotkey_stop", "str")],
}

class Wizard(tk.Toplevel):
    def __init__(self, master, cfg, on_save):
        super().__init__(master)
        self.title("Настройки"); self.resizable(False, False)
        self.cfg, self.on_save = cfg, on_save
        self.vars = []
        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, padx=10, pady=10)
        for tab, fields in WIZARD_SPEC.items():
            fr = ttk.Frame(nb, padding=10); nb.add(fr, text=tab)
            for label, path, kind in fields:
                row = ttk.Frame(fr); row.pack(fill="x", pady=2)
                ttk.Label(row, text=label, width=38, anchor="w").pack(side="left")
                val = cfg_get(cfg, path)
                if kind == "bool":
                    var = tk.BooleanVar(value=bool(val)); ttk.Checkbutton(row, variable=var).pack(side="left")
                else:
                    var = tk.StringVar(value=json.dumps(val) if kind == "list" else str(val))
                    ttk.Entry(row, textvariable=var, width=24).pack(side="left")
                self.vars.append((path, kind, var))
        bf = ttk.Frame(self); bf.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bf, text="💾 Сохранить", command=self._save).pack(side="right")
        ttk.Button(bf, text="Отмена", command=self.destroy).pack(side="right", padx=6)

    def _save(self):
        try:
            new = json.loads(json.dumps(self.cfg))
            for path, kind, var in self.vars:
                raw = var.get()
                if kind == "bool":   v = bool(raw)
                elif kind == "int":  v = int(float(raw))
                elif kind == "float": v = float(raw)
                elif kind == "list":
                    v = json.loads(raw)
                    if not isinstance(v, list): raise ValueError("%s: нужен список" % path)
                else: v = str(raw).strip()
                cfg_set(new, path, v)
            if new["steer_mode"] not in ("camera", "keys"):
                raise ValueError("steer_mode: camera или keys")
            for k in ("approach_key", "sprint_key", "left_key", "right_key", "back_key", "jump_key", "interact_key"):
                if new[k].lower() not in SCANCODES: raise ValueError("Неизвестная клавиша %r в %s" % (new[k], k))
            self.cfg.clear(); self.cfg.update(new)
            save_config(self.cfg); self.on_save()
            self.destroy()
        except Exception as e:
            messagebox.showerror("Ошибка", str(e), parent=self)

# ╔══════════════════════════════════════════════════════════╗
# ║                          GUI                            ║
# ╚══════════════════════════════════════════════════════════╝
class App:
    def __init__(self, root):
        self.root = root
        root.title("PORT BOT CHILIAD v3.0")
        root.geometry("900x700"); root.minsize(780, 600)
        self._events = queue.Queue()
        self.bot = PortBot(self._events.put)
        self._hk_prev = {}
        self._build()
        self._poll(); self._tick(); self._poll_hotkeys()

    def _build(self):
        r = self.root
        r.columnconfigure(0, weight=1); r.rowconfigure(1, weight=1)
        hdr = tk.Frame(r, bg="#1a1a2e"); hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(hdr, text="PORT BOT  CHILIAD  v3.0", font=("Segoe UI", 16, "bold"),
                 bg="#1a1a2e", fg="#e0e0ff", pady=10).pack(side="left", padx=16)
        tk.Label(hdr, text="Только собственный / тестовый сервер", font=("Segoe UI", 9),
                 bg="#1a1a2e", fg="#8888aa").pack(side="left")
        body = ttk.Frame(r, padding=10); body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1); body.rowconfigure(4, weight=1)

        row = ttk.Frame(body); row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._vars = {}
        for i, (k, lbl, d) in enumerate([("state", "Состояние", "⏸ Ожидание"), ("boxes", "Коробок", "0"),
                ("earned", "Начислено*", "$0.00"), ("errors", "Ошибок", "0"), ("stuck", "Застреваний", "0"),
                ("rate", "Темп*", "$0/ч"), ("time", "Время", "00:00")]):
            f = ttk.LabelFrame(row, text=lbl, padding=(6, 3)); f.grid(row=0, column=i, padx=3, sticky="ew")
            row.columnconfigure(i, weight=1)
            self._vars[k] = tk.StringVar(value=d)
            tk.Label(f, textvariable=self._vars[k],
                     font=("Segoe UI", 9 if k == "state" else 12, "bold")).pack()

        ctrl = ttk.Frame(body); ctrl.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self._btn_start = ttk.Button(ctrl, text="▶ СТАРТ (F6)", command=self.start, width=16); self._btn_start.pack(side="left")
        self._btn_stop = ttk.Button(ctrl, text="⏹ СТОП (F7)", command=self.stop, width=14, state="disabled")
        self._btn_stop.pack(side="left", padx=6)
        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(ctrl, text="⚙ Настройки", command=self.open_wizard).pack(side="left")
        ttk.Button(ctrl, text="📄 Лог", command=lambda: self._open(LOG_PATH)).pack(side="left", padx=6)
        ttk.Button(ctrl, text="🗂 config.json", command=lambda: self._open(CFG_PATH)).pack(side="left")

        cal = ttk.LabelFrame(body, text="Быстрая калибровка (снимок через 4 с — переключись в игру)", padding=6)
        cal.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        for txt, kind in [("🔴 Цвет метки", "marker"), ("🎯 Область мини-игры", "roi"),
                          ("🟢 Цвет ползунка", "slider"), ("⬜ Цвет зоны", "zone"),
                          ("🔍 Тест распознавания", "test")]:
            ttk.Button(cal, text=txt, command=lambda k=kind: self._cal_begin(k)).pack(side="left", padx=3)
        cal2 = ttk.LabelFrame(body, text="Шаблоны для автостарта / конца смены", padding=6)
        cal2.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(cal2, text="Кнопка «Начать смену»", command=lambda: self._cal_begin("tpl_start")).pack(side="left", padx=3)
        ttk.Button(cal2, text="Признак «Конец смены»", command=lambda: self._cal_begin("tpl_end")).pack(side="left", padx=3)

        lf = ttk.LabelFrame(body, text="Журнал", padding=4); lf.grid(row=4, column=0, sticky="nsew")
        lf.columnconfigure(0, weight=1); lf.rowconfigure(0, weight=1)
        self._log = tk.Text(lf, wrap="word", state="disabled", font=("Consolas", 9),
                            bg="#0d0d1a", fg="#c8c8e8")
        sb = ttk.Scrollbar(lf, command=self._log.yview); self._log.configure(yscrollcommand=sb.set)
        self._log.grid(row=0, column=0, sticky="nsew"); sb.grid(row=0, column=1, sticky="ns")
        ft = ttk.Frame(body); ft.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(ft, text="* расчётные значения. Остановка: F7 (работает и вне фокуса).", foreground="#888").pack(side="left")
        if not IS_WIN:
            ttk.Label(ft, text="Не Windows: ввод отключён", foreground="#c06050").pack(side="right")

    # ── действия ──
    def _open(self, path):
        if not os.path.exists(path):
            messagebox.showinfo("Файл", "Файл ещё не создан:\n" + path); return
        try: os.startfile(path)
        except Exception: messagebox.showinfo("Файл", path)

    def _append(self, msg):
        self._log.configure(state="normal"); self._log.insert("end", msg + "\n")
        self._log.see("end"); self._log.configure(state="disabled")

    def start(self, confirm=True):
        if IMPORT_ERROR:
            messagebox.showerror("Нет библиотек", IMPORT_ERROR); return
        if self.bot.running:
            return
        if confirm and not messagebox.askyesno("Запуск",
                "Запустить?\n\n• Игра в оконном/безрамочном режиме\n• Подойди к началу работы\n"
                "• После нажатия у тебя будет %s с, чтобы переключиться в игру\n• Остановка: F7"
                % self.bot.cfg.get("start_delay_seconds", 5)):
            return
        self._btn_start.configure(state="disabled"); self._btn_stop.configure(state="normal")
        self.bot.start()

    def stop(self):
        if self.bot.running:
            self.bot.stop()

    def open_wizard(self):
        Wizard(self.root, self.bot.cfg, lambda: self._append("[i] Настройки сохранены."))

    # ── калибровка ──
    def _cal_begin(self, kind):
        if IMPORT_ERROR:
            messagebox.showerror("Нет библиотек", IMPORT_ERROR); return
        hints = {"marker": "красная метка должна быть видна на экране", "roi": "мини-игра должна быть на экране",
                 "slider": "мини-игра должна быть на экране", "zone": "мини-игра должна быть на экране",
                 "test": "метка и/или мини-игра на экране",
                 "tpl_start": "кнопка «Начать смену» должна быть на экране", "tpl_end": "признак конца смены на экране"}
        messagebox.showinfo("Снимок экрана", "Через 4 секунды я сделаю снимок.\nПереключись в игру: %s." % hints[kind], parent=self.root)
        self.root.iconify()
        self.root.after(4000, lambda: self._cal_capture(kind))

    def _cal_capture(self, kind):
        try:
            frame = capture_screen(self.bot.cfg)
        except Exception as e:
            self.root.deiconify(); messagebox.showerror("Снимок", str(e)); return
        self.root.deiconify()
        P = lambda mode, prompt, cb: ScreenPicker(self.root, frame, mode, prompt, cb)
        if kind == "marker":   P("point", "Кликни по красной метке", lambda p: self._apply_color(frame, p, "marker"))
        elif kind == "slider": P("point", "Кликни по ползунку", lambda p: self._apply_color(frame, p, "slider"))
        elif kind == "zone":   P("point", "Кликни по целевой зоне", lambda p: self._apply_color(frame, p, "zone"))
        elif kind == "roi":    P("rect", "Выдели рамкой полосу мини-игры", self._apply_roi)
        elif kind == "tpl_start": P("rect", "Выдели кнопку «Начать смену»", lambda r: self._apply_tpl(frame, r, "start_shift.png"))
        elif kind == "tpl_end":   P("rect", "Выдели признак конца смены", lambda r: self._apply_tpl(frame, r, "shift_end.png"))
        elif kind == "test":   self._show_test(frame)

    def _apply_color(self, frame, p, what):
        if not p: return
        c = self.bot.cfg
        h, s, v = sample_hsv(frame, p[0], p[1])
        if what == "marker":
            lo, hi, lo2, hi2 = marker_ranges(h, s, v)
            c["marker_hsv_low"], c["marker_hsv_high"], c["marker_hsv_low2"], c["marker_hsv_high2"] = lo, hi, lo2, hi2
        elif what == "slider":
            c["minigame"]["green_low"], c["minigame"]["green_high"] = hue_range(h, s, v)
        else:
            c["minigame"]["white_low"], c["minigame"]["white_high"] = zone_range(h, s, v)
        save_config(c)
        self._append("[i] Цвет (%s) сохранён: HSV=(%d,%d,%d)" % (what, h, s, v))

    def _apply_roi(self, r):
        if not r: return
        self.bot.cfg["minigame"]["roi"] = [int(v) for v in r]
        save_config(self.bot.cfg)
        self._append("[i] Область мини-игры сохранена: %s" % list(r))

    def _apply_tpl(self, frame, r, name):
        if not r: return
        x, y, w, h = r
        try:
            save_template(frame[y:y + h, x:x + w], name)
            self._append("[i] Шаблон сохранён: templates/%s" % name)
        except Exception as e:
            messagebox.showerror("Шаблон", str(e))

    def _show_test(self, frame):
        c = self.bot.cfg
        img = frame.copy()
        notes = []
        m = find_marker(frame, c)
        if m:
            cv2.circle(img, (m[0], m[1]), int(math.sqrt(m[2] / math.pi)) + 8, (0, 255, 255), 3)
            near = "рядом" if m[2] >= float(c["close_area"]) else "далеко"
            notes.append("Метка: площадь %d (порог %d, %s)" % (m[2], c["close_area"], near))
        else:
            notes.append("Метка: НЕ найдена")
        x, y, w, h = [int(v) for v in c["minigame"]["roi"]]
        cv2.rectangle(img, (x, y), (x + w, y + h), (255, 120, 0), 2)
        roi = frame[y:y + h, x:x + w]
        r = mg_analyze(roi, c["minigame"]) if roi.size else None
        if r:
            sx, lo, hi = r
            cv2.rectangle(img, (x + int(lo), y), (x + int(hi), y + h), (0, 0, 255), 2)
            cv2.line(img, (x + int(sx), y), (x + int(sx), y + h), (0, 255, 0), 3)
            notes.append("Мини-игра: ползунок %.0f, зона %.0f–%.0f" % (sx, lo, hi))
        else:
            notes.append("Мини-игра: НЕ найдена")
        ScreenPicker(self.root, img, "view", " | ".join(notes) + "  — клик закрывает", lambda _: None)

    # ── цикл GUI ──
    def _tick(self):
        if self.bot.running and self.bot.started:
            m, s = divmod(int(time.time() - self.bot.started), 60)
            self._vars["time"].set("%02d:%02d" % (m, s))
        self.root.after(1000, self._tick)

    def _poll_hotkeys(self):
        if IS_WIN:
            for name, fn in (("hotkey_start", lambda: self.start(False)), ("hotkey_stop", self.stop)):
                vk = vk_for(self.bot.cfg.get(name, ""))
                down = bool(vk and key_pressed(vk))
                if down and not self._hk_prev.get(name, False):
                    fn()
                self._hk_prev[name] = down
        self.root.after(60, self._poll_hotkeys)

    def _poll(self):
        try:
            while True:
                it = self._events.get_nowait()
                if it[0] == "log": self._append(it[1])
                elif it[0] == "state": self._vars["state"].set(it[1])
                elif it[0] == "stopped":
                    self._btn_start.configure(state="normal"); self._btn_stop.configure(state="disabled")
                elif it[0] == "stats":
                    _, boxes, earned, errors, stuck, started = it
                    self._vars["boxes"].set(str(boxes)); self._vars["earned"].set("$%.2f" % earned)
                    self._vars["errors"].set(str(errors)); self._vars["stuck"].set(str(stuck))
                    el = max(1.0, time.time() - (started or time.time()))
                    self._vars["rate"].set("$%.0f/ч" % (earned * 3600 / el))
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

def main():
    root = tk.Tk()
    if IMPORT_ERROR:
        root.title("PORT BOT — ошибка"); root.geometry("520x180")
        tk.Label(root, text="Не установлены зависимости:", font=("Segoe UI", 11, "bold"), fg="red").pack(pady=(20, 4))
        tk.Label(root, text=IMPORT_ERROR, font=("Consolas", 9), wraplength=480).pack()
        ttk.Button(root, text="Закрыть", command=root.destroy).pack(pady=12)
        root.mainloop(); return
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.stop(), root.destroy()))
    root.mainloop()

if __name__ == "__main__":
    main()
