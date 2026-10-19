#!/usr/bin/env python3
"""
Astral Guard - ASUS ROG Astral RTX 5090/5080 Per-Pin Imbalance Watchdog

Monitors per-connector current balance via the ITE IT8915FN on the ASUS
Astral PCB. If one 12V-2x6 connector carries dramatically more load than
the others, the connector heats up and can fail.

On trigger: hard power cut via SysRq 'b' (immediate ACPI power off).
"""
import argparse, ctypes, fcntl, glob, json, logging, os, struct
import subprocess, sys, time
from typing import Dict, List, Optional, Tuple

# ── IT8915FN I2C protocol ────────────────────────────────────────────────
IT8915_ADDR = 0x2B
IT8915_REG  = 0x80
IT8915_LEN  = 24
I2C_SLAVE   = 0x0703
I2C_SMBUS   = 0x0720
I2C_READ    = 1
SMBUS_BYTE  = 2
VOLTS_MIN   = 10000
VOLTS_MAX   = 14000
LOG_DIR     = "/var/log/astral-guard"
LOG_FILE    = "trigger.log"

log = logging.getLogger("astral-guard")

# ── ctypes I2C ───────────────────────────────────────────────────────────
class _smbus_data(ctypes.Union):
    _fields_ = [("byte", ctypes.c_ubyte),
                ("word", ctypes.c_ushort),
                ("block", ctypes.c_ubyte * 34)]

class _smbus_ioctl(ctypes.Structure):
    _fields_ = [("rw",   ctypes.c_ubyte),
                ("cmd",  ctypes.c_ubyte),
                ("size", ctypes.c_uint),
                ("data", ctypes.POINTER(_smbus_data))]

def i2c_open(bus: int) -> int:
    return os.open(f"/dev/i2c-{bus}", os.O_RDWR)

def i2c_slave(fd: int, addr: int) -> None:
    fcntl.ioctl(fd, I2C_SLAVE, addr)

def smbus_byte(fd: int, reg: int) -> int:
    d = _smbus_data()
    a = _smbus_ioctl(rw=I2C_READ, cmd=reg, size=SMBUS_BYTE,
                     data=ctypes.pointer(d))
    fcntl.ioctl(fd, I2C_SMBUS, a)
    return d.byte

def i2c_read(fd: int, reg: int, n: int) -> bytes:
    return bytes(smbus_byte(fd, reg + i) for i in range(n))

def read_pins(fd: int) -> List[Tuple[int, float, float]]:
    d = i2c_read(fd, IT8915_REG, IT8915_LEN)
    pins = []
    for r in range(6):
        p = 6 - r
        o = r * 4
        v = struct.unpack(">H", d[o:o+2])[0] / 1000.0
        a = struct.unpack(">H", d[o+2:o+4])[0] / 1000.0
        pins.append((p, v, a))
    return sorted(pins, key=lambda x: -x[2])

def probe(bus: int) -> bool:
    try:
        fd = i2c_open(bus); i2c_slave(fd, IT8915_ADDR)
        d = i2c_read(fd, IT8915_REG, 4)
        v = struct.unpack(">H", d[:2])[0]
        os.close(fd)
        return VOLTS_MIN <= v <= VOLTS_MAX
    except Exception:
        return False

def discover_buses() -> List[Tuple[int, str]]:
    buses = []
    for p in sorted(glob.glob("/sys/bus/i2c/devices/i2c-*/name")):
        try:
            with open(p) as f: n = f.read().strip()
        except Exception: continue
        if n.startswith("NVIDIA i2c adapter 1 at "):
            pci = n.split(" at ")[1]
            bd = os.path.basename(os.path.dirname(p))
            buses.append((int(bd.split("-")[1]), pci))
    return buses

def gpu_map() -> Dict[str, int]:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,pci.bus_id",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if r.returncode != 0: return {}
        m = {}
        for line in r.stdout.strip().split("\n"):
            parts = line.split(", ")
            if len(parts) == 2:
                dom = parts[1].strip().split(":")[1].lower()
                m[dom.lstrip("0").rjust(3, "0") or "0"] = int(parts[0])
        return m
    except Exception:
        return {}


# ── Log to file before shutdown ──────────────────────────────────────────
def write_trigger_log(readings):
    """Write trigger details to log file before power cut."""
    import datetime
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, LOG_FILE)
        ts = datetime.datetime.now().isoformat()
        with open(path, "a") as f:
            f.write("\n=== TRIGGERED %s ===\n" % ts)
            for r in readings:
                f.write("GPU %s (pci=%s)\n" % (r.get("gpu","?"), r.get("pci","?")))
                if "pins" in r:
                    for i, a in enumerate(r["pins"], 1):
                        f.write("  Pin %d: %.2fA\n" % (i, a))
                f.write("  Spread: %s\n" % r.get("spread", "N/A"))
                if r.get("error"):
                    f.write("  ERROR: %s\n" % r["error"])
            f.write("\n")
        log.info("Trigger log written: %s", path)
    except Exception as e:
        log.error("Failed to write trigger log: %s", e)

