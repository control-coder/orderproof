"""容器内收集器；返回的所有内容在宿主仍视为不可信数据。"""
import base64
import json
import os
import stat
import subprocess
import sys


def main():
    spec = json.loads(sys.argv[1])
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "/input/analysis.py"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        # 只传递分析解释器所需环境，不继承镜像附带的变量。
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    if completed.returncode:
        raise SystemExit(1)
    outputs = {}
    total = 0
    # 固定平面文件名，拒绝目录、符号链接、设备与跨文件硬链接。
    for name in spec["files"]:
        fd = os.open("/output/" + name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("只允许独立普通文件")
            content = handle.read(spec["max_bytes"] + 1)
        total += len(content)
        if total > spec["max_bytes"]:
            raise ValueError("产物超过限额")
        outputs[name] = base64.b64encode(content).decode("ascii")
    print(json.dumps({"outputs": outputs}, separators=(",", ":")))


if __name__ == "__main__":
    main()
