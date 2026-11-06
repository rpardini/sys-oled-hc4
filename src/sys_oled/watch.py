"""Kernel change notifications checked with one non-blocking syscall per cycle (no periodic rescans)."""

import logging
import select
import socket
import sys

log = logging.getLogger(__name__)

RTMGRP_LINK = 0x1
RTMGRP_IPV4_IFADDR = 0x10
RTMGRP_IPV4_ROUTE = 0x40


class MountWatcher:
    """The kernel flags POLLPRI|POLLERR on an open mountinfo when the mount table changes."""

    def __init__(self, path: str):
        self.path = path
        self._file = None
        if not sys.platform.startswith("linux"):  # elsewhere regular files report POLLPRI; Linux-only semantics
            return
        try:
            self._file = open(path)  # noqa: SIM115 - kept open for the lifetime of the watcher
            self._file.read()
            self._poll = select.poll()
            self._poll.register(self._file, select.POLLPRI | select.POLLERR)
        except (OSError, AttributeError) as e:
            log.debug("mount watcher unavailable for %s: %s", path, e)
            self._file = None

    def changed(self) -> bool:
        if self._file is None:
            return False
        if not any(ev & (select.POLLPRI | select.POLLERR) for _, ev in self._poll.poll(0)):
            return False
        self._file.seek(0)
        self._file.read()  # re-arms the notification
        return True

    def read(self) -> str:
        with open(self.path) as f:
            return f.read()


class RouteWatcher:
    """NETLINK_ROUTE multicast for IPv4 route/address/link changes; drained non-blocking each cycle."""

    def __init__(self):
        self._sock = None
        try:
            sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW | socket.SOCK_NONBLOCK, socket.NETLINK_ROUTE)
            sock.bind((0, RTMGRP_LINK | RTMGRP_IPV4_IFADDR | RTMGRP_IPV4_ROUTE))
            self._sock = sock
        except (OSError, AttributeError) as e:
            log.debug("netlink route watcher unavailable: %s", e)

    def changed(self) -> bool:
        if self._sock is None:
            return False
        got = False
        while True:
            try:
                if not self._sock.recv(65536):
                    break
                got = True
            except BlockingIOError:
                break
            except OSError as e:  # ENOBUFS on overflow still means "something changed"
                log.debug("netlink recv: %s", e)
                got = True
                break
        return got
