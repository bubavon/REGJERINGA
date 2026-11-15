"""Data collection for the ASRock / AMD BC-250 RAM monitor.

The BC-250 is a cut-down PS5 APU ("Cyan Skillfish", PCI 1002:13fe) with ONE
16 GB GDDR6 pool shared between the 6-core Zen 2 CPU and the RDNA2 GPU.
The BIOS "UMA Frame Buffer Size" decides how much of that pool is carved out
as dedicated VRAM; the rest is what Linux sees as system RAM. On top of the
carve-out the GPU can borrow system RAM through GTT.

Everything here reads plain procfs/sysfs, so no root and no extra packages.
Set BC250_SYSROOT=/some/dir to read a fake tree (used for testing).
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field

ROOT = os.environ.get("BC250_SYSROOT", "")

BC250_GPU_IDS = {"0x13fe"}          # Cyan Skillfish (BC-250 iGPU)
BC250_POOL_BYTES = 16 * 1024**3     # 16 GB GDDR6 on the board
MiB = 1024**2
PAGE = 4096
BC250_BANDWIDTH = 357e9      # measured peak Vulkan bandwidth (community benchmark)
LLM_NAMES = ("ollama", "llama-server", "llama-cli", "llama-bench", "llama-run",
             "koboldcpp", "lm-studio", "lms", "whisper-server", "whisper-cli",
             "sd", "sd-server", "vllm", "localai", "llamafile", "jan")
GiB = 1024**3


def _p(path: str) -> str:
    return ROOT + path


def _read(path: str, default: str = "") -> str:
    try:
        with open(_p(path)) as f:
            return f.read().strip()
    except OSError:
        return default


def _read_int(path: str, default: int = 0) -> int:
    try:
        return int(_read(path, str(default)).split()[0])
    except (ValueError, IndexError):
        return default


@dataclass
class Gpu:
    card: str = ""
    device_id: str = ""
    is_bc250: bool = False
    vram_total: int = 0
    vram_used: int = 0
    vis_vram_total: int = 0
    vis_vram_used: int = 0
    gtt_total: int = 0
    gtt_used: int = 0
    sclk_mhz: int = 0
    temp_c: float | None = None
    power_w: float | None = None


@dataclass
class Zram:
    name: str
    disksize: int
    orig: int        # uncompressed data stored
    compr: int       # compressed size
    mem_used: int    # RAM actually consumed


@dataclass
class Proc:
    pid: int
    name: str
    rss: int
    vram: int = 0
    gtt: int = 0


@dataclass
class Ttm:
    pages_limit: int = 0          # in 4 KiB pages, 0 = unknown
    page_pool_size: int = 0
    modprobe_value: int = 0       # value configured in /etc/modprobe.d
    tmpfiles_value: int = 0       # value written by /etc/tmpfiles.d (applied last!)

    @property
    def limit_bytes(self) -> int:
        return self.pages_limit * PAGE


@dataclass
class OllamaModel:
    name: str
    size: int
    size_vram: int
    context: int = 0
    expires: str = ""


@dataclass
class Snapshot:
    mem_total: int = 0
    mem_free: int = 0
    mem_available: int = 0
    buffers: int = 0
    cached: int = 0
    shmem: int = 0
    sreclaimable: int = 0
    swap_total: int = 0
    swap_free: int = 0
    gpu: Gpu | None = None
    zram: list[Zram] = field(default_factory=list)
    cmdline: str = ""
    procs: list[Proc] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ttm: Ttm = field(default_factory=Ttm)
    llm_procs: list[Proc] = field(default_factory=list)
    ollama: list[OllamaModel] | None = None    # None = Ollama API not reachable
    swap_in_rate: float = 0.0     # pages/s
    swap_out_rate: float = 0.0
    majflt_rate: float = 0.0

    # ---- derived values ----
    @property
    def used(self) -> int:
        return max(0, self.mem_total - self.mem_available)

    @property
    def cache(self) -> int:
        return max(0, self.buffers + self.cached + self.sreclaimable - self.shmem)

    @property
    def swap_used(self) -> int:
        return max(0, self.swap_total - self.swap_free)

    @property
    def carveout(self) -> int:
        return self.gpu.vram_total if self.gpu else 0

    @property
    def pool_total(self) -> int:
        """Size of the physical shared pool (always ~16 GB on a BC-250)."""
        seen = self.mem_total + self.carveout
        if self.gpu and self.gpu.is_bc250:
            return max(BC250_POOL_BYTES, seen)
        return seen

    @property
    def firmware_reserved(self) -> int:
        """Memory neither Linux nor the VRAM carve-out reports (firmware, PSP...)."""
        return max(0, self.pool_total - self.mem_total - self.carveout)

    @property
    def uma_mode(self) -> str:
        if not self.gpu or not self.gpu.vram_total:
            return "unknown"
        v = self.gpu.vram_total
        if v <= 600 * MiB:
            return "Dynamic (512 MB UMA, GPU grows via GTT)"
        return f"Fixed {v / GiB:.0f} GB VRAM / {self.mem_total / GiB:.0f} GB system"

    @property
    def gpu_ceiling(self) -> int:
        """Most memory the GPU can actually hold: VRAM carve-out + GTT, where GTT is
        further capped by TTM pages_limit (the real limit on BC-250 kernels)."""
        if not self.gpu:
            return 0
        gtt = self.gpu.gtt_total
        if self.ttm.pages_limit:
            gtt = min(gtt, self.ttm.limit_bytes)
        return self.gpu.vram_total + gtt

    @property
    def llm_headroom(self) -> int:
        """How much more model weights / KV cache can still be loaded on the GPU.
        GTT is backed by system RAM, so it is also bounded by MemAvailable."""
        if not self.gpu:
            return 0
        vram_free = max(0, self.gpu.vram_total - self.gpu.vram_used)
        gtt_free = max(0, self.gpu_ceiling - self.gpu.vram_total - self.gpu.gtt_used)
        return vram_free + min(gtt_free, self.mem_available)

    @property
    def gpu_total_used(self) -> int:
        return (self.gpu.vram_used + self.gpu.gtt_used) if self.gpu else 0


def read_meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    for line in _read("/proc/meminfo").splitlines():
        m = re.match(r"(\w+):\s+(\d+)", line)
        if m:
            out[m.group(1)] = int(m.group(2)) * 1024
    return out


def find_gpu() -> Gpu | None:
    cards = sorted(glob.glob(_p("/sys/class/drm/card[0-9]*")))
    best: Gpu | None = None
    for c in cards:
        if "-" in os.path.basename(c):          # skip connectors (card0-HDMI-A-1)
            continue
        dev = c[len(ROOT):] + "/device"
        if _read(dev + "/vendor") != "0x1002":
            continue
        g = Gpu(card=os.path.basename(c), device_id=_read(dev + "/device"))
        g.is_bc250 = g.device_id.lower() in BC250_GPU_IDS
        g.vram_total = _read_int(dev + "/mem_info_vram_total")
        g.vram_used = _read_int(dev + "/mem_info_vram_used")
        g.vis_vram_total = _read_int(dev + "/mem_info_vis_vram_total")
        g.vis_vram_used = _read_int(dev + "/mem_info_vis_vram_used")
        g.gtt_total = _read_int(dev + "/mem_info_gtt_total")
        g.gtt_used = _read_int(dev + "/mem_info_gtt_used")
        for line in _read(dev + "/pp_dpm_sclk").splitlines():
            if line.rstrip().endswith("*"):
                m = re.search(r"(\d+)\s*Mhz", line, re.I)
                if m:
                    g.sclk_mhz = int(m.group(1))
        for hw in glob.glob(_p(dev + "/hwmon/hwmon*")):
            h = hw[len(ROOT):]
            if os.path.exists(hw + "/temp1_input"):
                g.temp_c = _read_int(h + "/temp1_input") / 1000
            for pf in ("power1_average", "power1_input"):
                if os.path.exists(hw + "/" + pf):
                    g.power_w = _read_int(h + "/" + pf) / 1e6
                    break
            break
        if g.is_bc250:
            return g
        best = best or g
    return best


def read_zram() -> list[Zram]:
    out = []
    for d in sorted(glob.glob(_p("/sys/block/zram*"))):
        rel = d[len(ROOT):]
        size = _read_int(rel + "/disksize")
        if not size:
            continue
        mm = _read(rel + "/mm_stat").split()
        nums = [int(x) for x in mm[:3]] if len(mm) >= 3 else [0, 0, 0]
        out.append(Zram(os.path.basename(d), size, *nums))
    return out


def read_procs(limit: int = 10, gpu: bool = True) -> list[Proc]:
    procs: dict[int, Proc] = {}
    for st in glob.glob(_p("/proc/[0-9]*/status")):
        try:
            pid = int(st.split("/")[-2])
        except ValueError:
            continue
        name, rss = "", 0
        for line in _read(st[len(ROOT):]).splitlines():
            if line.startswith("Name:"):
                parts = line.split(None, 1)
                name = parts[1] if len(parts) > 1 else ""
            elif line.startswith("VmRSS:"):
                rss = int(line.split()[1]) * 1024
                break
        if rss:
            procs[pid] = Proc(pid, name, rss)

    if gpu:
        # amdgpu exposes per-client memory in fdinfo (kernel 5.17+/6.x)
        for pid, p in procs.items():
            clients: dict[str, dict[str, int]] = {}
            for fi in glob.glob(_p(f"/proc/{pid}/fdinfo/*")):
                txt = _read(fi[len(ROOT):])
                if not re.search(r"drm-driver:\s*amdgpu", txt):
                    continue
                cid = re.search(r"drm-client-id:\s*(\d+)", txt)
                c = clients.setdefault(cid.group(1) if cid else fi, {})
                for key in ("vram", "gtt"):
                    m = re.search(rf"drm-(?:memory|total)-{key}:\s*(\d+)\s*(KiB|MiB)?", txt)
                    if m:
                        mult = {"KiB": 1024, "MiB": MiB}.get(m.group(2) or "", 1)
                        c[key] = max(c.get(key, 0), int(m.group(1)) * mult)
            p.vram = sum(c.get("vram", 0) for c in clients.values())
            p.gtt = sum(c.get("gtt", 0) for c in clients.values())

    ranked = sorted(procs.values(), key=lambda p: p.rss + p.vram + p.gtt, reverse=True)
    return ranked[:limit]


def _conf_value(pattern: str, key: str) -> int:
    """Last configured value of a ttm parameter in modprobe.d / tmpfiles.d files."""
    val = 0
    for f in sorted(glob.glob(_p(pattern))):
        for line in _read(f[len(ROOT):]).splitlines():
            line = line.split("#", 1)[0]
            if key not in line or "ttm" not in line:
                continue
            m = re.search(rf"{key}=(\d+)", line)          # modprobe: options ttm pages_limit=N
            nums = re.findall(r"(?<![\w/])(\d+)\s*$", line)  # tmpfiles: w /sys/... - - - - N
            if m:
                val = int(m.group(1))
            elif nums:
                val = int(nums[-1])
    return val


def read_ttm() -> Ttm:
    t = Ttm(
        pages_limit=_read_int("/sys/module/ttm/parameters/pages_limit"),
        page_pool_size=_read_int("/sys/module/ttm/parameters/page_pool_size"),
    )
    t.modprobe_value = _conf_value("/etc/modprobe.d/*.conf", "pages_limit")
    t.tmpfiles_value = _conf_value("/etc/tmpfiles.d/*.conf", "pages_limit")
    return t


def read_vmstat() -> dict[str, int]:
    out = {}
    for line in _read("/proc/vmstat").splitlines():
        k, _, v = line.partition(" ")
        if k in ("pswpin", "pswpout", "pgmajfault"):
            out[k] = int(v)
    return out


def is_llm(name: str) -> bool:
    n = name.lower()
    return any(n == x or n.startswith(x) for x in LLM_NAMES)


def read_oom_adj(pid: int) -> int | None:
    v = _read(f"/proc/{pid}/oom_score_adj")
    return int(v) if v.lstrip("-").isdigit() else None


def ollama_ps(host: str | None = None, timeout: float = 0.3) -> list[OllamaModel] | None:
    """Loaded models from a local Ollama server (GET /api/ps). None if not running."""
    import json
    import urllib.request
    host = host or os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
    if not host.startswith("http"):
        host = "http://" + host
    if ROOT:   # test mode: read fake JSON instead of the network
        txt = _read("/ollama_ps.json")
        if not txt:
            return None
        data = json.loads(txt)
    else:
        try:
            with urllib.request.urlopen(host.rstrip("/") + "/api/ps", timeout=timeout) as r:
                data = json.load(r)
        except Exception:
            return None
    out = []
    for m in data.get("models", []):
        out.append(OllamaModel(m.get("name", "?"), int(m.get("size", 0)),
                               int(m.get("size_vram", 0)), int(m.get("context_length", 0) or 0),
                               m.get("expires_at", "")))
    return out


class RateTracker:
    """Turns /proc/vmstat counters into per-second rates between snapshots."""
    def __init__(self):
        self.prev: dict[str, int] | None = None
        self.t = 0.0

    def update(self, s: "Snapshot"):
        import time
        now, cur = time.monotonic(), read_vmstat()
        if self.prev is not None and now > self.t:
            dt = now - self.t
            s.swap_in_rate = max(0, cur.get("pswpin", 0) - self.prev.get("pswpin", 0)) / dt
            s.swap_out_rate = max(0, cur.get("pswpout", 0) - self.prev.get("pswpout", 0)) / dt
            s.majflt_rate = max(0, cur.get("pgmajfault", 0) - self.prev.get("pgmajfault", 0)) / dt
        self.prev, self.t = cur, now


def build_warnings(s: Snapshot) -> list[str]:
    w = []
    g = s.gpu
    if g is None:
        w.append("No AMD GPU found in /sys/class/drm - is amdgpu loaded?")
        return w
    if not g.is_bc250:
        w.append(f"GPU {g.device_id} is not a BC-250 (expected 0x13fe). Showing generic data.")
    dynamic = g.vram_total and g.vram_total <= 600 * MiB
    if dynamic and s.zram:
        w.append("ZRAM is active with the 512 MB dynamic UMA setting. BC-250 guides report "
                 "this can stop the GPU from growing its memory - consider shrinking/disabling "
                 "zram or using a fixed split.")
    if "amdgpu.gttsize" in s.cmdline:
        w.append("amdgpu.gttsize is on the kernel cmdline. It is deprecated; BC-250 LLM guides "
                 "report that removing it and raising ttm.pages_limit gives a larger (16 GiB) GTT.")
    t = s.ttm
    gtt_cap = s.gpu_ceiling - g.vram_total
    if dynamic and gtt_cap < 15 * GiB:
        if t.pages_limit and t.limit_bytes < g.gtt_total:
            why = f"TTM pages_limit={t.pages_limit} caps it at {fmt(t.limit_bytes)}"
        else:
            why = f"GTT is {fmt(g.gtt_total)} (kernel default is about half of RAM)"
        w.append(f"The GPU can only use {fmt(s.gpu_ceiling)} for LLMs: {why}. BC-250 guides "
                 "raise this to 16 GiB with 'options ttm pages_limit=4194304 page_pool_size=4194304' "
                 "in /etc/modprobe.d, then rebuild the initramfs and reboot.")
    if t.tmpfiles_value and t.pages_limit == t.tmpfiles_value and t.modprobe_value \
            and t.modprobe_value != t.tmpfiles_value:
        w.append(f"/etc/tmpfiles.d sets pages_limit={t.tmpfiles_value}, overriding the modprobe "
                 f"value {t.modprobe_value} (tmpfiles runs last). Fix or remove that line.")
    if s.swap_out_rate > 50 or s.majflt_rate > 200:
        w.append(f"Memory thrashing: {s.swap_out_rate:.0f} pages/s swapped out, {s.majflt_rate:.0f} "
                 "major faults/s. LLM tokens/s will collapse - use a smaller model, a lower "
                 "context, or a quantized KV cache (e.g. q4_0).")
    if s.llm_procs and s.gpu and s.llm_headroom < 512 * MiB:
        w.append(f"Only {fmt(s.llm_headroom)} GPU memory headroom left while an LLM is running. "
                 "Growing the context (KV cache) may give HTTP 500s, hangs or an OOM freeze.")
    for m in s.ollama or []:
        if m.size and m.size_vram < m.size * 0.98:
            w.append(f"Ollama model {m.name} is only {100 * m.size_vram / m.size:.0f}% on the GPU "
                     "- the rest runs on the 6 CPU cores and will be much slower.")
    for p in s.llm_procs:
        adj = read_oom_adj(p.pid)
        if adj is not None and adj > 0:
            w.append(f"{p.name} (pid {p.pid}) has oom_score_adj={adj}: the kernel will kill it "
                     "first under memory pressure. Guides use OOMScoreAdjust=-1000 for ollama.")

    if s.mem_total and s.mem_available / s.mem_total < 0.10:
        w.append("System RAM below 10% available - the shared pool is nearly exhausted.")
    if g.gtt_total and g.gtt_used / g.gtt_total > 0.9:
        w.append("GTT (GPU borrowed system RAM) above 90% - games may stutter or crash.")
    if g.vram_total > 600 * MiB and g.vram_used / g.vram_total > 0.95:
        w.append("Fixed VRAM carve-out is full; the GPU is spilling into GTT.")
    if g.temp_c and g.temp_c > 90:
        w.append(f"GPU temperature {g.temp_c:.0f} C - check the BC-250 cooling/fan.")
    return w


def snapshot(procs: bool = True, rates: "RateTracker | None" = None) -> Snapshot:
    mi = read_meminfo()
    s = Snapshot(
        mem_total=mi.get("MemTotal", 0),
        mem_free=mi.get("MemFree", 0),
        mem_available=mi.get("MemAvailable", mi.get("MemFree", 0)),
        buffers=mi.get("Buffers", 0),
        cached=mi.get("Cached", 0),
        shmem=mi.get("Shmem", 0),
        sreclaimable=mi.get("SReclaimable", 0),
        swap_total=mi.get("SwapTotal", 0),
        swap_free=mi.get("SwapFree", 0),
    )
    s.gpu = find_gpu()
    s.zram = read_zram()
    s.cmdline = _read("/proc/cmdline")
    s.ttm = read_ttm()
    if procs:
        allp = read_procs(limit=10_000)
        s.procs = allp[:10]
        s.llm_procs = [p for p in allp if is_llm(p.name)]
        s.ollama = ollama_ps() if any(p.name.startswith("ollama") for p in s.llm_procs) or ROOT \
            else None
    if rates is not None:
        rates.update(s)
    s.warnings = build_warnings(s)
    return s


def fmt(n: int | float) -> str:
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit in ("B", "KiB") else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TiB"
