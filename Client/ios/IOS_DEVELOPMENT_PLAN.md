# superweb-client iOS 端开发计划

## 目标

为 `Client/` 项目新增一个 iOS 原生客户端，交互结构按你要求拆成三层，并参考微信的信息架构与聊天视觉：

1. 第一界面：扫描并连接 `main node`
2. 第二界面：选择 `method`，交互上等同于“选择联系人”
3. 第三级界面：聊天页，上方显示消息记录，下方显示参数配置和发送区

现有仓库里已经有可以直接映射的协议和行为：

- 发现主节点：`python/superweb_client/discovery.py`
- TCP 会话与请求发送：`python/superweb_client/session.py`
- 运行时协议：`proto/superweb_cluster_runtime.proto`
- 现成聊天式桌面端参考：`windows_native/winui3/`

## 页面结构计划表

| 层级 | 页面 | UI 参考 | 主要功能 | 对应现有能力 |
| --- | --- | --- | --- | --- |
| 1 | 扫描连接页 | 微信启动后的“连接/发现”页风格，顶部标题，中间扫描状态卡片，底部手动输入兜底 | 扫描局域网 `main node`、显示发现结果、手动输入 `host:port`、执行 `CLIENT_JOIN` | `discover_main_node()`、`connect_session()`、`join()` |
| 2 | Method 列表页 | 微信联系人列表 | 展示 `gemv`、`conv2d`、`free_content/custom` 等 method，显示最近使用和说明 | `METHOD_GEMV`、`METHOD_CONV2D`、`METHOD_FREE_CONTENT` |
| 3 | 聊天详情页 | 微信聊天页 | 上半部分展示系统消息、请求消息、响应消息；下半部分是参数配置区和发送按钮 | `request_gemv()`、`request_conv2d()`、`request_free_content()` |

## 开发计划表

| 阶段 | 预计时间 | 目标 | 关键工作 | 交付物 |
| --- | --- | --- | --- | --- |
| 0. 协议对齐 PoC | 1 天 | 先确认 iOS 能否直接发现并连接现有主节点 | 验证 `_superweb-cluster._tcp.local.` 是否可被 iOS Bonjour/NWBrowser 直接发现；若不行，按 `wire/discovery.py` 复刻 UDP mDNS 查询；验证 TCP 长连接和 length-prefixed protobuf 收发 | 一份可跑通 discovery + join 的最小 PoC |
| 1. 工程搭建 | 0.5-1 天 | 建立 iOS 原生工程骨架 | 新建 `ios/` 下的 Xcode 工程；选 `SwiftUI + NavigationStack`；接入 `SwiftProtobuf`；生成 `proto/superweb_cluster_runtime.proto` 的 Swift 代码 | 可编译的 iOS 工程 |
| 2. 第一界面：扫描连接 | 1-2 天 | 完成主节点扫描和连接页 | 扫描按钮、扫描动画、节点列表、连接状态、手动地址输入、失败重试、连接成功后跳转 | 可连接 `main node` 的首页 |
| 3. 第二界面：method 列表 | 0.5-1 天 | 完成“像联系人列表一样选 method” | 列表项设计、最近使用排序、method 描述、状态徽标、点击进入聊天页 | 可选 `gemv` / `conv2d` / `custom` 的 method 列表页 |
| 4. 第三级界面：聊天页骨架 | 1-2 天 | 完成微信式消息流布局 | 顶部导航显示连接状态和 `client_id`；中部消息气泡；底部固定参数面板和发送区；系统消息、请求消息、响应消息三种样式 | 可展示消息流的聊天页 |
| 5. 参数配置面板 | 1-2 天 | 让底部配置区随 method 动态变化 | 通用参数：`iteration_count`、`object_id`、`stream_id`；`gemv` 参数：`size`、`vector seed`；`conv2d` 参数：`size`、本地下载名；`custom` 参数：`method`、UTF-8 内容 | 可按 method 生成不同表单 |
| 6. 请求发送与结果回显 | 2-3 天 | 打通真实请求与回包 | 发送 `CLIENT_REQUEST`；显示 `CLIENT_REQUEST_OK`；显示 `CLIENT_RESPONSE`；预览输出向量；错误气泡；会话断线处理 | 可完整收发 `gemv` / `conv2d` / `free_content` |
| 7. Artifact 下载与文件管理 | 1 天 | 支持大结果下载 | 根据 `result_artifact.transfer_host/transfer_port` 拉取结果；保存到 App sandbox；在聊天页展示文件名、大小、分享按钮 | 可下载并查看 `conv2d` 结果文件 |
| 8. 稳定性与测试 | 1-2 天 | 补齐质量 | 协议 round-trip 单测、 discovery 集成测试、聊天页 UI smoke test、断网/超时测试 | 可回归的测试集和稳定版 Demo |

