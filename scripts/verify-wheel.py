#!/usr/bin/env python3
"""产物自检：确认 wheel 结构正确、平台标签正确、_frida.abi3.so 真的是 aarch64 ELF 且不依赖 libpython。

用法: python3 verify-wheel.py <artifacts 目录> [期望平台标签]
退出码非 0 即表示产物不可用，避免把坏包发到 Release。
"""
import pathlib
import struct
import sys
import zipfile

REQUIRED_MEMBERS = (
    "frida/__init__.py",
    "frida/aio.py",
    "frida/_frida.pyi",
    "frida/_frida.abi3.so",
)

EM_AARCH64 = 183


def fail(msg: str) -> "SystemExit":
    return SystemExit(f"VERIFY FAILED: {msg}")


def main() -> int:
    art = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts")
    plat_tag = sys.argv[2] if len(sys.argv) > 2 else "android_24_arm64_v8a"

    wheels = sorted(art.glob("frida-*.whl"))
    if len(wheels) != 1:
        raise fail(f"{art} 里应当恰好有 1 个 frida-*.whl，实际 {[w.name for w in wheels]}")
    whl = wheels[0]
    if not whl.name.endswith(f"-cp37-abi3-{plat_tag}.whl"):
        raise fail(f"wheel 文件名平台标签不是 cp37-abi3-{plat_tag}: {whl.name}")

    with zipfile.ZipFile(whl) as z:
        names = set(z.namelist())
        missing = [m for m in REQUIRED_MEMBERS if m not in names]
        if missing:
            raise fail(f"wheel 缺少成员: {missing}")

        dist_infos = {n.split("/")[0] for n in names if n.endswith(".dist-info/WHEEL")}
        if len(dist_infos) != 1:
            raise fail(f"dist-info 不唯一: {dist_infos}")
        dist_info = dist_infos.pop()
        for member in ("METADATA", "WHEEL", "RECORD", "top_level.txt"):
            if f"{dist_info}/{member}" not in names:
                raise fail(f"dist-info 缺少 {member}")

        wheel_meta = z.read(f"{dist_info}/WHEEL").decode()
        if f"Tag: cp37-abi3-{plat_tag}" not in wheel_meta:
            raise fail(f"WHEEL 里的 Tag 与平台标签不一致:\n{wheel_meta}")

        so = z.read("frida/_frida.abi3.so")

    if so[:4] != b"\x7fELF":
        raise fail("_frida.abi3.so 不是 ELF 文件")
    if so[4] != 2:
        raise fail("_frida.abi3.so 不是 64 位 ELF")
    if so[5] != 1:
        raise fail("_frida.abi3.so 不是小端 ELF")
    machine = struct.unpack_from("<H", so, 0x12)[0]
    if machine != EM_AARCH64:
        raise fail(f"_frida.abi3.so 的 e_machine={machine}，不是 aarch64（183）")
    if b"libpython" in so:
        raise fail("_frida.abi3.so 里出现 libpython 符号/依赖，abi3 去依赖被破坏")

    print(f"OK: {whl.name}")
    print(f"  _frida.abi3.so: aarch64 ELF64, {len(so)} bytes, 无 libpython 依赖")
    others = sorted(p.name for p in art.iterdir() if p.name != whl.name)
    print(f"  同目录其他产物: {others}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
