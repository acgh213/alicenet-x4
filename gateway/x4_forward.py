#!/usr/bin/env python3
"""Forward one x4d message via Alicenet's existing Muse socket, without sudo.

Messages travel as stdin, never interpolated into the remote shell command.
CLI's successful ack proves Muse provider acceptance, not human consumption.
"""
import subprocess
import sys


def send(text):
    if not text or len(text.encode()) > 8192:
        return 1
    cmd = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
           '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=1',
           'cassie@192.168.18.53', '/usr/local/bin/musegadget send-user-msg -']
    try:
        result = subprocess.run(cmd, input=text.encode(), capture_output=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired):
        return 1
    return 0 if result.returncode == 0 else 1


if __name__ == '__main__':
    sys.exit(send(sys.argv[1]) if len(sys.argv) == 2 else 1)
