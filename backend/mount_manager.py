"""
FNHyperMount - Mount Management Core Engine
负责挂载点解析、安全修改 /etc/fstab、systemd 守护联动与全面体检
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from config import CONFIG_DIR, CONFIG_FILE, BACKUP_FSTAB_DIR, FSTAB_PATH, PRESETS, MSG_NFS_PERMISSION_ERROR
from probe_engine import ping_host, check_nfs_port, read_mount_probe, write_mount_probe, parse_kernel_mountstats, format_bytes

TAG_BEGIN = "# --- BEGIN FN-HYPERMOUNT MANAGED ---"
TAG_END = "# --- END FN-HYPERMOUNT MANAGED ---"


class MountManager:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        BACKUP_FSTAB_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_config()

    def _ensure_config(self):
        if not CONFIG_FILE.exists():
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"mounts": []}, f, indent=2, ensure_ascii=False)

    def load_managed_config(self) -> dict:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"mounts": []}

    def save_managed_config(self, data: dict):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def backup_fstab(self):
        if not FSTAB_PATH.exists():
            return
        ts = time.strftime("%Y%m%d_%H%M%S")
        dest = BACKUP_FSTAB_DIR / f"fstab.{ts}"
        try:
            shutil.copy2(FSTAB_PATH, dest)
            # 保留最近 10 个备份
            backups = sorted(BACKUP_FSTAB_DIR.glob("fstab.*"), key=os.path.getmtime)
            while len(backups) > 10:
                backups.pop(0).unlink(missing_ok=True)
        except Exception as e:
            print(f"[Warn] Backup fstab failed: {e}")

    def get_all_mounts(self) -> list:
        """
        获取当前系统中所有活跃和配置的 NFS 挂载点
        汇总：fstab 配置、活跃挂载状态、内核 mountstats 实时吞吐与容量
        """
        # 1. 采集内核 mountstats 吞吐数据
        stats = parse_kernel_mountstats()

        # 2. 读取 /proc/mounts 中的实时挂载点 (排除 nfsd 等内核虚拟文件系统)
        active_mounts = {}
        autofs_mounts = set()
        if Path("/proc/mounts").exists():
            try:
                with open("/proc/mounts", "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 3:
                            dev, mp, fs = parts[0], parts[1], parts[2]
                            if fs in ("nfs", "nfs4"):
                                active_mounts[mp] = {
                                    "device": dev,
                                    "mountpoint": mp,
                                    "fstype": fs,
                                    "options": parts[3] if len(parts) > 3 else ""
                                }
                            elif fs == "autofs":
                                autofs_mounts.add(mp)
            except Exception:
                pass

        # 3. 读取 fstab 中的所有配置项（包括非本 App 管理的现有挂载）
        fstab_entries = self._read_fstab_nfs_entries()

        # 4. 汇总挂载点字典
        mount_dict = {}

        # 4.1 加入 fstab 中的项
        for entry in fstab_entries:
            mp = entry["mountpoint"]
            is_mounted = mp in active_mounts
            is_automount = mp in autofs_mounts or "x-systemd.automount" in entry.get("options", "")
            
            if is_mounted:
                status = "online"
                status_text = "🟢 正常在线 (已挂载)"
            elif is_automount:
                status = "standby"
                status_text = "🟡 待命就绪 (访问时自动激活)"
            else:
                status = "unmounted"
                status_text = "⚪ 未挂载"

            mount_dict[mp] = {
                "id": entry.get("id") or mp.replace("/", "_").strip("_"),
                "device": entry["device"],
                "server": entry["server"],
                "export": entry["export"],
                "mountpoint": mp,
                "fstype": entry.get("fstype", "nfs"),
                "options": entry.get("options", ""),
                "preset": entry.get("preset", "custom"),
                "managed_by_app": entry.get("managed_by_app", False),
                "is_mounted": is_mounted,
                "is_automount": is_automount,
                "status": status,
                "status_text": status_text
            }

        # 4.2 补全在运行中但不在 fstab 中的临时真实 NFS 挂载 (排除系统 procfs)
        for mp, act in active_mounts.items():
            if mp not in mount_dict and not mp.startswith("/proc") and not mp.startswith("/sys"):
                device = act["device"]
                server, export = self._split_device(device)
                mount_dict[mp] = {
                    "id": mp.replace("/", "_").strip("_"),
                    "device": device,
                    "server": server,
                    "export": export,
                    "mountpoint": mp,
                    "fstype": act["fstype"],
                    "options": act["options"],
                    "preset": "system",
                    "managed_by_app": False,
                    "is_mounted": True,
                    "is_automount": False,
                    "status": "online",
                    "status_text": "🟢 正常在线 (系统原生)"
                }

        # 5. 补充容量与吞吐数据
        result = []
        for mp, item in mount_dict.items():
            # 吞吐
            st = stats.get(mp, {})
            item["read_speed_human"] = st.get("read_speed_human", "0 B/s")
            item["write_speed_human"] = st.get("write_speed_human", "0 B/s")
            item["read_bytes_total"] = st.get("read_bytes_total", 0)
            item["write_bytes_total"] = st.get("write_bytes_total", 0)
            item["read_bytes_human"] = format_bytes(item["read_bytes_total"])
            item["write_bytes_human"] = format_bytes(item["write_bytes_total"])

            # 容量 (仅对已挂载点安全获取)
            if item["is_mounted"]:
                try:
                    usage = shutil.disk_usage(mp)
                    item["disk_total"] = usage.total
                    item["disk_used"] = usage.used
                    item["disk_free"] = usage.free
                    item["disk_total_human"] = format_bytes(usage.total)
                    item["disk_used_human"] = format_bytes(usage.used)
                    item["disk_free_human"] = format_bytes(usage.free)
                    item["disk_percent"] = round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0
                except Exception:
                    item["disk_total"] = 0
                    item["disk_used"] = 0
                    item["disk_free"] = 0
                    item["disk_total_human"] = "-"
                    item["disk_used_human"] = "-"
                    item["disk_free_human"] = "-"
                    item["disk_percent"] = 0
            else:
                item["disk_total_human"] = "-"
                item["disk_used_human"] = "-"
                item["disk_free_human"] = "-"
                item["disk_percent"] = 0

            result.append(item)

        return result

    def add_nfs_mount(self, server: str, export_path: str, local_mountpoint: str, preset_key: str = "automount", custom_options: str = "") -> dict:
        """
        向导式添加新的 NFS 挂载
        1. 格式化设备源: server:export
        2. 确保本地目录存在
        3. 构造预设参数
        4. 写入 fstab
        5. systemctl daemon-reload 并尝试激活
        """
        server = server.strip()
        export_path = "/" + export_path.strip().lstrip("/")
        local_mountpoint = "/" + local_mountpoint.strip().lstrip("/")
        device = f"{server}:{export_path}"

        # 选择参数
        if preset_key in PRESETS:
            options = PRESETS[preset_key]["options"]
        elif custom_options:
            options = custom_options.strip()
        else:
            options = PRESETS["automount"]["options"]

        # 确保本地挂载目录存在
        mp_path = Path(local_mountpoint)
        try:
            mp_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"ok": False, "error": f"创建本地挂载目录失败: {str(e)}"}

        # 备份并写入 fstab
        self.backup_fstab()
        try:
            self._insert_or_update_fstab(device, local_mountpoint, "nfs", options)
        except Exception as e:
            return {"ok": False, "error": f"更新 /etc/fstab 失败: {str(e)}"}

        # 记录到 managed config
        cfg = self.load_managed_config()
        # 移除已有的同名项
        cfg["mounts"] = [m for m in cfg.get("mounts", []) if m["mountpoint"] != local_mountpoint]
        cfg["mounts"].append({
            "id": local_mountpoint.replace("/", "_").strip("_"),
            "server": server,
            "export": export_path,
            "device": device,
            "mountpoint": local_mountpoint,
            "preset": preset_key,
            "options": options,
            "created_at": time.time()
        })
        self.save_managed_config(cfg)

        # 触发 systemd 重载
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

        # 如果包含 automount，重启对应的 automount 单元以激活
        unit_name = self._path_to_unit_name(local_mountpoint)
        if "x-systemd.automount" in options:
            subprocess.run(["systemctl", "restart", f"{unit_name}.automount"], capture_output=True)
        else:
            # 直接挂载
            subprocess.run(["mount", local_mountpoint], capture_output=True)

        # 执行一次快速健康验收
        health = self.health_check_mount(local_mountpoint, server)

        return {
            "ok": True,
            "msg": "挂载配置成功添加并已就绪！",
            "health": health
        }

    def remove_nfs_mount(self, mountpoint: str) -> dict:
        """卸载并从 fstab 和管理列表中移除"""
        self.backup_fstab()
        # 尝试 umount
        unit_name = self._path_to_unit_name(mountpoint)
        subprocess.run(["systemctl", "stop", f"{unit_name}.automount"], capture_output=True)
        subprocess.run(["systemctl", "stop", f"{unit_name}.mount"], capture_output=True)
        subprocess.run(["umount", "-f", "-l", mountpoint], capture_output=True)

        # 从 fstab 中清理
        self._remove_from_fstab(mountpoint)
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)

        # 更新 config.json
        cfg = self.load_managed_config()
        cfg["mounts"] = [m for m in cfg.get("mounts", []) if m["mountpoint"] != mountpoint]
        self.save_managed_config(cfg)

        return {"ok": True, "msg": f"挂载点 {mountpoint} 已成功移除"}

    def remount(self, mountpoint: str) -> dict:
        """手动重连/重新挂载"""
        unit_name = self._path_to_unit_name(mountpoint)
        subprocess.run(["systemctl", "restart", f"{unit_name}.automount"], capture_output=True)
        subprocess.run(["systemctl", "restart", f"{unit_name}.mount"], capture_output=True)
        # 触发一次简单读取以唤醒 automount
        read_mount_probe(mountpoint, timeout=2.0)
        return {"ok": True, "msg": f"{mountpoint} 重新挂载信号已发送"}

    def health_check_mount(self, mountpoint: str, server: str = None) -> dict:
        """
        四重连环健康体检：
        1. 网络 Ping 连通性
        2. NFS 2049 端口握手
        3. 挂载点目录读取测试 (延迟 ms)
        4. 临时探针写入测试 (延迟 ms 与写权限)
        """
        if not server:
            # 从挂载点推导 server
            all_m = self.get_all_mounts()
            for m in all_m:
                if m["mountpoint"] == mountpoint:
                    server = m["server"]
                    break

        report = {
            "mountpoint": mountpoint,
            "server": server or "unknown",
            "ping": {"ok": False, "msg": "未检测"},
            "port": {"ok": False, "msg": "未检测"},
            "read": {"ok": False, "msg": "未检测"},
            "write": {"ok": False, "msg": "未检测"},
            "summary_ok": False,
            "diagnostic_tip": ""
        }

        # 1. Ping
        if server:
            report["ping"] = ping_host(server, timeout=1.5)
            if not report["ping"]["ok"]:
                report["diagnostic_tip"] = f"网络不可达：{report['ping']['msg']}"
                return report

            # 2. Port 2049
            report["port"] = check_nfs_port(server, 2049, timeout=1.5)
            if not report["port"]["ok"]:
                report["diagnostic_tip"] = f"端口未开放：{MSG_NFS_PERMISSION_ERROR}"
                return report

        # 3. Read Probe
        report["read"] = read_mount_probe(mountpoint, timeout=2.0)
        if not report["read"]["ok"]:
            report["diagnostic_tip"] = f"读取失败：{report['read'].get('msg')}。{MSG_NFS_PERMISSION_ERROR}"
            return report

        # 4. Write Probe
        report["write"] = write_mount_probe(mountpoint, timeout=2.5)
        if not report["write"]["ok"]:
            # 如果是只读挂载，不算致命失败，但需要提示
            report["diagnostic_tip"] = report["write"].get("msg")
            report["summary_ok"] = True # 读取正常即算基本可用
        else:
            report["summary_ok"] = True
            report["diagnostic_tip"] = "全部测试通过，读写连接极佳。"

        return report

    # 内部辅助函数
    def _split_device(self, device: str):
        if ":" in device:
            s, e = device.split(":", 1)
            return s.strip(), e.strip()
        return device, ""

    def _path_to_unit_name(self, path: str) -> str:
        # systemd 转义规范: /vol1/1000/Hero/play -> vol1-1000-Hero-play
        clean = path.strip("/").replace("/", "-")
        return clean

    def _read_fstab_nfs_entries(self) -> list:
        if not FSTAB_PATH.exists():
            return []
        entries = []
        in_managed_block = False
        try:
            with open(FSTAB_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped == TAG_BEGIN:
                        in_managed_block = True
                        continue
                    if stripped == TAG_END:
                        in_managed_block = False
                        continue
                    if not stripped or stripped.startswith("#"):
                        continue
                    parts = stripped.split()
                    if len(parts) >= 3 and parts[2].startswith("nfs"):
                        device = parts[0]
                        mountpoint = parts[1]
                        server, export = self._split_device(device)
                        entries.append({
                            "device": device,
                            "server": server,
                            "export": export,
                            "mountpoint": mountpoint,
                            "fstype": parts[2],
                            "options": parts[3] if len(parts) > 3 else "defaults",
                            "managed_by_app": in_managed_block
                        })
        except Exception as e:
            print(f"[Error] read fstab: {e}")
        return entries

    def _insert_or_update_fstab(self, device: str, mountpoint: str, fstype: str, options: str):
        """
        在 fstab 中更新或追加挂载记录，优先放在 FN-HYPERMOUNT MANAGED 块内
        """
        lines = []
        if FSTAB_PATH.exists():
            with open(FSTAB_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()

        new_line = f"{device}\t{mountpoint}\t{fstype}\t{options}\t0\t0\n"

        # 检查是否已有同一 mountpoint，已有则替换
        has_block = any(TAG_BEGIN in l for l in lines)
        replaced = False
        new_lines = []

        for l in lines:
            parts = l.strip().split()
            if len(parts) >= 2 and parts[1] == mountpoint:
                new_lines.append(new_line)
                replaced = True
            else:
                new_lines.append(l)

        if not replaced:
            if has_block:
                final_lines = []
                for l in new_lines:
                    final_lines.append(l)
                    if TAG_BEGIN in l:
                        final_lines.append(new_line)
                new_lines = final_lines
            else:
                # 追加 managed 块
                new_lines.append(f"\n{TAG_BEGIN}\n")
                new_lines.append(new_line)
                new_lines.append(f"{TAG_END}\n")

        with open(FSTAB_PATH, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    def _remove_from_fstab(self, mountpoint: str):
        if not FSTAB_PATH.exists():
            return
        with open(FSTAB_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        new_lines = []
        for l in lines:
            parts = l.strip().split()
            if len(parts) >= 2 and parts[1] == mountpoint:
                continue
            new_lines.append(l)
        with open(FSTAB_PATH, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
