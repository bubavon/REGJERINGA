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
```

No root needed. Everything is read from procfs/sysfs.

## Testing without a BC-250

```sh
tests/make_fake_root.sh /tmp/fakebc250
BC250_SYSROOT=/tmp/fakebc250 ./bc250-ram-monitor
```

Uninstall: `./install.sh --uninstall`
