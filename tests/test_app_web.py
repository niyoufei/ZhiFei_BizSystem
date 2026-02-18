"""
test_app_web.py - app/app.py 模块的单元测试

覆盖内容：
- 全局常量和配置
- load_projects / save_project 函数
- H 类的 HTTP 处理方法
- 认证和会话管理
- 路由处理
"""

import io
import os

# 在导入前设置环境
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAppConstants:
    """测试模块常量和配置"""

    def test_base_path_exists(self):
        """BASE 路径应该是有效的绝对路径"""
        from app import app

        assert os.path.isabs(app.BASE)

    def test_users_dict_exists(self):
        """USERS 字典应该包含用户"""
        from app import app

        assert isinstance(app.USERS, dict)
        assert len(app.USERS) > 0

    def test_admin_user_exists(self):
        """admin 用户应该存在且角色正确"""
        from app import app

        assert "admin" in app.USERS
        assert app.USERS["admin"]["role"] == "admin"

    def test_biz_user_exists(self):
        """biz 用户应该存在且角色正确"""
        from app import app

        assert "biz" in app.USERS
        assert app.USERS["biz"]["role"] == "biz"

    def test_sessions_is_dict(self):
        """SESSIONS 应该是字典"""
        from app import app

        assert isinstance(app.SESSIONS, dict)


class TestLoadProjects:
    """测试 load_projects 函数"""

    def test_load_projects_returns_list(self):
        """load_projects 应该返回列表"""
        from app import app

        # 创建临时 CSV 文件
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write("project_id,project_name,client_name\n")
            f.write("P001,测试项目,测试客户\n")
            temp_path = f.name

        try:
            # 临时替换 DATA 路径
            original_data = app.DATA
            app.DATA = temp_path

            result = app.load_projects()
            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0]["project_id"] == "P001"
        finally:
            app.DATA = original_data
            os.unlink(temp_path)

    def test_load_projects_empty_csv(self):
        """空 CSV 应该返回空列表"""
        from app import app

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write("project_id,project_name,client_name\n")
            temp_path = f.name

        try:
            original_data = app.DATA
            app.DATA = temp_path

            result = app.load_projects()
            assert result == []
        finally:
            app.DATA = original_data
            os.unlink(temp_path)


class TestSaveProject:
    """测试 save_project 函数"""

    def test_save_project_appends_row(self):
        """save_project 应该追加新行"""
        from app import app

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write("project_id,project_name,client_name\n")
            f.write("P001,项目1,客户1\n")
            temp_path = f.name

        try:
            original_data = app.DATA
            app.DATA = temp_path

            new_row = {"project_id": "P002", "project_name": "项目2", "client_name": "客户2"}
            app.save_project(new_row)

            # 验证保存结果
            result = app.load_projects()
            assert len(result) == 2
            assert result[1]["project_id"] == "P002"
        finally:
            app.DATA = original_data
            os.unlink(temp_path)


class TestHandlerHelpers:
    """测试 H 类的辅助方法"""

    def create_mock_handler(self):
        """创建模拟的 HTTP 处理器"""
        from app.app import H

        handler = mock.MagicMock(spec=H)
        handler.wfile = io.BytesIO()
        handler.headers = {}
        return handler

    def test_html_sends_response(self):
        """html 方法应该发送 HTML 响应"""
        from app.app import H

        handler = self.create_mock_handler()
        H.html(handler, "<h1>Test</h1>")

        handler.send_response.assert_called_once_with(200)
        handler.send_header.assert_called()
        handler.end_headers.assert_called_once()

    def test_redirect_sends_302(self):
        """redirect 方法应该发送 302 响应"""
        from app.app import H

        handler = self.create_mock_handler()
        H.redirect(handler, "/login")

        handler.send_response.assert_called_once_with(302)

    def test_redirect_handles_query_string(self):
        """redirect 应该正确处理查询字符串"""
        from app.app import H

        handler = self.create_mock_handler()
        H.redirect(handler, "/page?foo=bar")

        # 检查 Location header 被设置
        calls = handler.send_header.call_args_list
        location_set = any(call[0][0] == "Location" and "page" in call[0][1] for call in calls)
        assert location_set

    def test_get_user_no_cookie(self):
        """无 cookie 时 get_user 应该返回 None"""
        from app.app import H

        handler = self.create_mock_handler()
        handler.headers = mock.MagicMock()
        handler.headers.get.return_value = ""

        result = H.get_user(handler)
        assert result is None

    def test_get_user_with_valid_session(self):
        """有效会话时 get_user 应该返回用户信息"""
        from app import app
        from app.app import H

        # 设置会话
        app.SESSIONS["test_sid"] = {"role": "admin"}

        handler = self.create_mock_handler()
        handler.headers = mock.MagicMock()
        handler.headers.get.return_value = "sid=test_sid"

        result = H.get_user(handler)
        assert result is not None
        assert result["role"] == "admin"

        # 清理
        del app.SESSIONS["test_sid"]


