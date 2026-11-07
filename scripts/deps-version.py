#!/usr/bin/env python3
"""从 frida 源码树里解析依赖 bundle 版本号（deps.toml 的 [dependencies] version）。

releng 是独立子模块（frida/releng），monorepo 里 deps.toml 位于 releng/ 下；
直接读 frida-python/releng/deps.toml 的老写法在 monorepo 里会 404。
用法: cd <frida src> && python3 deps-version.py
"""
import pathlib
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("需要 Python >= 3.11（tomllib）")

CANDIDATES = (
    pathlib.Path("releng/deps.toml"),
    pathlib.Path("subprojects/frida-python/releng/deps.toml"),
    pathlib.Path("subprojects/frida-core/releng/deps.toml"),
)

paths = [pathlib.Path(p) for p in sys.argv[1:]] or list(CANDIDATES)
for path in paths:
    if path.is_file():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        version = data.get("dependencies", {}).get("version")
        if version:
            print(version)
            raise SystemExit(0)

sys.exit("无法在 frida 源码树里找到 deps.toml / dependencies.version")
