# ZIoT - Home Assistant 自定义集成合集

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/ZHonry/ziot.svg)](https://github.com/ZHonry/ziot/releases)
[![License](https://img.shields.io/github/license/ZHonry/ziot.svg)](LICENSE)

这是一个个人的 Home Assistant 自定义集成合集，包含我日常使用和开发的各种集成插件。

---

## 📦 包含的集成

### 🔌 PDU 相关

#### Changsui PDU（昌遂 PDU）

通过 HTTP 协议直接与昌遂品牌 PDU 硬件通信，控制插座、查看实时功率、电流等信息。

**主要功能:**
- ✅ 支持通过 UI 配置登录信息与插座数量
- ✅ 直接控制 PDU 插座开关
- ✅ 实时监控整机电参数（电压、电流、总功率、功率因数、总能耗）
- ✅ 可选显示每个插座的功率和电流传感器
- ✅ 可配置轮询间隔（默认 30 秒）
- ✅ 本地轮询，无需云服务
- ✅ 支持多台 PDU 设备
- ✅ **[优化]** 解析逻辑重构，更加健壮
- ✅ **[优化]** 增强会话管理与错误恢复

**集成域名:** `changsui_pdu`  
**支持型号:** 昌遂（CAN）系列，支持 16 孔和 20 孔型号

---

#### GWGJ PDU（GWGJ PDU 网关）

运行一个 TCP 服务器，接收 GWGJ 协议 PDU 的主动连接，实时获取数据并直接集成到 Home Assistant。

**主要功能:**
- ✅ 监听 TCP 端口并解析 GWGJ 协议数据
- ✅ 直接集成到 Home Assistant，无需 MQTT
- ✅ 自动注册新的 PDU 设备
- ✅ 动态创建开关和传感器实体
- ✅ 支持功率、电流、电压传感器
- ✅ 支持日志等级配置
- ✅ 本地推送，实时响应
- ✅ **[优化]** 采用 aiohttp 异步请求，提升稳定性
- ✅ **[优化]** 正则表达式预编译，降低资源占用

**集成域名:** `gwgj_pdu`  
**支持型号:** GWGJ 系列 PDU

---

## 🚀 安装方式

### 方法一: 通过 HACS 安装 (推荐)

1. 打开 Home Assistant 中的 **HACS**
2. 点击右上角的 **三个点** ⋮，选择 **自定义存储库**
3. 添加以下信息:
   - **存储库 URL:** `https://github.com/ZHonry/ziot`
   - **类别:** `Integration`
4. 点击 **添加**
5. 在 HACS 中搜索 **ZIoT**
6. 选择需要的集成并点击 **下载**
7. 重启 Home Assistant

### 方法二: 手动安装

1. 下载本仓库的最新版本
2. 将 `custom_components` 目录下你需要的集成复制到你的 Home Assistant 配置目录下的 `custom_components` 文件夹中
3. 重启 Home Assistant

**可用的集成:**
- `changsui_pdu` - 昌遂 PDU 集成
- `gwgj_pdu` - GWGJ PDU 网关

---

## ⚙️ 配置说明

### Changsui PDU 配置

1. 进入 **设置** → **设备与服务**
2. 点击 **添加集成**
3. 搜索 **Changsui PDU**
4. 按照提示输入配置信息

<details>
<summary>📋 配置选项详情</summary>

- **主机地址**: PDU 设备的 IP 地址
- **用户名**: 登录用户名（默认 `admin`）
- **密码**: 登录密码（默认 `admin`）
- **插座数量**: PDU 的插座数量（16 或 20）
- **PDU 名称**: 自定义设备名称（默认 "昌遂PDU"）
- **显示插座电流传感器**: 是否为每个插座创建电流传感器
- **显示插座功率传感器**: 是否为每个插座创建功率传感器
- **显示负载上下限**: 是否显示电流限制信息
- **轮询间隔**: 数据更新间隔（秒，默认 30）

</details>

### GWGJ PDU 配置

1. 进入 **设置** → **设备与服务**
2. 点击 **添加集成**
3. 搜索 **GWGJ PDU**
4. 按照提示输入配置信息

<details>
<summary>📋 配置选项详情</summary>

- **监听地址**: TCP 服务器监听地址（默认 `0.0.0.0`）
- **监听端口**: TCP 服务器端口（默认 `4600`）
- **日志等级**: 日志详细程度（可选，默认 `info`）

**设备配置文件位置:** `custom_components/gwgj_pdu/gwgj_pdu_ids/`

</details>

---

## 📊 实体说明

<details>
<summary>📌 Changsui PDU 实体列表</summary>

**开关实体:**
- `switch.{pdu_name}_outlet_{n}`: 插座开关（n = 1-16 或 1-20）

**传感器实体:**
- `sensor.{pdu_name}_电压`: 整机电压（V）
- `sensor.{pdu_name}_电流`: 整机电流（A）
- `sensor.{pdu_name}_总功率`: 整机总功率（W）
- `sensor.{pdu_name}_功率因数`: 功率因数
- `sensor.{pdu_name}_总能耗`: 总能耗（kWh）
- `sensor.{pdu_name}_今日能耗`: 今日能耗（kWh）
- `sensor.{pdu_name}_outlet_{n}_功率`: 插座功率（可选）
- `sensor.{pdu_name}_outlet_{n}_电流`: 插座电流（可选）

</details>

<details>
<summary>📌 GWGJ PDU 实体列表</summary>

**开关实体:**
- `switch.pdu_{device_id}_switch_{n}`: 插座开关（n = 1-8）

**传感器实体:**
- `sensor.pdu_{device_id}_功率`: 整机功率（W）
- `sensor.pdu_{device_id}_电流`: 整机电流（A）
- `sensor.pdu_{device_id}_电压`: 整机电压（V）

</details>

---

## 📝 常见问题

<details>
<summary>❓ 两个 PDU 集成有什么区别？</summary>

**Changsui PDU** 通过 HTTP 主动轮询昌遂品牌 PDU，适合直接控制单台或多台昌遂设备；**GWGJ PDU** 作为 TCP 服务器被动接收 GWGJ 协议设备的连接，适合需要集中管理多台 GWGJ 设备的场景。两者使用不同的通信协议和品牌。

</details>

<details>
<summary>❓ 可以同时使用多个集成吗？</summary>

可以，所有集成都是独立的，互不影响。您可以根据需要安装和使用任意组合。

</details>

<details>
<summary>❓ 如何添加新的集成到这个仓库？</summary>

这是一个个人集成合集，如果您有好的集成想法或需求，欢迎提交 Issue 讨论！

</details>

---

## 🗺️ 开发计划

- [x] Changsui PDU 集成
- [x] GWGJ PDU 集成
- [ ] 更多集成开发中...

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

如果您有好的集成想法或发现了 Bug，请随时提出。

## 📄 许可证

本项目采用 MIT 许可证。

---

**注:** AI 辅助开发，持续更新中 😊