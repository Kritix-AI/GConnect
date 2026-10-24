"""Joins this GConnect sandbox to a Ray GPU cluster.

Modes:
  python ray_node.py --head [--port 6379]
      Starts a Ray head node (run this on the FIRST PC only).
  python ray_node.py --join <head-ip>:6379
      Joins this PC's GPUs to an existing head.

Afterwards, from any node:
  ray.init("ray://<head-ip>:10001")  # 10001 = dashboard+client port (see output)
"""

import argparse
import socket
import subprocess
import sys


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main() -> int:
    parser = argparse.ArgumentParser(description="GConnect Ray cluster node")
    parser.add_argument("--head", action="store_true", help="start a Ray head node")
    parser.add_argument("--join", metavar="HEAD_IP:PORT", help="join an existing head")
    parser.add_argument("--port", type=int, default=6379, help="head GCS port")
    args = parser.parse_args()

    try:
        import ray  # noqa: F401
    except ImportError:
        print("Ray is not installed. Run:  pip install ray[train]")
        return 1

    if args.head:
        cmd = [
            "ray", "start", "--head", "--port", str(args.port),
            "--include-dashboard=true",
            # Ray client + dashboard ports for `ray://` connections:
            "--ray-client-server-port", "10001",
            "--dashboard-port", "8265",
        ]
        print(f"Starting Ray HEAD on {local_ip()} ...")
        subprocess.run(cmd, check=True)
        print()
        print("=" * 60)
        print(f" Head address : {local_ip()}:{args.port}")
        print(f" Client URL   : ray://{local_ip()}:10001")
        print(f" Dashboard    : http://{local_ip()}:8265")
        print(" Other PCs join with:")
        print(f"   python ray_node.py --join {local_ip()}:{args.port}")
        print("=" * 60)
        return 0

    if args.join:
        cmd = ["ray", "start", "--address", args.join]
        subprocess.run(cmd, check=True)
        print(f"Joined Ray cluster at {args.join}. This PC's GPUs are now pooled.")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
