#!/bin/sh
# Run from anywhere with: sh /path/to/HomeStorage/deploy.sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if ! docker compose version >/dev/null 2>&1; then
    echo "需要 Docker Compose v2，请先安装 NAS 的 Container Manager / Docker。" >&2
    exit 1
fi

created_env=0
if [ ! -e .env ]; then
    app_uid=$(id -u)
    app_gid=$(id -g)
    # Even when invoked through sudo, the application runs as a regular user.
    if [ "$app_uid" = 0 ]; then app_uid=1000; app_gid=1000; fi
    password=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
    if [ "${#password}" -ne 48 ]; then
        echo "无法生成访问密码，部署已停止。" >&2
        exit 1
    fi
    (
        umask 077
        set -C
        printf 'HOME_STORAGE_PASSWORD=%s\nHOME_STORAGE_PORT=8080\nAPP_TIMEZONE=Asia/Shanghai\nPUID=%s\nPGID=%s\n' \
            "$password" "$app_uid" "$app_gid" > .env
    )
    unset password
    created_env=1
fi

# Read numeric IDs only; never execute/source a user-controlled .env file.
read_id() {
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" .env | tail -n 1 \
        | tr -d '\r' | sed -e 's/[[:space:]]*$//' -e "s/^[\"']//" -e "s/[\"']$//"
}
app_uid=${PUID:-$(read_id PUID)}
app_gid=${PGID:-$(read_id PGID)}
app_uid=${app_uid:-1000}
app_gid=${app_gid:-1000}
case "$app_uid:$app_gid" in
    *[!0-9:]*|:*|*:) echo "PUID 和 PGID 必须是数字。" >&2; exit 1 ;;
esac
if [ "$app_uid" = 0 ]; then
    echo "请在 .env 中使用非 root 的 PUID，应用无需 root 权限。" >&2
    exit 1
fi

docker compose -f docker-compose.yml config --quiet
mkdir -p data
if [ "$(id -u)" = 0 ]; then
    chown -R "$app_uid:$app_gid" data
elif [ "$(id -u)" != "$app_uid" ]; then
    echo "data 需要由 PUID=$app_uid 的用户拥有，请使用 sudo sh deploy.sh 完成目录权限设置。" >&2
    exit 1
fi

docker compose -f docker-compose.yml up -d --build --wait --wait-timeout 120
echo "HomeStorage 已启动。用手机浏览器打开 http://NAS的局域网IP:端口（默认 8080）。"
if [ "$created_env" = 1 ]; then
    echo "已生成首次管理员初始密码并保存到 .env 的 HOME_STORAGE_PASSWORD；用户名为 admin。已有账号请使用网页中设置的密码。"
else
    echo "已保留现有 .env、数据库和照片。已有账号使用网页中设置的密码；端口以 .env / 环境变量为准。"
fi
