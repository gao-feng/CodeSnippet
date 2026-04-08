#!/usr/bin/env bash
set -euo pipefail

COUNT="${1:-1000}"
MODE="${2:-dlmopen}"

gcc -shared -fPIC -O2 -Wall -Wextra -o libempty.so empty.c
gcc -O2 -Wall -Wextra -o measure_dlopen measure_dlopen.c -ldl

echo "Built ./libempty.so and ./measure_dlopen"
echo "Running: ./measure_dlopen ./libempty.so ${COUNT} ${MODE}"
./measure_dlopen ./libempty.so "${COUNT}" "${MODE}"
