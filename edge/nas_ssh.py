#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 paramiko 在 NAS 上执行命令 / 上传文件。"""
import os
import sys

import paramiko

HOST = os.environ.get("NAS_HOST", "192.168.1.43")
USER = os.environ.get("NAS_USER", "FELIX")
# 密码不落盘：从环境变量 NAS_PASSWORD 读取。
PWD = os.environ.get("NAS_PASSWORD", "")


def connect(timeout=15):
    if not PWD:
        raise SystemExit("缺少 NAS 密码：请设置环境变量 NAS_PASSWORD")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=timeout,
              look_for_keys=False, allow_agent=False)
    return c


def run(cmd, timeout=120, sudo=False):
    """执行命令并返回 (stdout, stderr, exit_code)。

    sudo=True 时用 `sudo -S` 并通过 stdin 喂密码，避免 shell 管道问题。
    """
    c = connect()
    try:
        if sudo:
            cmd = "sudo -S -p '' sh -c %s" % _shq(cmd)
        _in, out, err = c.exec_command(cmd, timeout=timeout)
        if sudo:
            _in.write(PWD + "\n")
            _in.flush()
        so = out.read().decode("utf-8", "ignore")
        se = err.read().decode("utf-8", "ignore")
        rc = out.channel.recv_exit_status()
        return so, se, rc
    finally:
        c.close()


def _shq(s):
    """把命令用单引号安全包裹，供 sh -c 使用。"""
    return "'" + s.replace("'", "'\\''") + "'"


def upload(local, remote, mode=None):
    c = connect()
    try:
        sftp = c.open_sftp()
        sftp.put(local, remote)
        if mode:
            sftp.chmod(remote, mode)
        sftp.close()
        return True
    finally:
        c.close()


if __name__ == "__main__":
    if sys.argv[1] == "run":
        use_sudo = len(sys.argv) > 3 and sys.argv[3] == "sudo"
        so, se, rc = run(sys.argv[2], sudo=use_sudo)
        if so:
            print(so)
        if se:
            print("[stderr]", se, file=sys.stderr)
        sys.exit(rc)
    elif sys.argv[1] == "upload":
        upload(sys.argv[2], sys.argv[3],
               int(sys.argv[4], 8) if len(sys.argv) > 4 else None)
        print("uploaded:", sys.argv[2], "->", sys.argv[3])
    else:
        print(__doc__)
        sys.exit(1)
