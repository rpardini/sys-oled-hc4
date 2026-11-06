"""In-cluster support using only the stdlib: node watch (config + status) and a lazily fetched status page."""

import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
BAD_REASONS = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "InvalidImageName"}
PENDING_GRACE = 300


class ApiError(Exception):
    def __init__(self, code: int | str, msg: str = ""):
        super().__init__(f"{code} {msg}".strip())
        self.code = code


def in_cluster(environ=os.environ, sa_dir: Path = SA_DIR) -> bool:
    return bool(environ.get("KUBERNETES_SERVICE_HOST")) and (sa_dir / "token").exists()


class KubeClient:
    def __init__(self, base_url: str | None = None, sa_dir: Path = SA_DIR, environ=os.environ):
        if base_url is None:
            host = environ["KUBERNETES_SERVICE_HOST"]
            host = f"[{host}]" if ":" in host else host
            base_url = f"https://{host}:{environ.get('KUBERNETES_SERVICE_PORT', '443')}"
        self.base_url = base_url.rstrip("/")
        self.sa_dir = sa_dir
        self.ctx = None
        if self.base_url.startswith("https") and (sa_dir / "ca.crt").exists():
            self.ctx = ssl.create_default_context(cafile=str(sa_dir / "ca.crt"))

    def _request(self, path: str, timeout: float):
        headers = {"Accept": "application/json"}
        token_file = self.sa_dir / "token"
        if token_file.exists():  # re-read every time: projected tokens rotate
            headers["Authorization"] = "Bearer " + token_file.read_text().strip()
        req = urllib.request.Request(self.base_url + path, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=self.ctx)
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, e.reason) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ApiError("conn", str(getattr(e, "reason", e))) from e

    def get(self, path: str, timeout: float = 3.0) -> dict:
        with self._request(path, timeout) as resp:
            return json.load(resp)

    def list_all(self, path: str, timeout: float = 3.0, limit: int = 500) -> list[dict]:
        items, cont = [], ""
        sep = "&" if "?" in path else "?"
        while True:
            q = f"{path}{sep}limit={limit}" + (f"&continue={urllib.parse.quote(cont)}" if cont else "")
            data = self.get(q, timeout)
            items += data.get("items", [])
            cont = data.get("metadata", {}).get("continue", "")
            if not cont:
                return items

    def stream(self, path: str, timeout: float):
        return self._request(path, timeout)


class NodeWatcher(threading.Thread):
    """One long-lived watch on our own node; blocks on the socket, so it is free while idle."""

    def __init__(
        self,
        client: KubeClient,
        node_name: str,
        on_change: Callable[[dict], None],
        stop: threading.Event,
        watch_timeout: int = 300,
    ):
        super().__init__(name="node-watch", daemon=True)
        self.client, self.node_name, self.on_change, self.stop = client, node_name, on_change, stop
        self.watch_timeout = watch_timeout
        self.node: dict | None = None
        self.rv: str | None = None
        self.relists = 0

    def fetch(self, timeout: float = 3.0) -> dict:
        node = self.client.get(f"/api/v1/nodes/{urllib.parse.quote(self.node_name)}", timeout)
        self._update(node)
        self.relists += 1
        return node

    def _update(self, node: dict) -> None:
        self.node = node
        self.rv = node.get("metadata", {}).get("resourceVersion")
        self.on_change(node)

    def watch_once(self) -> None:
        if self.rv is None:
            self.fetch()
        sel = urllib.parse.quote(f"metadata.name={self.node_name}")
        path = (
            f"/api/v1/nodes?fieldSelector={sel}&watch=1&allowWatchBookmarks=true"
            f"&resourceVersion={self.rv}&timeoutSeconds={self.watch_timeout}"
        )
        with self.client.stream(path, timeout=self.watch_timeout + 30) as resp:
            for raw in resp:
                if self.stop.is_set():
                    return
                if not raw.strip():
                    continue
                ev = json.loads(raw)
                kind, obj = ev.get("type"), ev.get("object", {})
                if kind == "ERROR":
                    if obj.get("code") == 410:  # resourceVersion too old: re-list
                        self.rv = None
                        return
                    raise ApiError(obj.get("code", "watch"), obj.get("message", ""))
                if kind == "BOOKMARK":
                    self.rv = obj.get("metadata", {}).get("resourceVersion", self.rv)
                elif kind in ("ADDED", "MODIFIED"):
                    self._update(obj)

    def run(self) -> None:
        backoff = 1.0
        while not self.stop.is_set():
            try:
                self.watch_once()
                backoff = 1.0
            except Exception as e:
                log.info("node watch: %s; retrying in %.0fs", e, backoff)
                if self.stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 60.0)


