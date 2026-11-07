# frida android-arm64 自动构建（GitHub Actions）

自动跟踪上游 frida 版本 → 在 **x86_64 runner 上用官方 NDK r29 交叉编译** → 发布
`android_24_arm64_v8a` 的 abi3 wheel（Termux / 已 root 的 arm64 设备可直接 `pip install`）。

本机不需要算力、不需要 ARM 设备、不需要代理；上游发版后全自动出包。

## 为什么走 x86_64 + NDK 交叉编译

| 方案 | 问题 |
|---|---|
| ARM64 runner + Termux 容器（v1） | ARM runner 有更短的 job 时限，frida 全量构建（1h+）容易被杀；无缓存时每次 2–3h |
| 设备自编译（见 `docs/BUILD_FRIDA_17.18_TERMUX_REPRO.md`） | 可行但重活：自举 vala、打 releng/源码补丁、磁盘吃紧、2.5GB 产物 |
| **x86_64 + NDK r29 交叉编译（本方案）** | 6h job 上限；官方 CI 的 android 目标就是 x86_64 交叉；vala/ninja 用官方预编译 toolchain bundle，免自举 |

关键事实（都已写进 workflow，改动前请先核对这些上游约束）：

- 上游 `releng/env_android.py` 里 `NDK_REQUIRED = 29`，且 `ANDROID_NDK_ROOT/source.properties`
  的主版本号必须等于 29，否则 configure 直接报错 → 因此固定 NDK **r29**（r29/r29b/r29c 依次尝试）。
- 上游 frida 的 git tag **不带 `v` 前缀**（`17.26.1`）；本仓库发布时用 `v17.26.1` 与之区分。
- `releng` 是独立子模块（`frida/releng`），`deps.toml` 在 **`releng/deps.toml`**，
  不在 `subprojects/frida-python/releng/deps.toml`（老写法在 monorepo 里必然 404）。
- SDK/toolchain bundle 由 `deps.py` 落到 `<源码根>/deps/`（`sdk-android-arm64`、
  `toolchain-linux-x86_64`），缓存这一个目录就等于缓存了 1GB+ 的大件。

## 工作流结构

```
detect (ubuntu-latest, ~20s)
  PyPI 取 frida / frida-tools 最新版
  GitHub API 查本仓库是否已有 v<ver> Release → 有则跳过（省分钟数）

build (ubuntu-latest, timeout 330min)
  checkout 本仓库 → builder/            # 构建脚本
  checkout 上游 frida@<ver> → frida/    # submodules: recursive（含 releng）
  解析 deps.toml 的 bundle 版本
  cache: NDK r29 / frida/deps（SDK+toolchain）
  下载 NDK r29（校验 Pkg.Revision=29）
  patch releng/env_android.py（交叉构建注入构建机的 Python，见下）
  scripts/build-android.sh:
      路径 A：根目录 ./configure --host=android-arm64 --enable-frida-python && make
      路径 B（A 失败时）：subprojects/frida-python 独立 configure && make
      find _frida.abi3.so → 手搓 abi3 wheel
  pip download frida-tools wheel（--only-binary，不会退化成源码包）
  best-effort 附带同版本 frida-server（root 设备用）
  verify-wheel.py：校验 wheel 结构 / 平台标签 / aarch64 ELF / 无 libpython 依赖
  发布 Release v<ver>（含 wheel、frida-tools、frida-server.xz、fix-bti.sh）
```

## 使用

1. 建一个 GitHub 仓库（公开仓库 Actions 免费无限；私有仓库会消耗分钟数），把本目录推上去。
2. Settings → Actions → General → Workflow permissions → **Read and write permissions**
   （发布 Release 需要 `contents: write`）。
3. Actions → `Build frida android-arm64 wheels` → **Run workflow**
   - `frida_version` 留空 = PyPI 最新版；也可填 `17.26.1` / `v17.26.1`
   - `skip_cache` 勾上 = 忽略缓存全量重下（排查缓存污染时用）
4. 首次约 40–90 分钟；同一版本重跑会命中 NDK/deps 缓存。

## 设备端安装（Termux）

前置条件（不满足会出现"not a supported wheel"这类误导性报错）：

- **pip ≥ 25.1**：更早的 Termux pip 只认 `linux_aarch64`，不认 PEP 738 的
  `android_24_arm64_v8a`。
- **Termux 的 Python 3.13+**：此时 `sys.platform == "android"`。
- 老版 Termux（Python 3.12 及更早）报告的是 `linux_aarch64`，本仓库的 wheel 装不上，
  只能自行在设备上源码编译（见 `docs/`）。

```bash
pkg install python python-pip
pip install -U pip                     # 关键：升级到会识别 android_* 标签的 pip
pip install --force-reinstall --no-deps \
  https://github.com/<你的仓库>/releases/download/v17.26.1/frida-17.26.1-cp37-abi3-android_24_arm64_v8a.whl
pip install --no-deps \
  https://github.com/<你的仓库>/releases/download/v17.26.1/frida_tools-<版本>-py3-none-any.whl
python -c 'import frida; print(frida.__version__)'
```

root 设备还需要**同版本** frida-server：

