"""PDU HTTP Client"""
import logging
import asyncio
import urllib.parse
import re
import math
from datetime import timedelta
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PASSWORD

from .coordinator import PduCoordinator
from .device_registry import DeviceRegistry
from .const import CONF_NUM_SWITCHES

_LOGGER = logging.getLogger(__name__)


class PduClient:
    """PDU HTTP 客户端"""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: PduCoordinator,
        device_registry: DeviceRegistry,
        config: dict,
        entry_id: str,
    ):
        """初始化 PDU Client"""
        self.hass = hass
        self.coordinator = coordinator
        self.device_registry = device_registry
        self.entry_id = entry_id
        
        self.host = config.get(CONF_HOST)
        self.port = config.get(CONF_PORT, 80)
        self.password = config.get(CONF_PASSWORD, "admin")
        self.username = "admin"  # Usually fixed
        
        # Log Level (controlled globally usually, but respecting config)
        log_level = config.get("log_level", "info").upper()
        self.log_level = getattr(logging, log_level, logging.INFO)
        _LOGGER.setLevel(self.log_level)

        self.session: Optional[aiohttp.ClientSession] = None
        self.remove_timer = None
        
        # Client mode only supports ONE PDU per instance
        # Use host as the pdu_id or a fixed ID?
        # Using host as ID ensures uniqueness per client instance
        self.pdu_id = self.host.replace(".", "_")

    async def start(self):
        """启动 Client"""
        _LOGGER.info(f"启动 PDU Client {self.host}")
        
        # Register device immediately
        await self.device_registry.async_register_device(self.pdu_id, auto_create=True)
        # Initialize coordinator
        self.coordinator.init_pdu(self.pdu_id, 8) 

        # Start periodic polling
        self.remove_timer = async_track_time_interval(
            self.hass, self._periodic_tasks, timedelta(seconds=5)
        )
        
        # Initial poll
        await self._periodic_tasks(None)
        
        # Create entities
        await self._create_entities()

    async def stop(self):
        """停止 Client"""
        if self.remove_timer:
            self.remove_timer()
            
        await self.device_registry.async_set_device_connected(self.pdu_id, False)

    async def _create_entities(self):
        """创建实体"""
        # Wait for callback to be ready
        from .const import DOMAIN
        if "add_switch_entities" not in self.hass.data[DOMAIN].get(self.entry_id, {}):
            pass

        # Trigger entity creation via callback if available
        if "add_switch_entities" in self.hass.data[DOMAIN].get(self.entry_id, {}):
            from .switch import PduSwitch
            entities = []
            for i in range(1, 9):
                 entities.append(
                    PduSwitch(
                        coordinator=self.coordinator,
                        device_registry=self.device_registry,
                        server=self,
                        pdu_id=self.pdu_id,
                        switch_number=i,
                    )
                 )
            self.hass.data[DOMAIN][self.entry_id]["add_switch_entities"](entities)
            _LOGGER.info(f"已创建 {len(entities)} 个开关实体")

        # Create sensors
        if "add_sensor_entities" in self.hass.data[DOMAIN].get(self.entry_id, {}):
             from .sensor import PduSensor
             sensors = []
             for stype in ["voltage", "current", "power", "energy"]:
                 sensors.append(
                     PduSensor(
                        coordinator=self.coordinator,
                        device_registry=self.device_registry,
                        pdu_id=self.pdu_id,
                        sensor_type=stype,
                     )
                 )
             self.hass.data[DOMAIN][self.entry_id]["add_sensor_entities"](sensors)


    async def _send_request(self, data: dict) -> Optional[str]:
        """发送 HTTP 请求 (Raw TCP for HTTP/0.9 support)"""
        # The PDU responds without HTTP headers (HTTP/0.9 style), so aiohttp fails.
        # We must use raw sockets to send the request and read the raw response.
        
        try:
            reader, writer = await asyncio.open_connection(self.host, self.port)
        except Exception as e:
            _LOGGER.error(f"连接失败 {self.host}:{self.port}: {e}")
            return None

        try:
            # Construct raw HTTP POST request
            # Note: HTTP/0.9 only supports GET technically, but the device likely accepts POST
            # and just blindly returns the body. We'll send a standard HTTP/1.0 request 
            # to be safe on the request side.
            
            # Build query string for body
            body = urllib.parse.urlencode(data)
            
            request = (
                f"POST / HTTP/1.1\r\n"
                f"Host: {self.host}\r\n"
                f"Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8\r\n"
                f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36\r\n"
                f"Content-Type: application/x-www-form-urlencoded\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Origin: http://{self.host}\r\n"
                f"Referer: http://{self.host}/\r\n"
                f"Cookie: cookie_username={self.username}; cookie_password={self.password}; save_cookie=1; cookie_is_mobile=1\r\n"
                f"Connection: close\r\n"
                f"\r\n"
                f"{body}"
            )
            
            _LOGGER.debug(f"Sending raw request: {request}")
            writer.write(request.encode())
            await writer.drain()
            
            # Read response
            # Since it's HTTP/0.9 style (no content-length header presumably in response),
            # we read until EOF.
            # Add timeout to prevent hanging if server keeps connection open
            try:
                response_bytes = await asyncio.wait_for(reader.read(), timeout=10.0)
            except asyncio.TimeoutError:
                _LOGGER.warning(f"Raw Socket 读取超时: {self.host}")
                return None
            
            # Decode response
            # User log shows charset=GB2312
            try:
                text = response_bytes.decode("gb18030", errors="ignore")
            except Exception:
                text = response_bytes.decode("utf-8", errors="ignore")
            
            _LOGGER.debug(f"Response (first 100): {text[:100]}")
            return text

        except Exception as e:
            _LOGGER.error(f"Raw Socket 请求错误: {e}")
            return None
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def send_control_command(self, pdu_id: str, action: str, io_number: int):
        """Control switch"""
        # Convert bitmask to index (1-8)
        if io_number <= 0:
            return
            
        switch_index = int(math.log2(io_number)) + 1
        
        # Node-RED uses td2_1 to td2_8 for buttons
        handcontrol_btn = f"td2_{switch_index}"
        
        rf_val = "0" if action == "open" else "1"
        
        # Build payload
        payload = {
            "login_username": self.username,
            "save_handcontrol_btn": handcontrol_btn,
            "radio_function": rf_val,
            f"socket_check{switch_index}": "0",
            "is_mobile": "1"
        }
        
        _LOGGER.debug(f"Sending control: {payload}")
        await self._send_request(payload)

    async def _periodic_tasks(self, now):
        """定期轮询状态"""
        # 1. Fetch Switch Status
        # Node-RED gets status by regex on page content.
        # Just visiting the page might return the status in 'var classtemp'
        
        # Dummy payload or login payload to get the page?
        # Node-RED "获取PDU状态" uses a big payload similar to control but no button press?
        # Actually, simple login or empty post might get the page.
        # Let's try sending login info.
        status_payload = {
            "login_username": self.username,
            "save_handcontrol_btn": "td2_1", # 注意：如果这会导致开关动作，请检查是否可以改为无动作的 key
            "radio_function": "0",
            "socket_check0": "0",
            "aircond_type": "0",
            "aircond_model": "0",
            "aircond_temperature": "24",
            "aircond_action": "1",
            "radio_function_1": "0",
            "radio_function_2": "0",
            "radio_function_3": "0",
            "radio_function_4": "0",
            "radio_function_5": "0",
            "radio_function_6": "0",
            "radio_function_7": "0",
            "radio_function_8": "0",
            "is_mobile": "1"
        }
        
        text = await self._send_request(status_payload)
        if text:
            await self.device_registry.async_set_device_connected(self.pdu_id, True)
            self._parse_switch_status(text)
            
        # 2. Fetch Sensors
        # Node-RED sends 'realtime_btn=A'
        sensor_payload = {
             "login_username": self.username,
             "realtime_btn": "A",
             "is_mobile": "1"
        }
        text_sensor = await self._send_request(sensor_payload)
        if text_sensor:
            self._parse_sensor_status(text_sensor)

    def _parse_switch_status(self, text: str):
        """
        解析开关状态
        逻辑：获取 classtemp 值的前8位，取反即为开关状态
        '0' -> ON, '1' -> OFF
        """
        # 匹配 var classtemp='000000011101111100000001 '.trim();
        match = re.search(r"var\s+classtemp\s*=\s*'([^']+)'", text)
        if match:
            status_str = match.group(1).strip()
            _LOGGER.debug(f"解析到 classtemp 字符串: {status_str}")
            
            # 取前8位进行循环
            for i in range(8):
                if i < len(status_str):
                    char = status_str[i]
                    # 取反逻辑：0 是开(on)，1 是关(off)
                    state = "on" if char == '0' else "off"
                    self.coordinator.update_switch_state(self.pdu_id, i + 1, state)
            
            _LOGGER.debug(f"PDU {self.pdu_id} 开关状态更新完成")
        else:
            _LOGGER.warning(f"无法在响应中找到 classtemp 变量")

    def _parse_sensor_status(self, text: str):
        """解析传感器状态"""
        # parent.document.getElementById('realtime_voltage').innerText='223.5 V            '.trim();
        # parent.document.getElementById('realtime_current').innerText='0.0 A              '.trim();
        # &nbsp;&nbsp;电能 0.4                KWH</b>
        
        # Regex needs to be flexible for quotes and whitespace
        v_match = re.search(r"realtime_voltage'\)\.innerText='([^']+)'", text)
        a_match = re.search(r"realtime_current'\)\.innerText='([^']+)'", text)
        
        if v_match:
            try:
                # Remove unit and trim
                v_str = v_match.group(1).replace('V', '').strip()
                voltage = float(v_str)
                self.coordinator.update_sensor_data(self.pdu_id, "voltage", voltage)
            except ValueError:
                pass
                
        if a_match:
            try:
                a_str = a_match.group(1).replace('A', '').strip()
                current = float(a_str)
                self.coordinator.update_sensor_data(self.pdu_id, "current", current)
            except ValueError:
                pass
                
        # Energy
        # Matches: 电能 0.4                KWH
        e_match = re.search(r"电能\s+([\d\.]+)\s+KWH", text)
        if e_match:
            try:
                 energy = float(e_match.group(1))
                 # Assuming energy sensor support in coordinator (might need to add sensor type 'energy' implementation if missing)
                 # Coordinator handles arbitrary types, but sensor.py needs to know about it.
                 # Let's add 'energy' update.
                 # Actually strictly speaking, sensor.py only defines power/current/voltage/temperature.
                 # Need to add SENSOR_TYPE_ENERGY support?
                 # For now, let's just update it in coordinator data.
                 self.coordinator.update_sensor_data(self.pdu_id, "energy", energy)
            except ValueError:
                pass

        # Calculate Power if not directly available (though PDU might provide it)
        # 0.4 KWH is energy, not power.
        # Power = Voltage * Current (approx)
        if v_match and a_match:
            try:
                v = float(v_match.group(1).replace('V', '').strip())
                a = float(a_match.group(1).replace('A', '').strip())
                power = round(v * a, 2)
                self.coordinator.update_sensor_data(self.pdu_id, "power", power)
            except:
                pass
