# ==================== MASTER OFFICIAL FINAL PRO ====================
# BR + LW | Region Auto-Detect | Movement + Anti-AFK | Human Mode | Ban-Safe
# ===================================================================
import sys
import asyncio
import httpx
import random
import json
import socket
import struct
import time
import os
import uuid
import itertools
import secrets
import traceback
import base64
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from google_play_scraper import app as play_scraper
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from protobuf_decoder.protobuf_decoder import Parser
from message_ids import MESSAGE_ID_TO_NAME
import MasterTeam_pb2
import MasterFF_pb2
import thunderFF_pb2

from dashboard_server import bot_state, start_web_dashboard, AUTO_LW_LEVEL

# ==================== CONFIG ====================
WEB_HOST = "0.0.0.0"
WEB_PORT = int(os.environ.get("PORT", 20321))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")
TOKEN_CACHE_FILE = os.path.join(DATA_DIR, "token_cache.json")
DEVICES_FILE = os.path.join(DATA_DIR, "devices.json")
TOKEN_CACHE_TTL = 1200

# 🔥 Region auto-detect — default ID (Indonesia). Set env FF_REGION=IND/BD/SG/TH for other
DEFAULT_REGION = os.environ.get("FF_REGION", "ID").upper()
VALID_REGIONS = {"ID", "IND", "BD", "SG", "TH", "MY", "VN", "PH", "BR", "PK", "EG"}

START_MATCH_INTERVAL_MIN = 2.5
START_MATCH_INTERVAL_MAX = 5.0
START_MATCH_INTERVAL = 10.0
NEW_MATCH_DELAY = 4.0

# 🔥 Dual-mode timing (BR match lambi hoti hai)
LW_MAX_MATCH_DURATION = 800
BR_MAX_MATCH_DURATION = 1500
LW_MATCH_IDLE_TIMEOUT = 20.0
BR_MATCH_IDLE_TIMEOUT = 45.0

MAX_CONCURRENT_MATCHES = 1
HUMAN_MODE = True

MAX_CONSECUTIVE_PARSE_FAILURES = 5.0
NON_MATCH_RECONNECT_DELAY = 1.0

FALLBACK_UID = ""
FALLBACK_PASSWORD = ""

LOGIN_CONCURRENCY = 5
LOGIN_VERSION_CACHE_TTL = 120.0
_LOGIN_SEMAPHORE = asyncio.Semaphore(LOGIN_CONCURRENCY)
_VERSION_CONFIG_CACHE = None
_VERSION_CONFIG_CACHE_AT = 0.0
_VERSION_CONFIG_LOCK = asyncio.Lock()
_REFRESH_SEMAPHORE = asyncio.Semaphore(3)

# ==================== MOVEMENT / ANTI-AFK ====================
ENABLE_MOVEMENT = True
ENABLE_ANTI_AFK = True

MOVEMENT_TICK_MS = 240
MOVEMENT_RADIUS = 180
MOVEMENT_PATTERN = "circle"
MOVEMENT_SPEED_MIN = 1.0
MOVEMENT_SPEED_MAX = 1.4
MOVEMENT_UPDATE_SPEED_EVERY = 4.0

MOVEMENT_STATE_IDLE_CHANCE   = 0.10
MOVEMENT_STATE_WALK_CHANCE   = 0.28
MOVEMENT_STATE_RUN_CHANCE    = 0.32
MOVEMENT_STATE_SPRINT_CHANCE = 0.10
MOVEMENT_STATE_JUMP_CHANCE   = 0.08
MOVEMENT_STATE_CROUCH_CHANCE = 0.06
MOVEMENT_STATE_PRONE_CHANCE  = 0.06

ANTI_AFK_FIRE_CHANCE = 0.45
ANTI_AFK_MIN_INTERVAL = 1.8
ANTI_AFK_MAX_INTERVAL = 3.5

# ==================== REGION PREFIX MAPS ====================
TCP_PREFIX = {
    "OnLine": {"ID": "7119", "IND": "7114", "BD": "7115"},
    "ChaT":   {"ID": "9219", "IND": "9214", "BD": "9215"},
}
BR_PREFIX_MAP = {
    "ID": "031500", "IND": "031400", "BD": "031900",
    "SG": "031500", "TH": "031500", "MY": "031500",
    "VN": "031500", "PH": "031500", "PK": "031400",
    "BR": "031500", "EG": "031500",
}
LW_PREFIX_MAP = {
    "ID": "031400", "IND": "031400", "BD": "031400",
    "SG": "031400", "TH": "031400", "MY": "031400",
    "VN": "031400", "PH": "031400", "PK": "031400",
    "BR": "031400", "EG": "031400",
}


def normalize_region(region: str) -> str:
    """Normalize region to valid code."""
    r = str(region or DEFAULT_REGION).upper().strip()
    if r in ("IN", "INDIA"):
        r = "IND"
    if r not in VALID_REGIONS:
        r = DEFAULT_REGION if DEFAULT_REGION in VALID_REGIONS else "ID"
    return r


# ==================== HUMAN BEHAVIOUR ====================
HUMAN_BEHAVIOUR_ENABLED = True
MATCH_FAST_CHANCE = 0.65
MATCH_SLOW_MIN = 10.0
MATCH_SLOW_MAX = 20.0
SKIP_MATCH_CHANCE = 0.00


class HumanBehavior:
    def __init__(self, uid):
        self.uid = str(uid)
        self.matches_count = 0

    def get_next_interval(self):
        if not HUMAN_BEHAVIOUR_ENABLED:
            return random.uniform(START_MATCH_INTERVAL_MIN, START_MATCH_INTERVAL_MAX)
        if random.random() < MATCH_FAST_CHANCE:
            return random.uniform(START_MATCH_INTERVAL_MIN, START_MATCH_INTERVAL_MAX)
        return random.uniform(MATCH_SLOW_MIN, MATCH_SLOW_MAX)

    def on_match_started(self):
        self.matches_count += 1

    def should_skip_match(self):
        return HUMAN_BEHAVIOUR_ENABLED and random.random() < SKIP_MATCH_CHANCE


_behavior_engine: Dict[str, HumanBehavior] = {}
_behavior_lock = asyncio.Lock()


async def get_behavior(uid):
    uid_str = str(uid)
    async with _behavior_lock:
        if uid_str not in _behavior_engine:
            _behavior_engine[uid_str] = HumanBehavior(uid_str)
        return _behavior_engine[uid_str]


async def human_delay(base=1.0, variance=0.5):
    return max(0.1, base + random.uniform(-variance, variance))


async def human_jitter():
    return random.uniform(0.05, 0.35)


# ==================== DEVICE ====================
def get_device_for_account(account_identifier: str) -> dict:
    devices = {}
    if os.path.exists(DEVICES_FILE):
        try:
            with open(DEVICES_FILE, "r", encoding="utf-8") as f:
                devices = json.load(f)
        except Exception:
            pass

    acc_key = str(account_identifier)
    if acc_key in devices:
        return devices[acc_key]

    device_list = [
        ("Samsung", "SM-G998B", "Adreno (TM) 660", "Android OS 12 / API-31"),
        ("Xiaomi", "2201122G", "Adreno (TM) 730", "Android OS 13 / API-33"),
        ("Realme", "RMX3700", "Mali-G710", "Android OS 14 / API-34"),
        ("OnePlus", "CPH2451", "Adreno (TM) 740", "Android OS 13 / API-33"),
        ("OPPO", "CPH2611", "Adreno (TM) 720", "Android OS 14 / API-34"),
        ("Vivo", "V2203", "Mali-G710", "Android OS 12 / API-31"),
        ("Poco", "M2102J20SG", "Adreno (TM) 660", "Android OS 13 / API-33"),
        ("Samsung", "SM-G991B", "Adreno (TM) 660", "Android OS 13 / API-33"),
        ("Xiaomi", "2203121C", "Adreno (TM) 730", "Android OS 13 / API-33"),
    ]
    brand, model, gpu, os_ver = random.choice(device_list)

    new_device = {
        "unique_device_id": f"Google|{str(uuid.uuid4())}",
        "brand": brand, "model": model,
        "gpu_renderer": gpu, "system_software": os_ver,
        "screen_width": random.choice([1080, 1440, 720, 1280]),
        "screen_height": random.choice([2400, 3200, 1600, 2400]),
        "screen_dpi": str(random.randint(300, 420)),
        "memory": random.randint(2800, 6500),
        "processor_details": f"ARM64 FP ASIMD AES VMH | {random.randint(2200, 3200)} | {random.randint(6, 12)}",
        "client_ip": f"{random.randint(103, 223)}.{random.randint(10, 250)}.{random.randint(10, 250)}.{random.randint(10, 250)}",
        "android_id": secrets.token_hex(8),
        "gsf_id": secrets.token_hex(8),
        "build_fingerprint": f"{brand.lower()}/{model.lower()}/{model.lower()}:{os_ver.split(' / ')[0].replace(' ', '_')}/user/release-keys",
        "serial_number": secrets.token_hex(8),
        "hardware_id": secrets.token_hex(8),
    }
    devices[acc_key] = new_device
    try:
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(devices, f, indent=4)
    except Exception as e:
        log_err(f"Save device: {e}")
    return new_device


# ==================== DNS ====================
CLOUDFLARE_PRIMARY_DNS = "1.1.1.1"
CLOUDFLARE_SECONDARY_DNS = "1.0.0.1"
_DNS_CACHE: Dict[str, Tuple[str, float]] = {}
_DNS_CACHE_TTL = 300.0


async def resolve_host_cloudflare(hostname: str) -> str:
    if not hostname:
        return hostname
    parts = hostname.split('.')
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return hostname
    now = time.time()
    if hostname in _DNS_CACHE:
        ip, exp = _DNS_CACHE[hostname]
        if now < exp:
            return ip

    def _query(server_ip):
        s = None
        try:
            tx_id = random.randint(1000, 65535)
            header = struct.pack(">HHHHHH", tx_id, 0x0100, 1, 0, 0, 0)
            qname = b"".join(bytes([len(p)]) + p.encode('ascii') for p in hostname.split('.')) + b"\x00"
            query_pkt = header + qname + struct.pack(">HH", 1, 1)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.2)
            s.sendto(query_pkt, (server_ip, 53))
            resp, _ = s.recvfrom(1024)
            if len(resp) >= 12:
                ancount = struct.unpack(">H", resp[6:8])[0]
                if ancount > 0:
                    offset = 12 + len(qname) + 4
                    for _ in range(ancount):
                        if offset >= len(resp):
                            break
                        if (resp[offset] & 0xC0) == 0xC0:
                            offset += 2
                        else:
                            while offset < len(resp) and resp[offset] != 0:
                                offset += 1 + resp[offset]
                            offset += 1
                        if offset + 10 > len(resp):
                            break
                        rtype, rclass, ttl, rdlen = struct.unpack(">HHIH", resp[offset:offset+10])
                        offset += 10
                        if rtype == 1 and rdlen == 4 and offset + 4 <= len(resp):
                            return socket.inet_ntoa(resp[offset:offset+4])
                        offset += rdlen
        except Exception:
            pass
        finally:
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        return None

    loop = asyncio.get_running_loop()
    ip = await loop.run_in_executor(None, _query, CLOUDFLARE_PRIMARY_DNS)
    if not ip:
        ip = await loop.run_in_executor(None, _query, CLOUDFLARE_SECONDARY_DNS)
    if not ip:
        try:
            info = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
            if info:
                ip = info[0][4][0]
        except Exception:
            ip = hostname
    if ip:
        _DNS_CACHE[hostname] = (ip, now + _DNS_CACHE_TTL)
    return ip or hostname


def optimize_tcp_socket(sock):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
    except Exception:
        pass


def optimize_udp_socket(sock):
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
        if hasattr(socket, 'SIO_UDP_CONNRESET') and os.name == 'nt':
            try:
                sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except Exception:
                pass
    except Exception:
        pass


# ==================== NETWORK ====================
client = httpx.AsyncClient(
    verify=False, timeout=10.0,
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)
)

headers = {
    'User-Agent': 'UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)',
    'Connection': 'Keep-Alive',
    'Accept-Encoding': 'gzip',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Expect': '100-continue',
    'X-Unity-Version': '2018.4.12f1',
    'X-GA-SV': '1789535859',
    'X-GA': 'v1 1',
    'ReleaseVersion': 'OB55'
}

AES_KEY = b'Yg&tc%DEuh6%Zc^8'
AES_IV = b'6oyZDr22E3ychjM%'

CRC7_TABLE = bytes([
    0, 9, 18, 27, 36, 45, 54, 63, 72, 65, 90, 83, 108, 101, 126, 119,
    25, 16, 11, 2, 61, 52, 47, 38, 81, 88, 67, 74, 117, 124, 103, 110,
    50, 59, 32, 41, 22, 31, 4, 13, 122, 115, 104, 97, 94, 87, 76, 69,
    43, 34, 57, 48, 15, 6, 29, 20, 99, 106, 113, 120, 71, 78, 85, 92,
    100, 109, 118, 127, 64, 73, 82, 91, 44, 37, 62, 55, 8, 1, 26, 19,
    125, 116, 111, 102, 89, 80, 75, 66, 53, 60, 39, 46, 17, 24, 3, 10,
    86, 95, 68, 77, 114, 123, 96, 105, 30, 23, 12, 5, 58, 51, 40, 33,
    79, 70, 93, 84, 107, 98, 121, 112, 7, 14, 21, 28, 35, 42, 49, 56,
    65, 72, 83, 90, 101, 108, 119, 126, 9, 0, 27, 18, 45, 36, 63, 54,
    88, 81, 74, 67, 124, 117, 110, 103, 16, 25, 2, 11, 52, 61, 38, 47,
    115, 122, 97, 104, 87, 94, 69, 76, 59, 50, 41, 32, 31, 22, 13, 4,
    106, 99, 120, 113, 78, 71, 92, 85, 34, 43, 48, 57, 6, 15, 20, 29,
    37, 44, 55, 62, 1, 8, 19, 26, 109, 100, 127, 118, 73, 64, 91, 82,
    60, 53, 46, 39, 24, 17, 10, 3, 116, 125, 102, 111, 80, 89, 66, 75,
    23, 30, 5, 12, 51, 58, 33, 40, 95, 86, 77, 68, 123, 114, 105, 96,
    14, 7, 28, 21, 42, 35, 56, 49, 70, 79, 84, 93, 98, 107, 112, 121,
])

