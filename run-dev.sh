#!/bin/bash
# TiHA — yerel geliştirme çalıştırıcısı.
# bootstrap.sh'in aksine GitHub'dan indirmez; bulunduğunuz çalışma kopyasını
# kök yetkisiyle başlatır. Kaynak kodda yaptığınız değişiklikler bir sonraki
# çalıştırmada anında etkili olur.
#
# KULLANIM:
#   ./run-dev.sh              # tiha'yı yerel kaynaktan çalıştırır
#   ./run-dev.sh --foo bar    # ek argümanlar tiha'ya iletilir
#
# Eta-OTP-CLI aracını kullanmak isterseniz ortam değişkeniyle gösterin:
#   TIHA_ETA_OTP_CLI_DIR=/yol/eta-otp-cli ./run-dev.sh

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"

if [[ ! -d "$SCRIPT_DIR/tiha" ]]; then
    printf '\033[1;31m[HATA]\033[0m %s\n' "tiha paketi bulunamadı: $SCRIPT_DIR/tiha" >&2
    exit 1
fi

printf '\033[1;34m[TiHA-dev]\033[0m %s\n' "Yerel kaynaktan başlatılıyor: $SCRIPT_DIR"

exec sudo -E \
    PYTHONPATH="$SCRIPT_DIR" \
    TIHA_HOME="$SCRIPT_DIR" \
    TIHA_ETA_OTP_CLI_DIR="${TIHA_ETA_OTP_CLI_DIR:-}" \
    python3 -m tiha "$@"
