# Termux (aarch64) 自编译 frida 17.18.0 — 完整可复现手册

> 2026-09 实测成功。环境：XDF-N1 (MT8788, Android 10, API 29)，Termux 原生 aarch64，
> Python 3.14.6，clang 21，rust 1.98，ninja 1.13，meson 1.12(pip)。
> 产物：`frida-17.18.0-cp37-abi3-android_24_arm64_v8a.whl` (19.2MB) + `frida_tools-14.10.4-py3-none-any.whl`。

---

## 0. 为什么不能直接 pip install

- 官方 PyPI 没有 `android_arm64` 的 frida wheel，Termux 仓库内的版本过旧。
- 沙箱交叉编译不可行：官方 NDK r29 只有 **x86_64 宿主**版，aarch64 沙箱跑不了。
- 结论：**在 Termux 本机原生编译**（build_machine == host_machine == android）。

## 0.1 总体流程

```
获取源码 → 自举 Vala(特供版) → 打 releng 补丁 → 打源码补丁 → 预置 SDK bundle
→ configure → ninja(可分段续编) → 手搓 wheel → pip 安装验证 → 修 BTI_PLT linker 警告
```

---

## 1. 前置条件

### 1.1 Termux 安装依赖

```bash
pkg install clang rust cmake ninja git python python-pip libc++ \
  glib libglibutil pkg-config openssl  # 核心；缺什么按 configure 报错补
pip install meson==1.12
```

### 1.2 获取源码（frida-python 仓库 = 顶层构建项目）

```bash
# 方式A：无网络依赖（推荐，SDK 单独下载，见 §6）
# 方式B：直连 GitHub
git clone --depth 1 --branch 17.18.0 https://github.com/frida/frida-python.git
cd frida-python
# 子项目由 meson wrap 在 configure 时拉取；也可预置到 subprojects/
```

源码树顶层即 frida-python，`subprojects/` 下是 frida-core / frida-gum / frida-bridge 等 wrap。

### 1.3 网络与代理（Termux 直连 build.frida.re 被墙）

```bash
export https_proxy=http://127.0.0.1:7890 http_proxy=http://127.0.0.1:7890
# 7890 是设备上沙箱侧常驻代理端口，Termux 同机可直接用
```

---

## 2. 自举 Vala 0.58.6-frida（最关键的重难点）

### 2.1 为什么自举

frida 17.18.0 要求特供版 vala：`https://github.com/frida/vala.git` **commit `172348fa9123ff4a95d541c5f9e56837434c4b6e`**（releng/deps.toml `[vala] version`）。官方只发 linux-x86_64 预编译 → aarch64 必须自己编。

### 2.2 在沙箱（proot Ubuntu aarch64, glibc 2.39）编译 valac

```bash
# 沙箱内
git clone https://github.com/frida/vala.git && cd vala
git checkout 172348fa9123ff4a95d541c5f9e56837434c4b6e
apt install libglib2.0-dev flex bison autoconf automake libtool autoconf-archive gtk-doc-tools
./autogen.sh --prefix=$HOME/vala-install
make -j8 && make install
# 产物：$HOME/vala-install/bin/{valac,genie}  + share/vala-0.58/{vapi,gir}
```

### 2.3 gclib 打包（让 glibc 程序跑在 bionic 上）

Termux 是 bionic libc，valac 是 glibc 2.39 编译 → 需要**自带 glibc 运行时**。

```bash
# 沙箱内：把 Ubuntu 的 glibc 动态库收集到 gclib/
GCLIB=/workspace/termux-wheels/gclib   # 之后整个目录 scp 到 Termux
mkdir -p $GCLIB/vala-0.58
# 收集（驻目录为 aarch64 的 /lib/aarch64-linux-gnu/）：
#   ld-linux-aarch64.so.1  libc.so.6  libm.so.6  libpthread.so.0  libdl.so.2
#   librt.so.1  libgcc_s.so.1  libstdc++.so.6
#   libglib-2.0.so.0 libgobject-2.0.so.0 libgio-2.0.so.0
#   libffi.so.8 libpcre2-8.so.0 …（valac 的 ldd 全链路）
cp -r $HOME/vala-install/share/vala-*/vapi $HOME/vala-install/share/vala-*/gir $GCLIB/vala-0.58/
cp $HOME/vala-install/bin/valac $GCLIB/valac
# 打包 scp 到 Termux：
tar czf vala-gclib.tar.gz -C /workspace/termux-wheels gclib
# → scp → Termux: tar xzf vala-gclib.tar.gz -C $HOME
```

