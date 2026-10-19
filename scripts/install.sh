#!/usr/bin/env bash
# ==============================================================================
# FNHyperMount - 飞牛高可靠存储挂载管理中心 一键安装与自举脚本
# ==============================================================================

set -e

INSTALL_DIR="/opt/fn-hypermount"
SERVICE_NAME="fn-hypermount.service"
BOOT_SERVICE="fn-hypermount-boot.service"
PORT=8099

echo "======================================================================"
echo "🚀 开始安装 FNHyperMount - 飞牛高可靠存储挂载中心"
echo "======================================================================"

# 1. 检查 root 权限
if [ "$EUID" -ne 0 ]; then
  echo "❌ 错误：请使用 root 权限运行此脚本 (例如: sudo bash install.sh)"
  exit 1
fi

# 2. 检查依赖
echo "[1/5] 检查系统环境依赖..."
if ! command -v python3 &>/dev/null; then
  echo "正在安装 Python3..."
  apt-get update && apt-get install -y python3
fi

if ! command -v showmount &>/dev/null; then
  echo "正在安装 nfs-common (支持远程共享探测)..."
  apt-get update && apt-get install -y nfs-common || true
fi

# 3. 部署文件
echo "[2/5] 部署应用文件到 ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cp -r "${SCRIPT_DIR}/backend" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/frontend" "${INSTALL_DIR}/"

# 4. 配置持久化与备份目录
echo "[3/5] 初始化持久化配置与安全备份目录..."
mkdir -p /etc/fn-hypermount/backups
if [ ! -f /etc/fn-hypermount/mounts.json ]; then
  echo '{"mounts": []}' > /etc/fn-hypermount/mounts.json
fi

# 5. 安装 systemd 服务
echo "[4/5] 注册并启动系统守护服务..."
cp "${SCRIPT_DIR}/systemd/${SERVICE_NAME}" /etc/systemd/system/
cp "${SCRIPT_DIR}/systemd/${BOOT_SERVICE}" /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
systemctl enable --now "${BOOT_SERVICE}"

# 6. 注册到飞牛应用中心 (App Center) 与桌面快捷方式
echo "[5/6] 注册到飞牛应用中心 (已安装列表与桌面图标)..."
mkdir -p /var/apps/fn-hypermount/ui/images
ln -sfn "${INSTALL_DIR}/backend" /var/apps/fn-hypermount/target
ln -sfn /etc/fn-hypermount /var/apps/fn-hypermount/config

cat << 'EOF_MANIFEST' > /var/apps/fn-hypermount/manifest
appname               = fn-hypermount
version               = 1.0.0
display_name          = NFS 挂载中心
desc                  = 专为飞牛打造的高可靠 NFS 存储挂载与实时吞吐监控中心
platform              = x86
source                = thirdparty
maintainer            = HyperMount Team
maintainer_url        = https://github.com/Aragamiz/FNHyperMount
distributor           = HyperMount Team
distributor_url       = https://github.com/Aragamiz/FNHyperMount
os_min_version        = 1.1.0
desktop_uidir         = ui
desktop_applaunchname = fn-hypermount.main
service_port          = 8099
checkport             = true
EOF_MANIFEST

cp /var/apps/fn-ups-manager/ICON.PNG /var/apps/fn-hypermount/ICON.PNG 2>/dev/null || true
cp /var/apps/fn-ups-manager/ICON_256.PNG /var/apps/fn-hypermount/ICON_256.PNG 2>/dev/null || true
cp /var/apps/fn-ups-manager/ICON.PNG /var/apps/fn-hypermount/ui/images/icon_128.png 2>/dev/null || true

# 注入飞牛 appcenter postgres 数据库
sudo -u postgres psql -d appcenter -c "
DO \$fnos\$
DECLARE
    v_app_id bigint;
    v_svc_id bigint;
BEGIN
    SELECT id INTO v_app_id FROM app WHERE app_name = 'fn-hypermount';
    IF v_app_id IS NULL THEN
        INSERT INTO app (
            app_name, name, version, version_id, tags, maintainer, distributor,
            download_count, install_type, path, install_volume_id, data_share_volume_id, data_volume_id,
            manual_install, is_stop, is_uninstall, is_beta, is_docker, min_size, service_url,
            source, source_id, status, is_non_manual_stop, is_systemd_uint, disable_authorization_path,
            features, micro_app, native_app, support_backup, backup_check_storage, platform,
            file_types, is_power_off_stop, i18n_matadata, disabled_reason, disabled_at, online_time,
            created_at, updated_at
        ) VALUES (
            'fn-hypermount', 'NFS 挂载中心', '1.0.0', 1000, 'Practical Efficiency', 'HyperMount Team', 'HyperMount Team',
            1, 'volume', '/var/apps/fn-hypermount', 1, 1, 1,
            false, false, true, false, false, 20480, '',
            'thirdparty', '999', 'running', false, false, false,
            '{}', false, false, false, false, 'x86',
            '{}', 0, '{\"zh-CN\":{\"DisplayName\":\"NFS 挂载中心\",\"Desc\":\"专为飞牛打造的高可靠 NFS 存储挂载与实时吞吐监控中心。支持对端探测、automount自动自愈与各挂载点独立实时吞吐监控。\"}}', 0, 0, 0,
            NOW(), NOW()
        ) RETURNING id INTO v_app_id;

        INSERT INTO app_service (
            app_id, service_name, title, \"desc\", icon, type, url, default_url, is_admin,
            control, full_url, no_display, gateway_socket, gateway_prefix, feature, file_types,
            i18n_matadata, created_at, updated_at
        ) VALUES (
            v_app_id, 'fn-hypermount.main', 'NFS 挂载中心', 'NFS 挂载中心', 'ICON.PNG', 'url',
            'http://\${host}:8099/', 'http://\${host}:8099/', true,
            '{\"show\":0,\"showRoute\":0,\"auth\":0,\"port\":1,\"path\":0,\"fullUrl\":0,\"accessPerm\":\"editable\",\"portPerm\":\"readonly\",\"pathPerm\":\"editable\",\"fullUrlPerm\":\"editable\"}',
            '', false, '', '', '{}', '[]', '', NOW(), NOW()
        ) RETURNING id INTO v_svc_id;

        INSERT INTO app_shortcut (
            app_id, service_id, type, created_at, updated_at
        ) VALUES (
            v_app_id, v_svc_id, 'desktop', NOW(), NOW()
        );
    ELSE
        UPDATE app SET status = 'running', is_stop = false, updated_at = NOW() WHERE id = v_app_id;
    END IF;
END \$fnos\$;
" 2>/dev/null || true

# 7. 获取飞牛本机 IP
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$HOST_IP" ]; then
  HOST_IP="127.0.0.1"
fi

echo "[6/6] 部署完成并已启动！"
echo "======================================================================"
echo "🎉 FNHyperMount 安装成功！"
echo ""
echo "🌐 Web 管理控制台访问地址："
echo "   http://${HOST_IP}:${PORT}"
echo ""
echo "✨ 核心特性："
echo "   • 零外部依赖 (内置 Python 标准库运行，内存仅 ~25MB)"
echo "   • 工业级 automount + hard,intr 故障防卡死自愈"
echo "   • 远程机器共享目录一键探测 (向导式添加)"
echo "   • 各挂载点独立实时读写吞吐监控看板"
echo "======================================================================"
