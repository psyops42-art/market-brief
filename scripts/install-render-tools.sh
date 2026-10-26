#!/usr/bin/env bash
# The OG thumbnail uses Pillow, so only Korean fonts are needed.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
font_dir="${repo_root}/assets/fonts"
font_ref="e7db6ca603fd4862010383ff17ffd6cc4ffac1c0"
font_base="https://raw.githubusercontent.com/google/fonts/${font_ref}/ofl/nanumgothic"

download_font() {
  local name="$1" expected="$2" target="${font_dir}/$1" tmp
  if [[ -f "$target" ]] && printf '%s  %s\n' "$expected" "$target" | sha256sum -c --status; then
    return 0
  fi
  tmp="$(mktemp "${target}.XXXXXX")"
  if ! curl --fail --location --silent --show-error --retry 1 --retry-all-errors \
      --connect-timeout 10 --max-time 45 --output "$tmp" "${font_base}/${name}"; then
    rm -f "$tmp"
    return 1
  fi
  if ! printf '%s  %s\n' "$expected" "$tmp" | sha256sum -c --status; then
    rm -f "$tmp"
    echo "::error::Font checksum mismatch: ${name}"
    return 1
  fi
  mv "$tmp" "$target"
}

install_render_tools() {
  if [[ -f /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc &&
        -f /usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc ]]; then
    echo 'System Noto CJK fonts are ready; no download needed.'
    return 0
  fi
  mkdir -p "$font_dir"
  download_font NanumGothic-Regular.ttf \
    76f45ef4a6bcff344c837c95a7dcc26e017e38b5846d5ae0cdcb5b86be2e2d31
  download_font NanumGothic-Bold.ttf \
    f96298f9fb18e364d2370f4c3ce948ac67a2b61af992d7234bc15c42b033c674
  echo 'Korean fonts are ready for Pillow thumbnails.'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  install_render_tools
fi