### 2.4 Termux wrapper（关键：unset LD_PRELOAD）

Termux 的 bash 环境会把 `LD_PRELOAD=libtermux-exec.so`(bionic) 塞给子进程 → glibc 程序的加载器崩溃。wrapper 必须清掉：

```bash
cat > /data/data/com.termux/files/usr/bin/valac <<'EOF'
#!/data/data/com.termux/files/usr/bin/env bash
unset LD_PRELOAD LD_LIBRARY_PATH
GCLIB=$HOME/gclib
exec "$GCLIB/ld-linux-aarch64.so.1" --library-path "$GCLIB" \
  "$GCLIB/valac" "$@" \
  --vapidir="$GCLIB/vala-0.58/vapi" --girdir="$GCLIB/vala-0.58/gir"
EOF
chmod +x /data/data/com.termux/files/usr/bin/valac
valac --version   # 期望: vala 0.58.6
```

> 记得同样修好 `ninja` / `gdbus-codegen` / `glib-mkenums` / `glib-genmarshal`：
> 这些 Termux 系统脚本若 shebang 用 `/usr/bin/env` 会在 zsh 污染环境里挂掉，批量改成
> `#!/data/data/com.termux/files/usr/bin/env python3`（或 bash）。

---

## 3. releng 补丁（让构建走 Termux 本机工具链）

文件：`releng/env_android.py` 与 `releng/deps.py`（源码树顶层）。diff 见下，逐条应用。

### 3.1 env_android.py —— native_android 分支

```diff
@@ init_machine_config() 开头 @@
+    native_android = machine.os == "android"
+
-    try:
-        ndk_root = Path(environ["ANDROID_NDK_ROOT"])
-        if ndk_root.is_absolute():
-            ndk_props_file = ndk_root / "source.properties"
-            ndk_found = ndk_props_file.exists()
-    except:
-        pass
-    if not ndk_found:
-        raise NdkNotFoundError(f"ANDROID_NDK_ROOT must be set to the location of your r{NDK_REQUIRED} NDK")
+    if not native_android:
+        try:
+            ndk_root = Path(environ["ANDROID_NDK_ROOT"])
+            if ndk_root.is_absolute():
+                ndk_props_file = ndk_root / "source.properties"
+                ndk_found = ndk_props_file.exists()
+        except:
+            pass
+        if not ndk_found:
+            raise NdkNotFoundError(f"ANDROID_NDK_ROOT must be set to the location of your r{NDK_REQUIRED} NDK")

-    if sdk_prefix is not None:
+    if sdk_prefix is not None and not native_android:
         …(NDK 版本号检查原样保留)…

-    android_api = 21
+    android_api = 24 if native_android else 21

-    llvm_bindir = ndk_root / "toolchains" / "llvm" / "prebuilt" / f"{android_build_os}-{android_build_arch}" / "bin"
-    libcxx_include_dir = llvm_bindir.parent / "sysroot" / "usr" / "include" / "c++" / "v1"
+    if native_android:
+        import shutil
+        llvm_bindir = Path(shutil.which("clang")).parent
+        def resolve_tool(tool_name):
+            path = shutil.which(tool_name)
+            if path is None:
+                raise FileNotFoundError(f"Tool not found in PATH: {tool_name}")
+            return Path(path)
+    else:
+        llvm_bindir = ndk_root / "toolchains" / "llvm" / "prebuilt" / f"{android_build_os}-{android_build_arch}" / "bin"
+        def resolve_tool(tool_name):
+            return llvm_bindir / f"{tool_name}{build_machine.executable_suffix}"

+    if native_android:
+        libcxx_include_dir = llvm_bindir.parent / "include" / "c++" / "v1"
+    else:
+        libcxx_include_dir = llvm_bindir.parent / "sysroot" / "usr" / "include" / "c++" / "v1"

@@ NDK_BINARIES 循环里 @@
-        path = llvm_bindir / f"{tool_name}{build_machine.executable_suffix}"
+        path = resolve_tool(tool_name)
+        …(argv 组成逻辑原样)…

@@ common_flags 里加 -D_GNU_SOURCE @@
+        "-D_GNU_SOURCE",

@@ cxx_link_flags @@
-    cxx_link_flags = [
-        "-static-libstdc++",
-    ]
+    if native_android:
+        cxx_link_flags = [
+            f"-L{llvm_bindir.parent}",
+        ]
+    else:
+        cxx_link_flags = [
+            "-static-libstdc++",
+        ]
```

