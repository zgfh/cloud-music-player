"""
连接视图 Google Drive 来源交互测试：来源切换、凭据校验、授权流程、配置持久化
"""

import asyncio

from fakes import (
    FakeConfigManager,
    FakeNextcloudClient,
    FakePage,
    FakeViewManager,
    last_notification_text,
)

import nextcloud_music_player.gdrive_client as gdrive_module


def make_connection_view(page, app_context, view_manager):
    from nextcloud_music_player.views.connection_view import ConnectionView

    view = ConnectionView(page, app_context, view_manager)
    view.build()
    return view


def base_context(config_manager, nextcloud_client=None):
    return {
        "config_manager": config_manager,
        "nextcloud_client": nextcloud_client,
        "music_service": None,
        "lyrics_service": None,
    }


def make_view(config=None, client=None):
    page = FakePage()
    view = make_connection_view(
        page,
        base_context(FakeConfigManager(config), nextcloud_client=client),
        FakeViewManager(),
    )
    return page, view


def switch_to_gdrive(view):
    """模拟把来源切换器切到 Google 云盘（Flet 0.86 的 selected 是 list）"""
    view.source_selector.selected = ["gdrive"]
    view._on_source_type_changed(None)


def test_connect_button_has_stable_test_key():
    _, view = make_view()

    assert view.connect_button.key == "connect_button"


def fill_gdrive_credentials(
    view,
    client_id="cid.apps.googleusercontent.com",
    client_secret="GOCSPX-xxx",
    refresh_token="rt-ok",
):
    view.gdrive_client_id_input.value = client_id
    view.gdrive_client_secret_input.value = client_secret
    if refresh_token:
        view._gdrive_settings["refresh_token"] = refresh_token


class FakeReceiver:
    """LoopbackOAuthReceiver 替身：立即返回预设授权码"""

    redirect_uri = "http://127.0.0.1:54321"

    def start(self):
        pass

    def close(self):
        pass

    def wait_for_code(self, timeout=None):
        return "auth-code-123"


# === 来源切换 ===


async def test_source_switch_to_gdrive_toggles_forms_and_saves_choice():
    page, view = make_view()
    config = view.app_context["config_manager"]

    assert view.gdrive_form_card.visible is False
    assert view.nextcloud_form_card.visible is True

    switch_to_gdrive(view)

    assert view.gdrive_form_card.visible is True
    assert view.nextcloud_form_card.visible is False
    assert view.smb_form_card.visible is False
    assert view.title_text.value == "GOOGLE DRIVE"
    assert config.get("connection.source_type") == "gdrive"


async def test_build_with_gdrive_source_shows_authorized_state():
    page, view = make_view(
        {
            "connection": {
                "source_type": "gdrive",
                "gdrive": {
                    "client_id": "cid",
                    "client_secret": "sec",
                    "refresh_token": "kept-rt",
                    "default_sync_folder": "FOLDER1",
                    "sync_folder_labels": {"FOLDER1": "/音乐"},
                },
            }
        }
    )

    assert view.gdrive_form_card.visible is True
    assert view.gdrive_auth_status.value == "已授权 ✓"
    assert view.gdrive_sync_folder_input.value == "/音乐"


def test_gdrive_folder_selection_displays_name_but_saves_id(monkeypatch):
    from nextcloud_music_player.views.folder_selector import FolderSelector

    page, view = make_view(client=FakeNextcloudClient(directories={"/": []}))
    switch_to_gdrive(view)

    def select_music(selector, callback):
        selector.selected_display_path = "/音乐/周杰伦"
        callback("FOLDER2")

    monkeypatch.setattr(FolderSelector, "show_dialog", select_music)
    view._open_folder_selector(view.app_context["nextcloud_client"])

    config = view.app_context["config_manager"]
    assert view.gdrive_sync_folder_input.value == "/音乐/周杰伦"
    assert config.get("connection.gdrive.sync_folders") == ["FOLDER2"]
    assert config.get("connection.gdrive.sync_folder_labels") == {
        "FOLDER2": "/音乐/周杰伦"
    }


