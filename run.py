#!/usr/bin/env python3
"""
FNHyperMount 本地快速启动入口
"""
import os
import sys

# 将 backend 加入 sys.path
backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
sys.path.insert(0, backend_dir)

from app import run_server

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    run_server(port=port)
