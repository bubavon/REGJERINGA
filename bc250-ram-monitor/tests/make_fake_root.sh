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
