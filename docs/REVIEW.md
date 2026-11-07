# 审查结论（2026-11-07）

对象：`frida-build-action` 原始 v2（x86_64 + NDK r29 交叉编译发布 android-arm64 wheel）。
结论：**思路成立，但原工作流跑一次必然失败**（checkout 的仓库不对，紧接着的 deps.toml
路径也不存在）。下面按"硬伤 / Termux 适配 / 加固"分类列出问题与修正。

## A. 硬伤（必然失败）

| # | 问题 | 修正 |
|---|---|---|
| A1 | `actions/checkout@v4` 没有 `repository:`，check 出来的是**本仓库**（存放 workflow 的仓库），不是上游 frida。随后 `subprojects/frida-python/releng/deps.toml`、`./configure` 全都无从谈起 | 拆成两次 checkout：本仓库 → `builder/`（脚本），上游 → `frida/`（`repository: frida/frida`, `ref: <ver>`, `submodules: recursive`） |
| A2 | 读 deps 版本用的是 `subprojects/frida-python/releng/deps.toml`。上游 `releng` 是独立子模块（`frida/releng`），monorepo 里真身在 `releng/deps.toml`；这条路径在 monorepo 下不存在 | 新增 `scripts/deps-version.py`，按 `releng/deps.toml` → `subprojects/frida-python/releng/deps.toml` 顺序探测 |
| A3 | `find "$SRC/build" -name '_frida.abi3.so' -path '*frida-python*'` 只覆盖 monorepo 布局；路径 A（根目录 configure）产物在 `build/subprojects/frida-python/frida/`，路径 B 在 `subprojects/frida-python/build/frida/` | 全树 `find -type f -name '_frida.abi3.so'` + 候选打印 |
| A4 | **SDK bundle 预置步骤会破坏构建**：预先 `mkdir -p subprojects/frida-python/subprojects/frida-core/deps` 后再让 meson wrap 克隆 frida-core，wrap 会因目录已存在而非空直接失败；而预置路径本身也是猜的 | 删除预置步骤，改由上游 `deps.py` 自己下载（`build.frida.re` 上的 bundle 稳定），改为缓存 `<src>/deps` |
| A5 | frida-tools 的下载 URL `https://pypi.org/packages/py3/f/frida-tools/...` 不是 PyPI 的真实文件路径（真实路径是 `/packages/<hash>/<hash>/<file>`），必然 404，只是被 `|| true` 掩盖 | 改为 `pip download --no-deps --only-binary=:all:` |
| A6 | 版本探测用 `git ls-remote https://github.com/${GITHUB_REPOSITORY}.git`：私有仓库无凭据会失败；且探测的是 tag 而不是 Release，tag 存在但 Release 没发出去时会永久跳过 | 改用 `gh api repos/${REPO}/releases/tags/v<ver>` |
| A7 | `if [ -n "${{ github.event.inputs.frida_version }}" ]` 等把用户输入直接拼进 shell（脚本注入面），schedule 触发时该上下文为空依赖巧合 | 改成 `env:` 注入 + `inputs.*`，并对输入做 `${REQUESTED#v}` 归一化（上游 tag 无 `v` 前缀） |
| A8 | `pip install meson==1.6.1`：上游用自带 meson（`releng/meson`，`--with-meson=internal` 默认），外界装一个旧 meson 只会制造歧义 | 删除；只装 `ninja-build xz-utils unzip` |

## B. Termux 适配

| # | 问题 | 修正 |
|---|---|---|
| B1 | 设备端没有提 **pip 版本门槛**。Termux 的 `python-pip`（25.0.x）只接受 `linux_aarch64`，不认 PEP 738 的 `android_24_arm64_v8a`；用户会看到 "not a supported wheel on this platform" 却以为是 wheel 坏了 | README + Release 说明里写明：`pip install -U pip`（≥25.1），并要求 Termux Python 3.13+（此时 `sys.platform == "android"`） |
| B2 | wheel 元数据缺 `Requires-Dist: typing_extensions; python_version < "3.11"`（上游 `setup.py` 有）。用 `--no-deps` 安装时，Python < 3.11 上 `import frida` 会缺依赖 | `make_wheel.py` 补上该依赖声明 |
| B3 | `fix-bti.sh`：①`assert removed >= 1` —— 库本来没有该条目时脚本以非 0 退出（Termux 升级后常态）；②重复执行会把**已打补丁的文件**覆盖成 `.bak`，原件丢失；③shebang 写死 Termux 路径，换环境即坏；④只处理 BTI_PLT，不处理 PAC_PLT | 无条目时打印并 `exit 0`；备份只在缺失时创建；shebang 改 `#!/usr/bin/env bash`；同时清理 `DT_AARCH64_BTI_PLT(0x70000001)` 与 `DT_AARCH64_PAC_PLT(0x70000003)`；支持 `$1` 传目标 so |
| B4 | 设备上 `frida-ps` 需要**同版本** frida-server，但 Release 里没有，用户在 README 的 `su -c frida-ps` 一步必然失败 | best-effort 附带 `frida-server-<ver>-android-arm64.xz`（上游无资产时不阻塞），Release 说明补 push/授权命令 |
| B5 | `make_wheel.py` / `fix-bti.sh` 的 shebang 是 `/data/data/com.termux/files/usr/bin/env`，在 runner 上无意义 | runner 侧脚本改 `#!/usr/bin/env`；Termux 专用脚本保留对 Termux 的说明但不再绑死绝对路径 |
| B6 | 平台标签 `android_24_arm64_v8a` 与实际编译 API（上游 `android_api = 21`）不一致这件事没有任何说明，容易被后来者"修正"成 `android_21_*`（那样 Termux 反而装不上） | 在 README 与 workflow 注释里写明：**按设备侧 pip 支持的标签定 24，二进制按 21 编译只是更宽松，不冲突** |

