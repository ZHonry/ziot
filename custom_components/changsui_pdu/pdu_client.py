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

    def _parse_value(self, raw_value: str, scale: float = 1.0) -> float:
        """解析带 'd' 后缀的数值并缩放"""
        try:
            val = int(raw_value.replace("d", "").strip())
            return val / scale
        except (ValueError, AttributeError):
            return 0.0

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
                    if resp.status == 200:
                        # 注入 Cookie
                        self.session.cookie_jar.update_cookies(
                            {
                                "usrname": self.username,
                                "password": self.password,
                                "lg": "0",
                                "inst": "0",
                                "outlet_index": str(self.outlets), # Use instance outlet count
                            }
                        )
                        self._logged_in = True
                        _LOGGER.info("成功登录到 PDU %s", self.host)
                        return True
                    else:
                        text = await resp.text()
                        _LOGGER.debug(f"[Login] Failed status={resp.status} body={text[:100]}")

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
                lines = text.strip().split("\n")
                
                # 根据插座数量确定数据起始行
                data_start_line = 3 if self.outlets == 20 else 4
                
                # 提取插座数据
                data_lines = lines[data_start_line:-1]
                # 每个插座占11行
                chunk_size = 11
                if len(data_lines) % chunk_size != 0:
                     # 尝试容错: 也许只有 10 行？或者根据 outlets 数量反推
                     _LOGGER.debug(f"数据行数 {len(lines)} 可能不匹配预期")

                chunks = [data_lines[i : i + chunk_size] for i in range(0, len(data_lines), chunk_size)]
                
                outlet_data = []
                for chunk in chunks:
                    if len(chunk) < 4: continue
                    try:
                        name = chunk[0]
                        state = 1 - int(chunk[1].replace("d", ""))
                        current = self._parse_value(chunk[2], 100.0)
                        power = self._parse_value(chunk[3], 1.0) # Power is raw watts?
                        
                        # 尝试解析负载限制(可选)
                        current_min = None
                        current_max = None
                        if len(chunk) > 5:
                            current_min = self._parse_value(chunk[4], 100.0)
                            current_max = self._parse_value(chunk[5], 100.0)
                        
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
                        _LOGGER.warning("解析插座数据失败: %s", e)
                        continue

                return outlet_data

        except aiohttp.ClientError as e:
            _LOGGER.error("网络错误: %s", e)
            self._logged_in = False
            return []

    async def get_pdu_overview(self) -> Dict[str, Any]:
        """获取 PDU 电参总览"""
        await self.ensure_logged_in()

        try:
            url = f"http://{self.host}/pm.cgi?pdu_index=0"
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    _LOGGER.error("获取 PDU 总览失败: HTTP %s", resp.status)
                    return {}

                text = await resp.text()
                lines = text.strip().split("\n")

                if len(lines) < 8:
                    _LOGGER.error("PDU 总览响应格式无效")
                    return {}

                return {
                    "voltage": self._parse_value(lines[4], 10.0),
                    "current": self._parse_value(lines[3], 100.0),
                    "total_power": self._parse_value(lines[5], 1.0),
                    "power_factor": self._parse_value(lines[6], 1000.0),
                    "total_energy": self._parse_value(lines[7], 100.0),
                }

        except (aiohttp.ClientError, ValueError, IndexError) as e:
            _LOGGER.error("获取 PDU 总览错误: %s", e)
            if isinstance(e, aiohttp.ClientError):
                self._logged_in = False
            return {}

    async def get_daily_energy(self) -> Dict[str, Any]:
        """获取当天能耗数据"""
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
                    return {"total": {}, "outlets": []}

                text = await resp.text()
                lines = text.strip().split("\n")

                if len(lines) < 4:
                    return {"total": {}, "outlets": []}

                total_data = lines[3].split(",")
                total_val = {
                    "start": self._parse_value(total_data[0], 100.0),
                    "end": self._parse_value(total_data[1], 100.0),
                    "today": self._parse_value(total_data[2], 100.0),
                }

                outlet_energy = []
                for entry in lines[4:-1]:
                    try:
                        parts = entry.split(",")
                        outlet_energy.append(
                            {
                                "name": parts[0],
                                "start": self._parse_value(parts[1], 100.0),
                                "end": self._parse_value(parts[2], 100.0),
                                "today": self._parse_value(parts[3], 100.0),
                            }
                        )
                    except (ValueError, IndexError):
                        continue

                return {
                    "total": total_val,
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
        """
        await self.ensure_logged_in()

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
                    if resp.status == 200 and ("success" in text or "succesd" in text):
                        _LOGGER.info(f"插座 {outlet_idx} 设置为 {state} 成功")
                        return True

            except aiohttp.ClientError as e:
                _LOGGER.warning("尝试 %s 失败,插座 %s: %s", attempt, outlet_idx, e)

            if attempt < 3:
                await asyncio.sleep(2 ** (attempt - 1))

        _LOGGER.error("控制插座 %s (state=%s) 失败,已尝试 3 次", outlet_idx, state)
        return False

    async def get_outlet_energy(self) -> List[Dict[str, Any]]:
        """获取插座总能耗"""
        await self.ensure_logged_in()

        try:
            url = f"http://{self.host}/outenergy.cgi?pdu_index=0"
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    return []

                text = await resp.text()
                lines = text.strip().split("\n")
                
                if len(lines) < 4:
                    return []

                outlet_energy = []
                data_lines = lines[3:]  # 跳过前3行
                
                # 每个插座占2行
                for i in range(0, len(data_lines) - 1, 2):
                    try:
                        name = data_lines[i].replace("d", "").strip()
                        energy_kwh = self._parse_value(data_lines[i + 1], 100.0)
                        
                        outlet_energy.append({
                            "name": name,
                            "energy": energy_kwh,
                        })
                    except (ValueError, IndexError):
                        continue

                return outlet_energy

        except aiohttp.ClientError as e:
            _LOGGER.error("网络错误: %s", e)
            self._logged_in = False
            return []