要点（为什么）：
- `android_api = 24`：getifaddrs 等 API 需 ≥24；Termux libc 本身是 API 24。
- `-L{llvm_bindir.parent}` = `-L$PREFIX/lib`：Termux 的 clang++ 默认链 `libc++_shared.so`，
  链接器需要 `-L$PREFIX` 才能找到它（官方 `-static-libstdc++` 在 Termux 无效）。
- `-D_GNU_SOURCE`：Termux bionic 头对部分声明（asprintf 等）需要该宏。

### 3.2 deps.py —— SDK/Toolchain 404 跳过

```diff
@@ sync() 下载 404 分支 @@
         if e.code == 404:
+            if bundle in (Bundle.SDK, Bundle.TOOLCHAIN):
+                print(f"WARNING: SDK bundle 404 ({url}), skipping (using system libs)")
+                return state
```

背景：`sdk-android-arm64` 真实存在（§6 预置更稳），但 `toolchain-android-arm64`
官方只发 x86_64 宿主 → 必然 404，必须跳过而不是报错。

---

## 4. 源码补丁（Termux/bionic 适配）

### 4.1 libc-shim.c（frida-core/lib/payload/libc-shim.c）

bionic 的 `FILE` 是 **opaque**（`struct __sFILE` 不完整类型），必须走 opaque 分支；且
bionic 声明 `extern FILE* stdin`（非 const），frida 原版 opaque 分支定义了 `FILE * const` → 类型冲突。

```diff
@@ 函数裁剪宏列表（~line 58）@@
 #undef vsnprintf
+#undef tmpfile      ← 新增：Termux stdio.h 的 tmpfile 是 inline 实现，必须先 undef

@@ opaque 判定 @@
-#ifdef HAVE_MUSL
+#if defined (HAVE_MUSL) || defined (HAVE_ANDROID)
 # define FRIDA_STDIO_OPAQUE_FILE 1
 #endif

@@ opaque 分支（~line 270）stdin/stdout/stderr 定义去 const @@
-static FridaFileHandle frida_stdio[3];
-G_GNUC_INTERNAL FILE * const stdin  = (FILE *) &frida_stdio[0];
-G_GNUC_INTERNAL FILE * const stdout = (FILE *) &frida_stdio[1];
-G_GNUC_INTERNAL FILE * const stderr = (FILE *) &frida_stdio[2];
+static FridaFileHandle frida_stdio[3];
+G_GNUC_INTERNAL FILE * stdin  = (FILE *) &frida_stdio[0];
+G_GNUC_INTERNAL FILE * stdout = (FILE *) &frida_stdio[1];
+G_GNUC_INTERNAL FILE * stderr = (FILE *) &frida_stdio[2];
```

### 4.2 Termux 系统 stdio.h（全局的一次性改动）

Termux fork 的 stdio.h 把 `tmpfile` 实现为 `static __inline__`（与 frida 的定义冲突），
系统级解决：把该 inline 替换为普通 extern 声明。

