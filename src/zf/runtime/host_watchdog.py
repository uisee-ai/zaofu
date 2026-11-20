"""Main-loop notifications to an independently managed systemd watchdog.

No timer thread: a hung controller or tick service must stop notifications.
The service manager fences the old main process before restarting its unit.
"""
import os
import socket


def notify_host_watchdog(guard, *, ready: bool = False) -> bool:
    address = os.environ.get('NOTIFY_SOCKET', '')
    try:
        if (not address.startswith(('/', '@')) or int(os.environ.get('WATCHDOG_USEC', '0')) <= 0
                or int(os.environ.get('WATCHDOG_PID', '0')) != os.getpid()
                or not guard.heartbeat()):
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
            channel.settimeout(0.2)
            channel.connect('\0' + address[1:] if address.startswith('@') else address)
            channel.send(b'READY=1\nWATCHDOG=1' if ready else b'WATCHDOG=1')
        return True
    except (OSError, ValueError):
        return False
