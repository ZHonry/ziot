"""PDU 组件常量定义"""

DOMAIN = "gwgj_pdu"

# 配置键
CONF_HOST = "host"
CONF_PORT = "port"
CONF_LOG_LEVEL = "log_level"

# 默认值
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 4600
DEFAULT_LOG_LEVEL = "info"
DEFAULT_NUM_SWITCHES = 8

# 设备配置键
CONF_IDENTIFIERS = "identifiers"
CONF_MANUFACTURER = "manufacturer"
CONF_MODEL = "model"
CONF_NAME = "name"
CONF_SW_VERSION = "sw_version"
CONF_CONFIGURATION_URL = "configuration_url"
CONF_NUM_SWITCHES = "num_switches"

# 默认设备配置
DEFAULT_MANUFACTURER = "Generic"
DEFAULT_MODEL = "PDU"
DEFAULT_SW_VERSION = "1.0.0"

# 传感器类型(根据 PDU 实际发送的数据自动发现)
SENSOR_TYPE_POWER = "power"
SENSOR_TYPE_CURRENT = "current"
SENSOR_TYPE_VOLTAGE = "voltage"
SENSOR_TYPE_TEMPERATURE = "temperature"

# 数据键
DATA_COORDINATOR = "coordinator"
DATA_DEVICE_REGISTRY = "device_registry"
DATA_SERVER = "server"
DATA_UNSUB = "unsub"