```bash
# 备份后编辑 /data/data/com.termux/files/usr/include/stdio.h
# 找到类似：
#   static __inline__ FILE* tmpfile(void) { ... }
# 整段替换为：
#   extern FILE* tmpfile(void);
# 恢复：mv stdio.h.bak stdio.h （Termux 升级 libc 包也会还原）
```

### 4.3 libnice interfaces.c（frida-core/subprojects/libnice，wrap 下载后）

```diff
@@ libnice/agent/interfaces.c 头部 include 区 @@
+#include <ifaddrs.h>
```

libnice 由 `subprojects/libnice.wrap` 指向 `https://github.com/frida/libnice.git`
rev `9a3da6e3e5bbcf935fd85b6b8557ff8c7dd9032c`。首次构建时会 clone 到 build 目录，
直接改 build 内的源码即可（下次 ninja 自动重编该目标）。

---

## 5. SDK bundle 预置（替代被墙的运行时下载）

虽然 releng 会在 configure/sync 时自己下载，**预置可彻底避开网络波动**：

```bash
# 任意能上网的机器（或走代理）下载：
curl -x http://localhost:7890 -L -o sdk-android-arm64.tar.xz \
  https://build.frida.re/deps/20260717/sdk-android-arm64.tar.xz   # 34.5MB

# Termux 解压到 frida-core 的 deps 目录：
mkdir -p ~/frida-17.18.0-src/subprojects/frida-python/subprojects/frida-core/deps
tar xJf sdk-android-arm64.tar.xz -C \
  ~/frida-17.18.0-src/subprojects/frida-python/subprojects/frida-core/deps
# 解压后目录名必须为 sdk-android-arm64，内含 VERSION.txt（内容 20260717）
# releng 检测到 VERSION.txt 匹配 deps.toml 的版本号 → sync 跳过下载
```

> 工具链 `toolchain-android-arm64` 官方只有 x86_64 宿主 → 404，靠 §3.2 补丁跳过。
> SDK 里是架构无关的第三方库（glib/gio/capstone/quickjs 等的头+静态库 .a）。

---

## 6. configure

```bash
cd ~/frida-17.18.0-src/subprojects/frida-python   # 顶层项目
sh -c 'unset LD_PRELOAD LD_LIBRARY_PATH; ./configure \
  --without-prebuilds=toolchain \
  -- -Dfrida-core:compat=disabled' > ~/configure.log 2>&1
echo $?   # 期望 0
```

- `--without-prebuilds=toolchain`：不构建 arm 交叉用的工具链（那才是 404 的）。
- `-Dfrida-core:compat=disabled`：跳过 frida-core compat 的跨架构部分。
- `unset LD_PRELOAD`：防 bionic 的 libtermux-exec 污染 glibc 程序（valac 等）。
- 全部命令用 `sh -c '…'` 包裹：Termux 默认 zsh 环境被污染（`/usr/bin/env` 缺失等）。
- 成功标志：日志末尾 `Found ninja-1.13.x at …` 且 RC=0；sanity check 含 C++/Vala 全过。

---

## 7. ninja 构建（可分段续编）

```bash
cd ~/frida-17.18.0-src/subprojects/frida-python/build
sh -c 'unset LD_PRELOAD LD_LIBRARY_PATH; ninja -j4' > ~/ninja.log 2>&1
# 长时间任务：外层套 timeout 550 分片跑；断了就再跑一次（ninja 增量续编）
```

- 完成标志（build/ 内）：
  - `build/frida/_frida.abi3.so`（Python 扩展，55MB）
  - `build/subprojects/frida-core/compat/frida-agent-arm64.so` 及
    `build/subprojects/frida-core/src/frida-data-agent-blob.S.p/frida-agent-*.so`
- 注意：**不用 `meson compile`**（在本环境报 hostname 错误），直接跑 ninja。
- 磁盘：全套产物约 2.5GB，**构建完立刻 `rm -rf build/`**（设备只剩 3-4G 时尤其重要）。

---

## 8. 手搓 wheel（绕过 pip wheel 的磁盘/元数据坑）

`pip wheel .` 会重新执行 setup 的复制流程（需要再建一份 build 副本），设备磁盘紧张时
**直接手工构造 wheel**，完全等价：