# === 连接前校验 ===


async def test_connect_gdrive_requires_credentials():
    page, view = make_view()
    switch_to_gdrive(view)
    fill_gdrive_credentials(view, client_id="", client_secret="")

    await view._connect_to_server(None)

    assert view.is_connected is False
    assert "Client ID" in last_notification_text(page)


async def test_connect_gdrive_requires_authorization():
    page, view = make_view()
    switch_to_gdrive(view)
    fill_gdrive_credentials(view, refresh_token="")  # 填了 ID/Secret 但未授权

    await view._connect_to_server(None)

    assert view.is_connected is False
    assert "授权" in last_notification_text(page)


# === 连接流程 ===


async def test_connect_gdrive_success_flow(monkeypatch):
    page, view = make_view()
    view_manager = view.view_manager
    client = FakeNextcloudClient(connect_ok=True)
    monkeypatch.setattr(gdrive_module, "GoogleDriveClient", lambda **kwargs: client)
    switch_to_gdrive(view)
    fill_gdrive_credentials(view)

    await view._connect_to_server(None)

    assert view.is_connected is True
    assert view.app_context["nextcloud_client"] is client
    assert view_manager.switched_to == ["file_list"]


def test_build_client_passes_tokens_and_callback(monkeypatch):
    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return FakeNextcloudClient(connect_ok=True)

    monkeypatch.setattr(gdrive_module, "GoogleDriveClient", fake_client)
    page, view = make_view()
    switch_to_gdrive(view)
    fill_gdrive_credentials(view)
    view._gdrive_settings["access_token"] = "at-1"
    view._gdrive_settings["token_expiry"] = 123.5

    view._build_client_from_form()

    assert captured["client_id"] == "cid.apps.googleusercontent.com"
    assert captured["client_secret"] == "GOCSPX-xxx"
    assert captured["refresh_token"] == "rt-ok"
    assert captured["access_token"] == "at-1"
    assert captured["token_expiry"] == 123.5
    assert captured["on_tokens_updated"] == view._persist_gdrive_tokens
    assert captured["api_base_url"] == ""  # 默认走官方端点


def test_build_client_passes_custom_api_base(monkeypatch):
    """设置页配置了自定义 API 地址时，客户端应改走该地址"""
    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return FakeNextcloudClient(connect_ok=True)

    monkeypatch.setattr(gdrive_module, "GoogleDriveClient", fake_client)
    page, view = make_view(
        {"connection": {"gdrive": {"api_base_url": "http://127.0.0.1:8931"}}}
    )
    switch_to_gdrive(view)
    fill_gdrive_credentials(view)

    view._build_client_from_form()

    assert captured["api_base_url"] == "http://127.0.0.1:8931"


# === 授权流程 ===


async def test_authorize_gdrive_completes_and_persists(monkeypatch):
    page, view = make_view()
    switch_to_gdrive(view)
    fill_gdrive_credentials(view, refresh_token="")  # 尚未授权
    view.gdrive_sync_folder_input.value = "FOLDER9"

    monkeypatch.setattr(gdrive_module, "LoopbackOAuthReceiver", lambda: FakeReceiver())
    monkeypatch.setattr(
        gdrive_module,
        "build_authorization_url",
        lambda client_id, redirect_uri, auth_url=None: "https://auth.example/consent",
    )
    monkeypatch.setattr(
        gdrive_module,
        "exchange_authorization_code",
        lambda *args, **kwargs: {
            "access_token": "at-new",
            "refresh_token": "rt-new",
            "expires_in": 3600,
        },
    )

    await view._authorize_gdrive()

    assert page.launched_urls == ["https://auth.example/consent"]
    assert view._gdrive_settings["refresh_token"] == "rt-new"
    assert view._gdrive_settings["access_token"] == "at-new"
    assert view.gdrive_auth_status.value == "已授权 ✓"
    assert view.gdrive_authorize_button.disabled is False

    config = view.app_context["config_manager"]
    assert config.get("connection.gdrive.client_id") == "cid.apps.googleusercontent.com"
    assert config.get("connection.gdrive.client_secret") == "GOCSPX-xxx"
    assert config.get("connection.gdrive.refresh_token") == "rt-new"
    assert config.get("connection.gdrive.default_sync_folder") == "FOLDER9"
    assert "授权成功" in last_notification_text(page)


