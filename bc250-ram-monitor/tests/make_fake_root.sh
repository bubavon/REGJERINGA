#!/bin/sh
# Builds a fake /proc + /sys tree that looks like a BC-250 in 512 MB dynamic UMA mode.
# Usage: tests/make_fake_root.sh DIR ; BC250_SYSROOT=DIR ./bc250-ram-monitor --once
R=${1:?dir}
D=$R/sys/class/drm/card1/device
mkdir -p "$D/hwmon/hwmon3" "$R/sys/class/drm/card1-HDMI-A-1" "$R/sys/block/zram0" "$R/proc/4242/fdinfo"
echo 0x1002 > $D/vendor; echo 0x13fe > $D/device
echo 536870912 > $D/mem_info_vram_total; echo 402653184 > $D/mem_info_vram_used
echo 536870912 > $D/mem_info_vis_vram_total; echo 402653184 > $D/mem_info_vis_vram_used
echo 7864320000 > $D/mem_info_gtt_total; echo 4294967296 > $D/mem_info_gtt_used
printf '0: 1000Mhz\n1: 2000Mhz *\n' > $D/pp_dpm_sclk
echo 71000 > $D/hwmon/hwmon3/temp1_input
echo 8589934592 > $R/sys/block/zram0/disksize
echo "2147483648 715827882 750000000 0 750000000 0 0 0 0" > $R/sys/block/zram0/mm_stat
cat > $R/proc/meminfo <<M
MemTotal:       15400000 kB
MemFree:         1200000 kB
MemAvailable:    5100000 kB
Buffers:          120000 kB
Cached:          3900000 kB
Shmem:            400000 kB
SReclaimable:     300000 kB
SwapTotal:       8388604 kB
SwapFree:        6291452 kB
M
echo "BOOT_IMAGE=/vmlinuz-linux-cachyos root=UUID=x rw quiet" > $R/proc/cmdline
printf 'Name:\tCyberpunk2077.exe\nVmRSS:\t 6291456 kB\n' > $R/proc/4242/status
printf 'pos:\t0\ndrm-driver:\tamdgpu\ndrm-client-id:\t7\ndrm-memory-vram:\t327680 KiB\ndrm-memory-gtt:\t4000000 KiB\n' > $R/proc/4242/fdinfo/12
printf 'pos:\t0\ndrm-driver:\tamdgpu\ndrm-client-id:\t7\ndrm-memory-vram:\t327680 KiB\n' > $R/proc/4242/fdinfo/13
# --- LLM scenario: ollama running, TTM silently capped at 12 GiB by tmpfiles.d
mkdir -p $R/sys/module/ttm/parameters $R/etc/modprobe.d $R/etc/tmpfiles.d $R/proc/777/fdinfo
echo 3145728 > $R/sys/module/ttm/parameters/pages_limit
echo 3145728 > $R/sys/module/ttm/parameters/page_pool_size
echo "options ttm pages_limit=4194304 page_pool_size=4194304" > $R/etc/modprobe.d/ttm-gpu-memory.conf
echo "w /sys/module/ttm/parameters/pages_limit - - - - 3145728" > $R/etc/tmpfiles.d/gpu-ttm-memory.conf
echo 52000000 > $D/hwmon/hwmon3/power1_average
echo "BOOT_IMAGE=/vmlinuz-linux-cachyos root=UUID=x rw quiet amdgpu.gttsize=14336" > $R/proc/cmdline
printf 'Name:\tollama\nVmRSS:\t 1048576 kB\n' > $R/proc/777/status
echo 200 > $R/proc/777/oom_score_adj
printf 'drm-driver:\tamdgpu\ndrm-client-id:\t9\ndrm-memory-vram:\t300000 KiB\ndrm-memory-gtt:\t9000000 KiB\n' > $R/proc/777/fdinfo/20
printf 'pswpin 10\npswpout 20\npgmajfault 30\n' > $R/proc/vmstat
echo '{"models":[{"name":"qwen3:14b","size":9900000000,"size_vram":9000000000,"context_length":32768}]}' > $R/ollama_ps.json
# --- CPU: stock 6 cores / 12 threads, k10temp. Pass UNLOCKED=1 for an 8-core board
# whose new CPUs (12-15) have no C-states yet.
N=6; [ -n "$UNLOCKED" ] && N=8
: > $R/proc/cpuinfo
t=0; for th in 0 1; do for c in $(seq 0 $((N-1))); do
  printf 'processor\t: %d\nphysical id\t: 0\ncore id\t\t: %d\n\n' $t $c >> $R/proc/cpuinfo
  if [ $t -lt 12 ]; then mkdir -p $R/sys/devices/system/cpu/cpu$t/cpuidle/state0; fi
  t=$((t+1)); done; done
mkdir -p $R/sys/class/hwmon/hwmon1; echo k10temp > $R/sys/class/hwmon/hwmon1/name
echo 68500 > $R/sys/class/hwmon/hwmon1/temp1_input
mkdir -p $R/usr/local/bin; printf '#!/bin/sh\necho fake\n' > $R/usr/local/bin/bc250-8core-unlock
chmod +x $R/usr/local/bin/bc250-8core-unlock