```bash
WHL=~/wheelbuild
mkdir -p $WHL/frida $WHL/frida-17.18.0.dist-info
B=~/frida-17.18.0-src/subprojects/frida-python/build/frida
cp $B/__init__.py $B/aio.py $B/_frida.pyi $B/_frida.abi3.so $WHL/frida/
cp /usr/lib/python3.14/site-packages/frida/py.typed $WHL/frida/ 2>/dev/null || true
```

### 8.1 dist-info 三个文件

```bash
cat > $WHL/frida-17.18.0.dist-info/METADATA <<'MD'
Metadata-Version: 2.1
Name: frida
Version: 17.18.0
Summary: Dynamic instrumentation toolkit for developers, reverse-engineers, and security researchers
Home-page: https://frida.re
Author: Ole Andre Vadla Ravnaas
Author-email: oleavr@nowsecure.com
License: wxWindows Library Licence
Requires-Python: >=3.7
Classifier: Programming Language :: Python :: 3
Classifier: Operating System :: Android
MD
cat > $WHL/frida-17.18.0.dist-info/WHEEL <<'MD'
Wheel-Version: 1.0
Generator: bdist_wheel (0.42.0)
Root-Is-Purelib: false
Tag: cp37-abi3-android_24_arm64_v8a
MD
echo 'frida' > $WHL/frida-17.18.0.dist-info/top_level.txt
```

RECORD 用 Python 生成（sha256 + 大小），zip 打包：

```python
# python3 内执行
import os, hashlib, base64, zipfile
WHL='/path/to/wheelbuild'
dinfo=WHL+'/frida-17.18.0.dist-info'
rec=[]
for root,_,files in os.walk(WHL):
    for fn in files:
        p=os.path.join(root,fn); rel=os.path.relpath(p,WHL)
        if os.path.basename(WHL)!=rel:
            h=hashlib.sha256(open(p,'rb').read()).digest()
            rec.append(f'{rel},sha256={base64.urlsafe_b64encode(h).rstrip(b"=").decode()},{os.path.getsize(p)}')
rec.append('frida-17.18.0.dist-info/RECORD,,')
open(dinfo+'/RECORD','w').write('\n'.join(rec)+'\n')
whl=WHL+'/frida-17.18.0-cp37-abi3-android_24_arm64_v8a.whl'
with zipfile.ZipFile(whl,'w',zipfile.ZIP_DEFLATED) as z:
    for root,_,files in os.walk(WHL):
        for fn in files:
            p=os.path.join(root,fn); arc=os.path.relpath(p,WHL)
            if arc!=os.path.basename(whl): z.write(p,arc)
```

> Tag 含义：`cp37-abi3` = 3.7+ ABI 兼容（Python 3.14 可装）；`android_24_arm64_v8a` = 平台标签。

---

## 9. 安装与验证

```bash
pip install --force-reinstall --no-deps \
  ~/wheelbuild/frida-17.18.0-cp37-abi3-android_24_arm64_v8a.whl
python3 -c 'import frida; print(frida.__version__)'        # 17.18.0
frida-ps --version                                          # 17.18.0
# 进程枚举需要 root（写 /data/local/tmp + ptrace）：
su -c 'PATH=/data/data/com.termux/files/usr/bin:$PATH frida-ps'
```

frida-tools 14.10.4 是纯 Python wheel，直接 `pip install frida_tools-14.10.4-py3-none-any.whl`（无编译）。

---

## 10. Android 10 linker 警告修复（DT_AARCH64_BTI_PLT 刷屏）

### 10.1 现象

```text
WARNING: linker: Warning: "/data/data/com.termux/files/usr/lib/libc++_shared.so"
  unused DT entry: unknown processor-specific (type 0x70000001 arg 0x0) (ignoring)
```

### 10.2 根因

Termux(LLVM 21) 编的 `libc++_shared.so` 带动态条目 `DT_AARCH64_BTI_PLT`(0x70000001)，
Android 10(API 29) linker 不识别 → 每次加载该库的进程都警告。**功能无害，纯噪音。**