_DELTA = 0x9E3779B9
_ROUNDS = 16
_FIELD_SIZES = {0: 1, 1: 2, 2: 2, 3: 1, 4: 2}
_FIELD_NAMES = {0: "sendOption", 1: "cmd", 2: "orderId", 3: "flags", 4: "length"}


# ==================== LOG SYSTEM ====================
class Colors:
    GREEN = '\033[92m'
    FAIL = '\033[91m'
    WARNING = '\033[93m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    BLUE = '\033[94m'
    ENDC = '\033[0m'


def cprint(text, color=Colors.WHITE):
    try:
        print(f"{color}{text}{Colors.ENDC}")
    except Exception:
        try:
            print(f"{color}{text.encode('ascii', errors='replace').decode('ascii')}{Colors.ENDC}")
        except Exception:
            print(text)


def log_info(text):
    print(f"{Colors.CYAN}[ INFO  ]{Colors.ENDC} {text}")
    try: bot_state.log(text, "info")
    except Exception: pass

def log_ok(text):
    print(f"{Colors.GREEN}[   OK  ]{Colors.ENDC} {text}")
    try: bot_state.log(text, "success")
    except Exception: pass

def log_warn(text):
    print(f"{Colors.WARNING}[  WARN ]{Colors.ENDC} {text}")
    try: bot_state.log(text, "warning")
    except Exception: pass

def log_err(text):
    print(f"{Colors.FAIL}[  FAIL ]{Colors.ENDC} {text}")
    try: bot_state.log(text, "error")
    except Exception: pass

def log_login(text):
    print(f"{Colors.MAGENTA}[ LOGIN ]{Colors.ENDC} {text}")
    try: bot_state.log(text, "info")
    except Exception: pass

def log_match(text):
    print(f"{Colors.GREEN}[ MATCH ]{Colors.ENDC} {text}")
    try: bot_state.log(text, "success")
    except Exception: pass

def log_exp(text):
    print(f"{Colors.GREEN}[  EXP  ]{Colors.ENDC} {text}")
    try: bot_state.log(text, "success")
    except Exception: pass

def log_net(text):
    print(f"{Colors.BLUE}[  NET  ]{Colors.ENDC} {text}")
    try: bot_state.log(text, "info")
    except Exception: pass

def log_search(text):
    print(f"{Colors.CYAN}[SEARCH ]{Colors.ENDC} {text}")
    try: bot_state.log(text, "info")
    except Exception: pass

def log_cache(text):
    print(f"{Colors.MAGENTA}[ CACHE ]{Colors.ENDC} {text}")
    try: bot_state.log(text, "info")
    except Exception: pass

def log_move(text):
    print(f"{Colors.BLUE}[ MOVE  ]{Colors.ENDC} {text}")
    try: bot_state.log(text, "info")
    except Exception: pass

def log_afk(text):
    print(f"{Colors.MAGENTA}[  AFK  ]{Colors.ENDC} {text}")
    try: bot_state.log(text, "info")
    except Exception: pass


def log_separator(title=""):
    line = "=" * 60
    print("")
    print(f"{Colors.CYAN}{line}{Colors.ENDC}")
    if title:
        print(f"{Colors.BOLD}{Colors.CYAN}{title.center(60)}{Colors.ENDC}")
        print(f"{Colors.CYAN}{line}{Colors.ENDC}")
    print("")


def log_banner():
    print("")
    print(f"{Colors.CYAN}+{'=' * 58}+{Colors.ENDC}")
    print(f"{Colors.CYAN}|{Colors.ENDC}{' ' * 58}{Colors.CYAN}|{Colors.ENDC}")
    print(f"{Colors.CYAN}|{Colors.ENDC}{Colors.BOLD}{Colors.MAGENTA}{'MASTER OFFICIAL - FINAL PRO'.center(58)}{Colors.ENDC}{Colors.CYAN}|{Colors.ENDC}")
    print(f"{Colors.CYAN}|{Colors.ENDC}{Colors.DIM}{'BR + LW | Auto Region | Movement + Anti-AFK'.center(58)}{Colors.ENDC}{Colors.CYAN}|{Colors.ENDC}")
    print(f"{Colors.CYAN}|{Colors.ENDC}{Colors.DIM}{f'Auto-Switch: Lv1-2 = BR | Lv{AUTO_LW_LEVEL}+ = LW'.center(58)}{Colors.ENDC}{Colors.CYAN}|{Colors.ENDC}")
    print(f"{Colors.CYAN}|{Colors.ENDC}{' ' * 58}{Colors.CYAN}|{Colors.ENDC}")
    print(f"{Colors.CYAN}|{Colors.ENDC}{Colors.WHITE}{f'Dashboard: http://localhost:{WEB_PORT}'.center(58)}{Colors.ENDC}{Colors.CYAN}|{Colors.ENDC}")
    print(f"{Colors.CYAN}|{Colors.ENDC}{' ' * 58}{Colors.CYAN}|{Colors.ENDC}")
    print(f"{Colors.CYAN}+{'=' * 58}+{Colors.ENDC}")
    print("")


print_colored = cprint
print_success = log_ok
print_error = log_err
print_warning = log_warn
print_info = log_info


def get_proto_field(d, key, default=None):
    if not d or not isinstance(d, dict):
        return default
    if key in d:
        val = d[key].get('data')
        return val if val is not None else default
    if str(key) in d:
        val = d[str(key)].get('data')
        return val if val is not None else default
    return default


# ==================== MATCH COUNTERS ====================
_match_counters: Dict[str, int] = {}
_match_counter_lock = asyncio.Lock()


async def _inc_match(uid: str) -> int:
    async with _match_counter_lock:
        _match_counters[uid] = _match_counters.get(uid, 0) + 1
        return _match_counters[uid]


async def _dec_match(uid: str) -> int:
    async with _match_counter_lock:
        if uid in _match_counters and _match_counters[uid] > 0:
            _match_counters[uid] -= 1
        if uid in _match_counters and _match_counters[uid] == 0:
            del _match_counters[uid]
            return 0
        return _match_counters.get(uid, 0)


async def _get_match_count(uid: str) -> int:
    async with _match_counter_lock:
        return _match_counters.get(uid, 0)


async def _get_active_match_count() -> int:
    async with _match_counter_lock:
        return sum(_match_counters.values())


# ==================== TOKEN CACHE ====================
_token_cache_memo: Dict[str, Any] = {}
_token_cache_memo_time: float = 0.0
_TOKEN_CACHE_MEMO_TTL = 5.0


def _json_serializer(obj):
    if isinstance(obj, (bytes, bytearray)):
        return {"__bytes_hex__": bytes(obj).hex()}
    raise TypeError(f"Type {type(obj)} not serializable")


def _json_deserializer(obj):
    if isinstance(obj, dict):
        if "__bytes_hex__" in obj and len(obj) == 1:
            try: return bytes.fromhex(obj["__bytes_hex__"])
            except Exception: return b""
        return {k: _json_deserializer(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_deserializer(x) for x in obj]
    return obj


def _load_token_cache() -> Dict[str, Any]:
    global _token_cache_memo, _token_cache_memo_time
    now = time.time()
    if _token_cache_memo and (now - _token_cache_memo_time) < _TOKEN_CACHE_MEMO_TTL:
        return _token_cache_memo
    if not os.path.exists(TOKEN_CACHE_FILE):
        return {}
    try:
        with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return {}
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("root must be dict")
        parsed = _json_deserializer(data)
        _token_cache_memo = parsed
        _token_cache_memo_time = now
        return parsed
    except Exception as e:
        log_err(f"Token cache corrupt: {e}")
        try: os.remove(TOKEN_CACHE_FILE)
        except Exception: pass
        return {}


def _save_token_cache(cache: Dict[str, Any]):
    global _token_cache_memo, _token_cache_memo_time
    try:
        tmp_file = TOKEN_CACHE_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, default=_json_serializer)
        os.replace(tmp_file, TOKEN_CACHE_FILE)
        _token_cache_memo = cache
        _token_cache_memo_time = time.time()
    except Exception as e:
        log_err(f"Token cache save: {e}")


def cache_get(uid: str) -> Optional[Dict]:
    cache = _load_token_cache()
    entry = cache.get(str(uid))
    if not entry:
        return None
    if time.time() - entry.get("cached_at", 0) > TOKEN_CACHE_TTL:
        log_cache(f"UID {uid} expired - re-login")
        cache_invalidate(uid)
        return None
    if str(entry.get("account_id", "")).isdigit():
        entry["account_id"] = int(entry["account_id"])
    if not isinstance(entry.get("login_payload_data"), (bytes, bytearray)):
        log_warn(f"UID {uid} missing payload")
        cache_invalidate(uid)
        return None
    return entry


def cache_set(uid: str, account_data: Dict):
    cache = _load_token_cache()
    entry = dict(account_data)
    entry["cached_at"] = time.time()
    cache[str(uid)] = entry
    _save_token_cache(cache)
    log_cache(f"Saved UID {uid}")


def cache_invalidate(uid: str):
    cache = _load_token_cache()
    if str(uid) in cache:
        del cache[str(uid)]
        _save_token_cache(cache)
        log_warn(f"Invalidated: {uid}")


# ==================== CRYPTO ====================
async def aes_encrypt(payload, key, iv):
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(pad(payload, AES.block_size))


async def get_playstore_version():
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: play_scraper('com.dts.freefireth', lang='hi', country='id')
    )
    return result.get("version")


async def version_config():
    global _VERSION_CONFIG_CACHE, _VERSION_CONFIG_CACHE_AT
    now = time.monotonic()
    if _VERSION_CONFIG_CACHE and (now - _VERSION_CONFIG_CACHE_AT) < LOGIN_VERSION_CACHE_TTL:
        return _VERSION_CONFIG_CACHE
    async with _VERSION_CONFIG_LOCK:
        now = time.monotonic()
        if _VERSION_CONFIG_CACHE and (now - _VERSION_CONFIG_CACHE_AT) < LOGIN_VERSION_CACHE_TTL:
            return _VERSION_CONFIG_CACHE
        app_version = await get_playstore_version()
        api_url = (
            "https://version.ggwhitehawk.com/live/ver.php"
            f"?version={app_version}"
            "&lang=hi&device=android&channel=android"
            "&appstore=googleplay&region=ID"
            "&whitelist_version=1.3.0&whitelist_sp_version=1.0.0"
        )
        try:
            response = await client.get(api_url)
            response.raise_for_status()
            data = response.json()
            server_url = data.get("server_url")
            remote_version = data.get("remote_version")
            latest_release_version = data.get("latest_release_version")
            if not server_url or not remote_version or not latest_release_version:
                return None
            _VERSION_CONFIG_CACHE = (latest_release_version, remote_version, server_url)
            _VERSION_CONFIG_CACHE_AT = time.monotonic()
            return _VERSION_CONFIG_CACHE
        except Exception:
            return None


async def get_access_token(uid, password):
    url = "https://100067.connect.garena.com/oauth/guest/token/grant"
    hdrs = {
        "Host": "100067.connect.garena.com",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; SM-G998B Build/SP1A.210812.016)",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "close"
    }
    data = {
        "uid": uid, "password": password,
        "response_type": "token", "client_type": "2",
        "client_secret": "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
        "client_id": "100067"
    }
    for attempt in range(5):
        try:
            response = await client.post(url, headers=hdrs, data=data)
            if response.status_code == 200:
                response_data = response.json()
                open_id = response_data.get("open_id")
                access_token = response_data.get("access_token")
                platform = response_data.get("platform", 4)
                if open_id and access_token:
                    return open_id, access_token, platform
            if response.status_code == 429:
                await asyncio.sleep(1)
                continue
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return None


async def parse_results(parsed_results):
    result_dict = {}
    for result in parsed_results:
        field_data = {"wire_type": result.wire_type}
        if result.wire_type == "varint":
            field_data["data"] = result.data
        elif result.wire_type == "string":
            field_data["data"] = result.data
        elif result.wire_type == "bytes":
            field_data["data"] = result.data
        elif result.wire_type == "length_delimited":
            field_data["data"] = await parse_results(result.data.results)
        result_dict[result.field] = field_data
    return result_dict


async def decode_protobuf(data):
    parsed_results = Parser().parse(data)
    parsed_results_dict = await parse_results(parsed_results)
    return json.dumps(parsed_results_dict)


def encode_varint(n):
    if n < 0: return b''
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n: b |= 0x80
        out.append(b)
        if not n: break
    return bytes(out)


def build_proto(fields):
    parts = []
    for k, v in fields.items():
        if isinstance(v, dict):
            nested = build_proto(v)
            parts.append(encode_varint((k << 3) | 2) + encode_varint(len(nested)) + nested)
        elif isinstance(v, int):
            parts.append(encode_varint((k << 3) | 0) + encode_varint(v))
        elif isinstance(v, (str, bytes)):
            ev = v.encode() if isinstance(v, str) else v
            parts.append(encode_varint((k << 3) | 2) + encode_varint(len(ev)) + ev)
    return b''.join(parts)


def encrypt_api(plain_hex):
    return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(
        pad(bytes.fromhex(plain_hex), AES.block_size)
    ).hex()


def encrypt_api_bytes(plain_bytes):
    return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(
        pad(plain_bytes, AES.block_size)
    )


