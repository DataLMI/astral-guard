# Astral Guard

**ASUS ROG Astral RTX 5090 / 5080 — Per-Pin Imbalance Watchdog**

A systemd watchdog that monitors per-connector current balance on the
ASUS ROG Astral PCB via the onboard **ITE IT8915FN** power-monitor chip.
If one 12V-2×6 connector is carrying dramatically more load than the
others, the connector overheats and can fail. Astral Guard detects this
and performs an immediate hard power cut to protect the GPU.

## How it works

```
┌─────────────────────────────────────────────────────────────┐
│  ASUS ROG Astral PCB                                        │
│                                                             │
│   12V-2x6 #1 ──┐                                            │
│   12V-2x6 #2 ──┤──►  ITE IT8915FN  ──►  I2C bus (0x2B)    │
│   12V-2x6 #3 ──┘       (power mon.)      │                 │
│                                          ▼                 │
│                                    astral_guard.py          │
│                                    (root, systemd)          │
│                                                             │
│  reads 6 pins: (voltage, amps) × 6                          │
│  computes spread = (max − min) / avg  of active pins        │
│  if spread > threshold for N consecutive reads:             │
│      1. log trigger details to /var/log/astral-guard/       │
│      2. os.sync()                                           │
│      3. echo b > /proc/sysrq-trigger   (hard power off)     │
└─────────────────────────────────────────────────────────────┘
```

- **Polling interval**: 1 s by default (configurable)
- **Sustain**: 3 consecutive bad reads required before triggering
- **Spread metric**: `(max_amps − min_amps) / avg_amps` of active pins
- **Hard power off**: `SysRq 'b'` cuts power in ~50 ms via ACPI

## Requirements

| Requirement | Notes |
|---|---|
| **GPU** | ASUS ROG Astral RTX 5090 or 5080 (has the IT8915FN chip) |
| **OS** | Linux (tested on Fedora 44, any distro with I2C works) |
| **Python** | 3.8+ (only stdlib — no pip installs) |
| **I2C** | `i2c-dev` package, I2C bus device present |
| **NVIDIA** | `nvidia-smi` available (PCI address mapping) |
| **SysRq** | `/proc/sysrq-trigger` writable (root required) |

### Fedora / RHEL

```bash
sudo dnf install i2c-dev
```

### Debian / Ubuntu

```bash
sudo apt install i2c-tools
```

## Installation

```bash
git clone https://github.com/<your-username>/astral-guard.git
cd astral-guard
./install.sh
```

The installer:
1. Copies `astral_guard.py` → `/usr/local/lib/astral/`
2. Writes the systemd unit → `/etc/systemd/system/astral-guard.service`
3. Enables and starts the service
4. Verifies it's running

### Manual install

```bash
sudo cp astral_guard.py /usr/local/lib/astral/astral_guard.py
sudo cp astral-guard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now astral-guard
```

## Usage

### As a systemd service

```bash
systemctl status astral-guard
journalctl -u astral-guard -f
journalctl -u astral-guard --since "1 hour ago"
```

### Standalone / one-shot

```bash
# Single read — prints JSON, then exits
sudo python3 astral_guard.py --once

# Foreground with debug output
sudo python3 astral_guard.py -v

# Dry-run: logs imbalances but does NOT power cut
sudo python3 astral_guard.py --dry-run -v
```

### CLI options

| Flag | Default | Description |
|---|---|---|
| `--threshold` | `1.0` | Max spread ratio before alert. 1.0 = one pin > 2× the average. |
| `--min-current` | `1.0` | Minimum amps for a pin to count as "active" (filters off pins). |
| `--sustain` | `3` | Consecutive bad reads required before triggering. |
| `--interval` | `1.0` | Polling interval in seconds. |
| `--dry-run` | off | Log imbalances but do **not** power cut. |
| `--once` | off | Single read, print JSON to stdout, exit. |
| `-v`, `--verbose` | off | Enable debug logging (per-read spread values). |

## Trigger log

When the guard triggers, it writes a detailed snapshot to
`/var/log/astral-guard/trigger.log` before the power cut:

```
=== TRIGGERED 2025-09-26T23:41:12.834217 ===
GPU 0 (pci=01:00.0)
  Pin 1: 42.31A
  Pin 2: 18.07A
  Pin 3: 41.98A
  Spread: 1.2483
```

Check this file after any unexpected power loss to see if astral-guard
was the cause.

## `--once` JSON output

```json
[
  {
    "gpu": 0,
    "pci": "01:00.0",
    "bus": 14,
    "pins": [42.31, 41.98, 18.07, 0.02, 0.01, 0.00],
    "active": 3,
    "spread": 1.2483,
    "alert": true
  }
]
```

- **pins**: per-pin current in amps (sorted descending)
- **active**: number of pins above `--min-current`
- **spread**: `(max − min) / avg` of active pins
- **alert**: `true` when spread > `--threshold`

## I2C protocol

The IT8915FN is at I2C address `0x2B`, register base `0x80`.
Each of the 6 pins occupies 4 bytes (big-endian):

| Offset | Bytes | Meaning |
|---|---|---|
| `0x80 + n*4 + 0..1` | `>H` | Voltage (mV / 1000) |
| `0x80 + n*4 + 2..3` | `>H` | Current (mA / 1000) |

The script auto-discovers the I2C bus by matching the NVIDIA I2C adapter
at the GPU's PCI domain, then probes for a valid 12V reading to confirm
the IT8915FN is present.

## Uninstallation

```bash
./uninstall.sh
```

Stops/disables the service, removes the systemd unit, and deletes
`/usr/local/lib/astral/`. The trigger log is **not** removed.

## Disclaimer

This tool performs an **immediate hard power off** (SysRq `b`) when it
detects a sustained per-pin current imbalance. Any unsaved work will be
lost. Use `--dry-run` during initial testing to verify the thresholds
are appropriate for your workload before enabling live mode.

## License

[MIT](LICENSE)

