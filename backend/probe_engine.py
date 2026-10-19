"""
FNHyperMount - Probe & Health Engine
负责网络探针、NFS端口握手、安全读写体检以及内核 mountstats 实时吞吐解析
"""

import os
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from config import MSG_NFS_PERMISSION_ERROR, MSG_PING_TIMEOUT, MSG_PORT_UNREACHABLE, MOUNTSTATS_PATH

# 缓存上一次的统计数据，用于计算吞吐速率
_last_io_sample = {
    "timestamp": 0.0,
    "mounts": {} # { mountpoint: {"read_bytes": x, "write_bytes": y} }
}

executor = ThreadPoolExecutor(max_workers=8)


def ping_host(host: str, timeout: float = 1.5) -> dict:
    """测试到目标机器的 Ping 延时"""
    try:
        # Linux ping 1 packet
        cmd = ["ping", "-c", "1", "-W", str(int(timeout)), host]
        start_t = time.time()
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout + 0.5)
        elapsed = (time.time() - start_t) * 1000.0
        if res.returncode == 0:
            # 解析实际 rtt
            m = re.search(r"time=([0-9.]+)\s*ms", res.stdout)
            rtt = float(m.group(1)) if m else round(elapsed, 2)
            return {"ok": True, "latency_ms": rtt, "msg": f"{rtt:.1f} ms"}
        else:
            return {"ok": False, "latency_ms": -1, "msg": MSG_PING_TIMEOUT}
    except Exception as e:
        return {"ok": False, "latency_ms": -1, "msg": f"{MSG_PING_TIMEOUT} ({str(e)})"}


def check_nfs_port(host: str, port: int = 2049, timeout: float = 1.5) -> dict:
    """测试目标机器的 NFS 2049 端口连通性"""
    start_t = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        elapsed = (time.time() - start_t) * 1000.0
        s.close()
        return {"ok": True, "latency_ms": round(elapsed, 2), "msg": f"端口连通 ({elapsed:.1f} ms)"}
    except Exception:
        return {"ok": False, "latency_ms": -1, "msg": MSG_PORT_UNREACHABLE}


def discover_nfs_exports(host: str, timeout: float = 4.0) -> dict:
    """
    通过 showmount -e 探测目标机器导出的共享目录
    若失败，严格返回通用文案：请检查目标机器的NFS访问权限
    """
    cmd = ["showmount", "-e", "--no-headers", host]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        if res.returncode != 0:
            return {
                "ok": False,
                "exports": [],
                "error": MSG_NFS_PERMISSION_ERROR,
                "raw_stderr": res.stderr.strip()
            }
        
        exports = []
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            export_path = parts[0]
            allowed_clients = " ".join(parts[1:]) if len(parts) > 1 else "*"
            exports.append({
                "path": export_path,
                "clients": allowed_clients
            })
        
        return {
            "ok": True,
            "exports": exports,
            "error": None
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exports": [],
            "error": f"{MSG_NFS_PERMISSION_ERROR} (探测超时)"
        }
    except FileNotFoundError:
        # showmount 命令不存在时降级
        return {
            "ok": False,
            "exports": [],
            "error": "系统缺少 showmount 工具，请确认安装了 nfs-common。"
        }
    except Exception as e:
        return {
            "ok": False,
            "exports": [],
            "error": f"{MSG_NFS_PERMISSION_ERROR} ({str(e)})"
        }


def read_mount_probe(mountpoint: str, timeout: float = 2.0) -> dict:
    """
    在独立线程中执行目录读取探测，带严格超时保护，防止 NFS 假死阻塞主进程
    """
    def _do_read():
        p = Path(mountpoint)
        if not p.exists():
            return {"ok": False, "msg": "挂载点不存在"}
        start_t = time.time()
        # 列出前 10 个条目以测试真实元数据读取
        items = []
        with os.scandir(mountpoint) as it:
            for entry in it:
                items.append(entry.name)
                if len(items) >= 10:
                    break
        elapsed = (time.time() - start_t) * 1000.0
        return {"ok": True, "latency_ms": round(elapsed, 2), "count": len(items)}

    future = executor.submit(_do_read)
    try:
        res = future.result(timeout=timeout)
        return res
    except FutureTimeoutError:
        return {"ok": False, "latency_ms": -1, "msg": "读取超时（对端可能处于重启宽限期或网络挂起）"}
    except Exception as e:
        return {"ok": False, "latency_ms": -1, "msg": f"读取失败: {str(e)}"}