class TestDoGET:
    """测试 do_GET 路由"""

    def create_handler_for_get(self, path, user=None):
        """创建用于 GET 请求的处理器"""
        from app import app
        from app.app import H

        handler = mock.MagicMock(spec=H)
        handler.path = path
        handler.wfile = io.BytesIO()
        handler.headers = mock.MagicMock()

        if user:
            sid = "test_session"
            app.SESSIONS[sid] = user
            handler.headers.get.return_value = f"sid={sid}"
        else:
            handler.headers.get.return_value = ""

        return handler

    def test_root_redirects_without_user(self):
        """未登录访问根路径应该重定向到登录"""
        from app.app import H

        handler = self.create_handler_for_get("/")
        handler.redirect = mock.MagicMock()
        handler.get_user = mock.MagicMock(return_value=None)

        H.do_GET(handler)

        handler.redirect.assert_called_once_with("/login")

    def test_root_with_user_runs_dashboard(self):
        """登录后访问根路径应该运行 dashboard 并返回 HTML"""
        from app import app
        from app.app import H

        handler = self.create_handler_for_get("/", user={"role": "admin"})
        handler.html = mock.MagicMock()
        handler.get_user = mock.MagicMock(return_value={"role": "admin"})
        handler.redirect = mock.MagicMock()

        # 创建临时 dashboard HTML 文件
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write("<html><body>Dashboard</body></html>")
            temp_html_path = f.name

        original_dash_html = app.DASH_HTML
        app.DASH_HTML = temp_html_path

        try:
            # Mock subprocess.run to avoid actually running dashboard.py
            with mock.patch("subprocess.run") as mock_run:
                H.do_GET(handler)

                # 验证 subprocess.run 被调用
                mock_run.assert_called_once()
                # 验证 html 方法被调用（返回 dashboard 内容）
                handler.html.assert_called_once()
                html_content = handler.html.call_args[0][0]
                assert "Dashboard" in html_content
        finally:
            app.DASH_HTML = original_dash_html
            os.unlink(temp_html_path)

    def test_login_page_renders(self):
        """登录页面应该正常渲染"""
        from app.app import H

        handler = self.create_handler_for_get("/login")
        handler.html = mock.MagicMock()

        H.do_GET(handler)

        handler.html.assert_called_once()
        html_content = handler.html.call_args[0][0]
        assert "登录" in html_content

    def test_new_page_requires_biz_role(self):
        """新增项目页面需要 biz 角色"""
        from app.app import H

        # 无用户
        handler = self.create_handler_for_get("/new")
        handler.html = mock.MagicMock()
        handler.get_user = mock.MagicMock(return_value=None)

        H.do_GET(handler)

        handler.html.assert_called_once()
        assert "无权限" in handler.html.call_args[0][0]

    def test_new_page_renders_for_biz(self):
        """biz 用户可以访问新增页面"""
        from app.app import H

        handler = self.create_handler_for_get("/new")
        handler.html = mock.MagicMock()
        handler.get_user = mock.MagicMock(return_value={"role": "biz"})

        H.do_GET(handler)

        handler.html.assert_called_once()
        html_content = handler.html.call_args[0][0]
        assert "新增项目" in html_content

    def test_unknown_path_returns_404(self):
        """未知路径应该返回 404"""
        from app.app import H

        handler = self.create_handler_for_get("/unknown")

        H.do_GET(handler)

        handler.send_error.assert_called_once_with(404)


