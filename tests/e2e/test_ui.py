"""真实浏览器回归。RUN_E2E=1 时执行，默认后端测试不依赖浏览器。"""

import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from backend import main

pytestmark = pytest.mark.skipif(os.getenv("RUN_E2E") != "1", reason="设置 RUN_E2E=1 执行浏览器测试")


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "browser.db"))
    monkeypatch.setattr(main, "ENABLE_DEMO_MODE", False)
    monkeypatch.setenv("LLM_API_KEY", "")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    service = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get(address + "/api/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
            else:
                raise RuntimeError("测试服务器启动失败")
        yield address
    finally:
        service.should_exit = True
        thread.join(timeout=10)


@pytest.fixture()
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        channel = os.getenv("E2E_CHANNEL")
        instance = p.chromium.launch(headless=True, **({"channel": channel} if channel else {}))
        yield instance
        instance.close()


def screenshot(page, name):
    from playwright.sync_api import expect

    root = Path(__file__).resolve().parents[2]
    directory = root / ("docs/screenshots" if os.getenv("UPDATE_SCREENSHOTS") == "1" else ".artifacts")
    directory.mkdir(parents=True, exist_ok=True)
    target = page.locator("#modal") if name == "version-diff.png" else page
    expect(page.locator("#toast")).to_be_hidden(timeout=10000)
    target.screenshot(path=str(directory / name), **({"full_page": True} if target == page else {}))


@pytest.fixture()
def model_service(monkeypatch):
    """本地协议替身验证真实 HTTP 调用路径；不是模型效果评分。"""
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from backend import demo

    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            payload = json.loads(body["messages"][-1]["content"])
            calls.append(payload)
            if "sources" in payload:
                source = payload["sources"][0]
                result = demo.extraction({"id": source["source_id"]})
            else:
                source = payload["new_sources"][0]
                result = demo.comparison(payload["baseline"], {"id": source["source_id"]})
            response = json.dumps(
                {
                    "choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    service = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    monkeypatch.setenv("LLM_API_KEY", "local-test-key")
    monkeypatch.setenv("LLM_BASE_URL", f"http://127.0.0.1:{service.server_port}/v1")
    monkeypatch.setenv("LLM_MODEL", "local-test-model")
    monkeypatch.setenv("LLM_RESPONSE_FORMAT", "prompt")
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield calls
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)


def go_tab(page, tab):
    page.locator(f".project-tabs [data-tab={tab}]").click()


def register_and_create(page, server, name="品牌官网", username="browser_user"):
    from playwright.sync_api import expect

    page.goto(server)
    page.locator("#btn-auth-toggle").click()
    page.locator("#auth-username").fill(username)
    page.locator("#auth-password").fill("browser-pass123")
    page.locator("#btn-auth-submit").click()
    expect(page.locator("#view-list")).to_be_visible()
    page.locator("#btn-new-project").click()
    page.locator("#new-name").fill(name)
    page.locator("#create-submit").click()
    expect(page.locator("#wb-project-name")).to_have_text(name)


def test_register_edit_confirm_and_compare(browser, server, model_service):
    from playwright.sync_api import expect

    context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
    page = context.new_page()
    errors, comparison_sources = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "request",
        lambda request: comparison_sources.append(request.post_data_json["source_ids"])
        if request.url.endswith("/compare")
        else None,
    )
    page.goto(server)
    expect(page.locator("#auth-title")).to_have_text("登录工作空间")
    assert page.locator("#llm-status").count() == 0
    assert page.locator("#btn-public-demo").count() == 0
    screenshot(page, "landing.png")
    register_and_create(page, server)
    expect(page.locator("#overview-requirements")).to_have_text("0")
    go_tab(page, "materials")
    page.locator("#source-label").fill("品牌官网需求沟通")
    page.locator("#source-input").fill(main.DEMO_SOURCE_001)
    page.locator("#btn-add-source").click()
    expect(page.locator("#source-count")).to_have_text("已选 1 / 1")
    screenshot(page, "materials.png")
    page.locator("#btn-analyze").click()
    expect(page.locator("#draft-zone")).to_be_visible()
    expect(page.locator("#candidate-list .card")).to_have_count(8)
    assert "local-test-model" not in page.locator("body").inner_text()
    assert "示例" not in page.locator("body").inner_text()
    page.get_by_label("需求描述 1", exact=True).fill("人工修订：展示产品并提供联系入口")
    page.get_by_label("删除需求 2", exact=True).click()
    screenshot(page, "requirements.png")
    page.locator("#btn-confirm").click()
    expect(page.locator("#modal-title")).to_have_text("确认需求版本")
    page.locator("#modal-yes").click()
    expect(page.locator("#wb-current-version")).to_have_text("当前版本 V1")
    page.locator("#version-list [data-action=view]").click()
    expect(page.locator("#modal-body")).to_contain_text("人工修订：展示产品并提供联系入口")
    expect(page.locator("#modal-body .card")).to_have_count(7)
    page.locator("#modal-close").click()
    go_tab(page, "requirements")
    expect(page.locator("#draft-title")).to_have_text("正式需求清单")
    expect(page.locator("#candidate-list .card")).to_have_count(7)
    go_tab(page, "materials")
    page.locator("#source-label").fill("会员与价格范围调整")
    page.locator("#source-input").fill(main.DEMO_SOURCE_002)
    page.locator("#btn-add-source").click()
    expect(page.locator("#source-count")).to_have_text("已选 1 / 2")
    selected = page.locator("[data-select]:checked").get_attribute("data-select")
    page.locator("#btn-material-compare").click()
    expect(page.locator("#changes-zone")).to_be_visible()
    expect(page.locator("#change-list .card")).to_have_count(3)
    assert comparison_sources == [[selected]]
    assert len(model_service) == 2
    assert model_service[-1]["new_sources"][0]["source_id"] == selected
    screenshot(page, "workbench.png")
    page.locator("[data-decision=accept]").first.click()
    page.locator("[data-decision=accept]").nth(1).click()
    page.locator("[data-decision=ignore]").nth(2).click()
    page.locator("#btn-apply").click()
    expect(page.locator("#wb-current-version")).to_have_text("当前版本 V2")
    page.locator("#version-list [data-action=diff]").first.click()
    expect(page.locator("#modal-body")).to_contain_text("V1 → V2")
    expect(page.locator("#modal-body")).to_contain_text("老客户登录")
    screenshot(page, "version-diff.png")
    page.locator("#modal-close").click()
    with page.expect_download() as download_info:
        page.locator("#btn-export").click()
    assert download_info.value.suggested_filename == "需求文档_V2.md"
    go_tab(page, "overview")
    expect(page.locator("#overview-requirements")).to_have_text("7")
    expect(page.locator("#overview-sources")).to_have_text("2")
    expect(page.locator("#overview-versions")).to_have_text("2")
    expect(page.locator("#overview-changes")).to_have_text("0")
    screenshot(page, "overview.png")
    page.reload()
    expect(page.locator("#project-overview")).to_be_visible()
    expect(page.locator("#wb-project-name")).to_have_text("品牌官网")
    page.locator("#nav-projects").click()
    expect(page.locator("#view-list")).to_be_visible()
    expect(page.locator("#stat-projects")).to_have_text("1")
    expect(page.locator("#stat-baselined")).to_have_text("1")
    page.locator("#project-search").fill("不存在的项目")
    expect(page.locator("#search-empty")).to_be_visible()
    page.locator("#project-search").fill("品牌")
    expect(page.locator("#project-list .project-item")).to_have_count(1)
    screenshot(page, "projects.png")
    page.locator("#project-list .project-item").click()
    expect(page.locator("#project-overview")).to_be_visible()
    expect(page.locator("#nav-projects .nav-icon")).to_have_count(1)
    assert not errors, errors
    context.close()


def test_manual_workflow_when_ai_unavailable_mobile(browser, server):
    from playwright.sync_api import expect

    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, locale="zh-CN")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    register_and_create(page, server, name="客户服务门户", username="mobile_user")
    go_tab(page, "materials")
    page.locator("#source-label").fill("配送查询需求沟通")
    page.locator("#source-input").fill("客户：支持按订单号查询配送进度。")
    page.locator("#btn-add-source").click()
    expect(page.locator("#source-count")).to_have_text("已选 1 / 1")
    page.locator("#btn-analyze").click()
    expect(page.locator("#action-feedback")).to_contain_text("智能整理暂不可用，材料已保存")
    text = page.locator("body").inner_text()
    assert "LLM_" not in text and "配置模型" not in text and "演示模式" not in text
    go_tab(page, "requirements")
    page.locator("#btn-add-requirement").click()
    page.locator("#requirement-title").fill("订单配送进度查询")
    page.locator("#requirement-description").fill("用户可通过订单号查询配送状态。")
    page.locator("#requirement-submit").click()
    expect(page.locator("#candidate-list .card")).to_have_count(1)
    page.locator("#btn-confirm").click()
    page.locator("#modal-yes").click()
    expect(page.locator("#wb-current-version")).to_have_text("当前版本 V1")
    go_tab(page, "requirements")
    expect(page.locator("#draft-title")).to_have_text("正式需求清单")
    expect(page.locator("#candidate-list")).to_contain_text("订单配送进度查询")
    page.locator("#btn-create-draft").click()
    expect(page.locator("#draft-title")).to_have_text("需求审核草稿")
    page.get_by_label("需求描述 1", exact=True).fill("支持订单号查询当前状态与配送轨迹。")
    page.locator("#btn-confirm").click()
    page.locator("#modal-yes").click()
    expect(page.locator("#wb-current-version")).to_have_text("当前版本 V2")
    go_tab(page, "overview")
    expect(page.locator("#overview-versions")).to_have_text("2")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    screenshot(page, "mobile-workspace.png")
    assert not errors, errors
    context.close()


def test_expired_session_returns_to_login(browser, server):
    from playwright.sync_api import expect

    context = browser.new_context()
    context.add_init_script("sessionStorage.setItem('token', 'expired-test-token')")
    page = context.new_page()
    page.goto(server)
    expect(page.locator("#view-auth")).to_be_visible()
    expect(page.locator("#toast")).to_contain_text("会话无效")
    assert page.evaluate("sessionStorage.getItem('token')") is None
    assert page.locator("#sidebar").is_hidden()
    assert page.locator("#llm-status").count() == 0
    context.close()
