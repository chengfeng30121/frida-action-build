#!/usr/bin/env bash
# 本地冒烟测试（不依赖 frida / Linux）：验证 make_wheel / verify-wheel / fix-bti /
# deps-version / patch-cross-python 的逻辑是否正确。
set -uo pipefail
cd "$(dirname "$0")"

REPO="$(cd .. && pwd)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
echo "tmp: $T"

# ---- python3 垫片（本机只有 python）----
mkdir -p "$T/bin"
cat > "$T/bin/python3" <<'EOF'
#!/usr/bin/env bash
exec python "$@"
EOF
chmod +x "$T/bin/python3"
export PATH="$T/bin:$PATH"

# ---- 造一个假 aarch64 ELF，带 PT_DYNAMIC（含 BTI_PLT 条目）----
python - "$T/libfake.so" <<'PY'
import struct, sys
path = sys.argv[1]
# ELF64 header (64 bytes) + 1 program header (56 bytes) + dynamic section
ehsize, phentsize = 64, 56
dyn = [(0x70000001, 0), (6, 0x1234), (0x70000003, 0), (0, 0)]   # BTI, DT_SYMTAB, PAC, DT_NULL
dyn_bytes = b"".join(struct.pack("<QQ", t, v) for t, v in dyn)
dyn_off = ehsize + phentsize
total = dyn_off + len(dyn_bytes)
buf = bytearray(total)
buf[0:4] = b"\x7fELF"
buf[4] = 2          # ELFCLASS64
buf[5] = 1          # little endian
buf[6] = 1
struct.pack_into("<H", buf, 0x10, 3)      # e_type = ET_DYN
struct.pack_into("<H", buf, 0x12, 183)    # e_machine = AArch64
struct.pack_into("<Q", buf, 0x20, ehsize) # e_phoff
struct.pack_into("<H", buf, 0x36, phentsize)
struct.pack_into("<H", buf, 0x38, 1)      # e_phnum
# program header: PT_DYNAMIC
struct.pack_into("<IIQQQQQQ", buf, ehsize, 2, 0, dyn_off, 0, 0, len(dyn_bytes), 0, 0)
buf[dyn_off:] = dyn_bytes
open(path, "wb").write(buf)
print("fake ELF:", total, "bytes")
PY

echo
echo "== fix-bti.sh =="
cp "$T/libfake.so" "$T/libcxx.so"
bash "$REPO/scripts/fix-bti.sh" "$T/libcxx.so"
echo "-- 二次运行（应变成空操作，且不覆盖 .bak）--"
bash "$REPO/scripts/fix-bti.sh" "$T/libcxx.so"
python - "$T/libcxx.so" <<'PY'
import struct, sys
d = open(sys.argv[1], "rb").read()
phoff = struct.unpack_from("<Q", d, 0x20)[0]
_, _, po, _, _, pz, _, _ = struct.unpack_from("<IIQQQQQQ", d, phoff)
tags = []
for i in range(0, pz, 16):
    t = struct.unpack_from("<Q", d, po + i)[0]
    tags.append(hex(t))
print("dynamic tags after patch:", tags)
assert "0x70000001" not in tags and "0x70000003" not in tags, "BTI/PAC 条目没清掉"
assert 0x6 in [int(x, 16) for x in tags], "DT_SYMTAB 被误删"
assert pz % 16 == 0, "动态段大小被改变"
orig = open(sys.argv[1] + ".bak", "rb").read()
assert len(orig) == len(d), "文件大小被改变（原地平移不应改变大小）"
print("fix-bti: OK")
PY

echo
echo "== deps-version.py =="
mkdir -p "$T/src/releng"
cat > "$T/src/releng/deps.toml" <<'EOF'
[dependencies]
version = "20261106"
bootstrap_version = "20250801"
EOF
got="$(cd "$T/src" && python "$REPO/scripts/deps-version.py")"
echo "parsed: $got"
[ "$got" = "20261106" ] || { echo "deps-version: FAILED"; exit 1; }

echo
echo "== patch-cross-python.py =="
mkdir -p "$T/src/subprojects/frida-python/releng"
cat > "$T/src/releng/env_android.py" <<'EOF'
from configparser import ConfigParser
from pathlib import Path
import shlex

def init_machine_config(machine, is_cross_build, config):
    binaries = config["binaries"]
    return binaries
EOF
python "$REPO/scripts/patch-cross-python.py" "$T/src/releng/env_android.py" "$T/src/subprojects/frida-python/releng/env_android.py"
python "$REPO/scripts/patch-cross-python.py" "$T/src/releng/env_android.py"
echo "---- 补丁后的文件 ----"
cat "$T/src/releng/env_android.py"
python - "$T/src/releng/env_android.py" <<'PY'
import ast, sys
src = open(sys.argv[1], encoding="utf-8").read()
ast.parse(src)                       # 语法必须合法
assert src.count("FRIDA_CROSS_PYTHON_PATCH") == 1, "补丁不幂等"
assert "\nimport sys\n" in src, "缺 import sys"
print("patch-cross-python: OK")
PY

echo
echo "== python-include.py =="
inc="$(python "$REPO/scripts/python-include.py")"
echo "flags: $inc"
case "$inc" in
    *-I*) echo "python-include: OK" ;;
    *) echo "python-include: FAILED (没输出 -I 目录)"; exit 1 ;;
esac
python "$REPO/scripts/python-include.py" --lines | head -3

echo
echo "== make_wheel.py + verify-wheel.py =="
mkdir -p "$T/build/frida" "$T/artifacts"
cp "$T/libfake.so" "$T/build/frida/_frida.abi3.so"
for f in __init__.py aio.py _frida.pyi py.typed; do echo "# fake" > "$T/build/frida/$f"; done
python "$REPO/scripts/make_wheel.py" "$T/build/frida" "$T/artifacts" "17.26.1"
python "$REPO/scripts/verify-wheel.py" "$T/artifacts" android_24_arm64_v8a

echo "-- 反向用例：把 ELF 的 machine 改成 x86_64，应当失败 --"
python - "$T/build/frida/_frida.abi3.so" "$T/bad/frida" <<'PY'
import os, pathlib, struct, sys
src, dstdir = sys.argv[1], sys.argv[2]
os.makedirs(dstdir, exist_ok=True)
data = bytearray(open(src, "rb").read())
struct.pack_into("<H", data, 0x12, 62)   # EM_X86_64
open(os.path.join(dstdir, "_frida.abi3.so"), "wb").write(data)
for f in ("__init__.py", "aio.py", "_frida.pyi"):
    open(os.path.join(dstdir, f), "w").write("# fake\n")
PY
python "$REPO/scripts/make_wheel.py" "$T/bad/frida" "$T/artifacts" "17.26.1" >/dev/null
python "$REPO/scripts/verify-wheel.py" "$T/artifacts" android_24_arm64_v8a
rc=$?
echo "verify-wheel exit code (期望非 0): $rc"
[ "$rc" -ne 0 ] || { echo "verify-wheel 未拦截错误架构：FAILED"; exit 1; }

echo
echo "ALL LOCAL SMOKE TESTS PASSED"
exit 0