class TestDoPOST:
    """测试 do_POST 路由"""

    def create_handler_for_post(self, path, data, user=None):
        """创建用于 POST 请求的处理器"""
        # 编码 POST 数据
        from urllib.parse import urlencode

        from app import app
        from app.app import H

        encoded = urlencode(data, doseq=True).encode()

        handler = mock.MagicMock(spec=H)
        handler.path = path
        handler.wfile = io.BytesIO()
        handler.rfile = io.BytesIO(encoded)
        handler.headers = mock.MagicMock()
        handler.headers.get.side_effect = lambda key, default="": {
            "Content-Length": str(len(encoded)),
            "Cookie": "sid=test_session" if user else "",
        }.get(key, default)

        if user:
            app.SESSIONS["test_session"] = user

        return handler

    def test_login_success(self):
        """正确凭据应该登录成功"""
        from app.app import H

        handler = self.create_handler_for_post("/login", {"u": "admin", "p": "admin123"})

        H.do_POST(handler)

        handler.send_response.assert_called_with(302)

    def test_login_failure(self):
        """错误凭据应该登录失败"""
        from app.app import H

        handler = self.create_handler_for_post("/login", {"u": "admin", "p": "wrong"})
        handler.html = mock.MagicMock()

        H.do_POST(handler)

        handler.html.assert_called_once()
        assert "登录失败" in handler.html.call_args[0][0]

    def test_save_requires_biz_role(self):
        """保存项目需要 biz 角色"""
        from app.app import H

        handler = self.create_handler_for_post("/save", {}, user={"role": "admin"})
        handler.html = mock.MagicMock()
        handler.get_user = mock.MagicMock(return_value={"role": "admin"})

        H.do_POST(handler)

        handler.html.assert_called_once()
        assert "无权限" in handler.html.call_args[0][0]

    def test_save_project_success(self):
        """biz 用户可以保存项目"""
        from app import app
        from app.app import H

        # 创建临时 CSV
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        ) as f:
            f.write(
                "project_id,project_name,client_name,order_time,due_time,deliver_time,status,amount_total,amount_received,editor,renderer,staff_wei,staff_ni\n"
            )
            temp_path = f.name

        try:
            original_data = app.DATA
            app.DATA = temp_path

            data = {
                "project_id": "P001",
                "project_name": "测试",
                "client_name": "客户",
                "order_time": "2026-01-01",
                "due_time": "2026-02-01",
                "status": "进行中",
                "amount_total": "10000",
                "amount_received": "5000",
                "editor": "张三",
                "renderer": "李四",
                "staff_wei": "是",
                "staff_ni": "是",
            }

            handler = self.create_handler_for_post("/save", data, user={"role": "biz"})
            handler.redirect = mock.MagicMock()
            handler.get_user = mock.MagicMock(return_value={"role": "biz"})

            H.do_POST(handler)

            handler.redirect.assert_called_once_with("/")

            # 验证数据已保存
            projects = app.load_projects()
            assert len(projects) == 1
            assert projects[0]["project_id"] == "P001"
        finally:
            app.DATA = original_data
            os.unlink(temp_path)


class TestMain:
    """测试 main 函数"""

    def test_main_starts_server(self):
        """main 应该启动 HTTP 服务器"""
        from app import app

        with mock.patch.object(app, "HTTPServer") as mock_server:
            mock_instance = mock.MagicMock()
            mock_server.return_value = mock_instance

            # 让 serve_forever 立即返回
            mock_instance.serve_forever.side_effect = KeyboardInterrupt

            try:
                app.main()
            except KeyboardInterrupt:
                pass

            mock_server.assert_called_once()
            assert mock_server.call_args[0][0] == ("127.0.0.1", 8888)


class TestRedirectSecurity:
    """测试重定向安全性"""

    def test_redirect_strips_external_urls(self):
        """redirect 应该过滤外部 URL"""
        from app.app import H

        handler = mock.MagicMock(spec=H)
        H.redirect(handler, "http://evil.com/phish")

        # 检查 Location 不包含外部域名
        calls = handler.send_header.call_args_list
        for call in calls:
            if call[0][0] == "Location":
                assert "evil.com" not in call[0][1]


class TestEdgeCases:
    """测试边界情况"""

    def test_empty_cookie(self):
        """空 cookie 应该正常处理"""
        from app.app import H

        handler = mock.MagicMock(spec=H)
        handler.headers = mock.MagicMock()
        handler.headers.get.return_value = ""

        result = H.get_user(handler)
        assert result is None

    def test_malformed_cookie(self):
        """畸形 cookie 应该正常处理"""
        from app import app
        from app.app import H

        handler = mock.MagicMock(spec=H)
        handler.headers = mock.MagicMock()
        handler.headers.get.return_value = "invalid_format"

        # 确保不会因为畸形 cookie 崩溃
        result = H.get_user(handler)
        # 返回 None 或 SESSIONS 中对应的值
        assert result is None or result in app.SESSIONS.values()
