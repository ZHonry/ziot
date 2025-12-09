"""PDU TCP Server - 无 MQTT 版本"""
import asyncio
import logging
import re
import math
from typing import Optional, Tuple

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from datetime import timedelta

from .coordinator import PduCoordinator
from .device_registry import DeviceRegistry

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
        """初始化 PDU Server
        
        Args:
            hass: Home Assistant 实例
            coordinator: 数据协调器
            device_registry: 设备注册表
            config: 配置字典
        """
        self.hass = hass
        self.coordinator = coordinator
        self.device_registry = device_registry
        
        self.host = config.get("host", "0.0.0.0")
        self.port = config.get("port", 4600)
        
        # Log Level
        log_level = config.get("log_level", "info").upper()
        self.log_level = getattr(logging, log_level, logging.INFO)
        _LOGGER.setLevel(self.log_level)

        # 连接池: pdu_id -> (reader, writer)
        self.connection_pool = {}
        self.server = None
        self.remove_timer = None

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

            # 启动服务器循环
            asyncio.create_task(self.server.serve_forever())

        except Exception as e:
            _LOGGER.error(f"启动 PDU Server 失败: {e}")

    async def stop(self):
        """停止服务"""
        _LOGGER.info("正在停止 PDU Server...")
        
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        
        if self.remove_timer:
            self.remove_timer()

        # 关闭所有客户端连接
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
        """发送原始命令
        
        Args:
            writer: 写入流
            cmd_content: 命令内容
        """
        check_value = self.get_code(cmd_content)
        full_cmd = f"START {cmd_content} check='{check_value}' END"
        try:
            writer.write(full_cmd.encode())
            await writer.drain()
        except Exception as e:
            _LOGGER.debug(f"发送命令失败: {e}")

    async def send_control_command(self, pdu_id: str, action: str, io_number: int):
        """发送控制命令(供 Switch 实体调用)
        
        Args:
            pdu_id: PDU 设备 ID
            action: 动作 "open" 或 "close"
            io_number: IO 编号(位掩码)
        """
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
        """从命令中更新状态
        
        Args:
            pdu_id: PDU 设备 ID
            command: 命令内容
        """
        match = re.search(r"(open|close).*?io='(\d+)'", command)
        if match:
            action, io_str = match.groups()
            io_number = int(io_str)

            # 转换 IO 编号为开关编号
            if io_number <= 8:
                ha_switch_number = io_number
            elif (io_number & (io_number - 1)) == 0:  # 是 2 的幂
                ha_switch_number = int(math.log2(io_number)) + 1
            else:
                ha_switch_number = io_number

            state = "on" if action == "open" else "off"
            self.coordinator.update_switch_state(pdu_id, ha_switch_number, state)

    def _parse_and_publish_iostate(self, pdu_id: str, msg: str):
        """解析并发布 IO 状态
        
        Args:
            pdu_id: PDU 设备 ID
            msg: 消息内容
        """
        io_match = re.search(r"io8='(\d+)'", msg)
        if io_match:
            try:
                io_val = int(io_match.group(1))
                self.coordinator.update_all_switches(pdu_id, io_val)
            except Exception as e:
                _LOGGER.error(f"解析 iostate 错误: {e}")

    def _parse_and_publish_pvc(self, pdu_id: str, msg: str):
        """解析并发布 PVC 数据
        
        Args:
            pdu_id: PDU 设备 ID
            msg: 消息内容
        """
        p_match = re.search(r"[Pp]='([^']+)'", msg)
        a_match = re.search(r"[Aa]='([^']+)'", msg)
        v_match = re.search(r"[Vv]='([^']+)'", msg)

        if p_match:
            power = int(p_match.group(1))
            self.coordinator.update_sensor_data(pdu_id, "power", power)

        if a_match:
            current = round(int(a_match.group(1)) / 1000, 3)
            self.coordinator.update_sensor_data(pdu_id, "current", current)

        if v_match:
            voltage = round(int(v_match.group(1)) / 100, 2)
            self.coordinator.update_sensor_data(pdu_id, "voltage", voltage)

    async def handle_client(self, reader, writer):
        """处理客户端连接
        
        Args:
            reader: 读取流
            writer: 写入流
        """
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
                            # 登录阶段
                            match = re.search(r"id='([^']+)'", msg)
                            if match:
                                pdu_id = match.group(1)
                                
                                # 注册设备(自动创建)
                                await self.device_registry.async_register_device(pdu_id, auto_create=True)
                                
                                # 获取设备配置
                                device_config = self.device_registry.get_device(pdu_id)
                                num_switches = device_config.get("num_switches", 8)
                                
                                # 初始化协调器数据
                                self.coordinator.init_pdu(pdu_id, num_switches)
                                
                                # 保存连接
                                self.connection_pool[pdu_id] = (reader, writer)
                                
                                writer.write(b"Login Successful")
                                await writer.drain()
                                _LOGGER.info(f"PDU {pdu_id} 已从 {addr} 登录")

                                # 初始请求
                                await self._send_raw_command(writer, "iostate")
                                await self._send_raw_command(writer, "PVC_get")
                                
                                # 触发实体创建(如果是新设备)
                                await self._create_entities_for_device(pdu_id)
                            else:
                                _LOGGER.warning(f"无效的登录尝试来自 {addr}: {msg}")
                                return
                        else:
                            # 正常消息处理
                            if re.search(r"\b(open|close)\s+io='(\d+)'", msg):
                                content_match = re.search(r"START (.*) END", msg)
                                if content_match:
                                    self._update_state_from_command(pdu_id, content_match.group(1))
                            elif "START iostate" in msg:
                                self._parse_and_publish_iostate(pdu_id, msg)
                            elif "START PVC" in msg:
                                self._parse_and_publish_pvc(pdu_id, msg)

                except asyncio.TimeoutError:
                    # 超时,继续等待
                    pass
                except Exception as e:
                    _LOGGER.error(f"客户端循环错误 {addr}: {e}")
                    break

        finally:
            _LOGGER.info(f"连接关闭: {pdu_id} ({addr})")
            if pdu_id:
                if pdu_id in self.connection_pool:
                    del self.connection_pool[pdu_id]
                # 更新设备连接状态
                await self.device_registry.async_set_device_connected(pdu_id, False)
            writer.close()
            await writer.wait_closed()

    async def _create_entities_for_device(self, pdu_id: str):
        """为新设备创建实体
        
        Args:
            pdu_id: PDU 设备 ID
        """
        # 检查是否已有实体创建回调
        if "add_switch_entities" not in self.hass.data.get("gwgj_pdu", {}):
            _LOGGER.debug("实体创建回调尚未准备好,稍后将自动创建")
            return

        device_config = self.device_registry.get_device(pdu_id)
        if not device_config:
            return

        # 动态导入以避免循环依赖
        from .switch import PduSwitch
        from .sensor import PduSensor
        from .const import CONF_NUM_SWITCHES

        # 创建开关实体
        num_switches = device_config.get(CONF_NUM_SWITCHES, 8)
        switch_entities = []
        for i in range(1, num_switches + 1):
            switch_entities.append(
                PduSwitch(
                    coordinator=self.coordinator,
                    device_registry=self.device_registry,
                    server=self,
                    pdu_id=pdu_id,
                    switch_number=i,
                )
            )

        if switch_entities and "add_switch_entities" in self.hass.data["gwgj_pdu"]:
            self.hass.data["gwgj_pdu"]["add_switch_entities"](switch_entities)
            _LOGGER.info(f"为 PDU {pdu_id} 创建了 {len(switch_entities)} 个开关实体")

        # 创建传感器实体(强制包含基础电参传感器)
        # available_sensors = self.coordinator.get_available_sensors(pdu_id)
        # 默认创建功率、电流、电压传感器
        default_sensors = {"power", "current", "voltage"}
        # 如果有其他动态发现的传感器也加上
        available_sensors = self.coordinator.get_available_sensors(pdu_id)
        sensors_to_create = default_sensors.union(available_sensors)
        
        sensor_entities = []
        for sensor_type in sensors_to_create:
            sensor_entities.append(
                PduSensor(
                    coordinator=self.coordinator,
                    device_registry=self.device_registry,
                    pdu_id=pdu_id,
                    sensor_type=sensor_type,
                )
            )

        if sensor_entities and "add_sensor_entities" in self.hass.data["gwgj_pdu"]:
            self.hass.data["gwgj_pdu"]["add_sensor_entities"](sensor_entities)
            _LOGGER.info(f"为 PDU {pdu_id} 创建了 {len(sensor_entities)} 个传感器实体")
