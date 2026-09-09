"""Full packaged-app E2E for the Google Drive source against an in-process mock.

前置：在设置页「谷歌云盘」把 API 地址指向 mock（resolve_endpoints 会派生
/drive/v3、/auth、/token 三个端点）。授权无需真实浏览器：应用把授权页
指向 mock 的 /auth，mock 302 回 loopback；即使系统浏览器没有拉起，测试也
直接向固定优先端口上的 loopback 接收器投递授权码，两条路径等价。
"""

import asyncio

import flet.testing as ftt
import requests
from helpers import settle_network, tap_and_wait, wait_for
from mock_gdrive import AUTH_CODE, SONG_NAME

from nextcloud_music_player.gdrive_client import PREFERRED_LOOPBACK_PORTS

CLIENT_ID = "e2e-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-e2e-secret"


async def _set_custom_api_base(tester, base_url: str):
    """设置 → 谷歌云盘 → 填 API 地址并保存，随后回到连接页"""
    await tester.tap(await tester.find_by_text("设置"))
    await tester.pump_and_settle()
    await tester.tap(await tester.find_by_text("谷歌云盘"))
    field = await wait_for(tester, lambda: tester.find_by_key("gdrive_api_base"))
    assert field.count >= 1, "设置页应出现谷歌云盘 API 地址输入框"

    await tester.enter_text(field, base_url)
    await tester.pump_and_settle()
    await tester.tap(await tester.find_by_text("保存"))
    await settle_network(tester, 0.5)

    await tester.tap(await tester.find_by_text("连接"))
    await tester.pump_and_settle()


async def _goto_gdrive_form(tester):
    """从任意起始视图进入连接页的 Google 云盘表单"""
    await tester.tap(await tester.find_by_text("连接"))
    await tester.pump_and_settle()

    # 强制经历一次来源变更事件（与 Nextcloud/SMB e2e 同理），
    # 避免点击已选中的来源不产生 change 事件。
    await tester.tap(await tester.find_by_text("Nextcloud"))
    await tester.pump_and_settle()
    await tester.tap(await tester.find_by_text("Google 云盘"))
    # 来源切换会触发整视图重建，patch 是异步往返；pump_and_settle 可能在
    # patch 到达前返回（见 helpers.py 注释）。此时新表单虽挂载但布局未提交，
    # 隐藏卡片（Offstage）里的控件仍可被 finder 命中——必须额外等待，
    # 否则后续 tap 会落在旧布局的按钮上（warnIfMissed）。
    await settle_network(tester, 1.5)
    field = await wait_for(tester, lambda: tester.find_by_key("gdrive_client_id"))
    assert field.count >= 1, "连接页应出现 Google 云盘 Client ID 输入框"


def _deliver_to_loopback(params: dict, timeout: float = 1.0) -> bool:
    """向固定优先端口上的 loopback 接收器投递 query 参数，模拟浏览器回调"""
    for port in PREFERRED_LOOPBACK_PORTS:
        try:
            resp = requests.get(
                f"http://127.0.0.1:{port}/", params=params, timeout=timeout
            )
        except requests.RequestException:
            continue
        if resp.status_code == 200:
            return True
    return False


async def _deliver_to_loopback_with_retry(tester, params, timeout: float = 15.0):
    """轮询投递直到接收器就绪（点「授权」后接收器要一小段时间才启动）"""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        if _deliver_to_loopback(params):
            return True
        if asyncio.get_event_loop().time() >= deadline:
            return False
        await settle_network(tester, 0.4)


async def _fill_gdrive_credentials(tester):
    await tester.enter_text(await tester.find_by_key("gdrive_client_id"), CLIENT_ID)
    await tester.enter_text(await tester.find_by_key("gdrive_client_secret"), CLIENT_SECRET)
    await tester.pump_and_settle()