### 10.3 修复（删除该动态条目，段内平移，文件大小不变）

现成脚本：`/workspace/termux-wheels/fix-bti-0x70000001.sh`（或 Termux `~/fix-bti.sh`），一键：

```bash
bash ~/fix-bti.sh
# 内部逻辑：备份 .bak → 解析 PT_DYNAMIC → 移除 d_tag==0x70000001 的 16 字节条目
#           （后续条目前移、末尾补 DT_NULL）→ 写回
```

验证：

```bash
llvm-readelf --dynamic /data/data/com.termux/files/usr/lib/libc++_shared.so | grep -c AARCH64_BTI_PLT  # 0
python3 -c 'import ctypes; ctypes.CDLL("/data/data/com.termux/files/usr/lib/libc++_shared.so")'           # OK
```

### 10.4 注意事项

- 移除安全：MT8788 CPU 不支持 BTI，PLT 中的 bti 指令本来就被当 NOP。
- **Termux 升级 libc++ 包后警告会回来** → 重跑一次 `~/fix-bti.sh`。
- 恢复：把 `.bak` 改名回去。

---

## 11. 环境坑速查（FAQ）

| 现象 | 原因 | 解法 |
|---|---|---|
| `No space left on device` | 设备 /data 只剩 3-4G | 随时 `rm -rf build`；别在 Termux 解压大文件 |
| zsh 报 `/usr/bin/env` 缺失 / 环境被污染 | Termux zsh 配置问题 | 全部用 `sh -c '…'` 包裹；脚本 shebang 改 `#!/data/data/com.termux/files/usr/bin/env` |
| valac/glibc 程序崩溃 | `LD_PRELOAD=libtermux-exec.so`(bionic) 冲突 | wrapper 内 `unset LD_PRELOAD LD_LIBRARY_PATH` |
| Termux 无 `/tmp` | Termux 设计 | 日志/临时文件用 `$HOME` |
| `meson compile` 报 hostname 错误 | meson 1.12 在本环境怪异 | 直接 `ninja` |
| `Ninja no work / SDK 下载被重置` | 直连 build.frida.re 被墙 | 预置 SDK(§5)；或 `https_proxy=http://127.0.0.1:7890` |
| `frida-ps` 非 root 报 /data/local/tmp 权限 | frida 部署 helper 需 root | `su -c 'PATH=$PREFIX/bin:$PATH frida-ps'` |
| pip wheel 元数据失败 / 复制 No space | setup 重建 build 副本 | 手搓 wheel(§8) |
| 沙箱 curl 报 CA 错误 | 沙箱证书损坏 | `curl -k` |

---

## 12. 产物与存档清单

| 文件 | 位置 |
|---|---|
| frida wheel | `/workspace/termux-wheels/frida-17.18.0-cp37-abi3-android_24_arm64_v8a.whl` (19.2MB) |
| frida-tools wheel | `/workspace/termux-wheels/frida_tools-14.10.4-py3-none-any.whl` (4.7MB) |
| BTI 修复脚本 | `/workspace/termux-wheels/fix-bti-0x70000001.sh`（Termux: `~/fix-bti.sh`） |
| 补丁后的 releng 文件 | `/workspace/termux-wheels/patched/{env_android.py,deps.py}` |
| 沙箱编译产物 | `/workspace/termux-wheels/gclib/`（glibc 运行时 + valac） |
| 有补丁的源码树 | `/workspace/termux-wheels/frida-17.18.0-src/`（含全部 patch） |

## 13. 版本/时间戳备忘

- frida 17.18.0（2026-09 构建），Python abi3 → 3.7–3.14 通吃
- vala fork commit `172348fa9123ff4a95d541c5f9e56837434c4b6e`（构建期版本串 vala 0.58.6-frida）
- libnice rev `9a3da6e3e5bbcf935fd85b6b8557ff8c7dd9032c`
- SDK deps 版本 `20260717`（build.frida.re/deps/20260717/sdk-android-arm64.tar.xz）