## C. 加固（不修也能跑，但建议）

- `timeout-minutes: 330`（6h 上限内留余量），`concurrency` 保留；
- 失败时也上传 `logs/configure.log`、`logs/make.log`（`if: always()`），排错不用重跑；
- 新增 `verify-wheel.py`：断言 wheel 成员/平台标签/`e_machine == 183`/无 `libpython` 字样，
  坏包不进 Release；
- `ContinueOnError` 只用在 frida-server 这种可选资产上；
- 归档 `fix-bti.sh` 时注意仓库本身被 checkout 到 `builder/`，`files:` 路径要写 `builder/scripts/fix-bti.sh`。

## D. 仍未验证的点（诚实标注）

1. **`x86_64 → android-arm64` 的 frida-python 交叉编译**：上游 CI 的 android 目标不含
   `--enable-frida-python`，Termux 官方包是**原生**编译，所以这条组合没有先例。
   工作流为此打了 `patch-cross-python.py`（把构建机 Python 声明进 meson cross file，
   供 abi3 扩展取 CPython 头文件）。若 configure 在此处失败，日志会明确指向
   `import('python')`，届时按日志调整锚点或改为给 cross file 提供 host python 头文件目录。
2. **运行期可用性**：abi3 + 不链接 libpython 只是必要条件，能否在 bionic 上 dlopen
   必须在设备上验（`import frida`）。
3. `--enable-frida-python` 交叉时是否还会牵出 `frida-tools`/`frida-core` 的子构建差异，
   取决于上游版本，需要第一次实跑日志确认。

## E. 本地已验证的部分
`tests/smoke.sh`（离线可跑，不需要 frida/Linux）覆盖：

- `fix-bti.sh`：造一个含 `DT_AARCH64_BTI_PLT`/`PAC_PLT` 的 ELF，验证条目被移除、
  其余动态条目（如 `DT_SYMTAB`）保留、文件大小不变、二次执行变空操作且不覆盖 `.bak`；
- `make_wheel.py` + `verify-wheel.py`：正常产物通过；把 `e_machine` 改成 x86_64 后
  校验必须失败（负向用例）；
- `deps-version.py`：正确解析 `releng/deps.toml`；
- `patch-cross-python.py`：补丁后语法合法、幂等、缺文件时优雅跳过。

```
$ bash tests/smoke.sh
...
ALL LOCAL SMOKE TESTS PASSED
```

## F. 首次实跑反馈（2026-11-07，仓库 chengfeng30121/frida-action-build）

第一次真跑（两条 configure 路径）报同一个错：

```
frida/extension.c:16:11: fatal error: 'pyconfig.h' file not found
ninja: build stopped: subcommand failed.
ninja: ... /frida/subprojects/frida-python/deps/toolchain-linux-x86_64/bin/ninja
make: *** [Makefile:4: all] Error 1
```

两个结论：

1. **好消息**：交叉链路本身是通的。能走到 `extension.c` 这一步，说明 meson cross file 被接受、
   `import('python')` 没卡住、frida-core / frida-gum / 代码生成全部编译成功，
   只剩最后一步缺头文件。D 节里"交叉编 python 绑定没有先例"的最大不确定性基本被排除。
2. **根因**：Debian/Ubuntu 的 `pyconfig.h` 装在 multiarch 目录
   （`/usr/include/x86_64-linux-gnu/python3.x/pyconfig.h`），而 meson 的
   `python.dependency()` 只回 `sysconfig` 的 `include`（`/usr/include/python3.x`），
   于是 `<Python.h>` 找得到、`<pyconfig.h>` 找不到。

修正：

- 新增 `scripts/python-include.py`：收集 `include` / `platinclude` / `INCLUDEPY` 以及
  multiarch、toolcache、pyenv 布局里所有含 `Python.h`/`pyconfig.h` 的目录，输出 `-I...`；
  没有 `pyconfig.h` 时往 stderr 交警告。
- `build-android.sh` 把它塞进 `CPPFLAGS`：`env_android.py` 里
  `c_like_flags += read_envflags("CPPFLAGS")` 且 `c_args = c_like_flags + CFLAGS`，
  所以 android 目标的 C 编译会带上这些 `-I`，正对 `extension.c` 的需要。
- workflow 的 apt 列表补回 `python3-dev`（保证 `pyconfig.h` 存在），
  并把 deps 缓存拆成两条：`frida/deps`（路径 A）与 `frida/subprojects/frida-python/deps`
  （路径 B，这次实跑的实际落点，见日志里的 `.../subprojects/frida-python/deps/toolchain-linux-x86_64/bin/ninja`）。
- `build-android.sh` 的日志改为按尝试分文件（`configure-A.log`/`make-A.log`/`-B`），
  避免后一次覆盖前一次，排错时能同时看到两条路径的真实报错。

注意：这里用**构建机（Linux x86_64）的 pyconfig.h** 配 `-target aarch64-none-linux-android21`。
对 abi3 扩展这只是"结构体尺寸/特性宏"的来源（`SIZEOF_VOID_P=8` 一致、`Py_LIMITED_API` 生效），
不参与运行；真正的风险仍在运行期 dlopen，必须在设备上 `import frida` 验证。

