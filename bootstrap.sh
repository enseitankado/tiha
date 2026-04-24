#!/bin/bash
# TiHA — tek seferlik çalıştırma betiği (kurulum YOK).
#
# KULLANIM: etapadmin hesabıyla açılmış bir terminale bu satırı yapıştırın:
#   curl -fsSL <kısaltılmış-url> | bash
#
# Yaptığı işler:
#   1. Kullanıcının etapadmin olduğunu doğrular
#   2. Eksik sistem paketlerini (python3-gi, python3-pyotp, tar, curl) kurar
#   3. TiHA kaynak kodunu geçici bir klasöre (/tmp/tiha.XXXX) indirip açar
#   4. enseitankado/eta-otp-cli aracını da aynı klasöre indirir
#      (Modül 4 bu araçla toplu PIN anahtarı üretir)
#   5. GTK sihirbazını kök yetkisiyle başlatır
#   6. Sihirbaz kapanınca geçici klasörü siler — sistemde iz bırakmaz

set -euo pipefail

REPO_TARBALL="${TIHA_TARBALL:-https://codeload.github.com/enseitankado/tiha/tar.gz/refs/heads/main}"
ETA_OTP_RAW="${TIHA_ETA_OTP_RAW:-https://raw.githubusercontent.com/enseitankado/etap/main/eta-otp-cli}"
WORKDIR="$(mktemp -d /tmp/tiha.XXXXXX)"

cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

c_info()  { printf '\033[1;34m[TiHA]\033[0m %s\n' "$*"; }
c_ok()    { printf '\033[1;32m[TiHA]\033[0m %s\n' "$*"; }
c_warn()  { printf '\033[1;33m[UYARI]\033[0m %s\n' "$*"; }
c_err()   { printf '\033[1;31m[HATA]\033[0m %s\n' "$*" >&2; }

# --- 1) Kullanıcı denetimi -------------------------------------------------
if [[ "$(id -un)" != "etapadmin" ]]; then
    c_err "TiHA yalnızca 'etapadmin' kullanıcısıyla çalıştırılmalıdır."
    c_err "Geçerli kullanıcı: $(id -un)"
    exit 2
fi

# --- 2) sudo oturumu ön-doğrulama -----------------------------------------
# curl|bash akışında dahi sudo, parola isteğini /dev/tty üzerinden alır.
c_info "Yönetici yetkisi doğrulanıyor. İstenirse etapadmin parolasını girin."
if ! sudo -v; then
    c_err "Yetki doğrulanamadı; çıkılıyor."
    exit 3
fi

# --- 3) Gerekli paketler ---------------------------------------------------
c_info "Gerekli sistem paketleri kontrol ediliyor..."
# python3-qrcode eta-otp-cli için gerekli (QR kod üretimi)
REQUIRED=(python3 python3-gi gir1.2-gtk-3.0 python3-pyotp python3-qrcode tar curl policykit-1)
MISSING=()
for pkg in "${REQUIRED[@]}"; do
    if ! dpkg -l "$pkg" 2>/dev/null | awk 'NR>5{print $1}' | grep -q '^ii'; then
        MISSING+=("$pkg")
    fi
done
if (( ${#MISSING[@]} > 0 )); then
    c_info "Eksik paketler kuruluyor: ${MISSING[*]}"
    sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}"
else
    c_ok "Tüm paketler hazır."
fi

# --- 4) TiHA kaynak kodu indir --------------------------------------------
c_info "TiHA kaynak kodu indiriliyor..."
if ! curl -fsSL "$REPO_TARBALL" | tar -xz -C "$WORKDIR" --strip-components=1; then
    c_err "Kaynak kodu indirilemedi. İnternet bağlantınızı kontrol edin."
    exit 4
fi

# Yerelden çalıştırma (geliştirme) için: eğer TIHA_LOCAL_DIR tanımlıysa onu kullan
if [[ -n "${TIHA_LOCAL_DIR:-}" && -d "$TIHA_LOCAL_DIR/tiha" ]]; then
    c_info "TIHA_LOCAL_DIR kullanılıyor: $TIHA_LOCAL_DIR"
    WORKDIR="$TIHA_LOCAL_DIR"
    trap - EXIT   # yerel dizini silme
fi

# --- 5) eta-otp-cli aracını indir -----------------------------------------
# TiHA Modül 4 (PIN anahtarları) bu aracı kullanır. İndirme başarısız olursa
# TiHA dahili pyotp tabanlı yedek yolu kullanır — fatal değildir.
ETA_OTP_CLI_DIR="$WORKDIR/eta-otp-cli"
mkdir -p "$ETA_OTP_CLI_DIR"
c_info "enseitankado/eta-otp-cli aracı indiriliyor..."
ETA_OK=true
for f in otp-cli.py toplu-kullanici-olustur.py README.md; do
    if ! curl -fsSL "$ETA_OTP_RAW/$f" -o "$ETA_OTP_CLI_DIR/$f"; then
        c_warn "eta-otp-cli/$f indirilemedi."
        rm -f "$ETA_OTP_CLI_DIR/$f"
        ETA_OK=false
    fi
done
if $ETA_OK; then
    chmod +x "$ETA_OTP_CLI_DIR"/*.py 2>/dev/null || true
    c_ok "eta-otp-cli hazır: $ETA_OTP_CLI_DIR"
    export TIHA_ETA_OTP_CLI_DIR="$ETA_OTP_CLI_DIR"
else
    c_warn "eta-otp-cli kullanılamıyor; TiHA dahili PIN üretim yolunu kullanacak."
fi

# --- 6) Uygulamayı başlat --------------------------------------------------
c_info "Sihirbaz başlatılıyor..."
# DISPLAY/XAUTHORITY gibi değişkenleri sudo içinde de erişilebilir tut.
exec sudo -E \
    PYTHONPATH="$WORKDIR" \
    TIHA_HOME="$WORKDIR" \
    TIHA_ETA_OTP_CLI_DIR="${TIHA_ETA_OTP_CLI_DIR:-}" \
    python3 -m tiha "$@"