def write_mount_probe(mountpoint: str, timeout: float = 2.5) -> dict:
    """
    测试真实写入并立即清理临时探针文件，验证写权限与落盘耗时
    """
    def _do_write():
        p = Path(mountpoint)
        if not p.is_dir():
            return {"ok": False, "msg": "挂载点不是有效目录"}
        test_file = p / f".fn_hypermount_probe_{uuid.uuid4().hex[:8]}.tmp"
        start_t = time.time()
        try:
            with open(test_file, "w") as f:
                f.write(f"probe:{time.time()}")
                f.flush()
                os.fsync(f.fileno())
            elapsed = (time.time() - start_t) * 1000.0
            # 立即清理
            if test_file.exists():
                test_file.unlink()
            return {"ok": True, "latency_ms": round(elapsed, 2), "writable": True}
        except PermissionError:
            return {"ok": False, "latency_ms": -1, "writable": False, "msg": f"权限不足：只读挂载或目标机器未授予写入权限。{MSG_NFS_PERMISSION_ERROR}"}
        except OSError as e:
            if getattr(e, "errno", 0) == 30: # EROFS (Read-only file system)
                return {"ok": False, "latency_ms": -1, "writable": False, "msg": "当前为安全只读挂载 (Read-only)"}
            return {"ok": False, "latency_ms": -1, "writable": False, "msg": f"写入错误: {str(e)}"}
        finally:
            if test_file.exists():
                try:
                    test_file.unlink()
                except Exception:
                    pass

    future = executor.submit(_do_write)
    try:
        res = future.result(timeout=timeout)
        return res
    except FutureTimeoutError:
        return {"ok": False, "latency_ms": -1, "writable": False, "msg": "写入操作超时挂起"}
    except Exception as e:
        return {"ok": False, "latency_ms": -1, "writable": False, "msg": f"写入异常: {str(e)}"}


def parse_kernel_mountstats() -> dict:
    """
    从 /proc/self/mountstats 读取所有 NFS 挂载点的累计读写字节数与实时吞吐速率
    返回结构:
    {
       "/vol1/1000/Hero/play": {
           "device": "10.25.0.11:/play",
           "fstype": "nfs4",
           "read_bytes_total": 111620683135,
           "write_bytes_total": 4908633745,
           "read_speed_bps": 34500000.0,
           "write_speed_bps": 1200000.0,
           "read_speed_human": "32.9 MB/s",
           "write_speed_human": "1.1 MB/s",
           "opts": "rw,vers=4.1...",
           "lease_expired": False
       }
    }
    """
    global _last_io_sample
    now = time.time()
    dt = now - _last_io_sample["timestamp"]
    if dt <= 0:
        dt = 1.0

    current_samples = {}
    if not MOUNTSTATS_PATH.exists():
        return {}

    try:
        with open(MOUNTSTATS_PATH, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return {}

    # 以 "device " 分割挂载块
    blocks = content.split("\ndevice ")
    for block in blocks:
        if not block.strip():
            continue
        first_line = block.splitlines()[0]
        # 匹配: 10.25.0.11:/play mounted on /vol1/1000/Hero/play with fstype nfs4
        m = re.match(r"^(\S+)\s+mounted\s+on\s+(\S+)\s+with\s+fstype\s+(nfs\S*)", first_line)
        if not m:
            continue
        device, mountpoint, fstype = m.group(1), m.group(2), m.group(3)

        # 查找 bytes: 行或 READ/WRITE 行
        # bytes: normal_read normal_write direct_read direct_write server_read server_write ...
        bytes_match = re.search(r"^\s+bytes:\s+(\d+)\s+(\d+)", block, re.M)
        if bytes_match:
            read_bytes = int(bytes_match.group(1))
            write_bytes = int(bytes_match.group(2))
        else:
            read_bytes = 0
            write_bytes = 0

        # 计算实时吞吐速率
        prev = _last_io_sample["mounts"].get(mountpoint, {})
        prev_r = prev.get("read_bytes", read_bytes)
        prev_w = prev.get("write_bytes", write_bytes)

        # 避免初次统计或溢出产生的负数
        delta_r = max(0, read_bytes - prev_r)
        delta_w = max(0, write_bytes - prev_w)

        # 只有在距离上一次取样超过 0.5 秒时才计算速率
        if _last_io_sample["timestamp"] > 0:
            read_bps = delta_r / dt
            write_bps = delta_w / dt
        else:
            read_bps = 0.0
            write_bps = 0.0

        # 检查租约
        lease_match = re.search(r"lease_expired=(\d+)", block)
        lease_expired = bool(int(lease_match.group(1))) if lease_match else False

        current_samples[mountpoint] = {
            "device": device,
            "mountpoint": mountpoint,
            "fstype": fstype,
            "read_bytes_total": read_bytes,
            "write_bytes_total": write_bytes,
            "read_speed_bps": round(read_bps, 2),
            "write_speed_bps": round(write_bps, 2),
            "read_speed_human": format_speed(read_bps),
            "write_speed_human": format_speed(write_bps),
            "lease_expired": lease_expired
        }

    # 更新缓存
    _last_io_sample["timestamp"] = now
    _last_io_sample["mounts"] = {
        mp: {"read_bytes": data["read_bytes_total"], "write_bytes": data["write_bytes_total"]}
        for mp, data in current_samples.items()
    }

    return current_samples


def format_speed(bps: float) -> str:
    """人类可读速率格式化"""
    if bps < 1024:
        return f"{bps:.0f} B/s"
    elif bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    elif bps < 1024 * 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f} MB/s"
    else:
        return f"{bps / (1024 * 1024 * 1024):.2f} GB/s"


def format_bytes(b: int) -> str:
    """人类可读容量格式化"""
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    elif b < 1024 * 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024 * 1024):.1f} GB"
    else:
        return f"{b / (1024 * 1024 * 1024 * 1024):.2f} TB"
