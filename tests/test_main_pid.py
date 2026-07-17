"""测试 main.py 的 PID 锁进程身份核实（防 PID 复用误杀）"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import _pid_belongs_to_agent


def _mock_run(cmdline: str = "", returncode: int = 0):
    """构造 subprocess.run 的假返回"""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = cmdline
    return result


class TestPidBelongsToAgent:
    def test_agent_process_detected(self):
        """命令行含 main.py → 认定为本 agent"""
        with patch("main.subprocess.run",
                   return_value=_mock_run(r"C:\Python312\python.exe main.py --mode paper")):
            assert _pid_belongs_to_agent(1234) is True

    def test_unrelated_process_rejected(self):
        """命令行不含 main.py → 不是本 agent（PID 被复用）"""
        with patch("main.subprocess.run",
                   return_value=_mock_run(r"C:\Windows\notepad.exe")):
            assert _pid_belongs_to_agent(1234) is False

    def test_nonexistent_pid(self):
        """进程不存在（命令行为空）→ False"""
        with patch("main.subprocess.run", return_value=_mock_run("")):
            assert _pid_belongs_to_agent(999999) is False

    def test_query_failure_returns_none(self):
        """查询失败 → None（调用方应保守不杀）"""
        with patch("main.subprocess.run", return_value=_mock_run(returncode=1)):
            assert _pid_belongs_to_agent(1234) is None

    def test_timeout_returns_none(self):
        """查询超时 → None"""
        with patch("main.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="powershell", timeout=10)):
            assert _pid_belongs_to_agent(1234) is None


class TestPreflightCheck:
    """live 模式启动前检查"""

    def _root_config(self, api_key="k", secret_key="s", passphrase="p",
                     permissions="trade", deepseek_key="d"):
        cfg = MagicMock()
        cfg.exchange.api_key = api_key
        cfg.exchange.secret_key = secret_key
        cfg.exchange.passphrase = passphrase
        cfg.exchange.permissions = permissions
        cfg.agent.api_key = deepseek_key
        return cfg

    @pytest.mark.asyncio
    async def test_non_live_mode_skips(self):
        """非 live 模式直接通过"""
        from main import _preflight_check
        cfg = self._root_config(api_key="", secret_key="", passphrase="",
                                permissions="read", deepseek_key="")
        assert await _preflight_check(cfg, MagicMock(), "paper") is True

    @pytest.mark.asyncio
    async def test_missing_credentials_rejected(self):
        """live 缺凭证 → 拒绝启动"""
        from main import _preflight_check
        cfg = self._root_config(secret_key="")
        assert await _preflight_check(cfg, MagicMock(), "live") is False

    @pytest.mark.asyncio
    async def test_read_permission_rejected(self):
        """live 但权限 read（会静默模拟成交）→ 拒绝启动"""
        from main import _preflight_check
        cfg = self._root_config(permissions="read")
        assert await _preflight_check(cfg, MagicMock(), "live") is False

    @pytest.mark.asyncio
    async def test_balance_check_failure_rejected(self):
        """账户查询失败（凭证无效/网络断）→ 拒绝启动"""
        from main import _preflight_check
        okx = MagicMock()
        okx.get_balance.side_effect = RuntimeError("401 Unauthorized")
        cfg = self._root_config()
        assert await _preflight_check(cfg, okx, "live") is False

    @pytest.mark.asyncio
    async def test_all_good_passes(self):
        """凭证齐全 + 连通正常 → 通过"""
        from main import _preflight_check
        okx = MagicMock()
        okx.get_balance.return_value = [{"ccy": "USDT", "bal": "100"}]
        cfg = self._root_config()
        assert await _preflight_check(cfg, okx, "live") is True