async def _tap_authorize_with_retry(tester, timeout: float = 25.0) -> bool:
    """点「授权」并以「正在等待浏览器授权」状态确认点击生效。

    与视图重建的 patch 竞态中，首击可能落在旧布局控件上（点击无效甚至
    误触「建立连接」）。每次重试都重新填写凭据——旧挂载若吞掉了输入，
    新挂载的空表单会被校验拦截，同样表现为无等待状态。
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        await _fill_gdrive_credentials(tester)
        authorize = await tester.find_by_key("gdrive_authorize")
        if authorize.count:
            await tester.tap(authorize)
        waiting = await wait_for(
            tester,
            lambda: tester.find_by_text_containing("正在等待浏览器授权"),
            timeout=3,
        )
        if waiting.count >= 1:
            return True
        if asyncio.get_event_loop().time() >= deadline:
            return False
        await settle_network(tester, 0.8)


async def _authorize(tester, server):
    """填凭据 → 点授权 → 投递授权码 → 等待已授权状态"""
    clicked = await _tap_authorize_with_retry(tester)
    assert clicked, "点击「授权」后未进入等待浏览器授权状态"

    delivered = await _deliver_to_loopback_with_retry(tester, {"code": AUTH_CODE})
    assert delivered, "未能向 loopback 接收器投递授权码（接收器未启动？）"

    status = await wait_for(
        tester, lambda: tester.find_by_text_containing("已授权"), timeout=15
    )
    assert status.count >= 1, "授权码投递后应显示已授权"
    # 授权码已在 mock 端换取令牌
    assert ("authorization_code", True) in server.token_requests()


async def test_gdrive_authorize_sync_download_and_playback(
    flet_app: ftt.FletTestApp, mock_gdrive_server
):
    """完整链路：自定义端点 → 授权 → 建连 → 同步 → 下载 → 播放"""
    tester = flet_app.tester
    await tester.pump_and_settle()

    await _set_custom_api_base(tester, mock_gdrive_server.url)
    await _goto_gdrive_form(tester)
    await _authorize(tester, mock_gdrive_server)

    await tap_and_wait(
        tester,
        lambda: tester.find_by_key("connect_button"),
        lambda: tester.find_by_text("同步"),
        timeout=15,
    )

    await tap_and_wait(
        tester,
        lambda: tester.find_by_text("同步"),
        lambda: tester.find_by_key(f"song:{SONG_NAME}"),
        timeout=15,
    )

    await tester.tap(await tester.find_by_key(f"song:{SONG_NAME}"))
    await tester.pump_and_settle()
    await tester.tap(await tester.find_by_text("播放"))
    await settle_network(tester, 1.0)

    playing = await wait_for(
        tester, lambda: tester.find_by_text("播放中"), timeout=15
    )
    assert playing.count >= 1, "播放启动后应显示播放中状态"
    assert (await tester.find_by_text(SONG_NAME)).count >= 1


async def test_gdrive_authorize_denied_shows_error(
    flet_app: ftt.FletTestApp, mock_gdrive_server
):
    """用户在授权页拒绝（error=access_denied）：提示授权失败且不签发令牌"""
    tester = flet_app.tester
    await tester.pump_and_settle()

    await _set_custom_api_base(tester, mock_gdrive_server.url)
    await _goto_gdrive_form(tester)

    clicked = await _tap_authorize_with_retry(tester)
    assert clicked, "点击「授权」后未进入等待浏览器授权状态"

    delivered = await _deliver_to_loopback_with_retry(tester, {"error": "access_denied"})
    assert delivered, "未能向 loopback 接收器投递授权错误"

    error = await wait_for(
        tester, lambda: tester.find_by_text_containing("授权失败"), timeout=15
    )
    assert error.count >= 1, "授权被拒应出现错误提示"
    assert (await tester.find_by_text_containing("已授权")).count == 0
    # 授权被拒时不应发生任何令牌签发
    assert mock_gdrive_server.token_requests() == []


async def test_gdrive_expired_token_refresh_failure_then_recovery(
    flet_app: ftt.FletTestApp, mock_gdrive_server
):
    """令牌过期 + 刷新被拒（invalid_grant）：连接失败；故障解除后可立即恢复"""
    tester = flet_app.tester
    await tester.pump_and_settle()

    # 签发的令牌 1 秒后过期（客户端有 60 秒提前刷新偏移 → 立即触发刷新）
    mock_gdrive_server.set_expires_in(1)
    await _set_custom_api_base(tester, mock_gdrive_server.url)
    await _goto_gdrive_form(tester)
    await _authorize(tester, mock_gdrive_server)

    mock_gdrive_server.set_token_error("invalid_grant")
    await tester.tap(await tester.find_by_key("connect_button"))
    error = await wait_for(
        tester, lambda: tester.find_by_text_containing("连接失败"), timeout=15
    )
    assert error.count >= 1, "刷新被拒应出现连接失败提示"
    assert (await tester.find_by_text("同步")).count == 0, "失败后不应进入已连接状态"
    # 客户端确实尝试过用 refresh_token 刷新且被拒
    failed_grants = [
        grant for grant, ok in mock_gdrive_server.token_requests() if not ok
    ]
    assert "refresh_token" in failed_grants

    # 恢复：解除令牌故障后无需重新授权，重连即成功
    mock_gdrive_server.set_token_error("")
    mock_gdrive_server.set_expires_in(3600)
    await tap_and_wait(
        tester,
        lambda: tester.find_by_key("connect_button"),
        lambda: tester.find_by_text("同步"),
        timeout=15,
    )
