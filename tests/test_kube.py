import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import pytest

from sys_oled import kube

NOW = time.time()
OLD = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 3600))
NEW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 10))


def node(name, ready=True, unschedulable=False, labels=None, rv="1"):
    return {
        "metadata": {"name": name, "labels": labels or {}, "annotations": {}, "resourceVersion": rv},
        "spec": {"unschedulable": unschedulable},
        "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]},
    }


def pod(phase, created=OLD, waiting=None):
    st = {"phase": phase}
    if waiting:
        st["containerStatuses"] = [{"state": {"waiting": {"reason": waiting}}}]
    return {"metadata": {"creationTimestamp": created}, "status": st}


NODE_PODS = [
    pod("Running"),
    pod("Running"),
    pod("Running", waiting="CrashLoopBackOff"),
    pod("Pending", OLD),
    pod("Pending", NEW),
    pod("Failed"),
    pod("Succeeded"),
]
CLUSTER_PODS = [*NODE_PODS, pod("Running", waiting="ImagePullBackOff"), pod("Running")]


class FakeAPI(BaseHTTPRequestHandler):
    state: ClassVar[dict] = {}

    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        st = self.state
        st.setdefault("requests", []).append(self.path)
        st["auth"] = self.headers.get("Authorization")
        if st.get("forbidden"):
            return self.send_json({"kind": "Status", "code": 403}, 403)
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/version":
            return self.send_json({"gitVersion": "v1.34.2+k3s1"})
        if url.path == "/api/v1/nodes/hc4-1":
            return self.send_json(st["node"])
        if url.path == "/api/v1/nodes" and "watch" in q:
            self.send_response(200)
            self.end_headers()
            for ev in st.get("events", []):
                self.wfile.write((json.dumps(ev) + "\n").encode())
            return None
        if url.path == "/api/v1/nodes":
            return self.send_json({"items": [st["node"], node("n2"), node("n3", ready=False)]})
        if url.path == "/api/v1/pods":
            items = NODE_PODS if "fieldSelector" in q else CLUSTER_PODS
            page = int(q.get("continue", ["0"])[0])
            limit = 3  # force paging regardless of requested limit
            chunk = items[page * limit : (page + 1) * limit]
            more = (page + 1) * limit < len(items)
            return self.send_json({"items": chunk, "metadata": {"continue": str(page + 1) if more else ""}})
        return self.send_json({"code": 404}, 404)


@pytest.fixture
def api(tmp_path):
    state = {"node": node("hc4-1", labels={"node-role.kubernetes.io/control-plane": "true"})}
    handler = type("H", (FakeAPI,), {"state": state})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    (tmp_path / "token").write_text("tok123\n")
    client = kube.KubeClient(f"http://127.0.0.1:{srv.server_address[1]}", sa_dir=tmp_path)
    yield client, state
    srv.shutdown()


def test_summarize_and_paging(api):
    client, state = api
    page = kube.KubePage(client, "hc4-1")
    data = page.get(60, True, NOW)
    assert data == {
        "node": "hc4-1",
        "version": "v1.34",
        "control_plane": True,
        "status": "Ready",
        "pods_running": 3,
        "pods_total": 6,
        "pods_bad": 3,
        "nodes_ready": 2,
        "nodes_total": 3,
        "cluster_bad": 4,
    }
    assert state["auth"] == "Bearer tok123"
    assert any("continue=2" in r for r in state["requests"])


def test_fetch_only_when_due(api):
    client, state = api
    page = kube.KubePage(client, "hc4-1")
    page.get(60, False, NOW)
    page.get(60, False, NOW + 30)
    assert page.fetches == 1
    page.get(60, False, NOW + 61)
    assert page.fetches == 2
    assert not any(r == "/api/v1/pods?limit=500" for r in state["requests"])  # cluster_pods off


def test_node_status_variants():
    assert kube.node_status(node("a", ready=False)) == "NotReady"
    assert kube.node_status(node("a", unschedulable=True)) == "SchedDisabled"
    assert kube.short_version("v1.31.4-eks-abc") == "v1.31"


def test_errors_keep_stale_data(api):
    client, state = api
    page = kube.KubePage(client, "hc4-1")
    good = page.get(60, False, NOW)
    state["forbidden"] = True
    data = page.get(60, False, NOW + 100)
    assert data["error"] == 403 and data["stale"] and data["pods_total"] == good["pods_total"]
    fresh = kube.KubePage(client, "hc4-1").get(60, False, NOW)
    assert fresh == {"stale": False, "error": 403}


def test_connection_refused():
    page = kube.KubePage(kube.KubeClient("http://127.0.0.1:9", sa_dir=kube.Path("/nonexistent")), "x")
    assert page.get(60, False, NOW)["error"] == "conn"


def test_watch_applies_changes_and_relists_on_410(api):
    client, state = api
    seen = []
    w = kube.NodeWatcher(client, "hc4-1", lambda n: seen.append(n["metadata"]["labels"]), threading.Event())
    w.fetch()
    state["events"] = [
        {"type": "BOOKMARK", "object": {"metadata": {"resourceVersion": "5"}}},
        {"type": "MODIFIED", "object": node("hc4-1", labels={"sys-oled/display.rotate": "0"}, rv="6")},
        {"type": "ERROR", "object": {"code": 410, "message": "too old"}},
    ]
    w.watch_once()
    assert seen[-1] == {"sys-oled/display.rotate": "0"}
    assert w.rv is None  # 410 -> next watch_once re-lists
    state["events"] = []
    w.watch_once()
    assert w.relists == 2
    watch_req = next(r for r in state["requests"] if "watch=1" in r)
    assert "fieldSelector=metadata.name%3Dhc4-1" in watch_req and "resourceVersion=1" in watch_req


def test_app_reads_node_config_before_opening_display(api, tmp_path):
    from sys_oled.app import App
    from sys_oled.config import ConfigLoader

    client, state = api
    state["node"]["metadata"]["labels"]["sys-oled/display.model"] = "sh1106"
    stop = threading.Event()
    app = App(ConfigLoader([], environ={}), stop=stop, environ={"NODE_NAME": "hc4-1"})
    app.setup_kube(client)
    stop.set()
    app.apply_pending()
    assert app.cfg.display.model == "sh1106"
    assert app.cfg.sources["display.model"] == "node-label:sys-oled/display.model"
