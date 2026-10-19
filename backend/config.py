"""
FNHyperMount - Configuration and Constants
通用规范：绝不出现特定品牌或设备名称，完全通用化
"""

import os
from pathlib import Path

# 版本号
APP_VERSION = "0.1.0"

# 默认服务端口
DEFAULT_PORT = 8099
DEFAULT_HOST = "0.0.0.0"

# 配置文件与持久化路径
CONFIG_DIR = Path("/etc/fn-hypermount")
CONFIG_FILE = CONFIG_DIR / "mounts.json"
BACKUP_FSTAB_DIR = CONFIG_DIR / "backups"

# 系统路径
FSTAB_PATH = Path("/etc/fstab")
MOUNTSTATS_PATH = Path("/proc/self/mountstats")

# 挂载参数工业级预设
PRESETS = {
    "automount": {
        "name": "工业级高可靠模式 (推荐)",
        "description": "按需自动激活、开机不阻塞系统启动、对端重启自动重连自愈，防丢数据",
        "options": "x-systemd.automount,x-systemd.mount-timeout=30s,hard,intr,nofail,noatime,proto=tcp"
    },
    "direct": {
        "name": "常驻直接挂载模式",
        "description": "开机立即直接挂载，适合全天候稳定在线、高并发直接存取的目标存储",
        "options": "nofail,hard,intr,noatime,proto=tcp"
    },
    "readonly": {
        "name": "安全只读模式 (ro)",
        "description": "仅允许读取，禁止任何写入，保护目标机器上的数据不被误删或修改",
        "options": "ro,x-systemd.automount,x-systemd.mount-timeout=30s,hard,intr,nofail,noatime,proto=tcp"
    }
}

# 标准通用错误提示与诊断文案
MSG_NFS_PERMISSION_ERROR = "请检查目标机器的NFS访问权限（确认目标机器已开启NFS服务，且共享目录导出了飞牛主机的IP访问权限）。"
MSG_PING_TIMEOUT = "无法连接到目标机器，请检查网络连接、IP地址或防火墙设置。"
MSG_PORT_UNREACHABLE = "目标机器网络可达，但NFS服务端口（2049）未响应，请检查目标机器是否已启动NFS服务。"
MSG_PROBE_SUCCESS = "连接目标机器成功，已就绪。"
