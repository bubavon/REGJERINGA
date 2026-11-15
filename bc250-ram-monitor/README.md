# BC-250 RAM Monitor

A small CachyOS (and other Arch-based) desktop app that shows memory usage on the
**ASRock / AMD BC-250**, the PS5-derived mining board ("Cyan Skillfish" APU,
6 Zen 2 cores, 24 RDNA2 CUs).

![screenshot](docs/screenshot.png)

## Why a BC-250-specific monitor?

The BC-250 has **no separate RAM and VRAM**. It has one **16 GB GDDR6 pool** shared by
the CPU and GPU. Normal tools like `free` or htop only show the CPU's share, so the
numbers look wrong. This app shows the whole pool:

| Part | Where it comes from |
|---|---|
| **System RAM** (CPU side) | `/proc/meminfo` |
| **VRAM carve-out** set by the BIOS *UMA Frame Buffer Size* (512 MB dynamic, 4 GB, 8 GB...) | `mem_info_vram_*` in amdgpu sysfs |
| **GTT**: system RAM the GPU borrows (this is where games live in 512 MB dynamic mode) | `mem_info_gtt_*` |
| **Firmware reserved**: what is left of the 16 GB that neither side reports | 16 GB − the two above |
| **ZRAM / swap**, including compression ratio | `/sys/block/zram*` |
| **Per-process RAM + VRAM + GTT** (e.g. a game under Proton) | `/proc/*/status`, amdgpu `fdinfo` |

It also shows the GPU clock and temperature, and gives BC-250-specific warnings:

- ZRAM enabled together with the 512 MB dynamic UMA setting. Community guides report this can stop the GPU from growing its memory.
- `amdgpu.gttsize` missing from the kernel cmdline while in dynamic mode.
- The shared pool nearly full, GTT above 90%, the fixed carve-out full, or a hot GPU.

### LLM / inference panel

The BC-250 is popular for running local LLMs (Ollama or llama.cpp with Vulkan). The
problems that come up there are mostly about memory, so the app adds:

- **GPU memory ceiling.** VRAM carve-out + GTT, capped by **TTM `pages_limit`**, which is
  the real limit on current kernels (`/sys/module/ttm/parameters/pages_limit`). The
  default is about 7.4 GiB. BC-250 LLM guides raise it to 16 GiB with
  `options ttm pages_limit=4194304 page_pool_size=4194304` in `/etc/modprobe.d/`.
- **Headroom.** How much more model weights or KV cache can still load (free VRAM + free
  GTT, bounded by `MemAvailable`, since GTT comes out of system RAM).
- **LLM processes** (ollama, llama-server, koboldcpp, whisper, sd, ...) with their RAM, VRAM,
  GTT and `oom_score_adj`.
- **Ollama models** loaded (from `/api/ps` on `$OLLAMA_HOST` or `127.0.0.1:11434`): size, % on the GPU,
  context length, and a rough tokens/s ceiling (357 GB/s measured bandwidth ÷ model size).
  That ceiling only applies to dense models; MoE models can beat it.
- **Paging rates** (swap-in/out, major faults per second). If these rise during
  inference, tokens/s drops sharply.
- **GPU power** from the SMU sensor (uncalibrated).
- **Warnings** for:
  - the deprecated `amdgpu.gttsize`
  - a GTT/TTM cap under 15 GiB
  - a `/etc/tmpfiles.d` line silently overriding the modprobe `pages_limit` (a known 12 GiB trap)
  - thrashing
  - under 512 MB headroom while an LLM runs
  - an Ollama model partly on the CPU
  - an LLM process the OOM killer would pick first

For benchmarks, `bc250-ram-monitor --log run.csv` writes one CSV row per second.

### Unlock all 8 CPU cores (optional, at your own risk)

The BC-250 ships with 6 of its 8 Zen 2 cores enabled. The other two are masked by a
writable SMU register, not fused off. The **CPU cores** card shows how many cores Linux
sees and has an **Unlock all 8 cores…** button.

![unlock flow](docs/unlock-flow.png)

The app does **not** poke the hardware itself. It drives GabriWar's MIT-licensed
[`bc250-8core-unlock`](https://github.com/GabriWar/bc250-core-cu-unlock) script, which
flips the core-enable mask from `0x77` to `0xFF` through SMU mailbox message `0x98`. Read
that script, then install it:

```sh
git clone https://github.com/GabriWar/bc250-core-cu-unlock
cd bc250-core-cu-unlock
sudo pacman -S --needed pciutils
sudo install -Dm755 bc250-8core-unlock.sh /usr/local/bin/bc250-8core-unlock
```

(The app also finds it via `$BC250_UNLOCK_SCRIPT`, `$PATH`, or `~/bc250-core-cu-unlock/`.)

What the card does:

- **Unlock all 8 cores…** shows a warning dialog, then runs `pkexec bc250-8core-unlock apply`.
  The GPU clock governor (`cyan-skillfish-governor-smu`) uses the same SMU mailbox, so it is
  paused during the write and restarted afterwards.
- **Warm reboot now** sets `/sys/kernel/reboot/mode` to `warm` and reboots. The new cores
  only appear after a warm reset.
- **Check mask** runs `bc250-8core-unlock status` to read the current mask.
- **Re-apply after every cold boot** enables the script's systemd unit. Without it, a cold
  boot (power removed) goes back to 6 cores. The unit never reboots by itself, so after a
  cold boot the cores come back on your next reboot.

Things to know:

- The script refuses any board whose mask isn't the stock `0x77`, which could mean really
  defective cores.
- Nothing is written to flash. Cutting power is the escape hatch.
- Stress-test the new cores for a few hours (`stress-ng` / `mprime`) and watch `dmesg` for
  machine-check errors.
- 8 cores use more of the shared SoC power and thermal budget. Re-check temperatures and
  any undervolt.
- The stock ACPI tables give CPUs 12-15 no idle C-states. The app warns about this and
  points to the 8-core SSDT fix (`bc250-acpi-fix.sh` in the same repo).

The board is detected by its GPU PCI ID `1002:13fe`. On any other AMD GPU the app still
runs and shows generic data.

## Install on CachyOS

```sh
git clone <this repo> && cd bc250-ram-monitor
./install.sh --deps        # pacman -S python-gobject python-cairo gtk4, then install to ~/.local
```

Or as a real package: `makepkg -si`

Then open **BC-250 RAM Monitor** from the app menu, or run `bc250-ram-monitor`.

## Usage

```
bc250-ram-monitor          # GTK4 window, updates every second
bc250-ram-monitor --cli    # live terminal view (no GTK needed, works over SSH)
bc250-ram-monitor --once   # print one report and exit
bc250-ram-monitor --log run.csv   # CSV log every second (LLM benchmarks)
```

No root needed. Everything is read from procfs/sysfs.

One limit: Linux only lets you read GPU memory for your own processes. If Ollama runs as
a system service (user `ollama`), its VRAM/GTT columns show `n/a`. Run
`sudo bc250-ram-monitor --cli` to see them. The pool totals are unaffected.

## Testing without a BC-250

```sh
tests/make_fake_root.sh /tmp/fakebc250
BC250_SYSROOT=/tmp/fakebc250 ./bc250-ram-monitor
```

Uninstall: `./install.sh --uninstall`