## 建议的 iOS 技术方案

| 模块 | 建议方案 | 说明 |
| --- | --- | --- |
| UI | `SwiftUI` | 三层导航结构适合 `NavigationStack`，聊天页和表单都容易维护 |
| 状态管理 | `@Observable` 或 `ObservableObject` | 页面少、状态清晰，不需要引入更重的架构 |
| TCP Runtime | `Network.framework` 的 `NWConnection` 或底层 socket | 需要对齐当前 length-prefixed protobuf runtime 通道 |
| Discovery | 优先 `NWBrowser/Bonjour`，不通则回退到自定义 UDP mDNS | 当前仓库 discovery 是标准 DNS 记录拼包，但是否能被 Bonjour 直接消费需要 PoC 验证 |
| 协议序列化 | `SwiftProtobuf` | 直接复用 `proto/superweb_cluster_runtime.proto` |
| 下载 | `Network.framework` 或自定义 socket 传输 | 当前 `large_data_transfer.py` 使用的是自定义 framed TCP data-plane，不是 HTTP 下载 |
| 本地存储 | App Documents / Caches | 替代 Python 端的 `Client/python/download/`，桌面端则落到 exe 同级 `download/` |

## 聊天页消息模型建议

| 消息类型 | 展示方式 | 内容 |
| --- | --- | --- |
| System | 灰色居中提示 | 扫描开始、连接成功、断开、错误、下载完成 |
| Outgoing | 右侧绿色气泡 | method、参数摘要、发送时间 |
| Ack | 中性提示条 | `CLIENT_REQUEST_OK`、`task_id`、排队状态 |
| Incoming | 左侧白色气泡 | `status_code`、输出预览、错误信息、artifact 信息 |

## 关键风险与前置事项

| 风险 | 影响 | 处理方式 |
| --- | --- | --- |
| iOS 本地网络权限 | 不授权就无法扫描局域网节点 | 在 `Info.plist` 加 `NSLocalNetworkUsageDescription`，若采用 Bonjour 再补 `NSBonjourServices` |
| 当前 mDNS 行为和 Bonjour 兼容性未知 | 首页扫描可能需要自定义 UDP 实现 | 把 discovery PoC 放在第 0 阶段，先验证再决定最终实现 |
| 真机签名未配置 | 可以做模拟器开发，但不一定能直接装到 iPhone | 后续补 Apple 开发者账号或本机签名配置 |
| 大文件下载协议尚未在 iOS 侧实现 | `conv2d` 大结果不能立即闭环 | 先做小结果与 metadata，后补 artifact 下载 |

## 本机 iOS 开发环境检查

检查时间：`2026-04-18`

| 项目 | 实际结果 | 结论 |
| --- | --- | --- |
| 系统 | `macOS 26.2` on `arm64` | 满足 iOS 开发基础条件 |
| Xcode | `Xcode 26.4 (17E192)` | 已安装 |
| Swift | `Apple Swift 6.3` | 已安装 |
| iOS SDK | `iphoneos26.4` | 已安装 |
| iOS Simulator SDK | `iphonesimulator26.4` | 已安装 |
| 可用模拟器 | `iPhone 17 Pro`、`iPhone 17 Pro Max`、`iPhone 17e`、`iPhone Air`、`iPhone 17` 等 | 可以直接跑模拟器 |
| Code signing identity | `0 valid identities found` | 当前未发现可用签名身份 |

### 环境结论

这台机器可以直接开始开发 iOS 原生应用，并且可以在 iOS Simulator 上编译和运行。

当前未发现可用的 code signing identity，所以：

- 模拟器开发没有问题
- 真机安装和分发暂时不能直接确认可用
- 如果后续要上真机调试，需要补签名配置或 Apple Developer 账号

## 建议的实施顺序

1. 先做第 0 阶段 PoC，优先验证 discovery 和 TCP runtime。
2. PoC 打通后再建正式 SwiftUI 工程，避免 UI 先行但网络层返工。
3. 首版只保证 `gemv` 和 `conv2d`，`custom/free_content` 作为调试入口保留。
4. 首版先跑通模拟器，真机签名放到第二步处理。
