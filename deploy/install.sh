#!/bin/bash
# Stock-bot systemd installer — idempotent. Re-run after any
# deploy/*.{service,timer,sh} change. Designed for auto-update.sh
# auto-invocation (single sudoers NOPASSWD line covers all changes).
# 사용자 정책 2026-05-29: SV 패턴 mirror — sudo 1회 setup 후 SSH 영원히
# 미진입. 새 systemd unit add 시도 stock repo push 만으로 1분 내 자동
# 설치 + reload + enable + Telegram 알림.
#
# Units installed (canonical paths from stock repo):
#   • stock-bot.service                 — Telegram bot (Type=simple)
#   • stock-bot-dashboard.service       — dashboard HTTP server
#   • stock-bot-update.{service,timer}  — git auto-deploy (1 min poll)
#   • stock-bot-watchdog.{service,timer}— hang detector (12 min)
#   • trade-bot.service                 — Korea trade-bot (if present)
#   • trade-bot-update.{service,timer}  — trade-bot git poll
#   • trade-bot-watchdog.{service,timer}— trade-bot hang detector
#   • screener-gics-check.{service,timer} — 분기 GICS 변경 점검 (4x/year)
#
# Side effects:
#   • Each unit `install -m 0644` 으로 /etc/systemd/system/ 에 복사
#   • Shell scripts chmod +x (auto-update.sh · watchdog.sh · trade-* · install.sh)
#   • systemctl daemon-reload
#   • Timer 들 enable --now (idempotent — 이미 enabled 면 no-op)
#   • Main service 들 (stock-bot · dashboard) 는 restart 하지 않음 —
#     auto-update.sh 의 stock-bot restart 가 별도 관리. Dashboard 만
#     enable.

set -euo pipefail

DEPLOY_DIR=/home/higgack/stock/deploy

if [ "$(id -u)" -ne 0 ]; then
    echo "must be run as root (use sudo)"
    exit 1
fi

if [ ! -d "$DEPLOY_DIR" ]; then
    echo "deploy dir not found: $DEPLOY_DIR"
    exit 1
fi

echo "→ installing systemd units"
for unit in \
    stock-bot.service \
    stock-bot-dashboard.service \
    stock-bot-update.service        stock-bot-update.timer \
    stock-bot-watchdog.service      stock-bot-watchdog.timer \
    trade-bot.service \
    trade-bot-update.service        trade-bot-update.timer \
    trade-bot-watchdog.service      trade-bot-watchdog.timer \
    screener-gics-check.service     screener-gics-check.timer \
    daily-byte.service              daily-byte.timer \
    daily-byte-weekly.service       daily-byte-weekly.timer \
    us-market-daily.service         us-market-daily.timer \
    blog-watch.service              blog-watch.timer \
    realestate-byte.service         realestate-byte.timer \
    realestate-byte-monthly.service realestate-byte-monthly.timer \
    cheongyak-byte.service          cheongyak-byte.timer \
    reddit-insider-watch.service    reddit-insider-watch.timer \
    portfolio-watch.service         portfolio-watch.timer \
    watchlist-check.service         watchlist-check.timer \
    dart-feed.service               dart-feed.timer ;
do
    if [ -f "$DEPLOY_DIR/$unit" ]; then
        install -m 0644 "$DEPLOY_DIR/$unit" /etc/systemd/system/
    fi
done

# Shell scripts — auto-update / watchdog / install — must be exec.
for sh in auto-update.sh watchdog.sh trade-auto-update.sh trade-watchdog.sh install.sh; do
    if [ -f "$DEPLOY_DIR/$sh" ]; then
        chmod +x "$DEPLOY_DIR/$sh"
    fi
done

systemctl daemon-reload

# Provision NOPASSWD sudoers for the systemctl restarts that auto-update.sh
# needs — so the user's single install.sh NOPASSWD grant self-extends to
# cover stock-bot + dashboard restarts (no extra manual sudoers step).
# 2026-06-03: dashboard 서버 코드 변경(Cache-Control 등) 자동 배포를 위해
# stock-bot-dashboard restart 권한 추가. visudo -c 로 검증 후에만 설치 →
# 문법 오류로 sudo 잠기는 사고 방지. Idempotent (동일 내용이면 no-op).
SUDOERS_DROPIN=/etc/sudoers.d/higgack-stock-restart
SUDOERS_TMP="$(mktemp)"
cat > "$SUDOERS_TMP" <<'SUDOERS'
higgack ALL=(root) NOPASSWD: /bin/systemctl restart stock-bot
higgack ALL=(root) NOPASSWD: /bin/systemctl restart stock-bot-dashboard
SUDOERS
if visudo -cf "$SUDOERS_TMP" >/dev/null 2>&1; then
    if ! cmp -s "$SUDOERS_TMP" "$SUDOERS_DROPIN" 2>/dev/null; then
        install -m 0440 -o root -g root "$SUDOERS_TMP" "$SUDOERS_DROPIN"
        echo "  sudoers drop-in installed: $SUDOERS_DROPIN"
    fi