# ==================== MAJORLOGIN PAYLOAD (REGION-AWARE) ====================
async def build_majorlogin_payload(open_id, access_token, platform, client_version, device_info, region=None):
    """Region-aware MajorLogin payload"""
    try:
        reg = normalize_region(region or DEFAULT_REGION)
        payload_dict = {
            3: str(datetime.now())[:-7],
            4: "free fire", 5: "1",
            7: str(client_version) if client_version else "1.132.10",
            8: str(device_info.get("system_software", "Android OS 15 / API-35")),
            9: "Handheld", 10: "Airtel", 11: "CarrierDataNetwork",
            12: str(device_info.get("screen_width", 1639)),
            13: str(device_info.get("screen_height", 720)),
            14: str(device_info.get("screen_dpi", 320)),
            15: str(device_info.get("processor_details", "ARM64 FP ASIMD AES | 2000 | 8")),
            16: str(device_info.get("memory", 5653)),
            17: str(device_info.get("gpu_renderer", "Mali-G52 MC2")),
            18: "OpenGL ES 3.2 v1.r49p1-03bet0.19498e0ae1d5dac223383c39a2e58f04",
            19: str(device_info.get("unique_device_id", f"Google|{uuid.uuid4()}")),
            20: str(device_info.get("client_ip", "182.5.135.225")),
            21: "en", 22: str(open_id), 23: str(platform), 24: "Handheld",
            25: str(device_info.get("model", "Xiaomi 2409BRN2CY")),
            26: reg,                     # 🔥 dynamic region
            29: str(access_token), 30: "1",
            41: "Airtel", 42: "4G",
            57: "7428b253defc164018c604a1ebbfebdf",
            60: "109278", 61: "55402", 62: "868",
            64: "55927", 65: "109278", 66: "55927", 67: "109278",
            73: "2",
            74: "/data/app/~~6Cw028g_ld-8Z0z2qhrOLA==/com.dts.freefireth-i3wiaEmpx8CbiRVhUMx_ag==/lib/arm64",
            76: "1",
            77: "b8e0cd5e295eee42f5860d3c86e483dd|/data/app/~~6Cw028g_ld-8Z0z2qhrOLA==/com.dts.freefireth-i3wiaEmpx8CbiRVhUMx_ag==/base.apk",
            78: "3", 79: "2", 81: "64", 83: "2019121229",
            85: "3", 86: "OpenGLES2", 87: "8191", 88: str(platform),
            90: "Mumbai", 91: "MH", 92: "14466", 93: "android",
            94: "KqsHTyb7yHjsAiyNWUUZa0DSOD6U0AhDQ76smAo1WeHHHpSmJ8KMRyRrxb2ttgJqO9YOqO/K6AORAvDMAX/qBS8+JahIkrM7kHfzlfULbgRAoZ9r",
            95: "111207",
            96: "{\"cur_rate\":[60,90,120],\"support_etc2\":false}",
            97: "1", 98: "1", 99: str(platform), 100: str(platform),
            102: "D]C\x15\x05T\x0eU3", 103: "1", 104: "52837", 105: "1",
            106: "https://dl.gmc.freefiremobile.com/live/ABHotUpdates/|https://core-gmc.freefiremobile.com/live/ABHotUpdates/|211c933168f55902c7dfbfd8c4e2957d",
            107: "1.3fb8adbf45f44ce9",
        }
        raw_proto = build_proto(payload_dict)
        return encrypt_api_bytes(raw_proto)
    except Exception as e:
        log_err(f"MajorLogin payload: {e}")
        return None


async def send_majorlogin(data, release_version, server_url):
    try:
        if not data or not server_url:
            log_err("[MajorLogin] Empty payload or URL")
            return None
        base = server_url.rstrip('/')
        url = f"{base}/MajorLogin"
        req_headers = headers.copy()
        req_headers["ReleaseVersion"] = release_version
        response = await client.post(url, headers=req_headers, data=data)
        if response.status_code != 200:
            log_err(f"[MajorLogin] HTTP {response.status_code}")
            return None
        content = response.content
        if len(content) < 40:
            log_err(f"[MajorLogin] Response too small")
            return None
        res_proto = MasterTeam_pb2.MajorLoginRes()
        try:
            res_proto.ParseFromString(content)
            if res_proto.region and res_proto.token:
                return res_proto
        except Exception:
            pass
        if len(content) > 64:
            try:
                res_proto = MasterTeam_pb2.MajorLoginRes()
                res_proto.ParseFromString(content[64:])
                if res_proto.region and res_proto.token:
                    return res_proto
            except Exception:
                pass
        for offset in range(min(128, len(content))):
            try:
                candidate = MasterTeam_pb2.MajorLoginRes()
                candidate.ParseFromString(content[offset:])
                if candidate.region and candidate.token:
                    return candidate
            except Exception:
                pass
        res_proto = MasterTeam_pb2.MajorLoginRes()
        res_proto.ParseFromString(content)
        return res_proto
    except Exception as e:
        log_err(f"[MajorLogin] Exception: {e}")
        return None


async def send_getlogin(data, base_url, token, release_version):
    try:
        if not data:
            log_err("[GetLoginData] Empty payload")
            return None
        base = base_url.rstrip('/')
        url = f"{base}/GetLoginData"
        req_headers = headers.copy()
        req_headers["ReleaseVersion"] = release_version
        req_headers['Authorization'] = f"Bearer {token}"
        req_headers['Host'] = "clientbp.ppmainecoonghj.com"
        response = await client.post(url, headers=req_headers, data=data)
        if response.status_code != 200:
            log_err(f"[GetLoginData] HTTP {response.status_code}")
            return None
        content = response.content
        res_proto = MasterTeam_pb2.GetLoginDataRes()
        parsed = False
        try:
            res_proto.ParseFromString(content)
            if res_proto.functional_addrs or res_proto.informational_addrs:
                parsed = True
        except Exception:
            pass
        if not parsed:
            for offset in range(min(128, len(content))):
                try:
                    candidate = MasterTeam_pb2.GetLoginDataRes()
                    candidate.ParseFromString(content[offset:])
                    if candidate.functional_addrs or candidate.informational_addrs:
                        res_proto = candidate
                        break
                except Exception:
                    pass
        dict_res = {}
        try:
            parsed = Parser().parse(content.hex())
            dict_res = await parse_results(parsed)
        except Exception:
            pass
        return res_proto, dict_res
    except Exception as e:
        log_err(f"[GetLoginData] Exception: {e}")
        return None


async def build_tcp_startup_packet(account_id, token, server_time, key, iv, region="ID", typ='OnLine'):
    """Region-aware TCP startup packet"""
    uid_hex = f"{int(account_id):016x}"
    timestamp_hex = f"{int(server_time):08x}"
    encode_token = token.encode()
    encrypted_packet = (await aes_encrypt(encode_token, key, iv)).hex()
    encrypted_packet_length = f"{len(encrypted_packet) // 2:08x}"
    reg = normalize_region(region)
    prefix = TCP_PREFIX[typ].get(reg, TCP_PREFIX[typ]["ID"])
    if typ == 'OnLine':
        return f"{prefix}{uid_hex}{timestamp_hex}00000000{encrypted_packet_length}{encrypted_packet}"
    else:
        return f"{prefix}{uid_hex}{timestamp_hex}{encrypted_packet_length}{encrypted_packet}"


async def send_keep_alive(region="ID"):
    """Region-aware keepalive"""
    try:
        reg = normalize_region(region)
        if reg == "ID":
            return bytes.fromhex("0219")
        elif reg == "IND":
            return bytes.fromhex("0214")
        return bytes.fromhex("0215")
    except Exception:
        return bytes.fromhex("0219")


# ==================== MATCH SEARCH PACKETS ====================
async def start_game_battle_royale(region, client_version, writer, key, iv):
    packet = bytes.fromhex("080112800a0a010110013a110a044944433110aa011a064555524f50453a100a044944433210311a064555524f504540014a0801090a0b1219202758016291090a8001303838463832424630324139363736373032303130313030303030303030303030303136303030313030313530303032323246393745454530463030303030303436373632353134303030303030303030303030303030303030303030303030303030303030303030303030303066663030303030303030636163666131366410241afb02735d5e571400024a775d45414d1a041b1c001f11010449715f4243481a001e1d071c1703004b1a4066785c524570735c51486775421b5c5a4c07504042685a63610816054e19025e75196001477c015165406370195f5547404e4550640103020f1304064863754268676c755f65576e40467e5f0a417a4701026d675d6e73670b1108495a4c6a0b78470b740065645e525a057258425f584a447d4e6759440c11044e7c596d7f4b625f7d04055a47505c4e1d6b5b4107447d7201057d7f0f14084e430457674f7e517d72015172415d027473577c4d615f79535256780911030f4d5e027a797f614165067806505d53777750475e75064257076500460817014e741e7e5078487e7a7c465e7669767153497064605a7376677773550d160148037e18675966787f4c42607a645f577e7b441b460776026b18685d0b110205490060020f70676175654674706671797f41067346677c4e06585e780f15074c57047b40517075415f6364027259674b5b0166407f7340600407770a22047a5d5c52300b3a0a167305067162727516134208312e3133302e3232480350015ae90403626253513635686e556f4e36416456324b796f566c636f477776484f624e56526c4d727073504b4f43654177616848494176795556497273743752737149734a7a786b3247525268377a2f637664626d504f6a73552f79626d38547a4c69586d2f474351696d494b53486833447955726f39515152756c34545350626d6d624b7949565937545671577059455372323646572f59624578507338514f706d317372785455736c30796a434144444d4f34616a654b615753366361496c554b4963797a494e396d52516f715277687939797257476d337a644345337a6a61436f492f5a585233656f65365a42647a64677654636b6b665733356e4d4c6a6a565072564b6433523172756174394e50514150724a5546627859696c4c5a3859707336654d5447666b6649793574666a526c314d4648706b51774c6373374439656378566c41636f374e664f6d2b30654756466c4434744478706771385533595973587645384842502f70666c767a737138316a32524f4d7857437556445442492f684735625462773166456e4249725162762b636144775147696f74554e316d4c4b77734379456f4766706746614251457645672b736a764c4c78704743334c304a5344532f74526169504354553344374e6249306547516651622f5a466f4c36455630775a324d6f583932414c572f5049752f56634663584e70596b356f7966326151416a536971486a2f363276354843644f525551303578754e6171795251625653704654303137655237675255636b4966366c6f447476342b514e4a4670766d74757077707774396a5a5974437a4b56743657726d6e36785837706658456251555434684f3758a201050803108703a201050804108103a20105080510c001a20105081d10cc01a2010408161078a20105080e10af01a201020815")
    proto = thunderFF_pb2.StartMatch()
    proto.ParseFromString(packet)
    if hasattr(proto.main, 'region_list') and len(proto.main.region_list) > 0:
        proto.main.region_list[0].region = region
        if len(proto.main.region_list) > 1:
            proto.main.region_list[1].region = region
    if hasattr(proto.main, 'client_version'):
        proto.main.client_version.remote_version = client_version
    packet = proto.SerializeToString()
    encrypted_packet = (await aes_encrypt(packet, key, iv)).hex()
    packet_length = len(encrypted_packet) // 2
    hex_length = hex(packet_length)[2:]
    hex_length = hex_length if len(hex_length) > 1 else "0" + hex_length
    reg = normalize_region(region)
    reg_prefix = BR_PREFIX_MAP.get(reg, "031500")
    final_packet = reg_prefix + "0" * (6 - len(hex_length)) + hex_length + encrypted_packet
    writer.write(bytes.fromhex(final_packet))
    await writer.drain()
    log_search(f"BR Search Packet Sent ({packet_length}b) | Region: {reg}")


async def start_game_lone_wolf(region, client_version, writer, key, iv):
    packet = bytes.fromhex("080112800a0a010b102b3a110a044944433110aa011a064555524f50453a100a044944433210311a064555524f504540014a0801090a0b1219202758016291090a8001303838463832424630324139363736373032303130313030303030303030303030303136303030313030313530303032323246393745454530463030303030303436373632353134303030303030303030303030303030303030303030303030303030303030303030303030303066663030303030303030636163666131366410241afb02735d5e571400024a775d45414d1a041b1c001f11010449715f4243481a001e1d071c1703004b1a4066785c524570735c51486775421b5c5a4c07504042685a63610816054e19025e75196001477c015165406370195f5547404e4550640103020f1304064863754268676c755f65576e40467e5f0a417a4701026d675d6e73670b1108495a4c6a0b78470b740065645e525a057258425f584a447d4e6759440c11044e7c596d7f4b625f7d04055a47505c4e1d6b5b4107447d7201057d7f0f14084e430457674f7e517d72015172415d027473577c4d615f79535256780911030f4d5e027a797f614165067806505d53777750475e75064257076500460817014e741e7e5078487e7a7c465e7669767153497064605a7376677773550d160148037e18675966787f4c42607a645f577e7b441b460776026b18685d0b110205490060020f70676175654674706671797f41067346677c4e06585e780f15074c57047b40517075415f6364027259674b5b0166407f7340600407770a22047a5d5c52300b3a0a167305067162727516134208312e3133302e3232480350015ae90403626253513635686e556f4e36416456324b796f566c636f477776484f624e56526c4d727073504b4f43654177616848494176795556497273743752737149734a7a786b3247525268377a2f637664626d504f6a73552f79626d38547a4c69586d2f474351696d494b53486833447955726f39515152756c34545350626d6d624b7949565937545671577059455372323646572f59624578507338514f706d317372785455736c30796a434144444d4f34616a654b615753366361496c554b4963797a494e396d52516f715277687939797257476d337a644345337a6a61436f492f5a585233656f65365a42647a64677654636b6b665733356e4d4c6a6a565072564b6433523172756174394e50514150724a5546627859696c4c5a3859707336654d5447666b6649793574666a526c314d4648706b51774c6373374439656378566c41636f374e664f6d2b30654756466c4434744478706771385533595973587645384842502f70666c767a737138316a32524f4d7857437556445442492f684735625462773166456e4249725162762b636144775147696f74554e316d4c4b77734379456f4766706746614251457645672b736a764c4c78704743334c304a5344532f74526169504354553344374e6249306547516651622f5a466f4c36455630775a324d6f583932414c572f5049752f56634663584e70596b356f7966326151416a536971486a2f363276354843644f525551303578754e6171795251625653704654303137655237675255636b4966366c6f447476342b514e4a4670766d74757077707774396a5a5974437a4b56743657726d6e36785837706658456251555434684f3758a201050803108703a201050804108103a20105080510c001a20105081d10cc01a2010408161078a20105080e10af01a201020815")
    proto = MasterTeam_pb2.StartMatch()
    proto.ParseFromString(packet)
    if hasattr(proto.main, 'region_list') and len(proto.main.region_list) > 0:
        proto.main.region_list[0].region = region
        if len(proto.main.region_list) > 1:
            proto.main.region_list[1].region = region
    if hasattr(proto.main, 'client_version'):
        proto.main.client_version.remote_version = client_version
    packet = proto.SerializeToString()
    encrypted_packet = (await aes_encrypt(packet, key, iv)).hex()
    packet_length = len(encrypted_packet) // 2
    hex_length = hex(packet_length)[2:]
    hex_length = hex_length if len(hex_length) > 1 else "0" + hex_length
    reg = normalize_region(region)
    reg_prefix = LW_PREFIX_MAP.get(reg, "031400")
    final_packet = reg_prefix + "0" * (6 - len(hex_length)) + hex_length + encrypted_packet
    writer.write(bytes.fromhex(final_packet))
    await writer.drain()
    log_search(f"LW Search Packet Sent ({packet_length}b) | Region: {reg}")