```bash
xz -d frida-server-17.26.1-android-arm64.xz
adb push frida-server-17.26.1-android-arm64 /data/local/tmp/frida-server
adb shell "su -c 'chmod 755 /data/local/tmp/frida-server && /data/local/tmp/frida-server &'"
su -c 'PATH=/data/data/com.termux/files/usr/bin:$PATH frida-ps'
```

Android 10 上如出现 `unknown processor-specific (type 0x70000001)` 的 linker 刷屏
（Termux 的 `libc++_shared.so` 带 `DT_AARCH64_BTI_PLT`），执行 Release 里附带的
`fix-bti.sh`；Termux 升级 libc++ 后重跑一次即可（脚本幂等，只保留第一次的 `.bak`）。

## 已知限制 / 风险（必读）

| 项目 | 说明 |
|---|---|
| **交叉编译 python 绑定未被上游覆盖** | 上游 CI 的 android 目标只编 core/gadget/server，**不编** frida-python。本仓库是 `x86_64 → android-arm64` python 绑定交叉构建。2026-11 首次实跑已确认整条链路可走到 `extension.c` 编译（meson cross file、代码生成、frida-core/gum 全部通过），仅在 CPython 头文件路径上翻车，已用 `scripts/python-include.py`（`CPPFLAGS` 注入 `include`+`platinclude`）修掉。运行期能否在 bionic 上 dlopen 仍须设备实测。 |
| 双路径 configure | 路径 A（monorepo 根目录 + `--enable-frida-python`，Termux 官方包同一配方）为主；失败自动退回路径 B（`subprojects/frida-python` 独立 configure，设备自编译手册验证过的路径）。哪条成功由 `.so` 的实际位置决定，脚本用全树 `find` 兜底。 |
| 构建时长 | 全量 40–90 分钟；`deps` 缓存能省掉每轮 ~1GB 的 bundle 下载，但编译本身不可缓存（想再快可自行加 `build/` 缓存，用版本号 + deps 版本做 key）。 |
| 缓存上限 | GitHub 单仓库 cache 上限 10GB；本流程 NDK(~600MB) + deps(~1GB) 远低于上限。 |
| 产物自检的边界 | `verify-wheel.py` 能证明"是 aarch64 ELF 且不依赖 libpython"，但**不能**证明它能在 bionic 上 dlopen 成功——后者只能在设备上验。 |
| arm64 之外的架构 | 本仓库只做 arm64-v8a。32 位需要 NDK arm 目标 + `android_16_armv7a` 之类的标签，未实现。 |

排错：

| 现象 | 处理 |
|---|---|
| `ANDROID_NDK_ROOT must be set to the location of your r29 NDK` | NDK 缓存脏了/版本不对，勾 `skip_cache` 重跑 |
| `extension.c:16: fatal error: 'pyconfig.h' file not found` | 构建机缺 CPython 头文件，或 `python3-dev` 未安装。`scripts/python-include.py` 负责把 `include`+`platinclude`（Ubuntu 的 multiarch 目录）塞进 `CPPFLAGS`；若目录本身不存在就先 `apt install python3-dev` |
| `fatal error: 'aarch64-linux-gnu/python3.x/pyconfig.h' file not found` | Debian 的 `pyconfig.h` 是分发壳，按 `-target` 的架构去 include multiarch 目录。`python-include.py` 会在 `$RUNNER_TEMP/frida-pyconfig-shim` 造同名相对路径的 shim 并排在 `-I` 最前（不改系统文件、不需要 root） |
| configure 阶段报 `python` 相关错误 | 说明上游 `env_android.py` 结构变了，`scripts/patch-cross-python.py` 的锚点要跟着改（脚本会打印 `找不到锚点`） |
| `MISSING _frida.abi3.so` | 看 Actions 里 `build-logs-*` 产物里的 `configure-A/B.log`、`make-A/B.log` 尾部 |
| Termux 装包报 `not a supported wheel on this platform` | `pip -V` 低于 25.1，或 Termux Python < 3.13 |

## 目录结构

```
.github/workflows/build-frida.yml    # detect + build + release
scripts/build-android.sh             # configure + make + 手搓 wheel（宿主侧，带 A/B 两条路径）
scripts/make_wheel.py                # 构造 abi3 wheel（含 RECORD/metadata）
scripts/verify-wheel.py              # 产物自检（结构 / 平台标签 / aarch64 / 无 libpython）
scripts/deps-version.py              # 从 releng/deps.toml 解析 bundle 版本
scripts/python-include.py            # 收集构建机 CPython 头文件目录（含 Ubuntu multiarch 的 pyconfig.h）
scripts/patch-cross-python.py        # 交叉构建时给 releng/env_android.py 注入 python 属性（幂等）
scripts/fix-bti.sh                   # 设备端：移除 DT_AARCH64_BTI_PLT/PAC_PLT，消除 linker 刷屏
tests/smoke.sh                       # 本地冒烟测试（不需要 frida/Linux，可离线跑）
docs/BUILD_FRIDA_17.18_TERMUX_REPRO.md  # 设备自编译完整手册（备用手册）
docs/REVIEW.md                       # 本轮的审查结论与逐条修正记录
```

## 版本时间线

- v1（2026-09）：ARM64 runner + Termux 容器（job 时长与无缓存问题）
- v2（2026-09）：x86_64 + NDK r29 官方路径 + 三重缓存
- v2.1（2026-11）：修正 checkout 目标/路径/标签探测等硬伤，补产物自检与 Termux pip 前置条件
