#!/bin/sh
# Faithful POSIX-shell equivalent of the three c2e233fc regression cases.
# 2.13.9 uses Makefile Catatests, and its real catalog is named docbook.xml.
set -u
xmlcatalog=$1
catalog=$2
status=0
for test in command argument argv; do
    case "$test" in
        command) input=$(awk 'BEGIN { for (i=0;i<470;i++) printf "A" }'); expected='Invalid command';;
        argument) input=$(awk 'BEGIN { printf "public "; for (i=0;i<470;i++) printf "A" }'); expected='Invalid arg';;
        argv) input=$(awk 'BEGIN { printf "public "; for (i=0;i<80;i++) printf " x" }'); expected='Too much arguments';;
    esac
    if output=$(printf '%s\n' "$input" | "$xmlcatalog" --shell "$catalog"); then
        printf '%s\n' "$output"
        case "$output" in
            *"$expected"*) printf 'shell-%s PASS\n' "$test";;
            *) printf 'shell-%s FAIL: expected bounded rejection\n' "$test" >&2; status=1;;
        esac
    else
        printf 'shell-%s FAIL\n' "$test" >&2
        status=1
    fi
done
exit "$status"
