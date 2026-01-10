"""PDU TCP Server - 无 MQTT 版本"""
import asyncio
import aiohttp
import logging
import re
import math
from typing import Optional, Tuple, Set

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from datetime import timedelta

from .coordinator import PduCoordinator
from .device_registry import DeviceRegistry
from .const import (
    DOMAIN, 
    CONF_FETCH_OUTLET_CURRENT, 
    CONF_WEB_USERNAME, 
    CONF_WEB_PASSWORD,
    CONF_NUM_SWITCHES
)

_LOGGER = logging.getLogger(__name__)


class PduServer:
    """PDU TCP 服务器"""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: PduCoordinator,
        device_registry: DeviceRegistry,
        config: dict,
    ):
        """初始化 PDU Server"""
        self.hass = hass
        self.coordinator = coordinator
        self.device_registry = device_registry
        
        self.host = config.get("host", "0.0.0.0")
        self.port = config.get("port", 4600)

        # Web 抓取配置
        self.fetch_outlet_current = config.get(CONF_FETCH_OUTLET_CURRENT, False)
        self.web_username = config.get(CONF_WEB_USERNAME, "admin")
        self.web_password = config.get(CONF_WEB_PASSWORD, "admin")
        
        # Log Level
        log_level = config.get("log_level", "info").upper()
        self.log_level = getattr(logging, log_level, logging.INFO)
        _LOGGER.setLevel(self.log_level)

        # 连接池: pdu_id -> (reader, writer)
        self.connection_pool = {}
        self.server = None
        self.remove_timer = None
        
        # 新增：用于追踪后台任务，防止 Task destroyed 错误
        self._background_tasks: Set[asyncio.Task] = set()

    async def start(self):
        """启动 TCP Server"""
        try:
            self.server = await asyncio.start_server(
                self.handle_client, self.host, self.port
            )
            addr = self.server.sockets[0].getsockname()
            _LOGGER.info(f"PDU Server 监听于 {addr}")

            # 启动定时任务
            self.remove_timer = async_track_time_interval(
                self.hass, self._periodic_tasks, timedelta(seconds=5)
            )

            # 使用专用集合管理该任务
            loop_task = asyncio.create_task(self.server.serve_forever())
            self._background_tasks.add(loop_task)
            loop_task.add_done_callback(self._background_tasks.discard)

        except Exception as e:
            _LOGGER.error(f"启动 PDU Server 失败: {e}")

    async def stop(self):
        """停止服务"""
        _LOGGER.info("正在停止 PDU Server...")
        
        # 1. 取消所有后台抓取任务
        if self._background_tasks:
            for task in list(self._background_tasks):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        # 2. 停止定时器
        if self.remove_timer:
            self.remove_timer()

        # 3. 关闭 TCP Server
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        # 4. 关闭所有客户端连接
        for pdu_id, (reader, writer) in list(self.connection_pool.items()):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self.connection_pool.clear()

    def get_code(self, cmd_str: str) -> int:
        """计算命令校验码"""
        return sum(ord(ch) for ch in cmd_str) % 256

    async def _send_raw_command(self, writer, cmd_content: str):
        """发送原始命令"""
        check_value = self.get_code(cmd_content)
        full_cmd = f"START {cmd_content} check='{check_value}' END"
        try:
            writer.write(full_cmd.encode())
            await writer.drain()
        except Exception as e:
            _LOGGER.debug(f"发送命令失败: {e}")

    async def send_control_command(self, pdu_id: str, action: str, io_number: int):
        """发送控制命令"""
        if pdu_id not in self.connection_pool:
            _LOGGER.warning(f"PDU {pdu_id} 未连接")
            return

        _, writer = self.connection_pool[pdu_id]
        if writer.is_closing():
            _LOGGER.error(f"PDU {pdu_id} 连接已关闭")
            return

        cmd_content = f"{action} io='{io_number}'"
        check_value = self.get_code(cmd_content)
        full_cmd = f"START {cmd_content} check='{check_value}' END"

        try:
            writer.write(full_cmd.encode())
            await writer.drain()
            _LOGGER.debug(f"发送到 {pdu_id}: {full_cmd}")
        except Exception as e:
            _LOGGER.error(f"发送命令错误: {e}")

    async def _periodic_tasks(self, now):
        """定期任务:请求 IO 状态和 PVC 数据"""
        for pdu_id, (reader, writer) in list(self.connection_pool.items()):
            if writer.is_closing():
                continue
            # 请求 IO 状态
            await self._send_raw_command(writer, "iostate")
            # 请求 PVC (功率、电压、电流)
            await self._send_raw_command(writer, "PVC_get")

    def _update_state_from_command(self, pdu_id: str, command: str):
        """从命令中更新状态"""
        match = re.search(r"(open|close).*?io='(\d+)'", command)
        if match:
            action, io_str = match.groups()
            io_number = int(io_str)
            if io_number <= 8:
                ha_switch_number = io_number
            elif (io_number & (io_number - 1)) == 0:
                ha_switch_number = int(math.log2(io_number)) + 1
            else:
                ha_switch_number = io_number

            state = "on" if action == "open" else "off"
            self.coordinator.update_switch_state(pdu_id, ha_switch_number, state)

    def _parse_and_publish_iostate(self, pdu_id: str, msg: str):
        """解析并发布 IO 状态"""
        io_match = re.search(r"io8='(\d+)'", msg)
        if io_match:
            try:
                io_val = int(io_match.group(1))
                self.coordinator.update_all_switches(pdu_id, io_val)
            except Exception as e:
                _LOGGER.error(f"解析 iostate 错误: {e}")

    def _parse_and_publish_pvc(self, pdu_id: str, msg: str):
        """解析并发布 PVC 数据"""
        p_match = re.search(r"[Pp]='([^']+)'", msg)
        v_match = re.search(r"[Vv]='([^']+)'", msg)

        if p_match:
            try:
                power = int(p_match.group(1))
                self.coordinator.update_sensor_data(pdu_id, "power", power)
            except Exception: pass

        try:
            for a_match in re.finditer(r"[Aa](\d*)='([^']+)'", msg):
                idx = a_match.group(1)
                raw = a_match.group(2)
                try:
                    val = int(raw)
                    current_val = round(val / 1000, 3)
                    if idx == "":
                        self.coordinator.update_sensor_data(pdu_id, "current", current_val)
                    else:
                        sensor_name = f"current_{int(idx)}"
                        self.coordinator.update_sensor_data(pdu_id, sensor_name, current_val)
                except Exception: continue
        except Exception: pass

        if v_match:
            try:
                voltage = round(int(v_match.group(1)) / 100, 2)
                self.coordinator.update_sensor_data(pdu_id, "voltage", voltage)
            except Exception: pass

    async def handle_client(self, reader, writer):
        """处理客户端连接"""
        pdu_id = None
        addr = writer.get_extra_info("peername")
        _LOGGER.debug(f"新连接来自 {addr}")

        try:
            buffer = ""
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(1024), timeout=60.0)
                    if not chunk:
                        break
                    buffer += chunk.decode(errors="ignore")

                    while "END" in buffer:
                        end_pos = buffer.find("END") + 3
                        msg = buffer[:end_pos].strip()
                        buffer = buffer[end_pos:]

                        if not pdu_id:
                            match = re.search(r"id='([^']+)'", msg)
                            if match:
                                pdu_id = match.group(1)
                                await self.device_registry.async_register_device(pdu_id, auto_create=True)
                                
                                try:
                                    peer_ip = addr[0] if isinstance(addr, (list, tuple)) else str(addr)
                                    await self.device_registry.async_update_device(
                                        pdu_id,
                                        {
                                            "ip": peer_ip,
                                            "port": addr[1] if isinstance(addr, (list, tuple)) else None,
                                            "configuration_url": f"http://{peer_ip}",
                                        },
                                    )
                                except Exception: pass
                                
                                device_config = self.device_registry.get_device(pdu_id)
                                num_switches = device_config.get("num_switches", 8)
                                self.coordinator.init_pdu(pdu_id, num_switches)
                                self.connection_pool[pdu_id] = (reader, writer)
                                
                                writer.write(b"Login Successful")
                                await writer.drain()
                                _LOGGER.info(f"PDU {pdu_id} 已从 {addr} 登录")

                                await self._send_raw_command(writer, "iostate")
                                await self._send_raw_command(writer, "PVC_get")
                                await self._create_entities_for_device(pdu_id)

                                # 修复：将任务加入集合进行管理
                                if self.fetch_outlet_current:
                                    task = asyncio.create_task(self._fetch_outlet_currents(pdu_id, addr))
                                    self._background_tasks.add(task)
                                    task.add_done_callback(self._background_tasks.discard)
                            else:
                                _LOGGER.warning(f"无效的登录尝试来自 {addr}: {msg}")
                                return
                        else:
                            if re.search(r"\b(open|close)\s+io='(\d+)'", msg):
                                content_match = re.search(r"START (.*) END", msg)
                                if content_match:
                                    self._update_state_from_command(pdu_id, content_match.group(1))
                            elif "START iostate" in msg:
                                self._parse_and_publish_iostate(pdu_id, msg)
                            elif "START PVC" in msg:
                                self._parse_and_publish_pvc(pdu_id, msg)

                except asyncio.TimeoutError:
                    pass
                except Exception as e:
                    _LOGGER.error(f"客户端循环错误 {addr}: {e}")
                    break

        finally:
            if pdu_id:
                _LOGGER.info(f"连接关闭: {pdu_id} ({addr})")
                if pdu_id in self.connection_pool:
                    del self.connection_pool[pdu_id]
                await self.device_registry.async_set_device_connected(pdu_id, False)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception: pass

    async def _create_entities_for_device(self, pdu_id: str):
        """为新设备创建实体"""
        if "add_switch_entities" not in self.hass.data.get(DOMAIN, {}):
            _LOGGER.debug("实体创建回调尚未准备好,稍后将自动创建")
            return

        device_config = self.device_registry.get_device(pdu_id)
        if not device_config: return

        from .switch import PduSwitch
        from .sensor import PduSensor
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(self.hass)
        num_switches = device_config.get(CONF_NUM_SWITCHES, 8)
        
        switch_entities = []
        for i in range(1, num_switches + 1):
            uid = f"{pdu_id}_switch_{i}"
            if registry.async_get_entity_id("switch", DOMAIN, uid) is None:
                switch_entities.append(PduSwitch(self.coordinator, self.device_registry, self, pdu_id, i))

        if switch_entities:
            self.hass.data[DOMAIN]["add_switch_entities"](switch_entities)

        default_sensors = {"power", "current", "voltage"}
        # 使用 set 避免重复
        sensors_to_create = default_sensors.copy()
        # 这里尝试获取 coordinator 中已有的传感器列表
        if pdu_id in self.coordinator.data:
            for key in self.coordinator.data[pdu_id]:
                if key.startswith("current_"):
                    sensors_to_create.add(key)
        
        sensor_entities = []
        for sensor_type in sensors_to_create:
            uid = f"{pdu_id}_sensor_{sensor_type}"
            if registry.async_get_entity_id("sensor", DOMAIN, uid) is None:
                sensor_entities.append(PduSensor(self.coordinator, self.device_registry, pdu_id, sensor_type))

        if sensor_entities:
            self.hass.data[DOMAIN]["add_sensor_entities"](sensor_entities)

    async def _fetch_outlet_currents(self, pdu_id: str, peer):
        """后台抓取分口电流"""
        host_ip = peer[0] if isinstance(peer, (tuple, list)) else str(peer)
        port = 80
        data = "realtime_btn=8&radio_function=&select_temp=0&is_mobile=0"
        req = (
            f"POST / HTTP/1.1\r\nHost: {host_ip}\r\n"
            f"Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n{data}"
        )

        while True:
            try:
                reader, writer = await asyncio.open_connection(host_ip, port)
                writer.write(req.encode("utf-8"))
                await writer.drain()
                resp_bytes = await reader.read(-1)
                writer.close()
                await writer.wait_closed()

                text = resp_bytes.decode("utf-8", errors="ignore")
                for m in re.finditer(r"td2_(\d+)'?\)\.innerText\s*=\s*'([^']*)'", text):
                    idx = int(m.group(1))
                    raw = m.group(2).strip()
                    num = re.search(r"[-+]?[0-9]*\.?[0-9]+", raw)
                    if num:
                        val = round(float(num.group(0)), 3)
                        self.coordinator.update_sensor_data(pdu_id, f"current_{idx}", val)
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.debug(f"[{pdu_id}] 抓取电流失败: {e}")
            
            await asyncio.sleep(30) # 30秒抓取一次