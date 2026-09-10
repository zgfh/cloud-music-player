"""E2E 共享等待工具。

tap 触发的事件链路是异步往返：Dart 事件 → Python 处理（视图切换/表单校验/
网络请求）→ patch 回传 → Dart 重建。``pump_and_settle`` 只等 Dart 侧帧调度
稳定，可能在 patch 到达前就返回（CI 上稳定复现），因此对"交互后应出现某控件"
一律用带超时的轮询等待，而不是一次 find + 断言。
"""

import asyncio

import flet.testing as ftt


async def wait_for(tester: ftt.Tester, finder_factory, timeout: float = 10.0):
    """轮询直到 finder_factory() 找到控件；超时返回最后一个 finder（count=0），
    由调用方断言失败原因。"""
    deadline = asyncio.get_event_loop().time() + timeout
    finder = None
    while True:
        finder = await finder_factory()
        if finder.count:
            return finder
        if asyncio.get_event_loop().time() >= deadline:
            return finder
        await asyncio.sleep(0.3)
        await tester.pump_and_settle()


async def tap_and_wait(
    tester: ftt.Tester, target_finder_factory, expected_factory, timeout: float = 10.0
):
    """tap 目标控件并等待期望控件出现（tap 本身找不到目标时直接抛 RemoteTesterError）。"""
    target = await wait_for(tester, target_finder_factory, timeout=timeout)
    if not target.count:
        raise AssertionError("等待点击目标超时")
    await tester.tap(target.last)
    await tester.pump_and_settle()
    return await wait_for(tester, expected_factory, timeout=timeout)


async def settle_network(tester: ftt.Tester, seconds: float = 0.5):
    """等待网络往返：先 sleep 再 pump，配合 wait_for 使用。"""
    await asyncio.sleep(seconds)
    await tester.pump_and_settle()
