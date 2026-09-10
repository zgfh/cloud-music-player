"""
端到端 UI 集成测试（flet test）

通过官方 FletTestApp 在真实 Flutter 渲染进程中驱动打包形态的 app：
打开连接页 → 切换来源到 SMB → 断言 SMB 表单出现。

运行方式（默认 macOS 桌面平台，首次会 provision Flutter 测试宿主）：
    flet test -k integration

注意：app 以真实配置启动（auto_connect=False 不会发起网络请求），
切换来源会写入 source_type 配置——测试结束时切回 Nextcloud 还原。
"""

import flet.testing as ftt
from helpers import wait_for


async def test_integration_switch_source(flet_app: ftt.FletTestApp):
    """连接页来源切换：Nextcloud 表单 → SMB 表单，标题与控件同步切换"""
    tester = flet_app.tester
    await tester.pump_and_settle()

    # 底部导航进入连接页（启动默认停在上次使用的视图）
    await tester.tap(await tester.find_by_text("连接"))
    await tester.pump_and_settle()

    # 默认展示 Nextcloud 表单与标题
    title = await wait_for(tester, lambda: tester.find_by_text("NEXTCLOUD"))
    assert title.count == 1
    assert (await tester.find_by_key("smb_host")).count == 0  # SMB 表单未挂载

    # 切换到 SMB 来源
    await tester.tap(await tester.find_by_text("SMB 共享"))
    await tester.pump_and_settle()

    # 标题切换、SMB 表单出现（向导式：仅一个地址框 + 引导文案）
    assert (await wait_for(tester, lambda: tester.find_by_text("SMB"))).count == 1
    host_input = await wait_for(tester, lambda: tester.find_by_key("smb_host"))
    assert host_input.count == 1
    assert (await tester.find_by_text("访客")).count == 0  # 向导未打开时无认证界面

    # 切回 Nextcloud 还原配置与界面
    await tester.tap(await tester.find_by_text("Nextcloud"))
    await tester.pump_and_settle()
    assert (await wait_for(tester, lambda: tester.find_by_text("NEXTCLOUD"))).count == 1