# ==================== TEA + PROTOCOL ====================
async def has_ssan_zig(n):
    z = (n << 1) & 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while z >= 0x80:
        out.append((z & 0x7F) | 0x80)
        z >>= 7
    out.append(z)
    return bytes(out)


async def uleb_encode(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n: b |= 0x80
        out.append(b)
        if not n: break
    return bytes(out)


async def zigzag_encode_signed(n: int) -> bytes:
    try:
        n = int(n)
    except Exception:
        n = 0
    zz = (n << 1) ^ (n >> 63)
    if zz < 0:
        zz = -zz
    out = bytearray()
    while True:
        b = zz & 0x7F
        zz >>= 7
        if zz:
            b |= 0x80
        out.append(b)
        if not zz:
            break
    return bytes(out)


async def tea_enc(v0, v1, k0, k1, k2, k3):
    s = 0
    for _ in range(_ROUNDS):
        s = (s + _DELTA) & 0xFFFFFFFF
        v0 = (v0 + (((((v1 << 4) & 0xFFFFFFFF) + k0) & 0xFFFFFFFF ^
                      ((v1 + s) & 0xFFFFFFFF) ^
                      (((v1 >> 5) + k1) & 0xFFFFFFFF)))) & 0xFFFFFFFF
        v1 = (v1 + (((((v0 << 4) & 0xFFFFFFFF) + k2) & 0xFFFFFFFF ^
                      ((v0 + s) & 0xFFFFFFFF) ^
                      (((v0 >> 5) + k3) & 0xFFFFFFFF)))) & 0xFFFFFFFF
    return v0, v1


async def tea_dec(v0, v1, k0, k1, k2, k3):
    s = (_DELTA * _ROUNDS) & 0xFFFFFFFF
    for _ in range(_ROUNDS):
        v1 = (v1 - (((((v0 << 4) & 0xFFFFFFFF) + k2) & 0xFFFFFFFF ^
                      ((v0 + s) & 0xFFFFFFFF) ^
                      (((v0 >> 5) + k3) & 0xFFFFFFFF)))) & 0xFFFFFFFF
        v0 = (v0 - (((((v1 << 4) & 0xFFFFFFFF) + k0) & 0xFFFFFFFF ^
                      ((v1 + s) & 0xFFFFFFFF) ^
                      (((v1 >> 5) + k1) & 0xFFFFFFFF)))) & 0xFFFFFFFF
        s = (s - _DELTA) & 0xFFFFFFFF
    return v0, v1


async def tea_cbc_encrypt(padded, key_bytes):
    k0, k1, k2, k3 = (struct.unpack_from("<I", key_bytes, o)[0] for o in (0, 4, 8, 12))
    out = bytearray(len(padded))
    prev_cipher = bytearray(8)
    prev_intermediate = bytearray(8)
    for i in range(0, len(padded), 8):
        xored = bytearray(8)
        for j in range(8):
            xored[j] = padded[i + j] ^ prev_cipher[j]
        e0, e1 = await tea_enc(
            struct.unpack_from("<I", xored, 0)[0],
            struct.unpack_from("<I", xored, 4)[0],
            k0, k1, k2, k3,
        )
        enc = bytearray(8)
        struct.pack_into("<I", enc, 0, e0)
        struct.pack_into("<I", enc, 4, e1)
        for j in range(8):
            out[i + j] = enc[j] ^ prev_intermediate[j]
        prev_cipher[:] = out[i:i + 8]
        prev_intermediate[:] = xored
    return bytes(out)


async def build_padded(content):
    pad_len = (8 - (len(content) + 10) % 8) % 8
    return bytes([pad_len, 0, 0]) + b"\x00" * pad_len + content + b"\x00" * 7


async def encode_header(layout, send_option, cmd, order_id, flags, length, k, v80):
    out = bytearray()
    for code in layout:
        value = {0: send_option, 1: cmd, 2: order_id, 3: flags, 4: length}[code]
        if _FIELD_SIZES[code] == 1:
            out.append((value & 0xFF) ^ k)
        else:
            v = ((value & 0xFFFF) ^ v80) & 0xFFFF
            out.append(v & 0xFF)
            out.append((v >> 8) & 0xFF)
    return bytes(out)


async def crc7_buff(crc, buf):
    c = crc & 0x7F
    for b in buf:
        c = CRC7_TABLE[((2 * (c & 0xFF)) ^ (b & 0xFF)) & 0xFF] & 0x7F
    return c & 0x7F


async def sv_frame(msg_key, layout, send_option, cmd, order_id, flags, content, key, encrypted=True):
    k = key[0]
    v80 = ((k << 8) | k) & 0xFFFF
    body = await tea_cbc_encrypt(await build_padded(content), key) if encrypted else content
    hdr = bytearray([msg_key, 0]) + await encode_header(layout, send_option, cmd, order_id, flags, len(body), k, v80)
    packet = bytearray(hdr + body)
    packet[1] = await crc7_buff(0, bytes(packet[2:])) & 0x7F
    return bytes(packet)


async def build_match_startup_packets(token, udp_key, match_code, account_id, block_val,
                                      server_ip="", region="ID", client_version="1.132.10",
                                      client_version_code="2019121229", access_token="",
                                      mode_id=43, map_id=11):
    token = token.strip()
    udp_key = bytes.fromhex(udp_key)
    match_code = [int(ch) for ch in str(match_code).strip()]

    thunder_jwt = token[:660] if len(token) > 660 else token
    sharma_jwt = token[660:] if len(token) > 660 else ""
    encoded_thunder_jwt = thunder_jwt.encode() if isinstance(thunder_jwt, str) else thunder_jwt
    encoded_sharma_jwt = sharma_jwt.encode() if isinstance(sharma_jwt, str) else sharma_jwt

    garena420 = await has_ssan_zig(len(encoded_thunder_jwt)) + encoded_thunder_jwt

    reg = normalize_region(region)
    csoversea_block = bytes.fromhex(
        "ca0163736f7665727365612e7374726f6e67686f6c642e66726565666972656d6f62696c652e636f6d"
        "3b302e302e302e303b33342e3132362e37362e34353b33342e38372e3137372e31343b33342e38372e"
        "3137302e3233303b33352e3138352e3138332e35370000000000000100000000000000000000000001"
        "00000800000100000000000100a8a2d7bebd8d8bdf110200"
    )

    mid = bytes.fromhex('0000000001000102030101') + await has_ssan_zig(len(reg)) + reg.encode()
    mid += bytes.fromhex('0001030003000004')
    mid += await has_ssan_zig(len(client_version)) + client_version.encode()
    mid += await has_ssan_zig(len(client_version_code)) + client_version_code.encode()
    mid += csoversea_block

    clean_ip = server_ip.split(':')[0] if server_ip else "0.0.0.0"
    mid += await has_ssan_zig(len(clean_ip)) + clean_ip.encode()

    clean_acc_tok = access_token.strip() if access_token else ""
    if clean_acc_tok:
        mid += await has_ssan_zig(len(clean_acc_tok)) + clean_acc_tok.encode()

    mid += await has_ssan_zig(len(encoded_sharma_jwt)) + encoded_sharma_jwt

    tg_garena420 = (
        await uleb_encode(int(account_id)) +
        await uleb_encode(int(block_val)) +
        await uleb_encode(1) +
        await uleb_encode(int(mode_id)) +
        await uleb_encode(int(block_val)) +
        await uleb_encode(int(map_id)) +
        mid
    )

    process = await sv_frame(0x5E, match_code, 2, 447, 0, 1, garena420, udp_key)
    loading = await sv_frame(0x5A, match_code, 2, 448, 1, 1, tg_garena420, udp_key)
    return process.hex(), loading.hex()


async def produce_xor_key(secret_key):
    k = secret_key[0] if secret_key and len(secret_key) > 0 else 10
    return k, ((k << 8) | k) & 0xFFFF


async def parse_layout(layout):
    if isinstance(layout, str):
        return [int(ch) for ch in layout.strip()]
    return list(layout)


async def tea_cbc_decrypt(body, key_bytes):
    k0, k1, k2, k3 = (struct.unpack_from("<I", key_bytes, o)[0] for o in (0, 4, 8, 12))
    out = bytearray(len(body))
    prev_intermediate = bytearray(8)
    prev_cipher = bytearray(8)
    xored = bytearray(8)
    dec = bytearray(8)
    for i in range(0, len(body), 8):
        for j in range(8):
            xored[j] = body[i + j] ^ prev_intermediate[j]
        d0, d1 = await tea_dec(
            struct.unpack_from("<I", xored, 0)[0],
            struct.unpack_from("<I", xored, 4)[0],
            k0, k1, k2, k3
        )
        struct.pack_into("<I", dec, 0, d0)
        struct.pack_into("<I", dec, 4, d1)
        for j in range(8):
            out[i + j] = dec[j] ^ prev_cipher[j]
        prev_cipher[:] = body[i:i + 8]
        prev_intermediate[:] = dec
    return bytes(out)


async def build_hello_packet(text, key, layout):
    data = text.encode("utf-8")
    if len(data) > 25:
        raise ValueError(f"Text too long ({len(data)} bytes)")
    content = b"\x10\x00\x00\x00" + data + b"\x00" * (29 - 4 - len(data))
    k, v80 = await produce_xor_key(key)
    layout = await parse_layout(layout)
    padded = await build_padded(content)
    enc_body = await tea_cbc_encrypt(padded, key)
    header_bytes = await encode_header(layout, 1, 1, 0, 1, len(enc_body), k, v80)
    packet = bytearray([0x63, 0x00]) + header_bytes + enc_body
    packet[1] = await crc7_buff(0, packet[2:]) & 0x7F
    return bytes(packet).hex()


async def classify(frame):
    cmd = frame["cmd"]
    msg_name = MESSAGE_ID_TO_NAME.get(cmd, f"UNKNOWN_{cmd}")
    if msg_name == "UDP_HELLO": return "HELLO"
    if msg_name == "UDP_ACK": return "ACK"
    if msg_name == "UDP_PING": return "PING"
    if msg_name == "RUDP_JOIN_MATCH": return "JOIN_MATCH"
    if msg_name.startswith("RUDP_"): return msg_name
    if msg_name.startswith("UDP_"): return msg_name
    return "DATA"


async def build_packet(msg_key, layout, send_option, cmd, order_id, flags, content, key, encrypted=True):
    k = key[0]
    v80 = ((k << 8) | k) & 0xFFFF
    body = await tea_cbc_encrypt(await build_padded(content), key) if encrypted else content
    hdr = bytearray([msg_key, 0])
    for code in layout:
        value = {0: send_option, 1: cmd, 2: order_id, 3: flags, 4: len(body)}[code]
        if _FIELD_SIZES[code] == 1:
            hdr.append((value & 0xFF) ^ k)
        else:
            v = ((value & 0xFFFF) ^ v80) & 0xFFFF
            hdr.append(v & 0xFF)
            hdr.append((v >> 8) & 0xFF)
    packet = bytearray(hdr + body)
    packet[1] = await crc7_buff(0, bytes(packet[2:])) & 0x7F
    return bytes(packet)


async def layouts_from_mask(mask):
    ru = [int(c) for c in str(mask).strip()]
    nr = [c for c in ru if c != 2]
    return ru, nr


async def reply_for(frame, key, mask, ack_key=0x68, ping_key=0x6D, hello_key=0x5B, ack_style="short"):
    ru, nr = await layouts_from_mask(mask)
    typ = await classify(frame)
    if typ == "HELLO":
        if ack_style == "echo":
            content = frame["content"] if frame["content"] else b"\x10\x00\x00\x00"
            return typ, await build_packet(hello_key, nr, 1, 1, None, 1, content, key)
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, b"\x01\x00", key)
    if typ == "ACK":
        content = frame["content"] if frame["content"] else b"\x01\x00"
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, content, key)
    if typ == "PING":
        c = frame["content"]
        counter = c[:4] if len(c) >= 4 else c
        return typ, await build_packet(ping_key, nr, 0, 3, None, 0, counter + b"\x00\x00\x00", key, encrypted=False)
    if typ == "JOIN_MATCH":
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, b"\x02\x00", key)
    return typ, None


