"""PDU HTTP 客户端 - 优化版本"""
import logging
import aiohttp
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any

_LOGGER = logging.getLogger(__name__)


class PDUClient:
    """PDU HTTP 客户端,支持会话管理和错误恢复"""

    def __init__(self, host: str, username: str, password: str, outlets: int = 16):
        """初始化客户端
        
        Args:
            host: PDU 主机地址
            username: 用户名
            password: 密码
            outlets: 插座数量
        """
        self.host = host
        self.username = username
        self.password = password
        self.outlets = outlets
        self.session: Optional[aiohttp.ClientSession] = None
        self._logged_in = False

    async def ensure_logged_in(self):
        """确保已登录,如果未登录则自动登录"""
        if not self._logged_in or not self.session:
            await self.login()

    async def login(self):
        """登录 PDU,带重试机制"""
        for attempt in range(3):
            try:
                # 关闭旧会话
                if self.session:
                    await self.session.close()

                # 创建新会话
                self.session = aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(force_close=True),
                    timeout=aiohttp.ClientTimeout(total=10),
                )

                login_url = (
                    f"http://{self.host}/login.cgi?"
                    f"login=1&name={self.username}&psd={self.password}"
                )

                async with self.session.get(login_url) as resp:
                    text = await resp.text()
                    _LOGGER.debug(
                        "[Login] status=%s, response(first 200 chars)=%s",
                        resp.status,
                        text[:200],
                    )

                    if resp.status == 200:
                        # 注入 Cookie
                        self.session.cookie_jar.update_cookies(
                            {
                                "usrname": self.username,
                                "password": self.password,
                                "lg": "0",
                                "inst": "0",
                                "outlet_index": "16",
                            }
                        )
                        self._logged_in = True
                        _LOGGER.info("成功登录到 PDU %s", self.host)
                        return True

            except Exception as e:
                _LOGGER.warning("登录尝试 %d 失败: %s", attempt + 1, e)
                await asyncio.sleep(2**attempt)  # 指数退避

        raise Exception(f"登录 PDU {self.host} 失败,已尝试 3 次")

    async def close(self):
        """关闭会话"""
        if self.session:
            await self.session.close()
            self.session = None
        self._logged_in = False

    async def get_outlet_status(self) -> List[Dict[str, Any]]:
        """获取插座状态"""
        await self.ensure_logged_in()

        try:
            url = f"http://{self.host}/outlet.cgi?pdu_index=0"
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    _LOGGER.error("获取插座状态失败: HTTP %s", resp.status)
                    return []

                text = await resp.text()
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "[Get Outlet Status] resp.status=%s, sample=%s",
                        resp.status,
                        "\n".join(text.splitlines()[:8]),
                    )

                lines = text.strip().split("\n")
                
                # 根据插座数量确定数据起始行
                # 20孔: 第2行是"20d",数据从第3行开始
                # 16孔: 第3行是"16d",数据从第4行开始
                if self.outlets == 20:
                    data_start_line = 3
                else:
                    data_start_line = 4
                
                # 提取插座数据
                data_lines = lines[data_start_line:-1]
                # 每个插座占11行
                chunks = [data_lines[i : i + 11] for i in range(0, len(data_lines), 11)]
                
                _LOGGER.debug(
                    "总数据行: %d, 数据块大小: 11, 预计插座数: %d",
                    len(data_lines),
                    len(data_lines) // 11
                )
                
                outlet_data = []
                for chunk in chunks:
                    try:
                        name = chunk[0]
                        state = 1 - int(chunk[1].replace("d", ""))
                        current = int(chunk[2].replace("d", "")) / 100
                        power = int(chunk[3].replace("d", ""))
                        
                        # 尝试解析负载限制(可选)
                        current_min = None
                        current_max = None
                        try:
                            current_min = int(chunk[4].replace("d", "")) / 100
                            current_max = int(chunk[5].replace("d", "")) / 100
                        except:
                            pass
                        
                        outlet_data.append(
                            {
                                "name": name,
                                "state": state,
                                "current": current,
                                "power": power,
                                "current_min": current_min,
                                "current_max": current_max,
                            }
                        )
                    except (ValueError, IndexError) as e:
                        _LOGGER.warning("解析插座数据失败: %s, chunk=%s", e, chunk[:4] if len(chunk) >= 4 else chunk)
                        continue

                return outlet_data

        except aiohttp.ClientError as e:
            _LOGGER.error("网络错误: %s", e)
            self._logged_in = False
            return []

    async def get_pdu_overview(self) -> Dict[str, Any]:
        """获取 PDU 电参总览
        
        Returns:
            包含 voltage, current, total_power, power_factor, total_energy 的字典
        """
        await self.ensure_logged_in()

        try:
            url = f"http://{self.host}/pm.cgi?pdu_index=0"
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    _LOGGER.error("获取 PDU 总览失败: HTTP %s", resp.status)
                    return {}

                text = await resp.text()
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "[PDU Overview] resp.status=%s, sample=%s",
                        resp.status,
                        "\n".join(text.splitlines()[:8]),
                    )

                lines = text.strip().split("\n")

                if len(lines) < 8:
                    _LOGGER.error("PDU 总览响应格式无效")
                    return {}

                voltage = int(lines[4].replace("d", "")) / 10
                current = int(lines[3].replace("d", "")) / 100
                total_power = int(lines[5].replace("d", ""))
                power_factor = int(lines[6].replace("d", "")) / 1000
                total_energy = int(lines[7].replace("d", "")) / 100

                return {
                    "voltage": voltage,
                    "current": current,
                    "total_power": total_power,
                    "power_factor": power_factor,
                    "total_energy": total_energy,
                }

        except (aiohttp.ClientError, ValueError, IndexError) as e:
            _LOGGER.error("获取 PDU 总览错误: %s", e)
            if isinstance(e, aiohttp.ClientError):
                self._logged_in = False
            return {}

    async def get_daily_energy(self) -> Dict[str, Any]:
        """获取当天能耗数据
        
        Returns:
            包含 total 和 outlets 的字典
        """
        await self.ensure_logged_in()

        try:
            now = datetime.now()
            url = (
                f"http://{self.host}/energy.cgi?"
                f"pdu_index=0&sy={now.year}&sm={now.month}&sd={now.day}"
                f"&ey={now.year}&em={now.month}&ed={now.day}"
            )

            async with self.session.get(url) as resp:
                if resp.status != 200:
                    _LOGGER.error("获取每日能耗失败: HTTP %s", resp.status)
                    return {"total": {}, "outlets": []}

                text = await resp.text()
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "[Daily Energy] resp.status=%s, sample=%s",
                        resp.status,
                        "\n".join(text.splitlines()[:8]),
                    )

                lines = text.strip().split("\n")

                if len(lines) < 4:
                    _LOGGER.error("每日能耗响应格式无效")
                    return {"total": {}, "outlets": []}

                total_data = lines[3].split(",")
                total_start = int(total_data[0].replace("d", "")) / 100
                total_end = int(total_data[1].replace("d", "")) / 100
                total_today = int(total_data[2].replace("d", "")) / 100

                outlet_energy = []
                for entry in lines[4:-1]:
                    try:
                        parts = entry.split(",")
                        name = parts[0]
                        start = int(parts[1].replace("d", "")) / 100
                        end = int(parts[2].replace("d", "")) / 100
                        today = int(parts[3].replace("d", "")) / 100

                        outlet_energy.append(
                            {
                                "name": name,
                                "start": start,
                                "end": end,
                                "today": today,
                            }
                        )
                    except (ValueError, IndexError) as e:
                        _LOGGER.warning("解析插座能耗数据失败: %s", e)
                        continue

                return {
                    "total": {
                        "start": total_start,
                        "end": total_end,
                        "today": total_today,
                    },
                    "outlets": outlet_energy,
                }

        except (aiohttp.ClientError, ValueError, IndexError) as e:
            _LOGGER.error("获取每日能耗错误: %s", e)
            if isinstance(e, aiohttp.ClientError):
                self._logged_in = False
            return {"total": {}, "outlets": []}

    async def set_outlet_state(self, outlet_idx: int, state: int) -> bool:
        """控制插座开关
        
        Args:
            outlet_idx: 插座编号 (1-based)
            state: 1=开, 0=关
            
        Returns:
            是否成功
        """
        await self.ensure_logged_in()

        # 计算参数
        idx = 1 - state
        outlet_key = f"t{outlet_idx - 1:02d}"
        data = {"pdu_index": "0", "idx": str(idx), outlet_key: "1"}

        url = f"http://{self.host}/outlet.cgi"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"http://{self.host}/outlet.html",
        }

        # 重试逻辑,使用指数退避
        for attempt in range(1, 4):
            try:
                async with self.session.post(url, data=data, headers=headers) as resp:
                    text = await resp.text()
                    _LOGGER.debug(
                        "[尝试 %s] outlet=%s state=%s status=%s body_sample=%s",
                        attempt,
                        outlet_idx,
                        state,
                        resp.status,
                        text[:200],
                    )

                    # 成功条件
                    if resp.status == 200 and ("success" in text or "succesd" in text):
                        _LOGGER.info(
                            "插座 %s 设置为状态 %s 成功(尝试 %s 次)",
                            outlet_idx,
                            state,
                            attempt,
                        )
                        return True

            except aiohttp.ClientError as e:
                _LOGGER.warning("尝试 %s 失败,插座 %s: %s", attempt, outlet_idx, e)

            # 指数退避
            if attempt < 3:
                await asyncio.sleep(2 ** (attempt - 1))

        _LOGGER.error(
            "控制插座 %s (state=%s) 失败,已尝试 3 次",
            outlet_idx,
            state,
        )
        return False

    async def get_outlet_energy(self) -> List[Dict[str, Any]]:
        """获取插座总能耗
        
        Returns:
            包含每个插座能耗的列表，格式: [{"name": "Outlet1", "energy": 6.08}, ...]
        """
        await self.ensure_logged_in()

        try:
            url = f"http://{self.host}/outenergy.cgi?pdu_index=0"
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    _LOGGER.error("获取插座能耗失败: HTTP %s", resp.status)
                    return []

                text = await resp.text()
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    _LOGGER.debug(
                        "[Get Outlet Energy] resp.status=%s, sample=%s",
                        resp.status,
                        "\n".join(text.splitlines()[:8]),
                    )

                lines = text.strip().split("\n")
                
                if len(lines) < 4:
                    _LOGGER.error("插座能耗响应格式无效")
                    return []

                # 第3行是插座数量
                # 从第4行开始，每个插座占2行：名称 + 能耗值
                outlet_energy = []
                data_lines = lines[3:]  # 跳过前3行
                
                # 每个插座占2行
                for i in range(0, len(data_lines) - 1, 2):
                    try:
                        name = data_lines[i].replace("d", "").strip()
                        energy_value = int(data_lines[i + 1].replace("d", "").strip())
                        # 能耗单位是 kWh，需要除以100（根据其他API的模式）
                        energy_kwh = energy_value / 100
                        
                        outlet_energy.append({
                            "name": name,
                            "energy": energy_kwh,
                        })
                    except (ValueError, IndexError) as e:
                        _LOGGER.warning("解析插座能耗数据失败: %s", e)
                        continue

                _LOGGER.debug("成功获取 %d 个插座的能耗数据", len(outlet_energy))
                return outlet_energy

        except aiohttp.ClientError as e:
            _LOGGER.error("网络错误: %s", e)
            self._logged_in = False
            return []

