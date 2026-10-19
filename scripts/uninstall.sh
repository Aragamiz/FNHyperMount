#!/usr/bin/env bash
# ==============================================================================
# FNHyperMount - 卸载与清理脚本
# ==============================================================================

set -e

if [ "$EUID" -ne 0 ]; then
  echo "❌ 错误：请使用 root 权限运行此脚本 (例如: sudo bash uninstall.sh)"
  exit 1
fi

echo "正在卸载 FNHyperMount..."

systemctl stop fn-hypermount.service 2>/dev/null || true
systemctl disable fn-hypermount.service 2>/dev/null || true
systemctl stop fn-hypermount-boot.service 2>/dev/null || true
systemctl disable fn-hypermount-boot.service 2>/dev/null || true

rm -f /etc/systemd/system/fn-hypermount.service
rm -f /etc/systemd/system/fn-hypermount-boot.service
systemctl daemon-reload

rm -rf /opt/fn-hypermount
rm -rf /var/apps/fn-hypermount

# 从飞牛应用中心数据库注销
sudo -u postgres psql -d appcenter -c "
DO \$fnos\$
DECLARE
    v_app_id bigint;
BEGIN
    SELECT id INTO v_app_id FROM app WHERE app_name = 'fn-hypermount';
    IF v_app_id IS NOT NULL THEN
        DELETE FROM app_shortcut WHERE app_id = v_app_id;
        DELETE FROM app_service WHERE app_id = v_app_id;
        DELETE FROM app WHERE id = v_app_id;
    END IF;
END \$fnos\$;
" 2>/dev/null || true

echo "✅ FNHyperMount 服务与应用中心注册信息已成功清理。"
echo "（注意：/etc/fn-hypermount 下的配置与安全备份已保留，如需彻底清除可手动执行 rm -rf /etc/fn-hypermount）"