# ── Hard power cut ───────────────────────────────────────────────────────
def hard_poweroff() -> None:
    """Sync dirty pages then hard power cut via SysRq 'b' (~50ms)."""
    log.critical("HARD POWER CUT: sync + sysrq 'b'")
    try:
        os.sync()  # flush all dirty pages to disk
        with open("/proc/sysrq-trigger", "w") as f:
            f.write("b")
    except Exception as e:
        log.error("sysrq failed: %s — fallback", e)
        try:
            subprocess.run(["shutdown", "-h", "now", "-f"], timeout=5)
        except Exception:
            pass

# ── Guard class ──────────────────────────────────────────────────────────
class Guard:
    def __init__(self, thr: float, mnc: float, sust: int,
                 ivl: float, dry: bool):
        self.thr  = thr
        self.mnc  = mnc
        self.sust = sust
        self.ivl  = ivl
        self.dry  = dry
        self.gpus = []
        self.fds  = {}

    def discover(self):
        gm = gpu_map()
        for bus, pci in discover_buses():
            if not probe(bus):
                continue
            dom = pci.split(":")[1].lower()
            idx = gm.get(dom.lstrip("0").rjust(3, "0") or "0", -1)
            self.gpus.append((idx, bus, pci))
        self.gpus.sort(key=lambda x: x[0] if x[0] >= 0 else 999)

    def fd(self, bus: int) -> int:
        if bus not in self.fds:
            fd = i2c_open(bus); i2c_slave(fd, IT8915_ADDR)
            self.fds[bus] = fd
        return self.fds[bus]

    def close(self):
        for f in self.fds.values():
            try: os.close(f)
            except Exception: pass

    @staticmethod
    def spread(cur: List[float]) -> Optional[float]:
        if len(cur) < 2: return None
        mx, mn = max(cur), min(cur)
        avg = sum(cur) / len(cur)
        if avg < 0.1: return None
        return (mx - mn) / avg

    def read(self) -> List[dict]:
        res = []
        for idx, bus, pci in self.gpus:
            try:
                pins = read_pins(self.fd(bus))
                act  = [a for _, _, a in pins if a >= self.mnc]
                sp   = self.spread(act)
                res.append({
                    "gpu": idx, "pci": pci, "bus": bus,
                    "pins": [round(a, 3) for _, _, a in pins],
                    "active": len(act),
                    "spread": round(sp, 4) if sp is not None else None,
                    "alert": sp is not None and sp > self.thr,
                })
            except Exception as e:
                res.append({"gpu": idx, "pci": pci, "error": str(e)})
        return res

    def shutdown(self, reason: str, readings: List[dict]):
        log.critical("*** TRIGGERED: %s ***", reason)
        write_trigger_log(readings)
        if self.dry:
            log.info("[DRY-RUN] would hard power cut")
            return
        hard_poweroff()

    def run(self) -> int:
        if not self.gpus:
            log.error("No Astral GPUs found")
            return 1
        log.info("Started: %d GPU(s) thr=%.2f min=%.1fA sust=%d ivl=%.1fs dry=%s",
                 len(self.gpus), self.thr, self.mnc, self.sust, self.ivl, self.dry)
        bad = 0
        try:
            while True:
                results = self.read()
                ok   = True
                worst = 0.0
                for r in results:
                    if "error" in r or r.get("spread") is None:
                        continue
                    sp = r["spread"]
                    if sp > worst: worst = sp
                    if r["alert"]: ok = False
                    log.debug("GPU%s spread=%.3f %s",
                              r["gpu"], sp,
                              "ALERT" if r["alert"] else "ok")
                if ok:
                    if bad:
                        log.info("Cleared (%d consecutive alerts)", bad)
                    bad = 0
                else:
                    bad += 1
                    log.warning("Imbalance: spread=%.2f (thr=%.2f) %d/%d",
                                worst, self.thr, bad, self.sust)
                    if bad >= self.sust:
                        detail = ", ".join(
                            f"GPU{r['gpu']}={r.get('spread')}"
                            for r in results if r.get("spread") is not None)
                        self.shutdown(detail, results)
                        return 0
                time.sleep(self.ivl)
        except KeyboardInterrupt:
            log.info("Stopped by user")
        finally:
            self.close()
        return 0

# ── main ─────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Astral GPU Per-Pin Imbalance Watchdog")
    ap.add_argument("--threshold", type=float, default=1.0,
                    help="max spread ratio (default: 1.0)")
    ap.add_argument("--min-current", type=float, default=1.0,
                    help="min amps for active pin (default: 1.0)")
    ap.add_argument("--sustain", type=int, default=3,
                    help="consecutive bad reads (default: 3)")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="poll seconds (default: 1.0)")
    ap.add_argument("--dry-run", action="store_true",
                    help="log but do not power cut")
    ap.add_argument("--once", action="store_true",
                    help="single read, print JSON, exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr)

    g = Guard(a.threshold, a.min_current, a.sustain, a.interval, a.dry_run)
    g.discover()
    if not g.gpus:
        log.error("No Astral GPUs found")
        sys.exit(1)
    if a.once:
        print(json.dumps(g.read(), indent=2))
        g.close()
        return
    sys.exit(g.run())

if __name__ == "__main__":
    main()
