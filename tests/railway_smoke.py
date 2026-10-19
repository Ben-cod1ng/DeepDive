"""Exercise Railway-style root volumes and networking in disposable Docker resources.

Run: python tests/railway_smoke.py --image deepdive:local
Requires a built image and Docker. Never loads .env or calls Mesh.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=120).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="deepdive:local")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    name = "deepdive-railway-test-" + uuid.uuid4().hex[:12]
    api, ui, volume, network = (name + suffix for suffix in ("-api", "-ui", "-data", "-net"))
    token = uuid.uuid4().hex + uuid.uuid4().hex
    host = "deepdive.railway.internal"
    containers = []
    volume_created = network_created = False

    def request(base, path, *, method="GET", data=None, authenticated=True, hostname=host):
        headers = {"Host": hostname, "Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.load(response)

    def address(container, port):
        bindings = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", container))
        return "http://127.0.0.1:" + bindings[str(port) + "/tcp"][0]["HostPort"]

    def wait_ready(base, path, json_body=True):
        deadline = time.monotonic() + 180
        last_error = None
        while time.monotonic() < deadline:
            try:
                if json_body:
                    request(base, path)
                else:
                    with urllib.request.urlopen(base + path, timeout=3) as response:
                        assert response.status == 200
                return
            except (OSError, ValueError) as exc:
                last_error = exc
                time.sleep(1)
        raise AssertionError("Container did not become ready: " + path) from last_error

    try:
        docker("volume", "create", volume)
        volume_created = True
        docker("network", "create", network)
        network_created = True
        docker("run", "-d", "--name", api, "--user", "0:0", "--network", network,
               "--network-alias", host, "-p", "127.0.0.1::8000",
               "--mount", f"type=volume,source={volume},target=/data,volume-nocopy",
               "-e", "RAILWAY_ENVIRONMENT_ID=test", "-e", "RAILWAY_PRIVATE_DOMAIN=" + host,
               "-e", "RAILWAY_VOLUME_MOUNT_PATH=/data", "-e", "DEEPDIVE_API_TOKEN=" + token,
               "-e", "PORT=8000", args.image)
        containers.append(api)
        base = address(api, 8000)
        print("Checking API startup with a fresh root-owned volume...", flush=True)
        wait_ready(base, "/ready")
        assert request(base, "/health", authenticated=False, hostname="healthcheck.railway.app")["status"] == "ok"
        for path, authenticated, hostname, expected in (
            ("/sessions", False, host, 401), ("/health", False, "untrusted.example", 400),
        ):
            try:
                request(base, path, authenticated=authenticated, hostname=hostname)
            except urllib.error.HTTPError as exc:
                assert exc.code == expected
            else:
                raise AssertionError("Request crossed the access boundary")
        docker("exec", api, "python", "-c", "from pathlib import Path; import urllib.request; "
               "s=Path('/proc/1/status').read_text(); "
               "assert next(x for x in s.splitlines() if x.startswith('Uid:')).split()[1:] == ['10001']*4; "
               "assert Path('/data').stat().st_uid == 10001; "
               "r=urllib.request.Request('http://[::1]:8000/health', headers={'Host':'" + host + "'}); "
               "assert urllib.request.urlopen(r,timeout=5).status == 200")
        session = request(base, "/sessions", method="POST", data=b'{"name":"Railway persistence check"}')
        query = urllib.parse.urlencode({"session_id": session["id"], "filename": "demo.txt"})
        req = urllib.request.Request(base + "/documents?" + query,
                                     data=b"Public synthetic deployment document.",
                                     headers={"Host": host, "Authorization": "Bearer " + token,
                                              "Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=30) as response:
            document = json.load(response)
        docker("restart", "--time", "30", api)
        base = address(api, 8000)
        print("Checking persistence after restart...", flush=True)
        wait_ready(base, "/ready")
        assert request(base, "/sessions/" + session["id"])["name"] == session["name"]
        documents = request(base, "/documents?" + urllib.parse.urlencode({"session_id": session["id"]}))
        assert any(d["id"] == document["id"] for d in documents["documents"])
        docker("run", "-d", "--name", ui, "--network", network, "--no-healthcheck", "-p", "127.0.0.1::8501",
               "-e", "RAILWAY_ENVIRONMENT_ID=test", "-e", "DEEPDIVE_API_URL=http://" + host + ":8000",
               "-e", "DEEPDIVE_API_TOKEN=" + token, "-e", "DEEPDIVE_DEMO_ACCESS_CODE=" + uuid.uuid4().hex,
               args.image, "python", "-m", "streamlit", "run", "streamlit_app.py",
               "--server.address=0.0.0.0", "--server.port=8501")
        containers.append(ui)
        wait_ready(address(ui, 8501), "/_stcore/health", json_body=False)
        docker("exec", ui, "python", "-c", "from deepdive.client import Client; "
               "assert Client().request('GET','/ready')['status']=='ready'")
        result = {"passed": True, "environment": "local Docker emulating Railway",
                  "image_id": docker("image", "inspect", "--format", "{{.Id}}", args.image),
                  "checks": ["fresh root-owned volume", "API drops to UID 10001", "IPv4 and IPv6",
                             "host and token boundaries", "document and session survive restart",
                             "Streamlit health", "frontend private-network API access"],
                  "mesh_calls": 0, "hosted_url_validated": False}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
    except Exception:
        for container in containers:
            subprocess.run(["docker", "logs", "--tail", "60", container], timeout=30, check=False)
        raise
    finally:
        # Delete only resources this invocation created; never touch the user's stack.
        for container in reversed(containers):
            docker("rm", "-f", container)
        if network_created:
            docker("network", "rm", network)
        if volume_created:
            docker("volume", "rm", volume)


if __name__ == "__main__":
    main()
