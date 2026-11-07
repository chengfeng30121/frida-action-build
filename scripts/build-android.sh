#!/usr/bin/env bash
# 在 x86_64 Linux（GitHub runner）上用 NDK r29 交叉编译 frida 的 android-arm64 python 绑定，
# 产出手搓 abi3 wheel。
#
# 环境变量：
#   FRIDA_VERSION    必填，版本号（如 17.26.1），用于 wheel 版本
#   SRC              必填，上游 frida 源码树（含 configure 的那一层）
#   OUT              产物目录（wheel），默认 ./artifacts
#   LOGDIR           日志目录，默认 $OUT
#   SCRIPTS          本脚本所在目录（用于调用 make_wheel.py）
#   ANDROID_TARGET   默认 android-arm64
set -euo pipefail

FRIDA_VERSION="${FRIDA_VERSION:?need FRIDA_VERSION}"
SRC="${SRC:?need SRC (upstream frida checkout)}"
OUT="${OUT:-$PWD/artifacts}"
LOGDIR="${LOGDIR:-$OUT}"
SCRIPTS="${SCRIPTS:-$(cd "$(dirname "$0")" && pwd)}"
TARGET="${ANDROID_TARGET:-android-arm64}"
: "${ANDROID_NDK_ROOT:?need ANDROID_NDK_ROOT}"
export ANDROID_NDK_ROOT

mkdir -p "$OUT" "$LOGDIR"
cd "$SRC"

dump_logs() {
    for f in "$LOGDIR/configure.log" "$LOGDIR/make.log"; do
        [ -f "$f" ] || continue
        echo "----- tail -80 $f -----"
        tail -80 "$f" || true
    done
}

# 路径 A：monorepo 根目录 configure（Termux 官方包同一配方：--host=android-* + --enable-frida-python）
attempt_root() {
    echo "==> 尝试 A：根目录 configure（--enable-frida-python）"
    rm -rf build
    ./configure --host="$TARGET" --enable-frida-python > "$LOGDIR/configure.log" 2>&1 || return 1
    tail -3 "$LOGDIR/configure.log"
    make -j"$(nproc)" > "$LOGDIR/make.log" 2>&1 || return 1
    echo "make done"
}

# 路径 B：subprojects/frida-python 独立 configure（设备自编译手册里验证过的路径）
attempt_subproject() {
    [ -x subprojects/frida-python/configure ] || return 1
    echo "==> 尝试 B：subprojects/frida-python 独立 configure"
    rm -rf subprojects/frida-python/build
    ( cd subprojects/frida-python && ./configure --host="$TARGET" ) > "$LOGDIR/configure.log" 2>&1 || return 1
    tail -3 "$LOGDIR/configure.log"
    ( cd subprojects/frida-python && make -j"$(nproc)" ) > "$LOGDIR/make.log" 2>&1 || return 1
    echo "make done"
}

if ! attempt_root; then
    echo "!! 路径 A 失败"
    dump_logs
    if ! attempt_subproject; then
        echo "!! 路径 B 也失败，构建终止"
        dump_logs
        exit 1
    fi
fi

echo "==> 定位 _frida.abi3.so"
mapfile -t SOS < <(find "$SRC" -type f -name '_frida.abi3.so' | sort)
if [ "${#SOS[@]}" -eq 0 ]; then
    echo "MISSING _frida.abi3.so，最近的 .so 产物："
    find "$SRC" -type f -name '*.so' | head -20 || true
    dump_logs
    exit 1
fi
SO="${SOS[0]}"
echo "found: $SO ($(stat -c %s "$SO") bytes)"
if [ "${#SOS[@]}" -gt 1 ]; then
    printf '  其余候选（忽略）：%s\n' "${SOS[@]:1}"
fi

echo "==> 手搓 wheel"
python3 "$SCRIPTS/make_wheel.py" "$(dirname "$SO")" "$OUT" "$FRIDA_VERSION"
ls -la "$OUT"
