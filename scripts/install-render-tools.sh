#!/usr/bin/env bash
# Run on the disposable Ubuntu Actions runner; do not change its APT sources.
set -euo pipefail

render_tools_ready() {
  command -v wkhtmltoimage >/dev/null &&
    [[ -f /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc ]] &&
    [[ -f /usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc ]]
}

install_render_tools() {
  local -a apt_options=(
    -o Acquire::Retries=1
    -o Acquire::http::Timeout=20
    -o Acquire::https::Timeout=20
    -o DPkg::Lock::Timeout=30
    -o Dpkg::Use-Pty=0
  )
  if render_tools_ready; then
    echo 'Rendering tools already installed; skipping APT.'
    return 0
  fi

  echo 'Installing wkhtmltopdf and regular/bold CJK fonts using cached package lists (120s limit).'
  if ! timeout --kill-after=10s 120s sudo env DEBIAN_FRONTEND=noninteractive \
      apt-get "${apt_options[@]}" install -y --no-install-recommends wkhtmltopdf fonts-noto-cjk; then
    echo '::warning::Initial install failed; refreshing package lists once (90s limit).'
    # Runner images include unrelated third-party package repositories. Only
    # refresh Ubuntu sources when the standard source file is available.
    local -a source_options=()
    if [[ -f /etc/apt/sources.list.d/ubuntu.sources ]]; then
      source_options=(-o Dir::Etc::sourcelist=/etc/apt/sources.list.d/ubuntu.sources -o Dir::Etc::sourceparts=-)
    elif [[ -f /etc/apt/sources.list ]]; then
      source_options=(-o Dir::Etc::sourcelist=/etc/apt/sources.list -o Dir::Etc::sourceparts=-)
    fi
    if ! timeout --kill-after=10s 90s sudo env DEBIAN_FRONTEND=noninteractive \
        apt-get "${apt_options[@]}" "${source_options[@]}" update; then
      echo '::error::Package-list refresh failed or timed out. See repository/network errors above.'
      return 1
    fi
    echo 'Retrying rendering package installation once (120s limit).'
    if ! timeout --kill-after=10s 120s sudo env DEBIAN_FRONTEND=noninteractive \
        apt-get "${apt_options[@]}" install -y --no-install-recommends wkhtmltopdf fonts-noto-cjk; then
      echo '::error::Rendering package installation failed or timed out.'
      return 1
    fi
  fi
  if ! render_tools_ready; then
    echo '::error::wkhtmltoimage or the required regular/bold CJK font files are missing.'
    return 1
  fi
  echo 'Rendering tools and Korean fonts are ready.'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  install_render_tools
fi
