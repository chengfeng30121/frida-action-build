#!/usr/bin/env python3
"""手搓 frida abi3 wheel（等价于 `pip wheel .`，但不需要跑 setuptools 的复制流程）。

用法: python3 make_wheel.py <build/frida 目录> <输出目录> <版本>
环境变量: WHEEL_PLAT_TAG  平台标签，默认 android_24_arm64_v8a
"""
import base64
import hashlib
import os
import shutil
import sys
import tempfile
import zipfile

PLAT_TAG = os.environ.get("WHEEL_PLAT_TAG", "android_24_arm64_v8a")

src, out, ver = sys.argv[1:4]
plat_tag = PLAT_TAG
wheel_name = f"frida-{ver}-cp37-abi3-{plat_tag}.whl"

required = ("__init__.py", "aio.py", "_frida.pyi", "_frida.abi3.so")
missing = [fn for fn in required if not os.path.isfile(os.path.join(src, fn))]
if missing:
    sys.exit(f"源码目录 {src} 缺少文件: {', '.join(missing)}")

tmp = tempfile.mkdtemp(prefix="frida-whl-")
try:
    pkg = os.path.join(tmp, "frida")
    dinfo = os.path.join(tmp, f"frida-{ver}.dist-info")
    os.makedirs(pkg)
    os.makedirs(dinfo)

    for fn in required:
        shutil.copy(os.path.join(src, fn), pkg)
    # py.typed 是可选文件（上游 17.x 才有）
    pytyped = os.path.join(src, "py.typed")
    if os.path.exists(pytyped):
        shutil.copy(pytyped, pkg)

    meta = f"""Metadata-Version: 2.1
Name: frida
Version: {ver}
Summary: Dynamic instrumentation toolkit for developers, reverse-engineers, and security researchers
Home-page: https://frida.re
Author: Ole Andre Vadla Ravnaas
Author-email: oleavr@nowsecure.com
License: wxWindows Library Licence
Requires-Python: >=3.7
Requires-Dist: typing_extensions; python_version < "3.11"
Classifier: Programming Language :: Python :: 3
Classifier: Operating System :: Android
"""
    with open(os.path.join(dinfo, "METADATA"), "w", encoding="utf-8") as f:
        f.write(meta)
    with open(os.path.join(dinfo, "WHEEL"), "w", encoding="utf-8") as f:
        f.write(
            "Wheel-Version: 1.0\n"
            "Generator: bdist_wheel (0.42.0)\n"
            "Root-Is-Purelib: false\n"
            f"Tag: cp37-abi3-{plat_tag}\n"
        )
    with open(os.path.join(dinfo, "top_level.txt"), "w", encoding="utf-8") as f:
        f.write("frida\n")

    rec = []
    for root, _, files in os.walk(tmp):
        for fn in files:
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, tmp)
            h = hashlib.sha256(open(p, "rb").read()).digest()
            rec.append(
                f"{rel},sha256={base64.urlsafe_b64encode(h).rstrip(b'=').decode()},"
                f"{os.path.getsize(p)}"
            )
    rec.append(f"frida-{ver}.dist-info/RECORD,,")
    with open(os.path.join(dinfo, "RECORD"), "w", encoding="utf-8") as f:
        f.write("\n".join(rec) + "\n")

    os.makedirs(out, exist_ok=True)
    whl = os.path.join(out, wheel_name)
    with zipfile.ZipFile(whl, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(tmp):
            for fn in files:
                p = os.path.join(root, fn)
                z.write(p, os.path.relpath(p, tmp))
    print(f"WHEEL: {whl} ({os.path.getsize(whl)} bytes)")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