async def keepalive_ping(sock, ip, port, key_bytes, mask, stop_event):
    nr = (await layouts_from_mask(mask))[1]
    ping_keys = [0x66, 0x6D, 0x69, 0x6C, 0x6B, 0x6E, 0x6F, 0x70]
    loop = asyncio.get_event_loop()
    i = 0
    while not stop_event.is_set():
        pk = ping_keys[i % len(ping_keys)]
        counter = int(time.time() * 1000) & 0xFFFFFFFF
        pkt = await build_packet(pk, nr, 0, 3, None, 0, struct.pack("<I", counter) + b"\x00\x00\x00", key_bytes, encrypted=False)
        try:
            await loop.sock_sendto(sock, pkt, (ip, port))
        except Exception:
            pass
        i += 1
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass


async def try_header(buf, layout, k, v80):
    off = 2
    out = {}
    for code in layout:
        size = _FIELD_SIZES[code]
        if off + size > len(buf):
            return None
        out[_FIELD_NAMES[code]] = (buf[off] ^ k) if size == 1 else ((buf[off] | (buf[off + 1] << 8)) ^ v80) & 0xFFFF
        off += size
    out["headerLen"] = off
    return out


async def oicq_unpad(padded):
    if not padded or len(padded) < 8:
        return None
    if not all(padded[-1 - i] == 0 for i in range(7)):
        return None
    pad_len = padded[0] & 0x07
    s = 3 + pad_len
    e = len(padded) - 7
    return padded[s:e] if s < e else b""


async def decode_packet(packet, key, mask=None):
    data = bytes(packet) if isinstance(packet, bytes) else bytes.fromhex(packet)
    if len(data) < 8:
        return None
    k = key[0]
    v80 = ((k << 8) | k) & 0xFFFF
    crc_ok = (data[1] & 0x7F) == await crc7_buff(0, data[2:])
    candidates = []
    if mask:
        ru, nr = await layouts_from_mask(mask)
        layouts = [("RUDP", ru), ("nonRUDP", nr)]
    else:
        layouts = [("RUDP", list(p)) for p in itertools.permutations([0, 1, 2, 3, 4])]
        layouts += [("nonRUDP", list(p)) for p in itertools.permutations([0, 1, 3, 4])]
    for kind, layout in layouts:
        f = await try_header(data, layout, k, v80)
        if not f:
            continue
        if f["flags"] > 7 or f["sendOption"] > 7:
            continue
        if f["length"] != len(data) - f["headerLen"]:
            continue
        body = data[f["headerLen"]:f["headerLen"] + f["length"]]
        content = None
        padded = None
        if f["flags"] & 1:
            if len(body) < 8 or len(body) % 8 != 0:
                continue
            padded = await tea_cbc_decrypt(body, key)
            content = await oicq_unpad(padded)
            if content is None:
                continue
        else:
            content = body
        score = (1 if crc_ok else 0) + (1 if content is not None else 0)
        candidates.append({
            "kind": kind, "layout": layout, "headerLen": f["headerLen"],
            "msgKey": data[0], "cmd": f["cmd"], "flags": f["flags"],
            "sendOption": f["sendOption"], "orderId": f.get("orderId"),
            "length": f["length"], "content": content, "crcOk": crc_ok,
            "padded": padded, "score": score, "total": len(data),
        })
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c["kind"] == "RUDP" or c["kind"] == "nonRUDP", c["score"]), reverse=True)
    return candidates[0]


# ==================== MOVEMENT PACKET (cmd 2001) ====================
async def build_movement_packet_2001(key_bytes, msg_key, player_state, x, y, z,
                                     tick, counter, move_state, account_uid,
                                     room_code, phase="arena"):
    if phase == "arena":
        TAIL = (b"\x00" * 7 +
                b"\xbd\xb6\xe1\xf3\x03" +
                b"\x00" +
                b"\xc4\xb6\xe1\xfb\x0b" +
                b"\x00\x00")
    elif phase == "game":
        TAIL = (b"\x00" * 7 +
                b"\x93\xca\xd5\xf9\x0b" +
                b"\x8e\xfe\xfc\xf7\x0b" +
                b"\xf4\xe0\xfe\xf7\x03" +
                b"\x00\x00")
    else:
        TAIL = (b"\x00" * 7 +
                b"\xbd\xb6\xe1\xf3\x03" +
                b"\x00" +
                b"\xc4\xb6\xe1\xfb\x0b" +
                b"\x00\x00")

    x_i = int(x)
    y_i = int(y)
    z_i = int(z)

    body = bytearray()
    body += await uleb_encode(int(account_uid))
    body += await uleb_encode(int(player_state))
    body += await zigzag_encode_signed(x_i)
    body += await zigzag_encode_signed(y_i)
    body += await zigzag_encode_signed(z_i)
    body += b"\x00"
    body += await uleb_encode(int(move_state))
    body += TAIL
    body += await uleb_encode(int(tick))
    body += b"\x00\x00\x00"
    body += await uleb_encode(int(counter))
    body = bytes(body)

    k = key_bytes[0]
    v80 = ((k << 8) | k) & 0xFFFF

    h = bytearray()
    h.append(msg_key & 0xFF)
    h.append(0x00)
    h.append(0x00 ^ k)
    h += ((2001 ^ v80) & 0xFFFF).to_bytes(2, "little")
    h += ((len(body) ^ v80) & 0xFFFF).to_bytes(2, "little")
    h.append(0x00 ^ k)

    h[1] = (await crc7_buff(0, bytes(h[2:]) + body)) & 0x7F
    return bytes(h) + body


class MovementTracker:
    PLAYER_STATE = {
        "idle":   0,
        "walk":   1,
        "run":    2,
        "sprint": 3,
        "jump":   4,
        "crouch": 5,
        "prone":  6,
    }

    def __init__(self, pattern="circle", radius=180):
        self.pattern = pattern
        self.radius = int(radius)
        self.tick = 0
        self.counter = 0
        self.angle = random.uniform(0, 6.2832)
        self.speed_multiplier = 1.0
        self.dir_flip = 1
        self._next_speed_roll = time.time()

        self.state = "walk"
        self.state_start = time.time()
        self.state_duration = random.uniform(2.0, 4.0)

        self._roll_new_speed()

    def _roll_new_speed(self):
        try:
            lo, hi = float(MOVEMENT_SPEED_MIN), float(MOVEMENT_SPEED_MAX)
            if hi < lo:
                lo, hi = hi, lo
            self.speed_multiplier = round(random.uniform(lo, hi), 2)
        except Exception:
            self.speed_multiplier = 1.0
        self._next_speed_roll = time.time() + MOVEMENT_UPDATE_SPEED_EVERY

    def _pick_next_state(self):
        r = random.random()
        c_idle   = MOVEMENT_STATE_IDLE_CHANCE
        c_walk   = c_idle   + MOVEMENT_STATE_WALK_CHANCE
        c_run    = c_walk   + MOVEMENT_STATE_RUN_CHANCE
        c_sprint = c_run    + MOVEMENT_STATE_SPRINT_CHANCE
        c_jump   = c_sprint + MOVEMENT_STATE_JUMP_CHANCE
        c_crouch = c_jump   + MOVEMENT_STATE_CROUCH_CHANCE

        if r < c_idle:
            return "idle",   random.uniform(0.6, 1.6)
        elif r < c_walk:
            return "walk",   random.uniform(1.6, 3.0)
        elif r < c_run:
            return "run",    random.uniform(2.0, 4.0)
        elif r < c_sprint:
            return "sprint", random.uniform(1.2, 2.5)
        elif r < c_jump:
            return "jump",   random.uniform(0.35, 0.65)
        elif r < c_crouch:
            return "crouch", random.uniform(1.2, 2.5)
        else:
            return "prone",  random.uniform(1.5, 3.5)

    def _generate_movement(self):
        p = (self.pattern or "circle").lower()
        x = y = z = 0
        ms = 0

        if p == "circle":
            self.angle += 0.35 * self.dir_flip
            if self.angle > 6.2832:
                self.angle -= 6.2832
            elif self.angle < 0:
                self.angle += 6.2832
            x = int(self.radius * random.uniform(0.9, 1.1) * (1.0 if self.angle % 3.14 < 1.57 else -1.0))
            y = int(self.radius * random.uniform(0.4, 0.6))
            z = int(self.radius * random.uniform(0.9, 1.1) * (1.0 if self.angle % 6.28 < 3.14 else -1.0))
            ms = 0x0F
        elif p == "zigzag":
            step = self.radius // 4
            x = random.choice([-1, 1]) * random.randint(step, self.radius)
            y = random.randint(-20, 20)
            z = self.dir_flip * random.randint(step, self.radius)
            if random.random() < 0.25:
                self.dir_flip *= -1
            ms = 0x03
        elif p == "forward":
            x = random.randint(-30, 30)
            y = 0
            z = self.radius * self.dir_flip
            ms = 0x01
        else:
            x = random.randint(-self.radius, self.radius)
            y = random.randint(-40, 40)
            z = random.randint(-self.radius, self.radius)
            ms = random.choice([0x01, 0x02, 0x04, 0x08, 0x0F])

        return x, y, z, ms

    def next(self):
        now = time.time()
        if now >= self._next_speed_roll:
            self._roll_new_speed()

        if now - self.state_start > self.state_duration:
            old_state = self.state
            new_state, new_dur = self._pick_next_state()
            self.state = new_state
            self.state_duration = new_dur
            self.state_start = now
            if old_state != new_state:
                log_move(f"State: {old_state} -> {new_state} ({new_dur:.2f}s)")

        self.tick = (self.tick + 1) & 0xFFFFFFFF
        self.counter = (self.counter + 1) & 0xFFFFFFFF

        player_state = self.PLAYER_STATE.get(self.state, 1)

        if self.state in ("idle", "crouch", "prone"):
            x = y = z = 0
            move_state = 0
        else:
            x, y, z, move_state = self._generate_movement()

        return player_state, x, y, z, move_state, self.tick, self.counter, self.speed_multiplier, self.state


async def movement_worker(sock, resolved_ip, port, udp_key_bytes, match_code,
                          account_id, stop_event, match_index):
    if not ENABLE_MOVEMENT:
        return
    loop = asyncio.get_event_loop()
    server_addr = (resolved_ip, port)
    tracker = MovementTracker(pattern=MOVEMENT_PATTERN, radius=MOVEMENT_RADIUS)
    sent = 0

    try:
        await asyncio.sleep(random.uniform(2.5, 3.5))
        while not stop_event.is_set():
            try:
                (player_state, x, y, z, move_state,
                 tick, counter, speed_mult, state_name) = tracker.next()

                pkt = await build_movement_packet_2001(
                    udp_key_bytes, 0x6B, player_state,
                    x, y, z, tick, counter, move_state,
                    account_id, match_code,
                    phase="arena"
                )
                await loop.sock_sendto(sock, pkt, server_addr)
                sent += 1

                if sent % 30 == 0:
                    log_move(
                        f"#{match_index} sent={sent} "
                        f"state={state_name}({player_state}) rate={speed_mult}x"
                    )

                base_wait = MOVEMENT_TICK_MS / 1000.0
                try:
                    sm = max(0.7, min(float(speed_mult), 1.6))
                except Exception:
                    sm = 1.0
                adj_wait = base_wait / sm
                adj_wait = max(0.10, min(adj_wait, 0.40))
                wait = max(0.10, adj_wait + random.uniform(-0.04, 0.04))
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=wait)
                    break
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                log_warn(f"[MOVE] loop error #{match_index}: {e}")
                await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass
    finally:
        try:
            log_move(f"#{match_index} stopped | total_sent={sent}")
        except Exception:
            pass


# ==================== ANTI-AFK (Fire) ====================
async def send_anti_afk_fire(sock, server_addr, udp_key_bytes, match_code, account_id):
    try:
        nr = (await layouts_from_mask(match_code))[1]
        try:
            player_id = int(account_id)
        except Exception:
            player_id = 0
        ts = int(time.time() * 1000) & 0xFFFFFFFF
        weapon_id = random.choice([101, 102, 103, 201, 202])
        fire_body = struct.pack("<IIHB", player_id & 0xFFFFFFFF, ts, weapon_id, 1)
        fire_pkt = await build_packet(0x68, nr, 0, 104, None, 0, fire_body, udp_key_bytes, encrypted=False)
        loop = asyncio.get_event_loop()
        await loop.sock_sendto(sock, fire_pkt, server_addr)
        await asyncio.sleep(random.uniform(0.15, 0.30))
        stop_body = struct.pack("<IIHB", player_id & 0xFFFFFFFF, ts + 200, weapon_id, 0)
        stop_pkt = await build_packet(0x68, nr, 0, 105, None, 0, stop_body, udp_key_bytes, encrypted=False)
        await loop.sock_sendto(sock, stop_pkt, server_addr)
        return True
    except Exception:
        return False


async def anti_afk_worker(sock, resolved_ip, port, udp_key_bytes, match_code,
                          account_id, stop_event, match_index):
    if not ENABLE_ANTI_AFK:
        return
    server_addr = (resolved_ip, port)
    fire_count = 0
    try:
        await asyncio.sleep(random.uniform(2.0, 3.0))
        while not stop_event.is_set():
            try:
                ok = await send_anti_afk_fire(sock, server_addr, udp_key_bytes, match_code, account_id)
                if ok:
                    fire_count += 1
                wait_time = random.uniform(ANTI_AFK_MIN_INTERVAL, ANTI_AFK_MAX_INTERVAL)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=wait_time)
                    break
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass
    finally:
        try:
            if fire_count > 0:
                log_afk(f"#{match_index} | Fires: {fire_count} | UID: {str(account_id)}")
        except Exception:
            pass


