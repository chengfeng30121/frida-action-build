#!/usr/bin/env python3
"""为交叉编译 abi3 扩展准备构建机 CPython 头文件，并输出 -I 旗标。

要解决的两个坑（Debian/Ubuntu）：

  1) meson 的 `python.dependency()` 只回 sysconfig 的 include（/usr/include/python3.x），
     而 pyconfig.h 的真身在 multiarch 目录（/usr/include/<triplet>/python3.x/pyconfig.h）
     → `#include <pyconfig.h>` 找不到。

  2) 找到的 /usr/include/python3.x/pyconfig.h 往往只是个「分发壳」：
         #if defined(__aarch64__)
         #  include <aarch64-linux-gnu/python3.14/pyconfig.h>
     交叉编 aarch64 时 -target 会定义 __aarch64__，它就去找 aarch64 的 multiarch 目录，
     而构建机是 x86_64，只有 x86_64-linux-gnu 那份 → 仍然 fatal error。

本脚本**不改系统文件**、不需要 root：在临时目录里造一个 shim，把壳要求的相对路径
（如 aarch64-linux-gnu/python3.14/pyconfig.h）用具体那份配置填上，并把 shim 放进 -I 最前面。
两份配置都是 LP64（SIZEOF_LONG / SIZEOF_VOID_P / 对齐等 ABI 宏一致），对 abi3 扩展足够；
运行期行为仍由设备上的 CPython 决定。

用法:
  python3 python-include.py                 # 自动探测 + 造 shim，输出 "-I/shim -I/usr/include/python3.x"
  python3 python-include.py --lines         # 每行一个目录
  python3 python-include.py --dirs /x /y    # 只用给定目录（测试用）
环境变量:
  FRIDA_PYCONFIG_ROOTS   冒号分隔的搜索根，默认 /usr/include:/usr/local/include
  RUNNER_TEMP            临时目录（shim 落在 $RUNNER_TEMP/frida-pyconfig-shim）
"""
import os
import pathlib
import re
import sys
import sysconfig

WRAPPER_RE = re.compile(r"#\s*include\s*<([^>]*python[\d.]*/pyconfig\.h)>")

# 实在找不到具体配置时的兜底：只给 ABI 尺寸宏
MINIMAL_PYCONFIG = """\
/* 交叉编译降级配置：仅提供 ABI 尺寸宏，供 abi3 扩展编译使用 */
#ifndef Py_PYCONFIG_H
#define Py_PYCONFIG_H
#define SIZEOF_INT 4
#define SIZEOF_LONG 8
#define SIZEOF_LONG_LONG 8
#define SIZEOF_SHORT 2
#define SIZEOF_SIZE_T 8
#define SIZEOF_VOID_P 8
#define SIZEOF_FLOAT 4
#define SIZEOF_DOUBLE 8
#define SIZEOF_PID_T 4
#define SIZEOF_OFF_T 8
#define SIZEOF_TIME_T 8
#define HAVE_LONG_LONG 1
#define HAVE_STDINT_H 1
#define HAVE_SYS_TYPES_H 1
#define HAVE_UNISTD_H 1
#define HAVE_PTHREAD 1
#define Py_GIL_DISABLED 0
#define DOUBLE_IS_LITTLE_ENDIAN_IEEE754 1
#endif
"""


def search_roots():
    raw = os.environ.get("FRIDA_PYCONFIG_ROOTS")
    if raw:
        return [pathlib.Path(p) for p in raw.split(os.pathsep) if p]
    return [pathlib.Path("/usr/include"), pathlib.Path("/usr/local/include")]


def shim_root() -> pathlib.Path:
    base = pathlib.Path(os.environ.get("RUNNER_TEMP") or os.environ.get("TMPDIR") or "/tmp")
    return base / "frida-pyconfig-shim"


def sysconfig_dirs():
    paths = sysconfig.get_paths()
    for key in ("include", "platinclude"):
        p = paths.get(key)
        if p:
            yield pathlib.Path(p)
    p = sysconfig.get_config_var("INCLUDEPY")
    if p:
        yield pathlib.Path(p)


def globbed_dirs():
    for base in search_roots():
        if not base.is_dir():
            continue
        for pattern in ("python3*", "*-linux-gnu/python3*", "*-linux-musl/python3*"):
            yield from sorted(base.glob(pattern))
    for base in (pathlib.Path("/opt/hostedtoolcache/Python"), pathlib.Path.home() / ".pyenv" / "versions"):
        if not base.is_dir():
            continue
        yield from sorted(base.glob("*/**/include/python3*"))


def find_concrete(version_dir_name: str):
    """找同一 Python 版本的具体 multiarch 配置，如 x86_64-linux-gnu/python3.14/pyconfig.h。"""
    for root in search_roots():
        if not root.is_dir():
            continue
        for candidate in sorted(root.glob(f"*-linux-*/{version_dir_name}/pyconfig.h")):
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not WRAPPER_RE.search(text):
                return candidate
    return None


def build_shim(include_dir: pathlib.Path, targets) -> int:
    """按壳里要求的相对路径造 shim，返回成功写入的个数。"""
    written = 0
    for rel in targets:
        rel_path = pathlib.Path(rel)
        version = rel_path.parent.name  # 例如 python3.14
        concrete = find_concrete(version)
        payload = concrete.read_bytes() if concrete is not None else MINIMAL_PYCONFIG.encode()
        dest = shim_root() / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        src = concrete if concrete is not None else "MINIMAL_PYCONFIG"
        print(f"  shim {rel} ← {src}", file=sys.stderr)
        written += 1
    return written


def usable(p: pathlib.Path) -> bool:
    return p.is_dir() and ((p / "Python.h").exists() or (p / "pyconfig.h").exists())


def main() -> int:
    argv = sys.argv[1:]
    lines = "--lines" in argv
    explicit = []
    if "--dirs" in argv:
        explicit = [pathlib.Path(a) for a in argv[argv.index("--dirs") + 1:]]

    ordered = []
    for d in explicit + list(sysconfig_dirs()) + list(globbed_dirs()):
        if d not in ordered:
            ordered.append(d)

    dirs, need_shim = [], False
    for d in ordered:
        if not usable(d):
            continue
        dirs.append(d)
        config = d / "pyconfig.h"
        if not config.is_file():
            continue
        text = config.read_text(encoding="utf-8", errors="replace")
        targets = WRAPPER_RE.findall(text)
        if not targets:
            print(f"{d}: pyconfig.h 自包含", file=sys.stderr)
            continue
        print(f"{d}: pyconfig.h 是分发壳，造 shim", file=sys.stderr)
        if build_shim(d, targets):
            need_shim = True

    flags = [f"-I{shim_root()}"] if need_shim else []
    flags += [f"-I{d}" for d in dirs]
    print("\n".join(flags) if lines else " ".join(flags))

    if not dirs:
        print("WARNING: 没找到任何 CPython 头文件目录（缺 python3-dev？）", file=sys.stderr)
    elif not any((d / "pyconfig.h").exists() for d in dirs):
        print("WARNING: 有头文件目录但都没有 pyconfig.h，请确认已安装 python3-dev", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