def _ts(s: str | None) -> float:
    if not s:
        return 0.0
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()


def node_ready(node: dict) -> bool:
    return any(
        c.get("type") == "Ready" and c.get("status") == "True" for c in node.get("status", {}).get("conditions", [])
    )


def node_status(node: dict) -> str:
    if node.get("spec", {}).get("unschedulable"):
        return "SchedDisabled"
    return "Ready" if node_ready(node) else "NotReady"


def pod_bad(pod: dict, now: float) -> bool:
    st = pod.get("status", {})
    phase = st.get("phase")
    if phase == "Failed":
        return True
    for cs in st.get("containerStatuses", []) + st.get("initContainerStatuses", []):
        if cs.get("state", {}).get("waiting", {}).get("reason") in BAD_REASONS:
            return True
    return phase == "Pending" and now - _ts(pod.get("metadata", {}).get("creationTimestamp")) > PENDING_GRACE


def short_version(git_version: str) -> str:
    parts = git_version.lstrip("v").split("+")[0].split(".")
    return "v" + ".".join(parts[:2]) if len(parts) >= 2 else git_version


def summarize(
    node: dict, nodes: list[dict], pods: list[dict], cluster_pods: list[dict] | None, version: str, now: float
) -> dict:
    active = [p for p in pods if p.get("status", {}).get("phase") != "Succeeded"]
    labels = node.get("metadata", {}).get("labels", {})
    out = {
        "node": node.get("metadata", {}).get("name", "?"),
        "version": version,
        "control_plane": "node-role.kubernetes.io/control-plane" in labels
        or "node-role.kubernetes.io/master" in labels,
        "status": node_status(node),
        "pods_running": sum(1 for p in active if p.get("status", {}).get("phase") == "Running"),
        "pods_total": len(active),
        "pods_bad": sum(1 for p in active if pod_bad(p, now)),
        "nodes_ready": sum(1 for n in nodes if node_ready(n)),
        "nodes_total": len(nodes),
        "cluster_bad": None,
    }
    if cluster_pods is not None:
        out["cluster_bad"] = sum(1 for p in cluster_pods if pod_bad(p, now))
    return out


class KubePage:
    """Fetched only when the page is about to be shown and the cache is older than `refresh`."""

    def __init__(self, client: KubeClient, node_name: str, watcher: NodeWatcher | None = None):
        self.client, self.node_name, self.watcher = client, node_name, watcher
        self.version: str | None = None
        self.data: dict = {}
        self.fetched_at = 0.0
        self.fetches = 0

    def get(self, refresh: float, cluster_pods: bool, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        if now - self.fetched_at < refresh and self.data:
            return self.data
        self.fetched_at = now
        self.fetches += 1
        try:
            if self.version is None:
                self.version = short_version(self.client.get("/version").get("gitVersion", "?"))
            node = (
                self.watcher.node
                if self.watcher and self.watcher.node
                else self.client.get(f"/api/v1/nodes/{urllib.parse.quote(self.node_name)}")
            )
            nodes = self.client.list_all("/api/v1/nodes")
            sel = urllib.parse.quote(f"spec.nodeName={self.node_name}")
            pods = self.client.list_all(f"/api/v1/pods?fieldSelector={sel}")
            cluster = self.client.list_all("/api/v1/pods") if cluster_pods else None
            self.data = summarize(node, nodes, pods, cluster, self.version, now)
        except (ApiError, ValueError, KeyError) as e:
            log.info("k8s page fetch failed: %s", e)
            code = getattr(e, "code", "?")
            self.data = {**self.data, "stale": bool(self.data), "error": code}
        return self.data
