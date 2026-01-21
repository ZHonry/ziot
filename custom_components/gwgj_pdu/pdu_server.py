"""PDU TCP Server - 无 MQTT 版本"""
import asyncio
import asyncio
import logging
import aiohttp
import re
import math
import urllib.parse
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
    CONF_WEB_PASSWORD
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
        entry_id: str,
    ):
        """初始化 PDU Server
        
        Args:
            hass: Home Assistant 实例
            coordinator: 数据协调器
            device_registry: 设备注册表
            config: 配置字典
            entry_id: ConfigEntry ID
        """
        self.hass = hass
        self.coordinator = coordinator
        self.device_registry = device_registry
        self.entry_id = entry_id
        
        self.host = config.get("host", "0.0.0.0")
        self.port = config.get("port", 4600)
        
        # Web 抓取配置
        # 默认启用，以恢复原有功能
        self.fetch_outlet_current = config.get(CONF_FETCH_OUTLET_CURRENT, True)
        self.web_username = config.get(CONF_WEB_USERNAME, "admin")
        self.web_password = config.get(CONF_WEB_PASSWORD, "admin")
        
        _LOGGER.info(f"PDU Server 初始化: 分口电流抓取={'启用' if self.fetch_outlet_current else '禁用'}, 用户={self.web_username}")
        
        # Log Level
        log_level = config.get("log_level", "info").upper()
        self.log_level = getattr(logging, log_level, logging.INFO)
        _LOGGER.setLevel(self.log_level)

        # 连接池: pdu_id -> (reader, writer)
        self.connection_pool = {}
        self.server = None
        self.remove_timer = None
        
        # 新增：用于追踪后台任务，防止 Task destroyed 错误
        self._background_tasks = set()
        
        # 命令锁，防止并发写入冲突
        self._cmd_lock = asyncio.Lock()

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
        """发送原始命令
        
        Args:
            writer: 写入流
            cmd_content: 命令内容
        """
        check_value = self.get_code(cmd_content)
        full_cmd = f"START {cmd_content} check='{check_value}' END"
        try:
            async with self._cmd_lock:
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
            async with self._cmd_lock:
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
            
            # 增加延时，防止命令粘连或设备处理不过来
            await asyncio.sleep(0.5)

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
        
        兼容两种协议格式:
        1. (Sec 2.3) START PVC_Info ... p='9' v='23342' c='4' ... (c/100, v/100)
        2. (Sec 2.2) START PVC ... P='0' A='0' V='22249' ... (A/1000?, V/100)
        """
        # 保持警告日志以便您确认
        _LOGGER.warning(f"[PVC_DEBUG] Raw msg: {msg}")
        
        # 1. 功率 (p/P)
        p_match = re.search(r"[\s]([Pp])='(\d+)'", " " + msg) # Hack: prepend space to msg to simplify regex
        if p_match:
            try:
                power = int(p_match.group(2))
                self.coordinator.update_sensor_data(pdu_id, "power", power)
            except Exception: pass

        # 2. 电压 (v/V) -> /100
        v_match = re.search(r"[\s]([Vv])='(\d+)'", " " + msg)
        if v_match:
            try:
                val = int(v_match.group(2))
                voltage = round(val / 100.0, 2)
                self.coordinator.update_sensor_data(pdu_id, "voltage", voltage)
            except Exception: pass

        # 3. 总电流
        # 优先寻找 c/C (Sec 2.3, scale /100)
        c_match = re.search(r"[\s]([Cc])='(\d+)'", " " + msg)
        total_current_found = False
        
        if c_match:
            try:
                val = int(c_match.group(2))
                current = round(val / 100.0, 3) 
                self.coordinator.update_sensor_data(pdu_id, "current", current)
                total_current_found = True
            except Exception: pass
            
        # 如果没找到 c/C，尝试找 a/A (Sec 2.2, usually scale /1000 for Amps in other protocols, potentially /100 here?)
        # 安全起见，如果电压是 /100，假设 A 也是 /1000 比较常见，但文档没细说 A 的比例。
        # 鉴于 Sec 2.3 是"实时数据"，Sec 2.2 是"验证返回"，我们主要依赖 c。
        # 如果真出现了 A，我们先按 /1000 处理，如果不准(差10倍)用户会反馈。
        if not total_current_found:
            a_match = re.search(r"[\s]([Aa])='(\d+)'", " " + msg)
            if a_match:
                try:
                    val = int(a_match.group(2))
                    current = round(val / 1000.0, 3) # 猜测 /1000
                    self.coordinator.update_sensor_data(pdu_id, "current", current)
                except Exception: pass

        # 4. 电能 (e/E)
        e_match = re.search(r"[\s]([Ee])='([\d\.]+)'", " " + msg)
        if e_match:
             try:
                energy = float(e_match.group(2))
                self.coordinator.update_sensor_data(pdu_id, "energy", energy)
             except Exception: pass

        # 5. 分路电流 (c0 - c7) -> /100
        # 匹配 c0, C0, c1, C1 ...
        c_matches = re.finditer(r"[\s]([Cc])(\d+)='(\d+)'", " " + msg)
        for m in c_matches:
            try:
                # group(1) is 'c'/'C', group(2) is index '0', group(3) is value
                idx = int(m.group(2)) # 0-7
                val = int(m.group(3))
                current_val = round(val / 100.0, 3)
                
                # 映射到 entity id (1-8)
                switch_idx = idx + 1
                self.coordinator.update_sensor_data(pdu_id, f"current_{switch_idx}", current_val)
            except Exception: pass

        # 4. 电能 (e)
        e_match = re.search(r" e='([\d\.]+)'", msg) # 电能可能是小数? 协议只说 e:电能
        if e_match:
             try:
                energy = float(e_match.group(1))
                # 如果 coordinator 支持 energy
                self.coordinator.update_sensor_data(pdu_id, "energy", energy)
             except Exception: pass

        # 5. 分路电流 (c0 - c7) -> /100
        # 协议文档里写 C0-c7，可能是大小写混合，这里匹配 c(\d)
        # 注意: 之前的代码逻辑是 current_1 到 current_8
        # c0 对应 current_1 ? 协议说 "io为要开/关的插座口(1-8)"
        # 假设 c0 -> 1, c7 -> 8
        c_matches = re.finditer(r" [Cc](\d+)='(\d+)'", msg)
        for m in c_matches:
            try:
                idx = int(m.group(1)) # 0-7
                val = int(m.group(2))
                current_val = round(val / 100.0, 3)
                
                # 映射到 entity id (1-8)
                switch_idx = idx + 1
                self.coordinator.update_sensor_data(pdu_id, f"current_{switch_idx}", current_val)
            except Exception: pass
    async def _fetch_outlet_currents(self, pdu_id: str, peer):
        """后台抓取分口电流 (Raw Socket 模式，因为设备不返回标准 HTTP 头)"""
        host_ip = peer[0] if isinstance(peer, (tuple, list)) else str(peer)
        port = 80
        
        # 构建原始 HTTP 请求
        # 注意：这里保持最原始的请求格式，因为设备可能对 header 顺序或内容敏感
        # 且设备返回的数据不包含 HTTP 头，导致标准库无法解析
        data = "realtime_btn=8&radio_function=&select_temp=0&is_mobile=0"
        req = (
            f"POST / HTTP/1.1\r\n"
            f"Host: {host_ip}\r\n"
            f"Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: {len(data)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{data}"
        )

        while True:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host_ip, port), timeout=10
                )
                
                writer.write(req.encode("utf-8"))
                await writer.drain()
                
                # 读取响应直到连接关闭 (EOF)
                response_bytes = b""
                try:
                    while True:
                        chunk = await asyncio.wait_for(reader.read(4096), timeout=10)
                        if not chunk:
                            break
                        response_bytes += chunk
                except asyncio.TimeoutError:
                    pass # 读取超时也视为读取结束，尝试解析已有的数据
                
                writer.close()
                await writer.wait_closed()

                text = response_bytes.decode("utf-8", errors="ignore")
                
                # Debug (可选，如果不再需要可删除)
                # _LOGGER.debug(f"[{pdu_id}] HTTP Response len: {len(text)}")

                # 正则匹配
                matches = list(re.finditer(r"td2_(\d+)'?\)\.innerText\s*=\s*'([^']*)'", text))
                
                if matches:
                    for m in matches:
                        idx = int(m.group(1))
                        raw = m.group(2).strip()
                        num = re.search(r"[-+]?[0-9]*\.?[0-9]+", raw)
                        if num:
                            val = round(float(num.group(0)), 3)
                            self.coordinator.update_sensor_data(pdu_id, f"current_{idx}", val)
                else:
                    _LOGGER.debug(f"[{pdu_id}] 未能从 HTTP 响应中匹配到电流数据")

            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.debug(f"[{pdu_id}] 抓取电流失败: {e}")
            
            await asyncio.sleep(30) # 30秒抓取一次

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
                                
                                # 启动分口电流抓取任务 (如果启用)
                                if self.fetch_outlet_current:
                                    _LOGGER.info(f"[{pdu_id}] 正在启动分口电流抓取任务 (目标: {addr[0]})")
                                    task = asyncio.create_task(self._fetch_outlet_currents(pdu_id, addr))
                                    self._background_tasks.add(task)
                                    task.add_done_callback(self._background_tasks.discard)

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
                     if self.connection_pool[pdu_id][1] == writer:
                        del self.connection_pool[pdu_id]
                        await self.device_registry.async_set_device_connected(pdu_id, False)

            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _create_entities_for_device(self, pdu_id: str):
        """为新设备创建实体
        
        Args:
            pdu_id: PDU 设备 ID
        """
        if "add_switch_entities" not in self.hass.data[DOMAIN].get(self.entry_id, {}):
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

        if switch_entities and "add_switch_entities" in self.hass.data[DOMAIN].get(self.entry_id, {}):
            self.hass.data[DOMAIN][self.entry_id]["add_switch_entities"](switch_entities)
            _LOGGER.info(f"为 PDU {pdu_id} 创建了 {len(switch_entities)} 个开关实体")

        # 创建传感器实体(强制包含基础电参传感器)
        default_sensors = {"power", "current", "voltage"}
        
        # 如果启用了分口电流抓取，预先添加对应的传感器
        if self.fetch_outlet_current:
             for i in range(1, 9):
                 default_sensors.add(f"current_{i}")

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

        if sensor_entities and "add_sensor_entities" in self.hass.data[DOMAIN].get(self.entry_id, {}):
            self.hass.data[DOMAIN][self.entry_id]["add_sensor_entities"](sensor_entities)
            _LOGGER.info(f"为 PDU {pdu_id} 创建了 {len(sensor_entities)} 个传感器实体")
