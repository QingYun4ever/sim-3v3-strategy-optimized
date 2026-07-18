# Booster Agent Framework Python API

## API组成与使用概述

### 1\.1 模块划分

可用 API 可分为以下几个子系统：

- Agent 基类：     运行配置、生命周期响应、组件管理器获取。

- UI 组件系统：    组件声明、组件更新、页面切换、Toast 推送。

- 参数系统：        参数读写、参数校验、参数变更回调。

- 手柄与快捷键：  组合键描述、快捷键元数据查询。

- 机器人状态：     机器人模式等运行状态访问。

- 存储系统：        Agent 配置目录下的文件读写。

- 工具函数：        配置读取、系统 API Level 查询。

### 1\.2 使用范式

step1\. 导入接口: import `booster_agent_framework`模块

step2\. 实现Agent: 继承`AgentBase`类, 复写/扩展方法

step3\. 通过Agent类`self`指针获取顶层API对象

- `self.component_m``ana``g``e``r`：访问组件管理器。

- `self.parameter_m``ana``g``e``r`：访问参数管理器。

- `self.storage_m``ana``g``e``r`：访问存储管理器。

- `self.robot_states`：读取当前机器人状态集合。

- `self.agent_state`：读取当前 Agent 生命周期状态。

- `self.logger`：访问日志对象。

step4\. 调用具体接口

### 1\.3 最小Agent示例

```Python
from booster_agent_framework import (
    AgentBase,
    AgentFeatures,
    DefaultStateIconComponent,
    LocaleString,
)


class MyAgent(AgentBase):
    def __init__(self) -> None:
        super().__init__(AgentFeatures())

        demo_component = DefaultStateIconComponent(
            "demo",
            LocaleString("Demo", "示例"),
            "res/demo.png",
            False,
            self.on_demo_click,
        )
        self.component_manager.add_component(demo_component)

    def on_demo_click(self, component):
        self.logger.info(f"clicked: {component.id}")
        return LocaleString("Clicked", "已点击")

    def on_agent_activated(self) -> None:
        self.logger.info("agent activated")

    def on_agent_close(self) -> None:
        self.logger.info("agent closing")
```

### 1\.4 线程模型说明

框架的 API 回调模型类似 Android 的事件回调模型，所有用户回调都在主线程串行执行，而不是每个回调运行在独立业务线程中。框架会等待需要返回结果的回调完成，因此单个回调耗时过长会阻塞后续 Python 回调、延迟当前请求的返回，并可能造成前端 APP 操作卡顿。

**注意事项：**

- 回调应尽快返回，不要在回调里做长时间 IO、sleep、join、future wait，也不要等待另一个 Python 回调、app 后续操作、参数事件或 robot state 事件来完成当前回调。

- Python 中使用 `asyncio` 不等于后台执行：`asyncio.run()` 仍会占住当前回调直到 coroutine 完成，只创建 coroutine 但不运行也不会产生实际异步效果。

**耗时操作建议方案：**

- 耗时任务应投递到开发者自己的后台线程、进程或外部服务中执行；

- 回调只做参数校验、状态记录、任务启动并快速返回。任务完成后再通过状态发布、组件更新、toast、事件或下次查询通知结果；

- 如果必须同步等待，应设置明确的短超时，并确认被等待的结果不依赖当前回调返回后才有机会执行。

## Agent 基类

### 2\.1 AgentBase

`AgentBase` 是所有 Python Agent 的基类。它处理与BoosterAgent管理器的通信、操纵杆事件和组件生命周期，Agent 业务类应继承该类型。

构造函数：`AgentBase(agent_features: AgentFeatures)`

**方法**

- `on_agent_activated() -> None`

Agent 激活时的生命周期回调。Python 子类可重写。

- `on_agent_close() -> None`

Agent 关闭前的生命周期回调。Python 子类可重写。

- `register_robot_states_callback(callback) -> None`

注册机器人状态变化回调。

回调签名：`Callable[[RobotStatesAggregation, RobotStatesAggregation], None]`

回调参数顺序为：`(old_state, cur_state)`。

- `call_booster_interface_api(loco_api_id, body, timeout_ms) -> tuple[int, str]`

调用 Booster Interface API\(高层运控接口\)。

- 参数：

    - `loco_api_id: int` , 取值参考：`booster_robotics_sdk_python.LocoApiId`

    - `body: str`，与 `loco_api_id` 对应的 JSON 请求体

    - `timeout_ms: int`，超时时间，单位毫秒