async def test_authorize_gdrive_requires_credentials_first():
    page, view = make_view()
    switch_to_gdrive(view)
    fill_gdrive_credentials(view, client_id="", client_secret="")

    await view._authorize_gdrive()

    assert page.launched_urls == []
    assert "Client ID" in last_notification_text(page)


async def test_authorize_gdrive_cancelled_shows_error(monkeypatch):
    class CancellingReceiver(FakeReceiver):
        def wait_for_code(self, timeout=None):
            raise TimeoutError("等待浏览器完成授权超时")

    page, view = make_view()
    switch_to_gdrive(view)
    fill_gdrive_credentials(view, refresh_token="")

    monkeypatch.setattr(
        gdrive_module, "LoopbackOAuthReceiver", lambda: CancellingReceiver()
    )
    monkeypatch.setattr(
        gdrive_module,
        "build_authorization_url",
        lambda client_id, redirect_uri, auth_url=None: "https://auth.example/consent",
    )

    await view._authorize_gdrive()

    assert view._gdrive_settings.get("refresh_token", "") == ""
    assert view.gdrive_auth_status.value == "未授权"
    assert "授权失败" in last_notification_text(page)


async def test_authorize_gdrive_uses_custom_endpoints(monkeypatch):
    """自定义 API 地址时，授权页与令牌交换都改走派生端点"""
    captured = {}

    def fake_build(client_id, redirect_uri, auth_url=None):
        captured["auth_url"] = auth_url
        return "https://auth.example/consent"

    def fake_exchange(client_id, client_secret, code, redirect_uri, token_url=None):
        captured["token_url"] = token_url
        return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}

    page, view = make_view(
        {"connection": {"gdrive": {"api_base_url": "http://127.0.0.1:8931"}}}
    )
    switch_to_gdrive(view)
    fill_gdrive_credentials(view, refresh_token="")

    monkeypatch.setattr(gdrive_module, "LoopbackOAuthReceiver", lambda: FakeReceiver())
    monkeypatch.setattr(gdrive_module, "build_authorization_url", fake_build)
    monkeypatch.setattr(gdrive_module, "exchange_authorization_code", fake_exchange)

    await view._authorize_gdrive()

    assert captured["auth_url"] == "http://127.0.0.1:8931/auth"
    assert captured["token_url"] == "http://127.0.0.1:8931/token"
    assert view._gdrive_settings["refresh_token"] == "rt"


async def test_authorize_gdrive_does_not_background_device_e2e(monkeypatch):
    """设备 E2E 直接投递 loopback 回调，不应拉起浏览器使测试连接断开。"""
    page, view = make_view()
    switch_to_gdrive(view)
    fill_gdrive_credentials(view, refresh_token="")

    monkeypatch.setenv("FLET_TEST", "true")
    monkeypatch.setattr(gdrive_module, "LoopbackOAuthReceiver", lambda: FakeReceiver())
    monkeypatch.setattr(
        gdrive_module,
        "exchange_authorization_code",
        lambda *args, **kwargs: {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 3600,
        },
    )

    await view._authorize_gdrive()

    assert page.launched_urls == []
    assert view.gdrive_auth_status.value == "已授权 ✓"


