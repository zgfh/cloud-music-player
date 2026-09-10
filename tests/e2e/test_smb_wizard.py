"""端到端 UI 集成测试（flet test）：SMB 向导式连接入口

回归点：
1. SMB 空地址点「建立连接」→ SnackBar 提示（此前版本曾静默无反馈）
2. 填地址后点「建立连接」→ 弹出向导认证对话框（访客/用户登录）

运行方式：flet test --tests-dir tests/e2e -k smb
"""

import flet.testing as ftt
from helpers import wait_for


async def _goto_smb(tester):
    await tester.tap(await tester.find_by_text("连接"))
    await tester.pump_and_settle()

    # 强制经历 Nextcloud -> SMB，确保即使模拟器保留了上个测试的来源，
    # 也会触发一次确定的来源变更事件。
    await tester.tap(await tester.find_by_text("Nextcloud"))
    await tester.pump_and_settle()
    await tester.tap(await tester.find_by_text("SMB 共享"))
    await tester.pump_and_settle()
    host_input = await wait_for(
        tester, lambda: tester.find_by_key("smb_host"), timeout=5
    )
    assert host_input.count >= 1, "连接页应出现 SMB 服务器地址输入框"


async def test_smb_empty_address_shows_feedback(flet_app: ftt.FletTestApp):
    """空地址直接点击连接：必须出现提示，不能无响应"""
    tester = flet_app.tester
    await tester.pump_and_settle()
    await _goto_smb(tester)

    host_input = await tester.find_by_key("smb_host")
    await tester.enter_text(host_input, "")
    await tester.pump_and_settle()

    connect = await wait_for(tester, lambda: tester.find_by_key("connect_button_smb"))
    assert connect.count >= 1, "应找到当前 SMB 表单的连接按钮"
    await tester.tap(connect.last)

    feedback = await wait_for(
        tester, lambda: tester.find_by_text_containing("请先输入 SMB 服务器地址")
    )
    assert feedback.count >= 1, "空地址点击连接应有 SnackBar 提示"


async def test_smb_address_opens_wizard_dialog(flet_app: ftt.FletTestApp):
    """填入地址点击连接：弹出向导认证对话框（访客/用户登录）"""
    tester = flet_app.tester
    await tester.pump_and_settle()
    await _goto_smb(tester)

    host_input = await tester.find_by_key("smb_host")
    await tester.enter_text(host_input, "192.0.2.1")
    await tester.pump_and_settle()

    connect = await wait_for(tester, lambda: tester.find_by_key("connect_button_smb"))
    assert connect.count >= 1, "应找到当前 SMB 表单的连接按钮"
    await tester.tap(connect.last)

    # 向导第一步：身份选择 + 连接按钮
    guest = await wait_for(tester, lambda: tester.find_by_text("访客"), timeout=15)
    assert guest.count == 1, "向导弹窗应出现访客身份选项"
    assert (await tester.find_by_text("用户登录")).count == 1

    # 取消关闭向导，不发起网络请求
    await tester.tap(await tester.find_by_text("取消"))
    await tester.pump_and_settle()
    assert (await tester.find_by_text("访客")).count == 0
