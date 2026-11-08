#!/bin/bash
# TiHA — tek seferlik çalıştırma betiği (kurulum YOK).
#
# KULLANIM: etapadmin hesabıyla açılmış bir terminale bu satırı yapıştırın:
#   curl -fsSL <kısaltılmış-url> | bash
#
# Yönetim sunucusundan (SSH, root) komut satırı seçenekleriyle:
#   ssh root@tahta 'curl -fsSL <url> | bash -s -- --json -l'
# root'ta sudo ve parola sorulmaz; sihirbaz değil yalnız komut satırı
# kullanılabilir. TIHA_LOCAL_DIR verilirse TiHA indirilmez, o klasördeki
# kopya çalıştırılır (ör. yönetim sunucusunun SFTP ile koyduğu sürüm).
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
ETA_OTP_RAW="${TIHA_ETA_OTP_RAW:-https://raw.githubusercontent.com/enseitankado/eta-otp-cli/main}"
ETA_112_RAW="${TIHA_ETA_112_RAW:-https://raw.githubusercontent.com/enseitankado/eta-112/main}"
WORKDIR="$(mktemp -d /tmp/tiha.XXXXXX)"

cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

# Durum satırları stderr'e gider: stdout TiHA'nın çıktısına (ör. --json
# olaylarına) ayrılmıştır. Renk yalnız terminalde.
if [[ -t 2 ]]; then B=$'\033[1;34m' G=$'\033[1;32m' Y=$'\033[1;33m' R=$'\033[1;31m' N=$'\033[0m'
else B="" G="" Y="" R="" N=""; fi
c_info()  { printf '%s[TiHA]%s %s\n' "$B" "$N" "$*" >&2; }
c_ok()    { printf '%s[TiHA]%s %s\n' "$G" "$N" "$*" >&2; }
c_warn()  { printf '%s[UYARI]%s %s\n' "$Y" "$N" "$*" >&2; }
c_err()   { printf '%s[HATA]%s %s\n' "$R" "$N" "$*" >&2; }

# --- 1) Kullanıcı denetimi -------------------------------------------------
# root (ör. yönetim sunucusunun SSH oturumu): sudo gerekmez. Sihirbaz
# yalnız etapadmin'den açılır; root'ta seçeneksiz çalıştırılırsa TiHA
# kendisi reddeder.
if [[ "$(id -u)" == 0 ]]; then
    SUDO=()
elif [[ "$(id -un)" != "etapadmin" ]]; then
    c_err "TiHA yalnızca 'etapadmin' kullanıcısıyla (ya da root olarak) çalıştırılmalıdır."
    c_err "Geçerli kullanıcı: $(id -un)"
    exit 2
else
    SUDO=(sudo)
    # --- 2) sudo oturumu ön-doğrulama -------------------------------------
    # curl|bash akışında dahi sudo, parola isteğini /dev/tty üzerinden alır.
    c_info "Yönetici yetkisi doğrulanıyor. İstenirse etapadmin parolasını girin."
    if ! sudo -v; then
        c_err "Yetki doğrulanamadı; çıkılıyor."
        exit 3
    fi
fi

# --- 3) Gerekli paketler ---------------------------------------------------
c_info "Gerekli sistem paketleri kontrol ediliyor..."
# python3-qrcode: PIN kâğıdındaki QR kodları TiHA tarafından buradan
# üretilir (eta-otp-cli de aynı paketi kullanır). Yoksa kâğıt QR'sız
# çıkar, anahtar metni yine yazılı olur.
REQUIRED=(python3 python3-gi gir1.2-gtk-3.0 python3-pyotp python3-qrcode tar curl policykit-1)
MISSING=()
for pkg in "${REQUIRED[@]}"; do
    if ! dpkg -l "$pkg" 2>/dev/null | awk 'NR>5{print $1}' | grep -q '^ii'; then
        MISSING+=("$pkg")
    fi
