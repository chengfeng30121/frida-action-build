#!/usr/bin/env bash
# 移除某个 .so 的 DT_AARCH64_BTI_PLT(0x70000001) / DT_AARCH64_PAC_PLT(0x70000003) 动态条目。
#
# 原因: Android 10(API 29) 老 linker 不认识这些条目, 每次加载都刷屏:
#   WARNING: linker: Warning: "...libc++_shared.so" unused DT entry:
#            unknown processor-specific (type 0x70000001 arg 0x0) (ignoring)
# 功能无害, 纯噪音。MT8788 等不支持 BTI 的 CPU 上, PLT 里的 bti 本来被当 NOP。
#
# 用法:
#   bash fix-bti.sh [目标 .so]        # 默认 /data/data/com.termux/files/usr/lib/libc++_shared.so
# Termux 升级 libc++ 包后警告会重现, 重跑本脚本即可。
set -euo pipefail

LIB="${1:-/data/data/com.termux/files/usr/lib/libc++_shared.so}"
[ -f "$LIB" ] || { echo "no such file: $LIB"; exit 1; }

# 只保留第一次的原始备份, 重复执行不会把已打补丁的文件当成原件
if [ ! -f "$LIB.bak" ]; then
    cp -v "$LIB" "$LIB.bak"
else
    echo "keep existing backup: $LIB.bak"
fi

python3 - "$LIB" <<'PYEOF'
import struct
import sys

path = sys.argv[1]
TAGS = {0x70000001: "DT_AARCH64_BTI_PLT", 0x70000003: "DT_AARCH64_PAC_PLT"}

with open(path, "rb") as f:
    d = bytearray(f.read())

assert d[:4] == b"\x7fELF", "not an ELF file"
is64 = d[4] == 2
es = 16 if is64 else 8
phoff_pos = 0x20 if is64 else 0x1C
phentsize_pos = 0x36 if is64 else 0x2A
phnum_pos = 0x38 if is64 else 0x2C

phoff = struct.unpack_from("<Q" if is64 else "<I", d, phoff_pos)[0]
phentsize = struct.unpack_from("<H", d, phentsize_pos)[0]
phnum = struct.unpack_from("<H", d, phnum_pos)[0]

p_off = p_sz = None
for i in range(phnum):
    o = phoff + i * phentsize
    if is64:
        ptype, _, po, _, _, pz, _, _ = struct.unpack_from("<IIQQQQQQ", d, o)
    else:
        ptype, po, _, _, pz, _, _, _ = struct.unpack_from("<IIIIIIII", d, o)
    if ptype == 2:  # PT_DYNAMIC
        p_off, p_sz = po, pz
        break
assert p_off is not None, "no PT_DYNAMIC segment"

fmt = "<QQ" if is64 else "<II"
buf = bytes(d[p_off:p_off + p_sz])
removed = []
i = 0
while i + es <= len(buf):
    tag = struct.unpack_from(fmt, buf, i)[0]
    if tag == 0:  # DT_NULL, 动态段结束
        break
    if tag in TAGS:
        # 后续条目前移一个 slot, 尾部补 DT_NULL(全 0), 段大小不变
        tail = buf[i + es:]
        nxt = 0
        while nxt + es <= len(tail):
            t2 = struct.unpack_from(fmt, tail, nxt)[0]
            nxt += es
            if t2 == 0:
                break
        buf = bytes(buf[:i]) + tail[:nxt] + b"\x00" * es + buf[i + nxt + es:]
        removed.append(TAGS[tag])
        continue
    i += es

if not removed:
    print("no DT_AARCH64_BTI_PLT / DT_AARCH64_PAC_PLT entry, nothing to do")
    sys.exit(0)

d[p_off:p_off + p_sz] = buf
with open(path, "wb") as f:
    f.write(d)

print(f"removed {len(removed)} entry(s) from {path}: {', '.join(removed)}")
PYEOF

echo "OK - linker 警告应已清除（备份在 $LIB.bak，Termux 升级 libc++ 后重跑本脚本）"