else
    echo "  ⚠️ sudoers drop-in failed visudo validation — skipped"
fi
rm -f "$SUDOERS_TMP"

# Daily Byte 인포그래픽 한글 렌더용 NanumGothic 폰트 — idempotent.
# 이미 설치돼 있으면 apt 가 no-op. 실패해도(네트워크/오프라인) install 전체를
# 막지 않도록 || true. 폰트 부재 시 인포그래픽만 graceful skip(텍스트 정상).
if ! fc-list 2>/dev/null | grep -qi nanum; then
    echo "→ installing fonts-nanum (Daily Byte 인포그래픽 한글)"
    DEBIAN_FRONTEND=noninteractive apt-get install -y fonts-nanum >/dev/null 2>&1 \
        && fc-cache -f >/dev/null 2>&1 \
        && echo "  fonts-nanum installed" \
        || echo "  fonts-nanum install skipped (apt 불가 — 텍스트 브리프는 정상)"
fi

echo "→ enabling timers + dashboard"
# Timers — enable --now is idempotent (no-op when already enabled+active)
systemctl enable --now stock-bot-update.timer
systemctl enable --now stock-bot-watchdog.timer
if [ -f "$DEPLOY_DIR/screener-gics-check.timer" ]; then
    systemctl enable --now screener-gics-check.timer
fi
if [ -f "$DEPLOY_DIR/daily-byte.timer" ]; then
    systemctl enable --now daily-byte.timer
fi
if [ -f "$DEPLOY_DIR/daily-byte-weekly.timer" ]; then
    systemctl enable --now daily-byte-weekly.timer
fi
if [ -f "$DEPLOY_DIR/us-market-daily.timer" ]; then
    systemctl enable --now us-market-daily.timer
fi
if [ -f "$DEPLOY_DIR/blog-watch.timer" ]; then
    systemctl enable --now blog-watch.timer
fi
if [ -f "$DEPLOY_DIR/realestate-byte.timer" ]; then
    systemctl enable --now realestate-byte.timer
fi
if [ -f "$DEPLOY_DIR/realestate-byte-monthly.timer" ]; then
    systemctl enable --now realestate-byte-monthly.timer
fi
if [ -f "$DEPLOY_DIR/cheongyak-byte.timer" ]; then
    systemctl enable --now cheongyak-byte.timer
fi
if [ -f "$DEPLOY_DIR/reddit-insider-watch.timer" ]; then
    systemctl enable --now reddit-insider-watch.timer
fi
if [ -f "$DEPLOY_DIR/portfolio-watch.timer" ]; then
    systemctl enable --now portfolio-watch.timer
fi
if [ -f "$DEPLOY_DIR/watchlist-check.timer" ]; then
    systemctl enable --now watchlist-check.timer
fi
if [ -f "$DEPLOY_DIR/dart-feed.timer" ]; then
    systemctl enable --now dart-feed.timer
fi
if [ -f "$DEPLOY_DIR/trade-bot-update.timer" ]; then
    systemctl enable --now trade-bot-update.timer
fi
if [ -f "$DEPLOY_DIR/trade-bot-watchdog.timer" ]; then
    systemctl enable --now trade-bot-watchdog.timer
fi

# Main services — enable but don't restart (auto-update.sh handles stock-bot
# restart for code changes; dashboard restart only on user demand).
systemctl enable stock-bot.service
if [ -f "$DEPLOY_DIR/stock-bot-dashboard.service" ]; then
    systemctl enable --now stock-bot-dashboard.service
    # try-restart so a changed unit (e.g. ExecStart venv switch) actually
    # takes effect — enable --now is a no-op when already running.
    systemctl try-restart stock-bot-dashboard.service || true
fi
if [ -f "$DEPLOY_DIR/trade-bot.service" ]; then
    systemctl enable trade-bot.service
fi

echo
echo "→ active stock-bot timers:"
systemctl list-timers --no-pager --all 2>/dev/null \
    | grep -E "stock-bot|trade-bot|screener-gics" || true

echo
echo "✓ install complete."
