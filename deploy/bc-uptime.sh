#!/bin/bash
# bc-uptime.sh — availability checks run every 10 minutes on the main (Turkey)
# server. Covers both services:
#   - flexchat.top (this host)
#   - Nextcloud on the RU relay (the path contacts use)
# Two consecutive failures => alert; recovery afterwards => RECOVERED alert.
#   Cron: /etc/cron.d/bc-ops          Logs: /var/log/bc-uptime.log
set -u

run_check() { # name url human-label
    local name="$1" url="$2" human="$3"
    local fails=0 notified=0
    if [ -f "/var/lib/bc-uptime.${name}" ]; then
        read -r fails notified < "/var/lib/bc-uptime.${name}"
    fi
    local code
    code=$(curl -sk -o /dev/null -w "%{http_code}" -m 20 "$url" || echo 000)
    case "$code" in
        2*|3*)
            if [ "$notified" = "1" ]; then
                /usr/local/bin/bc-alert.sh uptime "RECOVERED: ${human} is back up"
            fi
            echo "0 0" > "/var/lib/bc-uptime.${name}"
            ;;
        *)
            fails=$((fails + 1))
            if [ "$fails" -ge 2 ] && [ "$notified" != "1" ]; then
                /usr/local/bin/bc-alert.sh uptime "DOWN: ${human} (${fails} consecutive failures, last HTTP ${code})"
                notified=1
            fi
            echo "$fails $notified" > "/var/lib/bc-uptime.${name}"
            ;;
    esac
}

run_check flexchat "https://flexchat.top/" "flexchat.top (main)"
run_check nextcloud "http://${BC_RELAY_IP:-62.109.10.170}/index.php/login" "Nextcloud on RU relay"
