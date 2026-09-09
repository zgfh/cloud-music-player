"""Full packaged-app E2E against an in-process Mock Nextcloud/WebDAV server."""

import flet.testing as ftt
from helpers import settle_network, tap_and_wait, wait_for

from mock_nextcloud import PASSWORD, SONG_NAME, USERNAME


async def _open_nextcloud_form(tester):
    """从任意起始视图进入连接页的 Nextcloud 表单，等待 url 输入框挂载。"""
    await tester.tap(await tester.find_by_text("连接"))
    await tester.pump_and_settle()

    # 每个 flet_app 会话共享模拟器应用数据。先切到另一来源再切回来，
    # 强制触发 SegmentedButton 的 change 回调，避免点击已选项不产生事件。
    await tester.tap(await tester.find_by_text("SMB 共享"))
    await tester.pump_and_settle()
    await tester.tap(await tester.find_by_text("Nextcloud"))
    await tester.pump_and_settle()
    url_field = await wait_for(
        tester, lambda: tester.find_by_key("nextcloud_url"), timeout=5
    )
    assert url_field.count >= 1, "连接页应出现 Nextcloud 服务器地址输入框"


async def _fill_credentials(tester, url, username, password):
    await tester.enter_text(await tester.find_by_key("nextcloud_url"), url)
    await tester.enter_text(await tester.find_by_key("nextcloud_username"), username)
    await tester.enter_text(await tester.find_by_key("nextcloud_password"), password)
    await tester.enter_text(
        await tester.find_by_key("nextcloud_sync_folder"), "/music"
    )
    # enter_text 更新需先往返到 Python 端；否则紧接的 click
    # 可能读到上一个会话持久化的表单值。
    await tester.pump_and_settle()


async def test_nextcloud_sync_download_and_playback(
    flet_app: ftt.FletTestApp, mock_nextcloud_server
):
    tester = flet_app.tester
    await tester.pump_and_settle()

    await _open_nextcloud_form(tester)
    await _fill_credentials(
        tester, mock_nextcloud_server.url, USERNAME, PASSWORD
    )

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


async def test_nextcloud_wrong_password_shows_error_then_recovers(
    flet_app: ftt.FletTestApp, mock_nextcloud_server
):
    """密码错误：SnackBar 提示且不进入同步界面；改对凭据后可立即重连成功。"""
    tester = flet_app.tester
    await tester.pump_and_settle()

    await _open_nextcloud_form(tester)
    await _fill_credentials(
        tester, mock_nextcloud_server.url, USERNAME, "not-the-password"
    )

    await tester.tap(await tester.find_by_key("connect_button"))
    await settle_network(tester, 1.0)

    error = await wait_for(
        tester, lambda: tester.find_by_text_containing("连接失败")
    )
    assert error.count >= 1, "凭据错误应出现错误提示"
    assert (await tester.find_by_text("同步")).count == 0, "失败后不应进入已连接状态"

    # 错误后 UI 仍可用：修正密码重连即成功
    await tester.enter_text(
        await tester.find_by_key("nextcloud_password"), PASSWORD
    )
    await tap_and_wait(
        tester,
        lambda: tester.find_by_key("connect_button"),
        lambda: tester.find_by_text("同步"),
        timeout=15,
    )


async def test_nextcloud_unreachable_server_shows_error(flet_app: ftt.FletTestApp):
    """服务器不可达（端口关闭）：立即反馈连接错误，而不是无响应或崩溃。"""
    tester = flet_app.tester
    await tester.pump_and_settle()

    await _open_nextcloud_form(tester)
    await _fill_credentials(tester, "http://127.0.0.1:1", USERNAME, PASSWORD)

    await tester.tap(await tester.find_by_key("connect_button"))
    await settle_network(tester, 1.0)

    error = await wait_for(
        tester, lambda: tester.find_by_text_containing("连接失败")
    )
    assert error.count >= 1, "不可达服务器应出现连接失败提示"
    assert (await tester.find_by_text("同步")).count == 0
