#!/usr/bin/env python3
"""给 frida 的 releng/env_android.py 打一个【交叉构建 python 扩展】补丁（幂等 / best-effort）。

背景
----
frida-python 的 meson.build:

    python = import('python').find_installation()
    python_dep = python.dependency()
    ...
    extension = python.extension_module('_frida', ..., limited_api: '3.7', ...)

`import('python').find_installation()` 需要能定位到一个 CPython 安装。原生编译
（Termux 官方包、设备自编译）时 host == build，meson 直接找到本机 python，没问题；
但本工作流是 x86_64 → android-arm64 交叉构建，meson 在 cross file 里找不到 python
就会在 `meson setup` 阶段硬失败。

本补丁把一个 `python` 属性写进 frida 生成的 cross file（meson 允许 cross file
properties 携带 python 安装路径），指向构建机（x86_64）的 Python。因为 wheel 里的
`_frida.abi3.so` 走 limited API 3.7、不链接 libpython，用构建机的头文件编译出的
abi3 扩展在设备上（Termux Python 3.13+）即可加载 —— 这也是 README 里标注的
"上游未覆盖、需要实测" 的组合点。

注意：这是 best-effort。文件不存在（例如只 checkout 了 frida-python 单仓）就跳过；
上游将来若原生支持交叉构建，本补丁也不会造成破坏（重复执行会跳过）。
"""
import pathlib
import sys

MARKER = "FRIDA_CROSS_PYTHON_PATCH"
ANCHOR = '    binaries = config["binaries"]\n'
IMPORT_SYS = "from pathlib import Path\n"

SNIPPET = (
    f'    # {MARKER}: 交叉构建时让 meson 用构建机的 Python（abi3 扩展不链接 libpython）\n'
    '    if is_cross_build and "python" not in config["properties"]:\n'
    '        config["properties"]["python"] = "\'" + sys.executable + "\'"\n'
)


def patch(path: pathlib.Path) -> str:
    if not path.is_file():
        return f"skip (不存在): {path}"
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return f"skip (已打过补丁): {path}"
    if ANCHOR not in text:
        return f"skip (找不到锚点，上游结构可能已变): {path}"

    patched = text.replace(ANCHOR, SNIPPET + ANCHOR, 1)
    if "\nimport sys\n" not in patched:
        patched = patched.replace(IMPORT_SYS, IMPORT_SYS + "import sys\n", 1)
    path.write_text(patched, encoding="utf-8")
    return f"patched: {path}"


def main() -> int:
    targets = [pathlib.Path(p) for p in sys.argv[1:]]
    if not targets:
        targets = [pathlib.Path("releng/env_android.py")]
    for target in targets:
        print(patch(target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
