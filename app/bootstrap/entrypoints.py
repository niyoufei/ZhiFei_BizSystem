from __future__ import annotations

import threading
import time
import webbrowser
from typing import Sequence

from app.bootstrap.app_factory import create_fastapi_app
from app.bootstrap.config import resolve_server_binding


def run_api(argv: Sequence[str] | None = None) -> None:
    binding = resolve_server_binding(argv)
    app = create_fastapi_app()

    def _open_browser() -> None:
        time.sleep(2.5)
        try:
            webbrowser.open(f"http://127.0.0.1:{binding.port}/")
        except Exception:
            pass

    if binding.open_browser:
        threading.Thread(target=_open_browser, daemon=True).start()
    print(f"浏览器将自动打开: http://127.0.0.1:{binding.port}/")
    print("按 Ctrl+C 停止")

    import uvicorn

    uvicorn.run(app, host=binding.host, port=binding.port, reload=False)


def run_cli() -> None:
    from app.interfaces.cli.runtime import app as cli_app

    cli_app()


def run_windows_secure_desktop() -> None:
    from app.interfaces.windows.secure_desktop import main

    main()