async def test_authorize_gdrive_succeeds_with_real_receiver(monkeypatch):
    """回归（iOS 实测）：浏览器提示授权成功、返回应用却仍显示未授权。

    wait_for_code 返回时会关闭接收器，换取令牌时再取 receiver.redirect_uri
    抛"接收器尚未启动"，令牌交换从未发生。用真实 LoopbackOAuthReceiver 走
    完整流程：浏览器回调经真实 HTTP 到达，换令牌用的 redirect_uri 必须与
    授权请求一致且流程整体成功。"""
    import threading
    import time

    import requests as requests_lib

    page, view = make_view()
    switch_to_gdrive(view)
    fill_gdrive_credentials(view, refresh_token="")

    exchanged = {}

    def fake_build(client_id, redirect_uri, auth_url=None):
        def browser_callback():
            # 模拟浏览器授权完成后重定向回 loopback 接收器
            time.sleep(0.1)
            requests_lib.get(redirect_uri, params={"code": "code-live"}, timeout=5)

        threading.Thread(target=browser_callback, daemon=True).start()
        return "https://auth.example/consent"

    def fake_exchange(client_id, client_secret, code, redirect_uri, token_url=None):
        exchanged.update({"code": code, "redirect_uri": redirect_uri})
        return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}

    monkeypatch.setattr(gdrive_module, "build_authorization_url", fake_build)
    monkeypatch.setattr(gdrive_module, "exchange_authorization_code", fake_exchange)

    await view._authorize_gdrive()

    assert exchanged["code"] == "code-live"
    assert exchanged["redirect_uri"].startswith("http://127.0.0.1:")
    assert view._gdrive_settings["refresh_token"] == "rt"
    assert view.gdrive_auth_status.value == "已授权 ✓"
    assert "授权成功" in last_notification_text(page)


def test_persist_gdrive_tokens_respects_no_remember():
    page, view = make_view()
    view.remember_password_switch.value = False
    config = view.app_context["config_manager"]

    view._persist_gdrive_tokens(
        {"access_token": "at", "refresh_token": "rt", "token_expiry": 1.5}
    )

    assert view._gdrive_settings["refresh_token"] == "rt"  # 内存中保留
    assert config.get("connection.gdrive.refresh_token", "") == ""  # 不落盘
    assert config.get("connection.gdrive.access_token", "") == ""


def test_persist_gdrive_tokens_saves_when_remembering():
    page, view = make_view()
    view.remember_password_switch.value = True
    config = view.app_context["config_manager"]

    view._persist_gdrive_tokens(
        {"access_token": "at", "refresh_token": "rt", "token_expiry": 1.5}
    )

    assert config.get("connection.gdrive.refresh_token") == "rt"
    assert config.get("connection.gdrive.access_token") == "at"
    assert config.get("connection.gdrive.token_expiry") == 1.5


# === 配置保存 ===


def test_save_config_persists_gdrive_fields():
    page, view = make_view()
    switch_to_gdrive(view)
    fill_gdrive_credentials(view)
    view._gdrive_settings["access_token"] = "at-1"
    view._gdrive_settings["token_expiry"] = 99.0
    view.gdrive_sync_folder_input.value = "FOLDER7"

    view._save_config()

    config = view.app_context["config_manager"]
    assert config.get("connection.source_type") == "gdrive"
    assert config.get("connection.gdrive.client_id") == "cid.apps.googleusercontent.com"
    assert config.get("connection.gdrive.client_secret") == "GOCSPX-xxx"
    assert config.get("connection.gdrive.refresh_token") == "rt-ok"
    assert config.get("connection.gdrive.access_token") == "at-1"
    assert config.get("connection.gdrive.token_expiry") == 99.0
    assert config.get("connection.gdrive.default_sync_folder") == "FOLDER7"


def test_save_config_without_remember_blanks_gdrive_secrets():
    page, view = make_view()
    switch_to_gdrive(view)
    fill_gdrive_credentials(view, client_secret="super-secret")
    view.remember_password_switch.value = False

    view._save_config()

    config = view.app_context["config_manager"]
    assert config.get("connection.gdrive.client_id") == "cid.apps.googleusercontent.com"
    assert config.get("connection.gdrive.client_secret") == ""
    assert config.get("connection.gdrive.refresh_token") == ""


# === 文件夹浏览 ===


async def test_browse_folder_gdrive_opens_selector():
    page, view = make_view(client=FakeNextcloudClient(directories={"/": []}))
    switch_to_gdrive(view)
    view.gdrive_sync_folder_input.value = ""

    view._browse_folder(None)
    await asyncio.sleep(0.05)

    assert len(page.dialogs) == 1
