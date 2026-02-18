import csv
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.path.join(BASE, "data", "projects.csv")
DASH_PY = os.path.join(BASE, "scripts", "dashboard.py")
DASH_HTML = os.path.join(BASE, "output", "dashboard.html")

# ====== 简易账号（后期可接数据库）======
USERS = {
    "admin": {"pwd": "admin123", "role": "admin"},  # 你
    "biz": {"pwd": "biz123", "role": "biz"},  # 业务录入
}

SESSIONS = {}


def load_projects():
    with open(DATA, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_project(row):
    rows = load_projects()
    rows.append(row)
    with open(DATA, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)


class H(BaseHTTPRequestHandler):
    def html(self, s):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(s.encode("utf-8"))

    def redirect(self, url):
        parsed = urlparse(url)
        safe_url = parsed.path or "/"
        if parsed.query:
            safe_url += "?" + parsed.query
        self.send_response(302)
        self.send_header("Location", safe_url)
        self.end_headers()

    def get_user(self):
        cookie = self.headers.get("Cookie", "")
        sid = cookie.replace("sid=", "")
        return SESSIONS.get(sid)

    def do_GET(self):
        u = self.get_user()

        if self.path == "/":
            if not u:
                return self.redirect("/login")
            subprocess.run(["python3", DASH_PY], cwd=BASE)
            self.html(open(DASH_HTML, encoding="utf-8").read())
            return

        if self.path == "/login":
            self.html(
                """
<h2>智飞施组 · 登录</h2>
<form method=post>
账号：<input name=u><br>
密码：<input type=password name=p><br>
<button>登录</button>
</form>
"""
            )
            return

        if self.path == "/new":
            if not u or u["role"] != "biz":
                return self.html("无权限")
            self.html(
                """
<h3>新增项目</h3>
<form method=post action="/save">
项目编号：<input name=project_id><br>
项目名称：<input name=project_name><br>
客户：<input name=client_name><br>
接单日期：<input name=order_time><br>
交稿日期：<input name=due_time><br>
状态：<input name=status value="进行中"><br>
总金额：<input name=amount_total><br>
已收：<input name=amount_received value="0"><br>
编制：<input name=editor><br>
效果图：<input name=renderer><br>
魏：<input name=staff_wei value="是"><br>
倪：<input name=staff_ni value="是"><br>
<button>保存</button>
</form>
"""
            )
            return

        self.send_error(404)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        d = parse_qs(self.rfile.read(content_length).decode())
        if self.path == "/login":
            u, p = d.get("u", [""])[0], d.get("p", [""])[0]
            if u in USERS and USERS[u]["pwd"] == p:
                sid = u
                SESSIONS[sid] = USERS[u]
                self.send_response(302)
                self.send_header("Set-Cookie", f"sid={sid}")
                self.send_header("Location", "/")
                self.end_headers()
                return
            return self.html("登录失败")

        if self.path == "/save":
            u = self.get_user()
            if not u or u["role"] != "biz":
                return self.html("无权限")
            save_project(
                {
                    "project_id": d["project_id"][0],
                    "project_name": d["project_name"][0],
                    "client_name": d["client_name"][0],
                    "order_time": d["order_time"][0],
                    "due_time": d["due_time"][0],
                    "deliver_time": "",
                    "status": d["status"][0],
                    "amount_total": d["amount_total"][0],
                    "amount_received": d["amount_received"][0],
                    "editor": d["editor"][0],
                    "renderer": d["renderer"][0],
                    "staff_wei": d["staff_wei"][0],
                    "staff_ni": d["staff_ni"][0],
                }
            )
            return self.redirect("/")


def main():
    print("Web APP 启动：http://127.0.0.1:8888")
    HTTPServer(("127.0.0.1", 8888), H).serve_forever()


if __name__ == "__main__":
    main()
