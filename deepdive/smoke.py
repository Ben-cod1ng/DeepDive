"""Exercise deployed HTTP persistence without an AI call or synthetic research."""
import urllib.request
from .client import Client


def main():
    client = Client()
    session_id = None
    try:
        assert client.request("GET", "/ready")["status"] == "ready"
        session = client.request("POST", "/sessions", json={"name": "Deployment smoke check"})
        session_id = session["id"]
        client.request("PATCH", "/sessions/" + session_id, json={"name": "Deployment smoke verified"})
        result = client.request("GET", "/sessions/" + session_id + "/export")
        assert result["session"]["name"] == "Deployment smoke verified"
        memory = client.request("POST", "/memory/preferences", params={"session_id": session_id},
                                json={"text": "Smoke-test preference"})
        assert memory["id"]
        document = client.upload("smoke.txt", b"Deployment document: durable local evidence.", session_id=session_id)
        listed = client.request("GET", "/documents", params={"session_id": session_id})["documents"]
        assert any(d["id"] == document["id"] and d["chunk_count"] == 1 for d in listed)
        client.request("DELETE", "/documents/" + document["id"], params={"session_id": session_id})
        assert client.request("GET", "/documents", params={"session_id": session_id})["documents"] == []
        with urllib.request.urlopen("http://127.0.0.1:8501/_stcore/health", timeout=5) as response:
            assert response.status == 200
        print("API persistence, document ingestion/removal, memory, export, and UI health checks passed; no AI requests made.")
    finally:
        if session_id:
            client.request("DELETE", "/sessions/" + session_id)


if __name__ == "__main__":
    main()
