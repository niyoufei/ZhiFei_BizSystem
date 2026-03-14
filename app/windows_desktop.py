from __future__ import annotations

import logging
import os
import socket
import threading
import time
import webbrowser
from pathlib import Path


def _configure_secure_desktop_env() -> Path:
    os.environ.setdefault("ZHIFEI_SECURE_DESKTOP", "1")
    local_appdata = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_appdata:
        app_root = Path(local_appdata) / "QingtianBidSystem"
    else:
        app_root = Path.cwd() / ".qingtian_secure_runtime"
    data_dir = app_root / "data"
    logs_dir = app_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("ZHIFEI_DATA_DIR", str(data_dir))
    os.environ.setdefault("HOST", "127.0.0.1")
    logging.basicConfig(
        filename=str(logs_dir / "desktop.log"),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return app_root


def _pick_port(preferred: int) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 10)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return preferred


def main() -> None:
    _configure_secure_desktop_env()
    from app.main import create_app

    preferred_port = int(os.environ.get("PORT", "8000"))
    port = _pick_port(preferred_port)
    os.environ["PORT"] = str(port)
    app = create_app()

    def _open_browser() -> None:
        time.sleep(2.2)
        try:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        except Exception:
            logging.getLogger(__name__).exception("failed_to_open_browser")

    threading.Thread(target=_open_browser, daemon=True).start()

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