- 返回：

    - `tuple[int, str]`：`(status_code, response_body)`

- 示例：

```Python
from booster_robotics_sdk_python import (
    LocoApiId,
    WaveHandParameter,
    HandIndex,
    HandAction,
)

status, body = self.call_booster_interface_api(
    LocoApiId.kWaveHand,
    WaveHandParameter(HandIndex.kRightHand, HandAction.kHandOpen).to_json_str(),
    1000,
)
```

**属性**

- `agent_id: str`

当前 Agent ID。

- `agent_state: AgentState`

当前 Agent 生命周期状态。

- `component_m``ana``g``e``r: ComponentM``ana``g``e``r`

组件管理器。

- `logger: Logger`

日志对象。

- `parameter_m``ana``g``e``r: BoosterAgentParameterM``ana``g``e``r`

参数管理器。

- `robot_states: RobotStatesAggregation`

机器人当前状态集合。

- `storage_m``ana``g``e``r: StorageM``ana``g``e``r`

存储管理器。

### 2\.2 AgentFeatures

`AgentFeatures` 用于配置 Agent 初始化时启用的内建能力。

**构造函数：**

```Python
AgentFeatures(
    enable_orchestration: bool = True,
    enable_auto_getup: bool = False,
    auto_get_up_shortcut: JoystickEvent | None = None,
    param_schema_path: str = "",
    enable_telemetry_report: bool = False,
)
```

**参数说明：**

- `enable_orchestration`：是否启用内建自定义动作能力。

- `enable_auto_getup`：是否启用自动起身。

- `auto_get_up_shortcut`：自动起身快捷键。

