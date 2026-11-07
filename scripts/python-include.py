#!/usr/bin/env python3
"""打印构建机 CPython 的头文件目录（-I 形式），用于交叉编译 abi3 扩展。

为什么需要这个：
  Debian/Ubuntu 把 pyconfig.h 装在 multiarch 目录（/usr/include/x86_64-linux-gnu/python3.x），
  而 meson 的 `python.dependency()` 只回 sysconfig 的 include（/usr/include/python3.x），
  于是 extension.c 里 `#include <pyconfig.h>` 直接 fatal error。
  frida 的 env_android.py 会把 CPPFLAGS 合进 android 目标的 c_args，
  所以把两个目录都塞进 CPPFLAGS 即可。

用法:
  python3 python-include.py            # 打印 "-I/a -I/b"（可直接赋给 CPPFLAGS）
  python3 python-include.py --lines    # 每行一个目录
"""
import pathlib
import sys
import sysconfig


def candidate_dirs():
    seen = set()

    def emit(path):
        if not path:
            return
        p = pathlib.Path(path)
        if p not in seen:
            seen.add(p)
            yield p

    paths = sysconfig.get_paths()
    for key in ("include", "platinclude"):
        yield from emit(paths.get(key))
    yield from emit(sysconfig.get_config_var("INCLUDEPY"))

    # Debian/Ubuntu 的 multiarch 目录、以及 setup-python toolcache 的布局
    for base in (pathlib.Path("/usr/include"), pathlib.Path("/usr/local/include")):
        if not base.is_dir():
            continue
        for pattern in ("python3*", "*-linux-gnu/python3*", "*-linux-musl/python3*"):
            for p in sorted(base.glob(pattern)):
                yield from emit(p)
    for base in (pathlib.Path("/opt/hostedtoolcache/Python"), pathlib.Path.home() / ".pyenv" / "versions"):
        if not base.is_dir():
            continue
        for p in sorted(base.glob("*/**/include/python3*")):
            yield from emit(p)


def usable(p: pathlib.Path) -> bool:
    if not p.is_dir():
        return False
    return (p / "Python.h").exists() or (p / "pyconfig.h").exists()


def main() -> int:
    dirs = [p for p in candidate_dirs() if usable(p)]
    if "--lines" in sys.argv:
        print("\n".join(str(p) for p in dirs))
    else:
        print(" ".join(f"-I{p}" for p in dirs))
    # 没有 pyconfig.h 时给出明确提示（比 ninja 阶段的 fatal error 好排查）
    if dirs and not any((p / "pyconfig.h").exists() for p in dirs):
        print("WARNING: 找到 CPython 头文件但缺少 pyconfig.h，请确认已安装 python3-dev", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
