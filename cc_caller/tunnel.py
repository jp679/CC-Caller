"""Public tunnels: cloudflared (default) or ngrok."""
import os
import re
import subprocess
import time


def start_tunnel(port: int, method: str) -> tuple:
    """Start a tunnel and return (public_url, cleanup_fn)."""
    if method == "cloudflare":
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
            stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        start = time.time()
        while time.time() - start < 15:
            line = proc.stderr.readline()
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                url = m.group(0)
                return url, lambda: proc.terminate()
        proc.terminate()
        raise RuntimeError("Cloudflare tunnel failed to start")
    else:
        from pyngrok import ngrok
        authtoken = os.getenv("NGROK_AUTHTOKEN", "")
        if authtoken:
            ngrok.set_auth_token(authtoken)
        domain = os.getenv("NGROK_DOMAIN", "")
        kwargs = {"addr": port, "proto": "http"}
        if domain:
            kwargs["domain"] = domain
        tunnel = ngrok.connect(**kwargs)
        url = tunnel.public_url
        return url, lambda: ngrok.disconnect(url)