- `param_schema_path`：参数 schema 文件路径，用于在APP中配置存储Agent需要的参数信息。详情请参考[AgentNode Parameter Configuration Guide\+](https://booster.feishu.cn/docx/E7qQdaGtUo1g3gx7T36cSaAWnuc)。

- `enable_telemetry_report`：是否启用遥测事件上报。

**示例：**

```Python
features = AgentFeatures(
    enable_orchestration=True,
    enable_auto_getup=True,
    auto_get_up_shortcut=JoystickEvent(
        JoystickEventType.kBUTTON_DOWN_OR_HAT,
        [JoystickKey.kLT, JoystickKey.kHAT_UP],
    ),
    param_schema_path="res/params/schema.json",
    enable_telemetry_report=False,
)
```

### 2\.3 AgentState

`AgentState` 为 Agent 生命周期状态枚举。

**成员：**

- `AgentState.kUninitialized`：尚未初始化。

- `AgentState.kInactive`：已初始化但未激活。

- `AgentState.kActive`：已激活并处于运行状态。

- `AgentState.kFinalized`：已结束生命周期。

## UI 组件系统

### 3\.1 基础组件类型

#### **`Component`**

`Component` 是所有 UI 组件的基类，代表移动应用上的一个UI元素（可点击的按钮）。

**构造函数：**

```Python
Component(
    id: str,
    name: LocaleString,
    click_callback: Callable[[Component], LocaleString | None] | None = None,
    shortcut_id: str = "",
    display_sequence: int = ComponentDisplaySequenceAuto,
)
```

**参数说明：**

- `id`：组件唯一标识。

- `name`：组件显示名称。

- `click_callback`：点击回调；返回 `LocaleString` 时框架会将其作为 Toast 文本显示。

    - 注意：click\_callback 接收到的是本次点击事件的 Component 副本，不是 ComponentManager 中的实时对象；如需刷新 UI，请用 component\.id 获取真实组件后更新；若组件由 ComponentStatePageProxy 管理，建议通过 proxy 的 predicate 驱动状态，避免在回调中手动改 state。

- `shortcut_id`：引用 `agent.toml` 中 `component_shortcuts.shortcut_list` 定义的快捷键 ID。

- `display_sequence`：显示顺序；按数值从小到大排序，数值小的优先显示，通常表现为从左到右；默认自动分配。

**属性：**

- `display_sequence: int`，可读写。按数值从小到大排序，数值小的优先显示。

- `id: str`，只读。

- `name: LocaleString`，可读写。

- `shortcut: JoystickEvent`，可读写。

- `type: ComponentType`，只读，枚举类型。

    - `kICON`（无状态）

    - `kSTATE_ICON`（整数状态）

    - `kDEFAULT_STATE_ICON`（布尔状态）。



#### **`IconComponent`**

带图标路径的基础图标组件，继承 `Component`。

**构造函数：**

```Python
IconComponent(
    id: str,
    name: LocaleString,
    icon_path: str,
    click_callback: Callable[[Component], LocaleString | None] | None = None,
    shortcut_id: str = "",
    display_sequence: int = ComponentDisplaySequenceAuto,
)
```

**属性：**

- `activated: bool`，可读写。是否已激活，false没有激活，true已经激活。

- `need_activate: bool`，可读写。是否需要激活，false不需要，true需要。

- `path: str`，可读写。图标图片的文件路径。



#### **`StateIconComponent`**

整型状态组件，继承 `IconComponent`。

**构造函数：**

```Python
StateIconComponent(
    id: str,
    name: LocaleString,
    icon_path: str,
    state: int,
    click_callback: Callable[[Component], LocaleString | None] | None = None,
    shortcut_id: str = "",
    display_sequence: int = ComponentDisplaySequenceAuto,
)
```

**属性：**

- `state: int`，可读写。组件当前的状态值。

适合多状态图标，例如 0/1/2 三态展示。



#### **`DefaultStateIconComponent`**

布尔状态组件，继承 `StateIconComponent`。组件状态将会在APP或Studio中通过高亮等方式展示。

**构造函数：**

```Python
DefaultStateIconComponent(
    id: str,
    name: LocaleString,
    icon_path: str,
    state: bool,
    click_callback: Callable[[Component], LocaleString | None] | None = None,
    shortcut_id: str = "",
    display_sequence: int = ComponentDisplaySequenceAuto,
)
```

**属性：**

- `state: bool`，可读写。组件当前的状态值。

适合开关类组件。

**示例：**

```Python
from booster_agent_framework import Component, DefaultStateIconComponent, LocaleString


def on_wave_click(component: Component) -> LocaleString:
    wave = component
    wave.state = not wave.state
    self.component_manager.update_component(wave)

    if wave.state:
        return LocaleString("Waving", "正在挥手")
    return LocaleString("Wave Stopped", "已停止挥手")


self.wave_component = DefaultStateIconComponent(
    "wave",
    LocaleString("Wave", "挥手"),
    "res/wave.png",
    False,
    on_wave_click,
)

self.component_manager.add_component(self.wave_component)
```



#### **`PlaceholderComponent`**

占位组件，继承 `IconComponent`。

**构造函数：**

```Python
PlaceholderComponent(
    id: str,
    name: LocaleString,
    icon_path: str,
    display_sequence: int = ComponentDisplaySequenceAuto,
)
```



#### **`OnlineWebviewComponent`**

在线网页组件，继承 `IconComponent`。点击后在 App 内通过 WebView 打开指定网页，并支持 Agent 与前端页面之间进行消息交互。

`Web` 页面发起的请求经 `App `转发后会进入 `Agent `侧 `callback`，`callback` 接收开发者自定义的 `JSON `兼容请求对象，并将返回结果同步回传给页面。若 `Agent `需要主动向页面发送消息，可调用 `component_manager.push_component_message(component, message)`，其中 `message `应

为开发者自定义的 `JSON `兼容对象。

`App `会在 `WebView `中注入 `window.BoosterNativeBridge` 供页面调用。`Web `页面可通过 `BoosterNativeBridge.sendMessageToAgent(req)`与 `Agent `通信，通过 `registerPushCallback(callback) `接收 `Agent `主动推送的消息。

**构造函数：**

```Python
OnlineWebviewComponent(
    id: str,
    name: LocaleString,
    icon_path: str,
    url: str,
    callback: Callable[[Any], Any] | None = None,
    orientation: ComponentOrientation = ...,
    shortcut_id: str = "",
    display_sequence: int = ComponentDisplaySequenceAuto,
)
```

**参数说明：**

- `url`：在线页面地址。

- `callback`：处理 WebView 请求消息的回调。请求和返回值均建议使用 JSON 兼容对象。

- `orientation`：页面方向。

    **`ComponentOrientation`**

    Online WebView 方向枚举。

    **成员：**

    - `kAUTO`：自动选择显示方向。

    - `kLANDSCAPE`：横屏显示。

    - `kPORTAIT`：竖屏显示。

**示例：**

```Python
def webview_callback(request: object) -> object:
    if not isinstance(request, dict):
        return {"success": False, "error": "request must be a JSON object"}

    command = request.get("command")
    if command == "ping":
        return {"message": "pong from agent"}
    if command == "push_test":
        self.component_manager.push_component_message(
            self.online_webview_component,
            {"event": "push_from_agent"},
        )
        return {"success": True}
    return {"success": False, "error": f"unknown command: {command}"}

self.online_webview_component = OnlineWebviewComponent(
    "web",
    LocaleString("Web test", "网页测试"),
    "res/web.png",
    "www.xxxx.com",
    webview_callback,
    ComponentOrientation.kAUTO,
)
```

```JavaScript

  const req = {
    command: "ping"
  };

const res = await window.BoosterNativeBridge.**sendMessageToAgent**(req);
```

### 3\.2 UI组件管理器

#### ComponentManager

`ComponentM``ana``g``e``r` 负责组件的增删改查、Toast 推送以及组件消息推送。

可通过 `AgentBase.component_m``ana``g``e``r` 访问。

**方法**

- `add_component(component) -> None`

添加单个组件。

- `add_components(components: Sequence[Component]) -> None`

批量添加组件。

- `get_component(component_id) -> Component | None`

按组件 ID 查询组件，组件不存在时返回 `None`。

- `get_components() -> dict[str, Component]`

获取 general section 下的组件映射。

- `update_component(component) -> None`

更新已注册组件。

该接口用于把组件状态变化同步到 App。

- `remove_component(component) -> None`

移除组件。

- `push_component_message(component, message) -> None`

向指定组件推送消息。该接口主要用于 WebView 类组件的双向通信。

参数 `message` 应为 JSON 兼容对象。

- `publish_toast(message, position=ToastPosition.kCENTER, icon=ToastIcon.kNONE) -> None`

向 App 推送 Toast。

示例：

```Python
self.component_manager.publish_toast(
    LocaleString({"en": "Saved", "zh": "已保存"}),
    ToastPosition.kCENTER,
    ToastIcon.kSUCCESS,
)
```

**`ToastIcon`**

Toast 图标枚举：

- `kNONE`：不显示图标。

- `kSUCCESS`：显示成功图标。

- `kWARNING`：显示警告图标。

- `kERROR`：显示错误图标。

**`ToastPosition`**

Toast 位置枚举：

- `kCENTER`：显示在屏幕中间。

- `kBOTTOM`：显示在屏幕底部。

- `kTOP`：显示在屏幕顶部。

**属性**

- `shortcut_m``ana``g``e``r: ShortcutM``ana``g``e``r`

快捷键管理器。

### 3\.3 UI组件状态页代理

#### ComponentStatePageProxy

`ComponentStatePageProxy` 用于根据机器人状态自动切换页面，并自动管理页面内组件的显示与状态刷新。

可通过该类型实现以下逻辑：

- 根据运行状态切换不同页面。

- 仅在激活页面中显示对应组件。

- 根据状态对象动态更新 `StateIconComponent` / `DefaultStateIconComponent` 的状态值。

构造函数：`ComponentStatePageProxy(agent: AgentBase)`

**方法**

- `register_page(page_id, predicate) -> None`

注册页面。

`predicate`回调签名：`Callable[[str, RobotStatesAggregation], bool]`。当`predicate`返回 `True` 时，该页面被判定为激活页面。

- `register_component(page_id, component, predicate) -> None`

向页面注册组件，并提供组件状态谓词。

`predicate`回调签名：`Callable[[Component, RobotStatesAggregation], ``int]`

返回值会作为组件默认状态。

- `register_component(page_id, component) -> None`

向页面注册组件，并使用默认状态谓词。

默认状态谓词始终返回 `0`。

- `register_component(page_id, components: Sequence[Component]) -> None`

向页面批量注册组件，并使用默认状态谓词。

默认状态谓词始终返回 `0`。

- `get_component(page_id, component_id) -> Component | None`

按页面和组件 ID 查询组件。

- 说明：

    - 组件不存在时返回 `None`。

    - 页面不存在时会抛异常。

- `unregister_component(page_id, component_id) -> None`

从页面注销组件。

- `force_update() -> None`

基于当前最新机器人状态，立即重新计算激活页面和组件状态。

**属性**

- `active_page: str`

当前激活页面 ID；没有激活页面时为空字符串。

- `all_components: set[Component]`

该代理管理的全部组件集合。

**使用示例**

```Python
from booster_agent_framework import (
    ComponentStatePageProxy,
    DefaultStateIconComponent,
    LocaleString,
    RobotMode
)

self.page_proxy = ComponentStatePageProxy(self)

self.page_proxy.register_page(
    "Walking",
    lambda page_id, robot_state: robot_state.robot_states_.current_mode == RobotMode.WALKING
)

self.wave_component = DefaultStateIconComponent(
    "wave",
    LocaleString("Wave", "挥手"),
    "res/wave.png",
    False,
    self.on_wave_click,
)

self.page_proxy.register_component(
    "Walking",
    self.wave_component,
)
```

**注意事项**

- `ComponentStatePageProxy` 应作为 Agent 实例成员长期持有，例如 `self.page_proxy`。

- 该对象应与 Agent 生命周期保持一致，不应在局部函数中临时创建后立即失去引用。

- 页面谓词应设计为互斥，避免多个页面在同一时刻同时满足激活条件。

## 参数系统

参数系统依赖 `AgentFeatures.param_schema_path` 指定的 schema 文件。详情请参考[AgentNode Parameter Configuration Guide\+](https://booster.feishu.cn/docx/E7qQdaGtUo1g3gx7T36cSaAWnuc)。

使用时注意以下行为：

- 参数类型推断依赖 schema。

- 参数回调仅对 schema 中定义的参数生效。

- 使用 `(name, value)` 写入参数时，框架会先根据当前参数定义推断类型。

### 4\.1 基础类型

#### BoosterAgentParameterType

参数类型枚举。

成员：

- `BOOL`：布尔值。

- `INT`：整数值。

- `INT_ARRAY`：整数数组。

- `FLOAT`：浮点数值。

- `FLOAT_ARRAY`：浮点数数组。

- `STRING`：字符串值。

- `STRING_ARRAY`：字符串数组。

- `ENUM_INT`：整数枚举值。

- `ENUM_FLOAT`：浮点数枚举值。

- `ENUM_STRING`：字符串枚举值。

- `ENUM_MULTI_INT`：多选整数枚举值。

- `ENUM_MULTI_FLOAT`：多选浮点数枚举值。

- `ENUM_MULTI_STRING`：多选字符串枚举值。

#### BoosterAgentParameter

表示单个参数值对象。

**构造方式**

- 基于 schema 推断类型

    `BoosterAgentParameter(name: str, value: Any)`

    - 该方式要求：

        - 存在可用的 Agent 运行时。

        - 参数名已在 schema 中声明。

否则会抛出 `ValueError`。

- 显式指定参数类型

`BoosterAgentParameter(type: BoosterAgentParameterType, name: str, value: bool | int | float | str | list[int] | list[float] | list[str])`

**取值方法**

`as_bool() -> bool`

`as_int() -> int`

`as_float() -> float`

`as_string() -> str`

`as_int_array() -> list[int]`

`as_float_array() -> list[float]`

`as_string_array() -> list[str]`

**属性**

`type``_``: BoosterAgentParameterType`：参数类型

**字符串表示**

`str(param)`：JSON 字符串表示。

`repr(param)`：调试用 JSON 字符串表示。

#### BoosterAgentParameterEvent

参数变更事件。

**方法**

- `empty() -> bool`

是否没有新增或更新参数。

**属性**

- `new_params: list[BoosterAgentParameter]`：本次事件中新声明/新增的参数列表。

- `updated_params: list[BoosterAgentParameter]`：本次事件中值被修改的已有参数列表。

#### SetParametersResult

参数设置结果。

属性：

- `successful: bool`：参数是否设置成功，true表示修改成功，false表示修改失败；

- `reason: str`：参数设置结果的说明，主要面向日志输出和界面提示，比如'success'，'not change'或具体的校验错误信息。

### 4\.2 参数管理器

#### BoosterAgentParameterManager

参数管理器。

可通过 `AgentBase.parameter_m``ana``g``e``r` 访问。

**回调注册**

- `add_parameter_callback(parameter_name, callback) -> None`

监听单个参数变化。

回调签名：`Callable[[BoosterAgentParameter], None]`

- `add_parameter_event_callback(callback) -> None`

监听参数事件集合。

回调签名：`Callable[[BoosterAgentParameterEvent], None]`

**参数读取**

- `get_parameter(parameter_name) -> BoosterAgentParameter`

按名称获取参数。

- 异常：

    参数不存在时抛 `ValueError`。

- `get_parameters(keys: list[str] = []) -> list[BoosterAgentParameter]`

    批量获取参数。

    `keys=[]` 表示获取全部参数。

    获取失败时抛 `ValueError`。

**参数写入**

- `set_parameter(param: BoosterAgentParameter) -> SetParametersResult`

以参数对象形式写入单个参数。

- `set_parameter(param: tuple) -> SetParametersResult`

以元组形式写入单个参数。

支持的元组形式：

`(BoosterAgentParameterType, name, value)`

`(name, value)`

- `set_parameters(params: list[BoosterAgentParameter]) -> list[SetParametersResult]`

批量写入参数对象。

- `set_parameters(params: list[tuple]) -> list[SetParametersResult]`

批量写入元组参数。

支持的元组形式：

`(BoosterAgentParameterType, name, value)`

`(name, value)`

**写入示例**

- 示例 1：显式类型写入

    ```Python
    from booster_agent_framework import BoosterAgentParameter, BoosterAgentParameterType
    
    result = self.parameter_manager.set_parameter(
        BoosterAgentParameter(BoosterAgentParameterType.INT, "maxCount", 10)
    )
    ```

- 示例 2：基于 schema 推断写入

`result = self.parameter_m``ana``g``e``r.set_parameter(("maxCount", 10))`

- 示例 3：批量写入

    ```Python
    results = self.parameter_manager.set_parameters([
        ("maxCount", 100),
        ("scale", 0.5),
        ("tags", ["tag1", "tag2"]),
    ])
    
    for result in results:
        if not result.successful:
            self.logger.warn(result.reason)
    ```

**说明**

- `set_parameters([])` 会抛 `TypeError`。

- 使用元组写入时，会对参数结构和基础类型做检查；参数元组长度不合法或类型不匹配时会抛 `TypeError`。

#### 

## 手柄与快捷键

### 5\.1 基础类型

#### JoystickEvent

`JoystickEvent` 表示一个手柄事件。

**构造方式**

```Python
JoystickEvent()
JoystickEvent(event_type: JoystickEventType, key_set: int)
JoystickEvent(event_type: JoystickEventType, key_set: int, axis_lx: float, axis_ly: float, axis_rx: float, axis_ry: float)
JoystickEvent(event_type: JoystickEventType, keys: list[str])
JoystickEvent(event_type: JoystickEventType, keys: list[JoystickKey])
```

说明：

- `JoystickEvent()` 创建空事件。

- `JoystickEvent(event_type, key_set)` 适用于已编码好的按键位图。

- `JoystickEvent(event_type, keys)` 适用于直接按按键列表构造组合键。

- 带摇杆轴参数的重载可用于表达包含轴值的事件。

**方法**

- `has_key(key: JoystickKey) -> bool`

判断当前事件是否包含指定按键。

- `is_none() -> bool`

判断当前事件是否为空事件。

- `is_valid(key_num: int) -> bool`

校验当前快捷键组合是否有效。

- 参数：

    `key_num: int`，期望的按键数量。

- `to_string() -> str`

将事件转换为字符串表示。

- `to_string_list() -> list[str]`

将事件转换为按键字符串列表。

**属性**

- `axis_lx_: float`：左摇杆 X 轴值。

- `axis_ly_: float`：左摇杆 Y 轴值。

- `axis_rx_: float`：右摇杆 X 轴值。

- `axis_ry_: float`：右摇杆 Y 轴值。

- `event_type_: JoystickEventType`：事件类型。

- `key_set_: int`：按键位图编码。

#### JoystickEventType

手柄事件类型枚举。

成员：

- `kNONE`：无事件。

- `kAXIS`：摇杆或轴值变化事件。

- `kHAT`：方向键位置变化事件。

- `kBUTTON_DOWN`：按键按下事件。

- `kBUTTON_UP`：按键抬起事件。

- `kREMOVE`：输入设备移除事件。

- `kBUTTON_DOWN_OR_HAT`：按键按下或方向键事件。

#### JoystickKey

手柄按键枚举。

成员：

- `kNONE`：无按键。

- `kA`：A 键。

- `kB`：B 键。

- `kX`：X 键。

- `kY`：Y 键。

- `kLB`：左肩键。

- `kRB`：右肩键。

- `kLT`：左扳机键。

- `kRT`：右扳机键。

- `kLS`：左摇杆按压。

- `kRS`：右摇杆按压。

- `kHAT_CENTER`：方向键居中。

- `kHAT_UP`：方向键上。

- `kHAT_DOWN`：方向键下。

- `kHAT_LEFT`：方向键左。

- `kHAT_RIGHT`：方向键右。

- `kHAT_LEFT_UP`：方向键左上。

- `kHAT_LEFT_DOWN`：方向键左下。

- `kHAT_RIGHT_UP`：方向键右上。

- `kHAT_RIGHT_DOWN`：方向键右下。

- `kBACK`：Back 键。

- `kSTART`：Start 键。

#### ShortcutInfo

快捷键信息结构。

构造函数：

```Python
ShortcutInfo()
ShortcutInfo(id: str, shortcut: JoystickEvent, locale_name: LocaleString)
```

属性：

- `id: str`：快捷键的唯一标识符。

- `shortcut: JoystickEvent`：触发该快捷键的手柄事件定义。

- `locale_name: LocaleString`：快捷键的显示名称。

### 5\.2 快捷键管理器

#### ShortcutManager

快捷键管理器。

可通过 `AgentBase.component_m``ana``g``e``r.shortcut_m``ana``g``e``r` 访问。

Agent需要使用的快捷键信息在 `agent.toml` 中定义。每个快捷键条目通常包含以下字段：

- `id`：快捷键唯一标识。

- `shortcut`：按键组合。

- `locale_name`：快捷键显示名称。

组件构造函数中的 `shortcut_id` 会引用这里定义的 `id`。运行时可通过 `ShortcutM``ana``g``e``r` 查询这些快捷键定义。

示例：

```TOML
[component_shortcuts]
version = "1.0"

[[component_shortcuts.shortcut_list]]
id = "wave"
shortcut = ["A"]
locale_name = { en = "Wave", zh = "挥手" }
```

**方法**

- `find_shortcut(shortcut) -> ShortcutInfo | None`

按手柄事件查找快捷键信息。

- 参数：

    `shortcut: JoystickEvent`

- 说明：

    未找到对应快捷键时返回 `None`。

- `get_shortcut_by_id(id) -> ShortcutInfo | None`

按快捷键 ID 查找快捷键信息。

- 参数：

    `id: str`

- 说明：

    未找到对应快捷键时返回 `None`。

- `remove_shortcuts_by_id(shortcut_ids) -> int`

按 ID 批量移除快捷键，返回成功移除的数量。

- 参数：

    `shortcut_ids: list[str]`

**属性**

- `shortcut_list: list[ShortcutInfo]`

- 当前快捷键列表。

**使用示例**

```Python
shortcut_info = self.component_manager.shortcut_manager.get_shortcut_by_id("wave")
if shortcut_info is not None:
    self.logger.info(shortcut_info.shortcut.to_string())
```

快捷键绑定示例：

```Python
wave_component = DefaultStateIconComponent(
    "waveIcon",
    LocaleString("Wave", "挥手"),
    "res/wave.png",
    False,
    self.on_wave_click,
    "wave",
)
```

其中 `"wave"` 为在 `agent.toml` 中定义的快捷键 ID。

## 机器人状态

### 6\.1 RobotStatesAggregation

机器人状态集合。

可通过 `AgentBase.robot_states` 访问，也可在机器人状态回调中获取。

**方法**

- `to_string() -> str`

- 返回当前机器人状态集合的字符串表示。

**属性**

- `robot_states_: RobotStatesMsg`

- 机器人运行主运行状态消息。

### 6\.2 RobotStatesMsg

机器人核心状态对象。

- `current_mode: int` 当前机器人模式： 

    - 0 damping 阻尼模式

    - 1 prepare 准备模式

    - 2 walking 行走模式

    - 3 custom 自定义模式

    - 4 soccer 足球模式

- `current_body_control: int` 当前机体控制状态，参考：`booster_robotics_sdk_python.BodyControl`。

- `current_actions: list[int]` 当前正在执行的动作 ID 列表，动作 ID 参考：`booster_robotics_sdk_python.Action`。

### 6\.3 FallDownState

跌倒状态对象。

可通过 `self.robot_states.fall_down_state_` 访问。

属性：

- `fall_down_state: FallDownStateType` 当前跌倒状态，例如 read、falling、fallen、getting up。

- `is_recovery_available: bool` 当前是否允许执行恢复动作，true表示当前允许执行恢复动作，false表示当前不具备恢复条件，不可使用恢复动作。

### 6\.4 FallDownStateType

跌倒状态枚举。

成员：

- `UNKNOWN`：状态未知。

- `IS_READY`：当前未跌倒，可正常恢复或继续运行。

- `IS_FALLING`：正在跌倒。

- `HAS_FALLEN`：已经跌倒。

- `IS_GETTING_UP`：正在起身。

## 存储管理

### 7\.1 StorageManager

`StorageM``ana``g``e``r` 用于读写 Agent 独立存储目录下的文件。

可通过 `AgentBase.storage_m``ana``g``e``r` 访问。

**使用约束**

- 所有文件和目录参数都必须使用相对路径。

- 绝对路径会被拒绝。

- 路径解析后如果逃逸出 Agent 独立存储目录会被拒绝，例如包含越界父目录的路径。

**静态方法**

- `generate_storage_path_string(relative_path, is_public=False) -> str`

将相对路径编码为存储路径字符串（存储路径用于APP侧获取按钮图标等资源）。

- `parse_storage_path_string(path) -> os.PathLike`

将存储路径字符串解析回相对路径对象。

**文件操作方法**

- `copy_file(src, dst) -> bool`

复制文件。

- `file_exists(relative_path) -> bool`

判断文件是否存在。

- `read_text_file(relative_path) -> str`

读取文本文件。

- `write_text_file(relative_path, content) -> None`

写入文本文件。

- `read_binary_file(relative_path) -> bytes`

读取二进制文件。

- `write_binary_file(relative_path, data: bytes) -> None`

    写入二进制文件。

- `remove_file(relative_path) -> bool`

删除文件。

- `remove_folder(relative_path) -> None`

递归删除目录。

**属性**

- `node_config_path: os.PathLike`

- 当前 Agent 的配置目录根路径。

**示例**

```Python
from pathlib import Path

result_path = Path("cache/result.txt")
data_path = Path("cache/data.bin")

self.storage_manager.write_text_file(result_path, "ok")
text = self.storage_manager.read_text_file(result_path)

self.storage_manager.write_binary_file(data_path, b"\x01\x02\x03\xff")
data = self.storage_manager.read_binary_file(data_path)
```

## 本地化与日志

### 8\.1 LocaleString

多语言字符串类型。

**构造方式**

```Python
LocaleString()
LocaleString(default_string: str)
LocaleString(en: str, zh: str)
LocaleString(json_config: Any)
LocaleString(translations: dict[str, str])
```

**方法**

- `add_translation(lang, text) -> None`

添加或更新单个语言版本。

- `get_string(lang) -> str`

获取指定语言文本。若目标语言不存在，则回退到默认语言文本。

- `size() -> int`

返回翻译条目数量。

**示例**

```Python
text = LocaleString({
    "en": "Start",
    "zh": "开始",
})
```

### 8\.2 Logger

日志对象。

可通过 `AgentBase.logger` 访问。目前提供5个日志等级，用于描述不同严重程度的运行信息和异常情况。

方法：

- `debug(msg: str) -> None`

调试级日志，用于输出开发和排障时需要的详细诊断信息，例如内部状态、执行路径、临时调试信息等。通常信息量较大，不适合作为常规运行日志的主要内容。

- `info(msg: str) -> None`

信息级日志，用于记录系统正常运行过程中的关键事件，例如初始化完成、状态切换、主要流程开始或结束等。

- `warn(msg: str) -> None`

警告级日志，表示出现了异常但系统仍可继续运行，通常意味着当前走了降级逻辑、回退路径，或某项能力暂时不可用。

- `error(msg: str) -> None`

错误级日志，表示某项操作或调用已经失败，需要关注和排查，但进程本身不一定立即退出。

- `fatal(msg: str) -> None`

致命级日志，表示严重故障或接近不可恢复的异常，通常会显著影响 agent 的正确运行，需要立即处理。

## 模块级函数

- `get_agent_config() -> AgentConfig`

读取 Agent 配置。

- `get_sys_api_level() -> int`

获取当前系统 API Level。

API Level：系统能力的版本标识。数值与固件版本对应，格式规则为 `主版本 × 10000 + 次版本 × 100 + 修订版本`（如 `10601` 对应固件 `1.6.1`）。开发者可通过查询该值判断当前运行环境可用的 API 范围。

