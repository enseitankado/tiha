#!/bin/bash
# TiHA kurulum betiği.
# Kullanım (etapadmin terminalinden):
#   curl -fsSL https://raw.githubusercontent.com/ozgurkoca/tiha/main/install.sh | sudo bash
#
# Yaptığı işler:
#  - Gerekli sistem paketlerini kurar (python3-gi, python3-pyotp, git, pkexec).
#  - Depoyu /opt/tiha altına kopyalar.
#  - /usr/local/bin/tiha sembolik bağlantısını kurar.
#  - Masaüstü girişini ve polkit politikasını yerleştirir.

set -euo pipefail

REPO_URL="${TIHA_REPO_URL:-https://github.com/ozgurkoca/tiha.git}"
BRANCH="${TIHA_BRANCH:-main}"
DEST="/opt/tiha"

log() { echo -e "\033[1;34m[TiHA]\033[0m $*"; }
err() { echo -e "\033[1;31m[TiHA HATA]\033[0m $*" >&2; }

require_root() {
    if [[ "$EUID" -ne 0 ]]; then
        err "Kurulum yönetici (kök) yetkisi gerektirir. 'sudo bash' ile çalıştırın."
        exit 1
    fi
}

install_packages() {
    log "Sistem paketleri kuruluyor…"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq \
        git \
        python3 \
        python3-gi \
        gir1.2-gtk-3.0 \
        python3-pyotp \
        policykit-1
}

clone_repo() {
    if [[ -d "$DEST/.git" ]]; then
        log "Depo zaten mevcut, güncelleniyor…"
        git -C "$DEST" fetch --depth 1 origin "$BRANCH"
        git -C "$DEST" checkout -q "$BRANCH"
        git -C "$DEST" reset --hard "origin/$BRANCH"
    else
        log "Depo indiriliyor → $DEST"
        rm -rf "$DEST"
        git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$DEST"
    fi
}

install_from_local() {
    # Yerel çalışmada (depoyu zaten kopyalamışsak) kullanılır.
    local src
    src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ "$src" != "$DEST" ]]; then
        log "Yerel kopya → $DEST'e kopyalanıyor"
        mkdir -p "$DEST"
        rsync -a --delete --exclude='.git/' --exclude='__pycache__/' "$src/" "$DEST/"
    fi
}

setup_launcher() {
    log "Fırlatıcı ve masaüstü girişi ayarlanıyor…"
    chmod +x "$DEST/bin/tiha"
    ln -sf "$DEST/bin/tiha" /usr/local/bin/tiha

    install -Dm644 "$DEST/data/tiha.desktop" /usr/share/applications/tiha.desktop
    if [[ -f "$DEST/data/tr.org.pardus.tiha.policy" ]]; then
        install -Dm644 "$DEST/data/tr.org.pardus.tiha.policy" \
            /usr/share/polkit-1/actions/tr.org.pardus.tiha.policy
    fi
}

main() {
    require_root
    install_packages

    # Yerel repo içinden çalıştırılıyorsa (install.sh depoyla beraber geldiyse)
    # git clone'a gitmeyiz.
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "$script_dir/pyproject.toml" && -d "$script_dir/tiha" ]]; then
        install_from_local
    else
        clone_repo
    fi

    setup_launcher
    log "Kurulum tamamlandı."
    log "Çalıştırmak için: tiha  (ya da menüden TiHA'yı bulun)."
}

main "$@"