# ==================== PLAY GAME ====================
async def play_game(server_ip_port, thunder, sharma, udp_key, match_code,
                    account_id, player_region, client_version, key, iv,
                    match_index: int, mode_label: str = "LONE_WOLF"):
    match_start_time = time.time()
    ping_task = None
    anti_afk_task = None
    movement_task = None
    sock = None
    ping_stop = asyncio.Event()
    anti_afk_stop = asyncio.Event()
    movement_stop = asyncio.Event()
    uid_str = str(account_id)
    completed_cleanly = False
    thunder_sent = False

    is_br = (mode_label == "BR")
    max_duration = BR_MAX_MATCH_DURATION if is_br else LW_MAX_MATCH_DURATION
    idle_timeout = BR_MATCH_IDLE_TIMEOUT if is_br else LW_MATCH_IDLE_TIMEOUT

    try:
        ip, port = server_ip_port.split(":")
        port = int(port)
        resolved_ip = await resolve_host_cloudflare(ip)

        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(('0.0.0.0', 0))
        except Exception:
            pass
        optimize_udp_socket(sock)
        sock.setblocking(False)

        udp_key_bytes = bytes.fromhex(udp_key)

        hello_packet = await build_hello_packet(f"{account_id}_2585", udp_key_bytes, match_code)
        await loop.sock_sendto(sock, bytes.fromhex(hello_packet), (resolved_ip, port))

        ack_state = "waiting_for_hello_reply"
        sharma_sent = False
        join_match_received = False
        local_closed = False
        send_lock = asyncio.Lock()
        anti_afk_started = False

        ping_task = asyncio.create_task(
            keepalive_ping(sock, resolved_ip, port, udp_key_bytes, match_code, ping_stop)
        )
        last_activity = time.time()
        MAX_IDLE_BEFORE_HELLO_RESEND = 7.0

        log_match(f"[MATCH #{match_index}] UDP [{mode_label}] -> {server_ip_port}")

        async def start_workers_once():
            nonlocal anti_afk_task, movement_task, anti_afk_started
            if anti_afk_started:
                return
            anti_afk_started = True
            if ENABLE_ANTI_AFK:
                anti_afk_task = asyncio.create_task(
                    anti_afk_worker(sock, resolved_ip, port, udp_key_bytes, match_code,
                                    account_id, anti_afk_stop, match_index)
                )
                log_afk(f"Worker started | Match #{match_index}")
            if ENABLE_MOVEMENT:
                movement_task = asyncio.create_task(
                    movement_worker(sock, resolved_ip, port, udp_key_bytes, match_code,
                                    account_id, movement_stop, match_index)
                )
                log_move(f"Worker started | Match #{match_index} | pattern={MOVEMENT_PATTERN}")

        async def send_thunder_sharma_inline():
            nonlocal ack_state, thunder_sent, sharma_sent
            if thunder_sent:
                return
            async with send_lock:
                if thunder_sent:
                    return
                try:
                    if HUMAN_MODE:
                        await human_delay(base=0.15, variance=0.08)
                    await loop.sock_sendto(sock, bytes.fromhex(thunder), (resolved_ip, port))
                    thunder_sent = True
                    await asyncio.sleep(0.15 if HUMAN_MODE else 0.1)
                    prepare_ack = await build_packet(
                        0x68, (await layouts_from_mask(match_code))[1],
                        0, 2, None, 1, b"\x01\x00", udp_key_bytes
                    )
                    await loop.sock_sendto(sock, prepare_ack, (resolved_ip, port))
                    await asyncio.sleep(0.25 if HUMAN_MODE else 0.2)
                    await loop.sock_sendto(sock, bytes.fromhex(sharma), (resolved_ip, port))
                    sharma_sent = True
                    ack_state = "thunder_sharma_sent"
                    log_match(f"[MATCH #{match_index}] Startup delivered")
                    await start_workers_once()
                except Exception as e:
                    log_err(f"[MATCH #{match_index}] send: {e}")

        while not local_closed:
            if time.time() - match_start_time > max_duration:
                break
            try:
                response, server_addr = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 65535), timeout=1.5
                )
                if response:
                    last_activity = time.time()
                    frame = await decode_packet(response, udp_key_bytes, match_code)
                    if frame:
                        ptype = await classify(frame)

                        if frame['cmd'] in [103, 107]:
                            log_match(f"[MATCH #{match_index}] Server ended (cmd {frame['cmd']})")
                            completed_cleanly = True
                            local_closed = True
                            continue

                        if frame['cmd'] == 101:
                            try:
                                ack_pkt = await build_packet(
                                    0x68, (await layouts_from_mask(match_code))[1],
                                    0, 2, None, 1, b"\x01\x00", udp_key_bytes
                                )
                                await loop.sock_sendto(sock, ack_pkt, server_addr)
                            except Exception:
                                pass
                            continue

                        if ptype in ["ACK", "PING", "HELLO", "JOIN_MATCH"]:
                            if ptype == "HELLO" and ack_state == "waiting_for_hello_reply":
                                typ, reply = await reply_for(frame, udp_key_bytes, match_code, ack_style="short")
                                if reply:
                                    await loop.sock_sendto(sock, reply, server_addr)
                                ack_state = "ack_sent_waiting"
                            elif ptype == "ACK":
                                if ack_state == "waiting_for_hello_reply":
                                    typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                    if reply:
                                        await loop.sock_sendto(sock, reply, server_addr)
                                    ack_state = "ready_to_send_thunder"
                                elif ack_state == "ack_sent_waiting":
                                    ack_state = "ready_to_send_thunder"
                                else:
                                    typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                    if reply:
                                        await loop.sock_sendto(sock, reply, server_addr)
                            elif ptype == "PING":
                                typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                if reply:
                                    await loop.sock_sendto(sock, reply, server_addr)
                            elif ptype == "JOIN_MATCH" and not join_match_received:
                                typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                if reply:
                                    await loop.sock_sendto(sock, reply, server_addr)
                                    join_match_received = True
            except asyncio.TimeoutError:
                if ack_state == "ready_to_send_thunder" and not thunder_sent:
                    await send_thunder_sharma_inline()
                elif ack_state == "waiting_for_hello_reply":
                    if (time.time() - last_activity) > MAX_IDLE_BEFORE_HELLO_RESEND:
                        try:
                            pkt = await build_hello_packet(f"{account_id}_2585", udp_key_bytes, match_code)
                            await loop.sock_sendto(sock, bytes.fromhex(pkt), (resolved_ip, port))
                        except Exception:
                            pass
                        last_activity = time.time()
                    if (time.time() - match_start_time) > 25.0:
                        log_warn(f"[MATCH #{match_index}] Handshake timeout")
                        break
                elif ack_state == "thunder_sharma_sent":
                    if (time.time() - last_activity) > idle_timeout:
                        log_match(f"[MATCH #{match_index}] Finished naturally")
                        completed_cleanly = True
                        break
                continue
            except BlockingIOError:
                await asyncio.sleep(0.05)
            except OSError:
                await asyncio.sleep(0.5)
                continue
            except Exception:
                await asyncio.sleep(0.5)
                continue

            if ack_state == "ready_to_send_thunder" and not thunder_sent:
                await send_thunder_sharma_inline()

        return f"match #{match_index} finished"
    except Exception as e:
        log_err(f"[MATCH #{match_index}] error: {e}")
        return f"match #{match_index} error"
    finally:
        if completed_cleanly:
            try: bot_state.increment_match(uid_str)
            except Exception: pass
        else:
            try:
                if thunder_sent:
                    bot_state.match_failed(uid_str)
            except Exception: pass

        anti_afk_stop.set()
        movement_stop.set()

        if anti_afk_task:
            try:
                await asyncio.wait_for(anti_afk_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                anti_afk_task.cancel()
                try: await anti_afk_task
                except Exception: pass

        if movement_task:
            try:
                await asyncio.wait_for(movement_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                movement_task.cancel()
                try: await movement_task
                except Exception: pass

        ping_stop.set()
        if ping_task:
            ping_task.cancel()
            try: await ping_task
            except asyncio.CancelledError: pass

        if sock:
            try: sock.close()
            except Exception: pass

        remaining = await _dec_match(uid_str)
        total = await _get_active_match_count()
        log_info(f"[MATCH #{match_index}] Closed | UID active: {remaining} | Total: {total}")
        try:
            bot_state.update_status(uid_str, "IN_MATCH" if remaining > 0 else "ONLINE", remaining)
        except Exception: pass


# ==================== FUNCTIONAL TCP ====================
async def functional_lone_wolf(addrs, starter_packet, account_region, client_version,
                                key, iv, account_id="", account_data=None, max_reconnects=10):
    reconnects = 0
    ip, port = addrs.split(":")
    play_matches: List[asyncio.Task] = []
    no_response_count = 0
    search_attempts = 0
    last_start_time = 0.0
    uid_str = str(account_id)
    consecutive_parse_failures = 0

    behavior = await get_behavior(uid_str)

    current_token = starter_packet
    current_key = key
    current_iv = iv
    current_account_data = account_data
    current_region = normalize_region(account_region)

    try:
        while True:
            writer = None
            try:
                if current_account_data:
                    fresh = None
                    if current_account_data.get('auth_type') == 'guest' and current_account_data.get('auth_uid'):
                        fresh = cache_get(str(current_account_data['auth_uid']))
                    elif current_account_data.get('auth_type') == 'token' and current_account_data.get('auth_token'):
                        fresh = cache_get(f"tok_{current_account_data['auth_token'][:20]}")
                    if fresh:
                        current_account_data = fresh
                        current_key = fresh['aes_ak']
                        current_iv = fresh['iv_i']
                        current_region = normalize_region(fresh.get('region', current_region))
                        current_token = await build_tcp_startup_packet(
                            fresh['account_id'], fresh['token'], fresh['server_time'],
                            current_key, current_iv, region=current_region, typ='OnLine'
                        )
                    else:
                        log_warn(f"Cache miss for {uid_str}")
                        try:
                            if current_account_data.get('auth_uid'):
                                cache_invalidate(str(current_account_data['auth_uid']))
                            if current_account_data.get('auth_token'):
                                cache_invalidate(f"tok_{current_account_data['auth_token'][:20]}")
                        except Exception: pass
                        raise ConnectionError("Cache expired")

                resolved_ip = await resolve_host_cloudflare(ip)
                reader, writer = await asyncio.open_connection(resolved_ip, int(port))
                raw_sock = writer.get_extra_info('socket')
                if raw_sock:
                    optimize_tcp_socket(raw_sock)

                writer.write(bytes.fromhex(current_token))
                await writer.drain()

                try:
                    init_ka = await send_keep_alive(current_region)
                    if init_ka and writer and not writer.is_closing():
                        writer.write(init_ka)
                        await asyncio.wait_for(writer.drain(), timeout=3)
                except Exception: pass

                log_net(f"TCP connected | UID: {uid_str} | Region: {current_region}")
                reconnects = 0
                no_response_count = 0
                last_start_time = 0.0

                async def send_start_match():
                    nonlocal search_attempts, last_start_time
                    search_attempts += 1
                    behavior.on_match_started()

                    if bot_state.is_paused(uid_str):
                        try:
                            bot_state.update_status(uid_str, "PAUSED", 0)
                        except Exception: pass
                        last_start_time = asyncio.get_running_loop().time()
                        return

                    # 🔥 AUTO MODE
                    selected_mode = bot_state.get_account_mode(uid_str)
                    acc_obj = bot_state.accounts.get(uid_str, {})
                    if not acc_obj.get("mode_manual", False):
                        lvl = int(acc_obj.get("level", 1) or 1)
                        selected_mode = "LONE_WOLF" if lvl >= AUTO_LW_LEVEL else "BR"

                    mode_label = "BR" if selected_mode == "BR" else "LW"
                    log_info(f"[{mode_label}] Match #{behavior.matches_count} | Region: {current_region}")

                    try:
                        if HUMAN_MODE:
                            await human_delay(base=1.2, variance=0.6)
                        else:
                            await asyncio.sleep(random.uniform(0.3, 0.6))

                        if selected_mode == "BR":
                            await start_game_battle_royale(current_region, client_version, writer, current_key, current_iv)
                        else:
                            await start_game_lone_wolf(current_region, client_version, writer, current_key, current_iv)

                        if HUMAN_MODE:
                            await human_jitter()
                        active = await _get_match_count(uid_str)
                        try:
                            bot_state.update_status(uid_str, f"SEARCHING ({mode_label})", active)
                        except Exception: pass
                    except Exception as e:
                        log_err(f"StartMatch ({mode_label}): {e}")
                    last_start_time = asyncio.get_running_loop().time()

                await send_start_match()

                while True:
                    play_matches[:] = [m for m in play_matches if not m.done()]

                    active_count = await _get_match_count(uid_str)
                    try:
                        bot_state.update_status(
                            uid_str,
                            "ONLINE" if active_count == 0 else "IN_MATCH",
                            active_count
                        )
                    except Exception: pass

                    if bot_state.is_paused(uid_str):
                        try:
                            bot_state.update_status(uid_str, "PAUSED", 0)
                        except Exception: pass
                        try:
                            data = await asyncio.wait_for(reader.read(8192), timeout=1.0)
                            if not data:
                                raise ConnectionError("Connection closed")
                        except asyncio.TimeoutError:
                            pass
                        continue

                    now = asyncio.get_running_loop().time()
                    current_interval = behavior.get_next_interval()

                    if behavior.should_skip_match():
                        log_info(f"[{uid_str}] Skipping match")
                        await asyncio.sleep(random.uniform(4.0, 10.0))
                        continue

                    if now - last_start_time >= current_interval:
                        if len(play_matches) < MAX_CONCURRENT_MATCHES:
                            await send_start_match()
                        else:
                            last_start_time = now

                    try:
                        data = await asyncio.wait_for(reader.read(8192), timeout=0.5)
                    except asyncio.TimeoutError:
                        no_response_count += 1
                        if no_response_count > 80:
                            log_warn(f"Gateway silent ({uid_str}). Reconnecting...")
                            raise ConnectionError("Gateway idle timeout")
                        continue

                    if not data:
                        raise ConnectionError("Connection closed by server")

                    hex_data = data.hex()
                    packet_length = len(data)
                    no_response_count = 0

                    if hex_data.startswith("0300") and 10 < packet_length < 30:
                        log_info("Match starting, please wait...")
                        continue

                    if hex_data.startswith("0300") and packet_length >= 300:
                        log_match("=" * 50)
                        log_match(f"MATCH FOUND! UID: {uid_str}")
                        log_match("=" * 50)

                        try:
                            res = json.loads(await decode_protobuf(hex_data[10:]))
                            token = None
                            udp_key = None
                            match_code = None
                            server_ip_port = None
                            match_account_id = None
                            block_val = None

                            if '42' in res and 'data' in res['42']:
                                match_code = res['42']['data']
                            if '5' in res and 'data' in res['5']:
                                res_field5 = res['5']['data']
                                server_ip_port = res_field5.get('2', {}).get('data')
                                udp_key = res_field5.get('3', {}).get('data')
                                token = res_field5.get('4', {}).get('data')
                                if '42' in res_field5:
                                    match_code = res_field5['42']['data']
                            if '1' in res and 'data' in res['1']:
                                match_account_id = res['1']['data']
                            if '5' in res and 'data' in res['5']:
                                block_val = res['5']['data'].get('1', {}).get('data')

                            effective_acc_id = match_account_id or account_id or "BOT"

                            if token and udp_key and match_code and server_ip_port:
                                acc_tok = ""
                                if current_account_data:
                                    acc_tok = current_account_data.get('access_token', '') or ""

                                match_mode = bot_state.get_account_mode(uid_str)
                                # BR = (1,1)  |  LW = (43,11)
                                mode_id, map_id = (1, 1) if match_mode == "BR" else (43, 11)
                                mode_label = "BR" if match_mode == "BR" else "LW"

                                thunder, sharma = await build_match_startup_packets(
                                    token, udp_key, match_code, effective_acc_id, block_val or 0,
                                    server_ip=server_ip_port,
                                    region=current_region,
                                    client_version=client_version,
                                    access_token=acc_tok,
                                    mode_id=mode_id, map_id=map_id
                                )

                                match_index = await _inc_match(uid_str)
                                try: bot_state.increment_match_started(uid_str)
                                except Exception: pass
                                total = await _get_active_match_count()
                                log_match(f"[{mode_label}] Match #{match_index} -> UDP {server_ip_port}")

                                new_match = asyncio.create_task(
                                    play_game(
                                        server_ip_port, thunder, sharma, udp_key, match_code,
                                        effective_acc_id, current_region, client_version,
                                        current_key, current_iv,
                                        match_index=match_index, mode_label=mode_label
                                    )
                                )
                                play_matches.append(new_match)
                                consecutive_parse_failures = 0

                                try:
                                    writer.close()
                                    await writer.wait_closed()
                                except Exception: pass

                                log_info(f"Offline {NEW_MATCH_DELAY}s -> reload token")
                                await asyncio.sleep(NEW_MATCH_DELAY)
                                reconnects = 0
                                break
                            else:
                                consecutive_parse_failures += 1
                                log_warn(f"Non-match packet ({consecutive_parse_failures}/{int(MAX_CONSECUTIVE_PARSE_FAILURES)})")
                                if consecutive_parse_failures >= MAX_CONSECUTIVE_PARSE_FAILURES:
                                    log_err(f"{int(MAX_CONSECUTIVE_PARSE_FAILURES)}x fails -> invalidation")
                                    if current_account_data:
                                        try:
                                            if current_account_data.get('auth_uid'):
                                                cache_invalidate(str(current_account_data['auth_uid']))
                                            if current_account_data.get('auth_token'):
                                                cache_invalidate(f"tok_{current_account_data['auth_token'][:20]}")
                                        except Exception: pass
                                    consecutive_parse_failures = 0
                                try:
                                    writer.close()
                                    await writer.wait_closed()
                                except Exception: pass
                                await asyncio.sleep(NON_MATCH_RECONNECT_DELAY)
                                break

                        except Exception as e:
                            log_err(f"Match packet error: {e}")
                            consecutive_parse_failures += 1
                            if consecutive_parse_failures >= MAX_CONSECUTIVE_PARSE_FAILURES:
                                if current_account_data:
                                    try:
                                        if current_account_data.get('auth_uid'):
                                            cache_invalidate(str(current_account_data['auth_uid']))
                                        if current_account_data.get('auth_token'):
                                            cache_invalidate(f"tok_{current_account_data['auth_token'][:20]}")
                                    except Exception: pass
                                consecutive_parse_failures = 0
                            try:
                                writer.close()
                                await writer.wait_closed()
                            except Exception: pass
                            await asyncio.sleep(NON_MATCH_RECONNECT_DELAY)
                            break

                    if 30 <= packet_length <= 40:
                        continue

            except asyncio.CancelledError:
                log_warn(f"Cancelled - stopping {len(play_matches)} matches")
                for m in play_matches:
                    if not m.done(): m.cancel()
                if play_matches:
                    await asyncio.gather(*play_matches, return_exceptions=True)
                play_matches.clear()
                raise
            except Exception as e:
                log_err(f"TCP state ({uid_str}): {e}")
                play_matches[:] = [m for m in play_matches if not m.done()]
                if writer:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception: pass
                if "Cache expired" in str(e):
                    log_warn(f"Re-login for {uid_str}")
                    break
                reconnects += 1
                if reconnects > max_reconnects:
                    log_err("Max reconnects reached")
                    reconnects = 0
                    await asyncio.sleep(3)
                    continue
                await asyncio.sleep(min(reconnects, 2))

    except asyncio.CancelledError:
        log_warn(f"Outer cancelled")
        for m in play_matches:
            if not m.done(): m.cancel()
        if play_matches:
            await asyncio.gather(*play_matches, return_exceptions=True)
        play_matches.clear()
        raise


async def informational(addrs, starter_packet, key, iv, region="ID", max_reconnects=3):
    reconnects = 0
    ip, port = addrs.split(":")
    region = normalize_region(region)
    while True:
        writer = None
        ping_task = None
        try:
            resolved_ip = await resolve_host_cloudflare(ip)
            reader, writer = await asyncio.open_connection(resolved_ip, int(port))
            raw_sock = writer.get_extra_info('socket')
            if raw_sock:
                optimize_tcp_socket(raw_sock)
            writer.write(bytes.fromhex(starter_packet))
            await writer.drain()
            reconnects = 0
            try:
                init_ka = await send_keep_alive(region)
                if init_ka and writer and not writer.is_closing():
                    writer.write(init_ka)
                    await asyncio.wait_for(writer.drain(), timeout=3)
            except Exception: pass

            async def info_keepalive():
                ka_bytes = await send_keep_alive(region)
                while True:
                    await asyncio.sleep(5)
                    try:
                        if writer and not writer.is_closing():
                            writer.write(ka_bytes)
                            await writer.drain()
                    except Exception:
                        break

            ping_task = asyncio.create_task(info_keepalive())
            while True:
                data = await reader.read(8192)
                if not data:
                    raise ConnectionError("Closed")
        except asyncio.CancelledError:
            if ping_task: ping_task.cancel()
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception: pass
            raise
        except Exception:
            if ping_task: ping_task.cancel()
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception: pass
            reconnects += 1
            if reconnects > max_reconnects:
                await asyncio.sleep(3)
                reconnects = 0
            else:
                await asyncio.sleep(1)


# ==================== ACCOUNT PROCESSING ====================
def _register_credentials(account_data: Dict):
    try:
        acc_id = str(account_data['account_id'])
        bot_state.account_credentials[acc_id] = account_data
        if account_data.get('auth_uid'):
            bot_state.account_credentials[str(account_data['auth_uid'])] = account_data
        if account_data.get('auth_token'):
            bot_state.account_credentials[f"tok_{account_data['auth_token'][:20]}"] = account_data
    except Exception: pass


async def refresh_account_profile(account_data_or_uid: Any):
    try:
        if isinstance(account_data_or_uid, str):
            uid = str(account_data_or_uid)
            account_data = bot_state.account_credentials.get(uid)
        else:
            account_data = account_data_or_uid
            uid = str(account_data.get('account_id'))
        if not account_data: return

        url = account_data.get('server_url')
        token = account_data.get('token')
        release_version = account_data.get('release_version')
        payload = account_data.get('login_payload_data')
        if not (url and token and release_version and payload): return

        async with _REFRESH_SEMAPHORE:
            res = await send_getlogin(payload, url, token, release_version)
        if res:
            res_proto, dict_res = res
            level = int(get_proto_field(dict_res, 6, 1))
            exp = int(get_proto_field(dict_res, 7, 0))
            likes = int(get_proto_field(dict_res, 8, 0))
            nickname = res_proto.nickname or get_proto_field(dict_res, 4, "")

            acc_id = str(account_data['account_id'])
            if exp > 0:
                old_exp = bot_state.accounts.get(acc_id, {}).get("current_exp", 0)
                bot_state.update_exp(acc_id, exp, level)
                if old_exp and exp > old_exp:
                    diff = exp - old_exp
                    log_exp(f"+{diff:,} EXP | UID: {acc_id} | Lv {level}")
            if likes > 0 and acc_id in bot_state.accounts:
                bot_state.accounts[acc_id]["likes"] = likes
            if nickname and acc_id in bot_state.accounts:
                bot_state.accounts[acc_id]["nickname"] = nickname
    except Exception as e:
        log_err(f"refresh_account_profile: {e}")


async def _process_account_uid_pass(uid: str, password: str) -> Optional[Dict]:
    cached = cache_get(uid)
    if cached:
        acc_id = str(cached['account_id'])
        nick = cached.get('nickname', f"Player_{acc_id}")
        lvl = cached.get('level', 1)
        exp_val = cached.get('exp', 0)
        region = cached.get('region', DEFAULT_REGION)
        log_cache(f"Loaded UID {acc_id} | {nick} | Lv{lvl} | EXP: {exp_val:,} | Region: {region}")
        bot_state.register_account(uid=acc_id, nickname=nick, region=region,
                                   level=lvl, exp=exp_val, likes=cached.get('likes', 0))
        _register_credentials(cached)
        return cached

    log_login(f"Authenticating UID {uid}...")
    try:
        verconfig_res = await version_config()
        if verconfig_res is None:
            log_err(f"version_config failed")
            return None
        release_version, client_version, server_url = verconfig_res

        tokengrant_response = await get_access_token(uid, password)
        if tokengrant_response is None:
            log_err(f"get_access_token failed")
            return None
        open_id, access_token, platform = tokengrant_response

        device_info = get_device_for_account(uid)

        # 🔥 try login with default region first
        login_payload_data = await build_majorlogin_payload(
            open_id, access_token, platform, client_version, device_info, DEFAULT_REGION
        )
        if not login_payload_data:
            log_err(f"payload build failed")
            return None

        majorlogin_response = await send_majorlogin(login_payload_data, release_version, server_url)
        if majorlogin_response is None:
            log_err(f"MajorLogin rejected")
            return None

        # 🔥 read real region from server response
        detected_region = normalize_region(majorlogin_response.region or DEFAULT_REGION)
        if detected_region != DEFAULT_REGION:
            log_info(f"Server region detected: {detected_region} — retrying login with correct region")
            # rebuild payload with detected region and retry
            login_payload_data = await build_majorlogin_payload(
                open_id, access_token, platform, client_version, device_info, detected_region
            )
            majorlogin_response2 = await send_majorlogin(login_payload_data, release_version, server_url)
            if majorlogin_response2 is not None:
                majorlogin_response = majorlogin_response2

        getlogin_result = await send_getlogin(
            login_payload_data, majorlogin_response.url,
            majorlogin_response.token, release_version
        )
        if getlogin_result is None:
            log_err(f"GetLoginData failed")
            return None
        res_proto, dict_res = getlogin_result

        acc_id = str(majorlogin_response.account_id)
        level = int(get_proto_field(dict_res, 6, 1))
        exp = int(get_proto_field(dict_res, 7, 0))
        likes = int(get_proto_field(dict_res, 8, 0))
        nickname = res_proto.nickname or get_proto_field(dict_res, 4, f"Player_{acc_id}")
        region = normalize_region(majorlogin_response.region or DEFAULT_REGION)

        bot_state.register_account(uid=acc_id, nickname=nickname, region=region,
                                   level=level, exp=exp, likes=likes)
        log_ok(f"Login OK | UID: {acc_id} | {nickname} | Lv{level} | EXP: {exp:,} | Region: {region}")

        account_data = {
            'account_id': majorlogin_response.account_id,
            'nickname': nickname, 'region': region,
            'level': level, 'exp': exp, 'likes': likes,
            'open_id': open_id, 'access_token': access_token,
            'platform': str(platform),
            'token': majorlogin_response.token,
            'server_time': majorlogin_response.server_time,
            'aes_ak': majorlogin_response.aes_ak,
            'iv_i': majorlogin_response.iv_i,
            'functional_addrs': res_proto.functional_addrs or get_proto_field(dict_res, 14),
            'informational_addrs': res_proto.informational_addrs or get_proto_field(dict_res, 32),
            'release_version': release_version,
            'client_version': client_version,
            'server_url': majorlogin_response.url,
            'login_payload_data': login_payload_data,
            'auth_type': 'guest',
            'auth_uid': uid, 'auth_password': password
        }
        _register_credentials(account_data)
        cache_set(uid, account_data)
        return account_data
    except Exception as e:
        log_err(f"process_account_uid_pass: {e}")
        traceback.print_exc()
        return None


async def _process_account_token(access_token: str) -> Optional[Dict]:
    cache_key = f"tok_{access_token[:20]}"
    cached = cache_get(cache_key)
    if cached:
        acc_id = str(cached['account_id'])
        nick = cached.get('nickname', f"Player_{acc_id}")
        lvl = cached.get('level', 1)
        exp_val = cached.get('exp', 0)
        region = cached.get('region', DEFAULT_REGION)
        log_cache(f"Token loaded | UID {acc_id} | {nick} | Lv{lvl} | EXP: {exp_val:,} | Region: {region}")
        bot_state.register_account(uid=acc_id, nickname=nick, region=region,
                                   level=lvl, exp=exp_val, likes=cached.get('likes', 0))
        _register_credentials(cached)
        return cached

    log_login("Authenticating with Token...")
    try:
        verconfig_res = await version_config()
        if verconfig_res is None:
            log_err("version_config failed")
            return None
        release_version, client_version, server_url = verconfig_res

        import requests
        url = f"https://100067.connect.garena.com/oauth/token/inspect?token={access_token}"
        hdrs = {
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "close",
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "100067.connect.garena.com",
            "User-Agent": "GarenaMSDK/4.0.19P4(G011A ;Android 9;en;US;)"
        }
        resp = await asyncio.to_thread(requests.get, url, headers=hdrs, timeout=10)
        data = resp.json()
        if 'error' in data:
            log_err(f"Token error: {data.get('error')}")
            return None
        open_id = data.get('open_id')
        platform = data.get('platform', 4)
        if not open_id:
            log_err("No open_id")
            return None

        device_info = get_device_for_account(open_id)
        login_payload_data = await build_majorlogin_payload(
            open_id, access_token, str(platform), client_version, device_info, DEFAULT_REGION
        )
        if not login_payload_data:
            return None

        majorlogin_response = await send_majorlogin(login_payload_data, release_version, server_url)
        if majorlogin_response is None:
            return None

        detected_region = normalize_region(majorlogin_response.region or DEFAULT_REGION)
        if detected_region != DEFAULT_REGION:
            log_info(f"Server region detected: {detected_region} — retrying with correct region")
            login_payload_data = await build_majorlogin_payload(
                open_id, access_token, str(platform), client_version, device_info, detected_region
            )
            majorlogin_response2 = await send_majorlogin(login_payload_data, release_version, server_url)
            if majorlogin_response2 is not None:
                majorlogin_response = majorlogin_response2

        getlogin_result = await send_getlogin(
            login_payload_data, majorlogin_response.url,
            majorlogin_response.token, release_version
        )
        if getlogin_result is None:
            return None

        res_proto, dict_res = getlogin_result
        acc_id = str(majorlogin_response.account_id)
        level = int(get_proto_field(dict_res, 6, 1))
        exp = int(get_proto_field(dict_res, 7, 0))
        likes = int(get_proto_field(dict_res, 8, 0))
        nickname = res_proto.nickname or get_proto_field(dict_res, 4, f"Player_{acc_id}")
        region = normalize_region(majorlogin_response.region or DEFAULT_REGION)

        bot_state.register_account(uid=acc_id, nickname=nickname, region=region,
                                   level=level, exp=exp, likes=likes)
        log_ok(f"Token OK | UID: {acc_id} | {nickname} | Lv{level} | EXP: {exp:,} | Region: {region}")

        account_data = {
            'account_id': majorlogin_response.account_id,
            'nickname': nickname, 'region': region,
            'level': level, 'exp': exp, 'likes': likes,
            'open_id': open_id, 'access_token': access_token,
            'platform': str(platform),
            'token': majorlogin_response.token,
            'server_time': majorlogin_response.server_time,
            'aes_ak': majorlogin_response.aes_ak,
            'iv_i': majorlogin_response.iv_i,
            'functional_addrs': res_proto.functional_addrs or get_proto_field(dict_res, 14),
            'informational_addrs': res_proto.informational_addrs or get_proto_field(dict_res, 32),
            'release_version': release_version,
            'client_version': client_version,
            'server_url': majorlogin_response.url,
            'login_payload_data': login_payload_data,
            'auth_type': 'token', 'auth_token': access_token
        }
        _register_credentials(account_data)
        cache_set(cache_key, account_data)
        return account_data
    except Exception as e:
        log_err(f"process_account_token: {e}")
        traceback.print_exc()
        return None


async def process_account_uid_pass(uid: str, password: str) -> Optional[Dict]:
    cached = cache_get(uid)
    if cached:
        acc_id = str(cached['account_id'])
        bot_state.register_account(
            uid=acc_id, nickname=cached.get('nickname', f"Player_{acc_id}"),
            region=cached.get('region', DEFAULT_REGION), level=cached.get('level', 1),
            exp=cached.get('exp', 0), likes=cached.get('likes', 0)
        )
        _register_credentials(cached)
        log_cache(f"Cache HIT for UID {uid}")
        return cached
    async with _LOGIN_SEMAPHORE:
        return await _process_account_uid_pass(uid, password)


async def process_account_token(access_token: str) -> Optional[Dict]:
    cache_key = f"tok_{access_token[:20]}"
    cached = cache_get(cache_key)
    if cached:
        acc_id = str(cached['account_id'])
        bot_state.register_account(
            uid=acc_id, nickname=cached.get('nickname', f"Player_{acc_id}"),
            region=cached.get('region', DEFAULT_REGION), level=cached.get('level', 1),
            exp=cached.get('exp', 0), likes=cached.get('likes', 0)
        )
        _register_credentials(cached)
        log_cache(f"Cache HIT for token")
        return cached
    async with _LOGIN_SEMAPHORE:
        return await _process_account_token(access_token)


# ==================== WORKER ====================
async def run_account_worker(account_data: Dict, label: str):
    acc_id = str(account_data['account_id'])
    informational_task = None
    exp_task = None
    try:
        reg = normalize_region(account_data.get('region', DEFAULT_REGION))

        tcp_packet_online = await build_tcp_startup_packet(
            account_data['account_id'], account_data['token'], account_data['server_time'],
            account_data['aes_ak'], account_data['iv_i'], region=reg, typ='OnLine'
        )

        tcp_packet_chat = await build_tcp_startup_packet(
            account_data['account_id'], account_data['token'], account_data['server_time'],
            account_data['aes_ak'], account_data['iv_i'], region=reg, typ='ChaT'
        )

        informational_task = asyncio.create_task(
            informational(
                account_data['informational_addrs'], tcp_packet_chat,
                account_data['aes_ak'], account_data['iv_i'], region=reg
            )
        )

        async def exp_refresher():
            await asyncio.sleep(random.uniform(15.0, 45.0))
            while True:
                try:
                    fresh = bot_state.account_credentials.get(acc_id)
                    if fresh:
                        await refresh_account_profile(fresh)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log_warn(f"[EXP] {acc_id}: {e}")
                await asyncio.sleep(random.uniform(120.0, 180.0))

        exp_task = asyncio.create_task(exp_refresher())

        functional_task = asyncio.create_task(
            functional_lone_wolf(
                account_data['functional_addrs'], tcp_packet_online, reg,
                account_data['client_version'], account_data['aes_ak'],
                account_data['iv_i'], account_id=acc_id, account_data=account_data
            )
        )

        await functional_task
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log_err(f"run_account_worker [{label}]: {e}")
    finally:
        for t in (informational_task, exp_task):
            if t and not t.done(): t.cancel()
        for t in (informational_task, exp_task):
            if t:
                try: await t
                except (asyncio.CancelledError, Exception): pass


async def account_loop_guest(uid: str, password: str):
    fail_count = 0
    while True:
        try:
            while bot_state.is_paused(str(uid)):
                try: bot_state.update_status(str(uid), "PAUSED")
                except Exception: pass
                await asyncio.sleep(5)

            log_login(f"Starting login for Guest UID: {uid}")
            try: bot_state.update_status(str(uid), "CONNECTING")
            except Exception: pass

            account_data = await process_account_uid_pass(uid, password)
            if not account_data:
                fail_count += 1
                try:
                    bot_state.mark_login_failure(str(uid))
                except Exception: pass

                if fail_count >= 5:
                    log_err(f"{uid} - 5 consecutive fails. Cooling down 5 min...")
                    try: bot_state.update_status(str(uid), "ERROR")
                    except Exception: pass
                    await asyncio.sleep(300)
                    fail_count = 0
                else:
                    backoff = min(60, 15 * fail_count)
                    log_err(f"Login failed for {uid}. Retry {backoff}s...")
                    try: bot_state.update_status(str(uid), "ERROR")
                    except Exception: pass
                    await asyncio.sleep(backoff)
                continue

            fail_count = 0
            await run_account_worker(account_data, uid)
            log_warn(f"Session finished. Reconnect 3s...")
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            log_warn(f"Worker for {uid} stopped.")
            try: bot_state.update_status(str(uid), "OFFLINE")
            except Exception: pass
            break
        except Exception as e:
            log_err(f"Error for UID {uid}: {e}. Retry 10s...")
            await asyncio.sleep(10)


async def account_loop_token(token: str):
    token_label = token[:10]
    fail_count = 0
    while True:
        try:
            log_login("Starting login with Token...")
            account_data = await process_account_token(token)
            if not account_data:
                fail_count += 1
                if fail_count >= 5:
                    log_err(f"Token {token_label} - 5 fails. Cooling 5 min...")
                    await asyncio.sleep(300)
                    fail_count = 0
                else:
                    backoff = min(60, 15 * fail_count)
                    log_err(f"Login failed. Retry {backoff}s...")
                    await asyncio.sleep(backoff)
                continue
            fail_count = 0
            acc_id = str(account_data['account_id'])
            await run_account_worker(account_data, acc_id)
            log_warn("Session finished. Reconnect 3s...")
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            log_warn(f"Worker for token {token_label} stopped.")
            break
        except Exception as e:
            log_err(f"Token error: {e}. Retry 10s...")
            await asyncio.sleep(10)


def load_accounts():
    accounts = []
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    accounts = data
        except Exception as e:
            log_err(f"Load {ACCOUNTS_FILE}: {e}")
    if not accounts and FALLBACK_UID and FALLBACK_PASSWORD:
        accounts.append({"uid": FALLBACK_UID, "password": FALLBACK_PASSWORD})
    return accounts


# ==================== MAIN ====================
async def main():
    log_banner()
    log_separator("CONFIGURATION")
    log_info(f"Default Region       : {DEFAULT_REGION}  (set FF_REGION=IND/BD/SG to change)")
    log_info(f"Auto Region Detect   : ON  (reads real region from server response)")
    log_info(f"Start Match Interval : {START_MATCH_INTERVAL_MIN}-{START_MATCH_INTERVAL_MAX}s (random)")
    log_info(f"Offline Wait         : {NEW_MATCH_DELAY}s")
    log_info(f"BR Max Duration      : {BR_MAX_MATCH_DURATION}s")
    log_info(f"LW Max Duration      : {LW_MAX_MATCH_DURATION}s")
    log_info(f"Cache TTL            : {TOKEN_CACHE_TTL}s")
    log_info(f"HUMAN MODE           : {'ON' if HUMAN_MODE else 'OFF'}")
    log_info(f"BEHAVIOUR ENGINE     : {'ON' if HUMAN_BEHAVIOUR_ENABLED else 'OFF'}")
    log_info(f"MOVEMENT             : {'ON' if ENABLE_MOVEMENT else 'OFF'}  | pattern={MOVEMENT_PATTERN}")
    log_info(f"ANTI-AFK FIRE        : {'ON' if ENABLE_ANTI_AFK else 'OFF'}")
    log_info(f"AUTO SWITCH          : Lv1-2 = BR | Lv{AUTO_LW_LEVEL}+ = LW")

    try:
        await start_web_dashboard(host=WEB_HOST, port=WEB_PORT)
        log_ok(f"Dashboard live -> http://localhost:{WEB_PORT}")
    except Exception as e:
        log_err(f"Dashboard failed: {e}")

    async def on_account_added_handler(data):
        if "token" in data and data["token"]:
            t = str(data["token"]).strip()
            key = t[:10]
            existing = bot_state.account_workers.get(key)
            if existing and not existing.done():
                return
            task = asyncio.create_task(account_loop_token(t))
            bot_state.account_workers[key] = task
        elif "uid" in data and "password" in data:
            u = str(data["uid"]).strip()
            p = str(data["password"]).strip()
            existing = bot_state.account_workers.get(u)
            if existing and not existing.done():
                return
            task = asyncio.create_task(account_loop_guest(u, p))
            bot_state.account_workers[u] = task

    async def on_refresh_account_handler(uid):
        await refresh_account_profile(uid)

    bot_state.refresh_callbacks["on_account_added"] = on_account_added_handler
    bot_state.refresh_callbacks["on_refresh_account"] = on_refresh_account_handler

    accounts = load_accounts()

    if not accounts:
        log_warn(f"No accounts in {ACCOUNTS_FILE}!")
        log_warn(f"Add via Dashboard: http://localhost:{WEB_PORT}")
    else:
        log_separator(f"LOADING {len(accounts)} ACCOUNTS")
        for index, acc in enumerate(accounts):
            if index:
                await asyncio.sleep(random.uniform(1.0, 2.5))
            if "token" in acc and acc["token"]:
                t = asyncio.create_task(account_loop_token(acc["token"]))
                bot_state.account_workers[acc["token"][:10]] = t
            elif "uid" in acc and "password" in acc and acc["uid"]:
                u = str(acc["uid"])
                t = asyncio.create_task(account_loop_guest(u, acc["password"]))
                bot_state.account_workers[u] = t
        log_ok(f"All {len(accounts)} accounts launched")

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log_warn("Shutting down...")
        for t in list(bot_state.account_workers.values()):
            t.cancel()
        await asyncio.gather(*bot_state.account_workers.values(), return_exceptions=True)
        try:
            await client.aclose()
        except Exception:
            pass
        log_ok("All sessions closed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log_warn("Program stopped.")