done
if (( ${#MISSING[@]} > 0 )); then
    c_info "Eksik paketler kuruluyor: ${MISSING[*]}"
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get update -qq >&2
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}" >&2
else
    c_ok "Tüm paketler hazır."
fi

# --- 4) TiHA kaynak kodu indir --------------------------------------------
# Yerel kopya (geliştirme ya da yönetim sunucusunun koyduğu sürüm) varsa
# indirme yapılmaz.
if [[ -n "${TIHA_LOCAL_DIR:-}" && -d "$TIHA_LOCAL_DIR/tiha" ]]; then
    c_info "TIHA_LOCAL_DIR kullanılıyor: $TIHA_LOCAL_DIR"
    rm -rf "$WORKDIR"
    WORKDIR="$TIHA_LOCAL_DIR"
    trap - EXIT   # yerel dizini silme
else
    c_info "TiHA kaynak kodu indiriliyor..."
    if ! curl -fsSL --connect-timeout 15 "$REPO_TARBALL" | tar -xz -C "$WORKDIR" --strip-components=1; then
        c_err "Kaynak kodu indirilemedi. İnternet bağlantınızı kontrol edin."
        exit 4
    fi
fi

# --- 5) eta-otp-cli aracını indir -----------------------------------------
# TiHA Modül 4 (PIN anahtarları) bu aracı kullanır. İndirme başarısız olursa
# TiHA dahili pyotp tabanlı yedek yolu kullanır — fatal değildir.
# Yerel kopyada araç zaten varsa (yönetim sunucusu birlikte koyduysa)
# yeniden indirilmez; internetsiz tahtada da çalışır.
ETA_OTP_CLI_DIR="$WORKDIR/eta-otp-cli"
mkdir -p "$ETA_OTP_CLI_DIR"
ETA_OK=true
if [[ -f "$ETA_OTP_CLI_DIR/otp-cli.py" ]]; then
    OTP_FILES=()
else
    c_info "enseitankado/eta-otp-cli aracı indiriliyor..."
    OTP_FILES=(otp-cli.py toplu-kullanici-olustur.py README.md)
fi
for f in "${OTP_FILES[@]}"; do
    if ! curl -fsSL --connect-timeout 15 --max-time 60 "$ETA_OTP_RAW/$f" -o "$ETA_OTP_CLI_DIR/$f"; then
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

# --- 5b) eta-112 aracını indir --------------------------------------------
# TiHA m14 (BIOS yönetici parolası) bu aracı kullanır. Yalnızca desteklenen
# donanım modellerinde işe yarar; indirme başarısız olursa modül kendisi
# de tekrar denemeyi yapar — bootstrap fatal değildir.
ETA_112_DIR="$WORKDIR/eta-112"
mkdir -p "$ETA_112_DIR"
if [[ ! -f "$ETA_112_DIR/eta-112.py" ]]; then
    c_info "enseitankado/eta-112 aracı indiriliyor..."
    curl -fsSL --connect-timeout 15 --max-time 60 "$ETA_112_RAW/eta-112.py" \
        -o "$ETA_112_DIR/eta-112.py" || rm -f "$ETA_112_DIR/eta-112.py"
fi
if [[ -f "$ETA_112_DIR/eta-112.py" ]]; then
    chmod +x "$ETA_112_DIR/eta-112.py" 2>/dev/null || true
    c_ok "eta-112 hazır: $ETA_112_DIR"
    export TIHA_ETA_112_DIR="$ETA_112_DIR"
else
    c_warn "eta-112 indirilemedi; TiHA modülün içinden tekrar deneyecek."
fi

# --- 6) Uygulamayı başlat --------------------------------------------------
c_info "TiHA başlatılıyor..."
# DISPLAY/XAUTHORITY gibi değişkenleri sudo içinde de erişilebilir tut
# (root'ta sudo yok; ortam zaten aynı).
if (( ${#SUDO[@]} )); then SUDO+=(-E); fi
exec "${SUDO[@]}" env \
    PYTHONPATH="$WORKDIR" \
    TIHA_HOME="$WORKDIR" \
    TIHA_ETA_OTP_CLI_DIR="${TIHA_ETA_OTP_CLI_DIR:-}" \
    TIHA_ETA_112_DIR="${TIHA_ETA_112_DIR:-}" \
    python3 -m tiha "$@"
