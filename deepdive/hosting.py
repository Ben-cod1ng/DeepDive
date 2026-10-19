"""Container API startup, including Railway volume ownership and dual-stack binding."""
import os
import socket
from pathlib import Path

from .config import Settings


def prepare_storage(settings):
    railway = bool(os.getenv("RAILWAY_ENVIRONMENT_ID"))
    if railway:
        if len(settings.api_token) < 32:
            raise ValueError("Railway requires a DEEPDIVE_API_TOKEN of at least 32 characters")
        if not os.getenv("RAILWAY_PRIVATE_DOMAIN"):
            raise ValueError("Railway private networking must be enabled")
        if os.getenv("RAILWAY_VOLUME_MOUNT_PATH") != "/data" or settings.data != Path("/data"):
            raise ValueError("Attach a Railway volume at /data and set DEEPDIVE_DATA_DIR=/data")

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # Railway mounts an empty volume as root. Change only its directory owner,
        # then permanently relinquish root before importing or serving the API.
        if settings.data != Path("/data") or settings.data.is_symlink():
            raise ValueError("Root startup is supported only for the /data directory")
        settings.data.mkdir(exist_ok=True)
        os.chown(settings.data, 10001, 10001)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)


def main():
    settings = Settings.load()
    prepare_storage(settings)
    port = int(os.getenv("PORT", "8000"))
    import uvicorn

    config = uvicorn.Config("deepdive.api:app", host="0.0.0.0", port=port,
                            workers=1, access_log=False, proxy_headers=False)
    if os.getenv("RAILWAY_PRIVATE_DOMAIN"):
        # One listener accepts both Railway's internal IPv6 and IPv4 probes.
        with socket.create_server(("::", port), family=socket.AF_INET6, dualstack_ipv6=True) as listener:
            uvicorn.Server(config).run(sockets=[listener])
    else:
        uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
