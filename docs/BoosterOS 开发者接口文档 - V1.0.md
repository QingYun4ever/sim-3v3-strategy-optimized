# BoosterOS 开发者接口文档 \- V1\.0

BoosterOS 是面向机器人应用开发的 Python SDK。你可以用它连接机器人，读取图像、IMU、关节、里程计等数据，并下发模式切换、速度控制、关节控制、动作、轨迹和音频播放等指令。

## 支持范围与运行要求

BoosterOS 定位为通用机器人 SDK，当前已支持的厂家、机型和运行要求见下表：

|项目|当前说明|
|---|---|
|厂家|Booster（加速进化）|
|机型|Booster K1 / T1|
|运行环境|当前仅支持在 Booster 真实机器人环境或 Booster Studio 创建的虚拟机器人仿真环境中运行|
|Python|Python \>=3\.10|
|固件版本|Booster 机器人固件版本 \>= `v1.7`|

## 1\. 快速上手

`boosteros` 需在 Booster 真机或虚拟机器人仿真环境中运行。首次使用时，建议先使用 Booster Studio 连接设备或仿真环境，再按照本文档完成依赖安装、连通性检查，并读取一帧数据以确认图像、关节和 IMU 均可用。

### 1\.1 安装

基础包：

用于机器人连接、状态读取、运动控制等基础接口。

```bash
python3 -m pip install boosteros
```

`brain` 可选包：

包含 `Speech` 语音能力和 `Detection` 视觉能力所需的相关依赖。

```bash
python3 -m pip install "boosteros[brain]"
```

### 1\.2 第一次连通性检查

创建机器人对象后调用基础信息接口，确认运动控制服务可用：

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()

info = robot.robot_info
print(f"{info.manufacturer}, {info.model}, {info.serial_number}")
# 输出示例：Booster Robotics, Booster K1, rivt9

mode = robot.get_mode()
print(f"mode={mode}")
# 输出示例：mode=prepare

joints = robot.list_joints()
print(f"joints={len(joints)}")
# 输出示例：joints=22
```

### 1\.3 第一次读取数据

读取一批最常用的数据，确认接口链路是通的：

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()

joint_states = robot.get_joint_states()
print(joint_states.names[:5])
# 输出示例：['AAHead_Yaw', 'Head_Pitch', 'ALeft_Shoulder_Pitch', 'Left_Shoulder_Roll', 'Left_Elbow_Pitch']

imu = robot.get_imu()
print(imu.rpy)
# 输出示例：[ 1.11528370e-05  4.49275300e-02 -1.56997216e+00]

img = robot.get_image(img_type="rgb")
img.save("quickstart_rgb.jpg")
print("saved quickstart_rgb.jpg")
```

## 2\. BoosterRobot

`BoosterRobot` 是 Booster 真机和虚拟机器人仿真环境的开发入口，提供连接机器人、读取传感器数据和下发控制指令的全部接口。

### 2\.1 构造与连接接口

#### `BoosterRobot`

**接口签名：**

```python
BoosterRobot(
    network_interface: str = "",
    virtual_robot_name: str = "",
    *,
    timeout: float = 5.0,
    callback_workers: int = 4,
    enable_tf_listener: bool = True,
    **kwargs: Any,
) -> BoosterRobot
```

**功能：**

创建并初始化 Booster 机器人客户端。

同一台机器人应只创建并复用一个 `BoosterRobot` 实例。每个实例会独立建立 ROS 节点、数据订阅和底层控制发布通道；重复创建不仅浪费资源，多个控制发布器并存还可能导致指令冲突。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`network_interface`|`str`|`""`|保留参数，当前固定使用默认值 `""`。|
|`virtual_robot_name`|`str`|`""`|虚拟机器人名称，仅多虚拟机器人模式下需要填写。|
|`timeout`|`float`|`5.0`|初始化、服务发现和数据就绪等待超时时间（秒）。|
|`callback_workers`|`int`|`4`|传感器回调的工作线程数，须 \>= 1。高频订阅场景（如同时订阅多路传感器）可适当调大。|
|`enable_tf_listener`|`bool`|`True`|是否启动 TF 监听，关闭后 `get_transform()` 不可用。|
|`**kwargs`|—|—|高级底层选项，通过关键字参数传入，一般无需设置。支持以下键：<br>• `domain_id` \(`int`\)：ROS 2 域 ID（`ROS_DOMAIN_ID`）。同一局域网有多台机器人时，为每台指定不同 ID 可避免消息串扰。未设置时使用环境变量值。<br>• `dds_profile` \(`str`\)：FastRTPS DDS 配置文件路径（XML 格式），用于自定义 QoS、网络传输等底层参数。|

**返回：**

`BoosterRobot`

初始化完成的机器人客户端实例。

**异常：**

`ValueError`: `callback_workers` 小于 1。

`LocoClientInitError`: 指定超时时间内没有发现机器人运动控制服务。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
robot_info = robot.robot_info
print(robot_info)
# 输出示例：RobotInfo(manufacturer='Booster Robotics', model='Booster K1', name='vr1', serial_number='rivt9', ...)
```

### 2\.2 信息与状态获取接口

#### `robot_info`

**属性访问：**

```python
robot.robot_info
```

**属性类型：**

`RobotInfo`

**功能：**

获取机器人基础元信息，包括名称、型号、序列号、固件版本、厂商信息和扩展字段。

**返回：**

`RobotInfo`，见「[4\.4 机器人元信息 / RobotInfo](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcngoJaAct5egCyRT63Lhn5Az)」

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
info = robot.robot_info
print(info.model, info.serial_number)
# 输出示例：Booster K1 rivt9
```

#### `get_mode`

**接口签名：**

```python
BoosterRobot.get_mode() -> RobotModeName
```

**功能：**

获取机器人当前模式。

**参数：**

无参数。

**返回：**

`RobotModeName`，见「[4\.4 机器人元信息 / RobotModeName](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnxSCcTMiQimzzjGUESEAQ6g)」

**异常：**

`RuntimeError`: 查询机器人当前模式失败。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
mode = robot.get_mode()
print(mode)
# 输出示例：prepare
```

#### `list_gaits`

**接口签名：**

```python
BoosterRobot.list_gaits() -> list[RobotGaitName]
```

**功能：**

获取 `"walk"` 模式可使用的步态名称。

**参数：**

无参数。

**返回：**

`list[RobotGaitName]`，见「[4\.4 机器人元信息 / RobotGaitName](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnun6IuNDpi1lNIdoqbZ5UJb)」

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
gaits = robot.list_gaits()
print(gaits)
# 输出示例：['default', 'soccer']
```

#### `get_gait`

**接口签名：**

```python
BoosterRobot.get_gait() -> RobotGaitName
```

**功能：**

获取机器人当前步态。返回最近一次通过 `set_gait()` 设置的步态名，未设置过时默认为 `"default"`；与当前模式无关，在非 `"walk"` 模式下同样可调用。

**参数：**

无参数。

**返回：**

`RobotGaitName`，见「[4\.4 机器人元信息 / RobotGaitName](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnun6IuNDpi1lNIdoqbZ5UJb)」

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
gait = robot.get_gait()
print(gait)
# 输出示例：default
```

#### `list_frames`

**接口签名：**

```python
BoosterRobot.list_frames() -> list[str]
```

**功能：**

获取当前可查询的坐标系名称。

**参数：**

无参数。

**返回：**

`list[str]`

坐标系名称列表；未获取到坐标系数据时返回空列表。

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot(enable_tf_listener=True)
frames = robot.list_frames()
print(frames)
# 输出示例：['head_color_optical_frame', 'head_pitch_link', 'head_point']
```

#### `list_joints`

**接口签名：**

```python
BoosterRobot.list_joints() -> list[JointInfo]
```

**功能：**

获取当前机型支持的关节列表。

**参数：**

无参数。

**返回：**

`list[JointInfo]`，见「[4\.4 机器人元信息 / JointInfo](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnODDIasVuVRZ0qpsNkAXJRg)」

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
for joint in robot.list_joints():
    print(joint.name)
    # 输出示例：AAHead_Yaw
```

#### `list_actions`

**接口签名：**

```python
BoosterRobot.list_actions() -> list[ActionInfo]
```

**功能：**

获取当前机器人支持的预定义动作列表；返回的动作 ID 可传给 `do_action()` 执行。

**参数：**

无参数。

**返回：**

`list[ActionInfo]`，见「[4\.4 机器人元信息 / ActionInfo](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcndSrPhQjb6iWYix4wczYaRb)」

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
for action in robot.list_actions():
    print(action.id, action.type, action.interruptible)
    # 输出示例：hand_shake upper_body True
```

### 2\.3 传感器与状态快照接口

#### `get_image`

**接口签名：**

```python
BoosterRobot.get_image(camera_id: str = "", img_type: ImageType = "rgb") -> AnyImage
```

**功能：**

获取最近一帧 RGB 或深度图像。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`camera_id`|`str`|`""`|相机 ID；留空使用默认相机。|
|`img_type`|`ImageType`|`"rgb"`|图像类型，见「[4\.2 感知与状态数据 / ImageType](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnOKmWDFwiGOaYNN9dGF6g2e)」。|

**返回：**

`AnyImage`，见「[4\.2 感知与状态数据 / AnyImage](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcn9T5Bh7qH1pbr2CxIc4vI4c)」

**异常：**

`ValueError`: `img_type` 不是支持的图像类型。

`DataNotReadyError`: 在超时时间内未获取到对应图像。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
image = robot.get_image(img_type="rgb")
print(image.width, image.height)
# 输出示例：320 240
```

#### `get_camera_info`

**接口签名：**

```python
BoosterRobot.get_camera_info(camera_id: str = "") -> CameraInfo
```

**功能：**

获取相机内参与标定信息。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`camera_id`|`str`|`""`|相机 ID；留空使用默认相机。|

**返回：**

`CameraInfo`，见「[4\.2 感知与状态数据 / CameraInfo](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnWZx2AAusBVKKSB15yPutfd)」

**异常：**

`DataNotReadyError`: 在超时时间内未获取到相机内参。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
info = robot.get_camera_info()
print(info.width, info.height)
# 输出示例：320 240
print(info.k)
# 输出示例：
# [[216.48573063   0.         160.        ]
#  [  0.         216.48573063 120.        ]
#  [  0.           0.           1.        ]]
```

#### `get_imu`

**接口签名：**

```python
BoosterRobot.get_imu(imu_id: str = "") -> IMUState
```

**功能：**

获取最近一帧 IMU 数据。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`imu_id`|`str`|`""`|IMU ID；留空使用默认 IMU。|

**返回：**

`IMUState`，见「[4\.2 感知与状态数据 / IMUState](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnDBFflMYQMF10BZ7fENTAVf)」

**异常：**

`DataNotReadyError`: 在超时时间内未获取到 IMU 数据。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
imu = robot.get_imu()
print(imu.timestamp, imu.rpy)
# 输出示例：1781085503.5147228 [ 1.11528370e-05  4.49275300e-02 -1.56997216e+00]
```

#### `get_odom`

**接口签名：**

```python
BoosterRobot.get_odom() -> OdomState
```

**功能：**

获取最近一帧里程计数据。

**参数：**

无参数。

**返回：**

`OdomState`，见「[4\.2 感知与状态数据 / OdomState](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcneioKwezCWSBSnI0hObTcRb)」

**异常：**

`DataNotReadyError`: 在超时时间内未获取到里程计数据。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
odom = robot.get_odom()
print(odom.position, odom.rpy)
# 输出示例：[-0.45956746 -0.10788245  0.        ] [ 0.          0.         -1.38284385]
```

#### `get_joint_states`

**接口签名：**

```python
BoosterRobot.get_joint_states() -> JointStates
```

**功能：**

获取所有关节的状态。

**参数：**

无参数。

**返回：**

`JointStates`，见「[4\.3 关节相关数据 / JointStates](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnUcWuEanEB8xzh1zyJUCh7m)」

**异常：**

`DataNotReadyError`: 在超时时间内未获取到关节状态。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
states = robot.get_joint_states()
first_name = robot.list_joints()[0].name
joint = states.get_joint(first_name)
print(joint)
# 输出示例：JointState(name='AAHead_Yaw', position=4.807196546607884e-06, ...)
```

#### `get_battery`

**接口签名：**

```python
BoosterRobot.get_battery() -> BatteryState
```

**功能：**

获取电池状态；虚拟机器人或插线供电场景下按 100% 满电状态返回。

**参数：**

无参数。

**返回：**

`BatteryState`，见「[4\.2 感知与状态数据 / BatteryState](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnTFtMCP6KRYq8ZIUqraGmGY)」

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
battery = robot.get_battery()
print(battery.percentage)
# 输出示例：100.0
```

#### `get_fall_down_state`

**接口签名：**

```python
BoosterRobot.get_fall_down_state() -> FallDownState
```

**功能：**

获取机器人摔倒检测状态。

**参数：**

无参数。

**返回：**

`FallDownState`，见「[4\.2 感知与状态数据 / FallDownState](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcn019qV1FaG0vv3PvRiXUJKb)」

**异常：**

`DataNotReadyError`: 在超时时间内未获取到摔倒状态。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
state = robot.get_fall_down_state()
print(state.state, state.recoverable)
# 输出示例：normal False
```

#### `get_transform`

**接口签名：**

```python
BoosterRobot.get_transform(target_frame: str, source_frame: str) -> Transform
```

**功能：**

查询 `source_frame` 到 `target_frame` 的坐标变换。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`target_frame`|`str`|必填|目标坐标系名称，可从 `list_frames()` 返回结果中选择。|
|`source_frame`|`str`|必填|源坐标系名称，可从 `list_frames()` 返回结果中选择。|

**返回：**

`Transform`，见「[4\.2 感知与状态数据 / Transform](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnzMwnDVKAdVdkl4REsZtOMf)」

**异常：**

`RuntimeError`: 初始化时关闭了 TF listener。

`Exception`: 坐标变换查询失败，例如坐标系不存在或暂无变换。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot(enable_tf_listener=True)
tf = robot.get_transform(target_frame="head_pitch_link", source_frame="head_point")
print(tf.source_frame, tf.target_frame, tf.translation)
# 输出示例：head_pitch_link head_point [0.0613 0. 0.108]
```

### 2\.4 传感器订阅接口

#### `subscribe_image`

**接口签名：**

```python
BoosterRobot.subscribe_image(
    callback: Callable[[AnyImage], None],
    *,
    camera_id: str = "",
    img_type: ImageType = "rgb",
    queue_size: int = 0,
    overflow: OverflowPolicy = "drop_oldest",
) -> SensorSubscription
```

**功能：**

订阅 RGB 或深度图像更新，数据到达时，回调函数会收到 `AnyImage` 对象。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`callback`|`Callable[[AnyImage], None]`|必填|图像回调函数，参数为 `AnyImage`。|
|`camera_id`|`str`|`""`|相机 ID；留空使用默认相机。|
|`img_type`|`ImageType`|`"rgb"`|图像类型，见「[4\.2 感知与状态数据 / ImageType](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnOKmWDFwiGOaYNN9dGF6g2e)」。|
|`queue_size`|`int`|`0`|待处理回调队列大小；`0` 表示不限制。|
|`overflow`|`OverflowPolicy`|`"drop_oldest"`|队列满时处理策略，见「[4\.5 任务与订阅 / OverflowPolicy](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcn5TWDwEUsxTKd2fEUCG2Ftg)」。|

**返回：**

`SensorSubscription`，见「[4\.5 任务与订阅 / SensorSubscription](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnjWingulFMQcB49ED0xI1Qe)」

**异常：**

`ValueError`: `img_type` 不是支持的图像类型。

**示例：**

```python
import time
from boosteros.robots.booster import BoosterRobot
from boosteros.types import AnyImage

robot = BoosterRobot()

def on_image(image: AnyImage) -> None:
    print(image.width, image.height)
    # 输出示例：320 240

sub = robot.subscribe_image(on_image)
try:
    time.sleep(2.0)
finally:
    sub.unsubscribe()
```

#### `subscribe_imu`

**接口签名：**

```python
BoosterRobot.subscribe_imu(
    callback: Callable[[IMUState], None],
    *,
    imu_id: str = "",
    queue_size: int = 0,
    overflow: OverflowPolicy = "drop_oldest",
) -> SensorSubscription
```

**功能：**

订阅 IMU 数据更新，数据到达时，回调函数会收到 `IMUState` 对象。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`callback`|`Callable[[IMUState], None]`|必填|IMU 回调函数。|
|`imu_id`|`str`|`""`|IMU ID；留空使用默认 IMU。|
|`queue_size`|`int`|`0`|待处理回调队列大小；`0` 表示不限制。|
|`overflow`|`OverflowPolicy`|`"drop_oldest"`|队列满时处理策略，见「[4\.5 任务与订阅 / OverflowPolicy](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcn5TWDwEUsxTKd2fEUCG2Ftg)」。|

**返回：**

`SensorSubscription`，见「[4\.5 任务与订阅 / SensorSubscription](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnjWingulFMQcB49ED0xI1Qe)」

**异常：**

无。

**示例：**

```python
import time
from boosteros.robots.booster import BoosterRobot
from boosteros.types import IMUState

robot = BoosterRobot()

def on_imu(data: IMUState) -> None:
    print(data.timestamp, data.rpy)
    # 输出示例：1781085634.9679394 [1.11528370e-05 4.49275300e-02 -1.56997216e+00]

sub = robot.subscribe_imu(on_imu)
try:
    time.sleep(2.0)
finally:
    sub.unsubscribe()
```

#### `subscribe_odom`

**接口签名：**

```python
BoosterRobot.subscribe_odom(
    callback: Callable[[OdomState], None],
    *,
    queue_size: int = 0,
    overflow: OverflowPolicy = "drop_oldest",
) -> SensorSubscription
```

**功能：**

订阅里程计数据更新，数据到达时，回调函数会收到 `OdomState` 对象。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`callback`|`Callable[[OdomState], None]`|必填|里程计回调函数。|
|`queue_size`|`int`|`0`|待处理回调队列大小；`0` 表示不限制。|
|`overflow`|`OverflowPolicy`|`"drop_oldest"`|队列满时处理策略，见「[4\.5 任务与订阅 / OverflowPolicy](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcn5TWDwEUsxTKd2fEUCG2Ftg)」。|

**返回：**

`SensorSubscription`，见「[4\.5 任务与订阅 / SensorSubscription](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnjWingulFMQcB49ED0xI1Qe)」

**异常：**

无。

**示例：**

```python
import time
from boosteros.robots.booster import BoosterRobot
from boosteros.types import OdomState

robot = BoosterRobot()

def on_odom(data: OdomState) -> None:
    print(data.pose_2d)
    # 输出示例：[0. 0. -1.56997216]

sub = robot.subscribe_odom(on_odom)
try:
    time.sleep(2.0)
finally:
    sub.unsubscribe()
```

#### `subscribe_battery`

**接口签名：**

```python
BoosterRobot.subscribe_battery(
    callback: Callable[[BatteryState], None],
    *,
    queue_size: int = 0,
    overflow: OverflowPolicy = "drop_oldest",
) -> SensorSubscription
```

**功能：**

订阅电池状态更新，数据到达时，回调函数会收到 `BatteryState` 对象。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`callback`|`Callable[[BatteryState], None]`|必填|电池状态回调函数。|
|`queue_size`|`int`|`0`|待处理回调队列大小；`0` 表示不限制。|
|`overflow`|`OverflowPolicy`|`"drop_oldest"`|队列满时处理策略，见「[4\.5 任务与订阅 / OverflowPolicy](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcn5TWDwEUsxTKd2fEUCG2Ftg)」。|

**返回：**

`SensorSubscription`，见「[4\.5 任务与订阅 / SensorSubscription](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnjWingulFMQcB49ED0xI1Qe)」

**异常：**

无。

**示例：**

```python
import time
from boosteros.robots.booster import BoosterRobot
from boosteros.types import BatteryState

robot = BoosterRobot()

def on_battery(data: BatteryState) -> None:
    print(data.percentage)
    # 输出示例：100.0

sub = robot.subscribe_battery(on_battery)
try:
    time.sleep(2.0)
finally:
    sub.unsubscribe()
```

#### `subscribe_fall_down_state`

**接口签名：**

```python
BoosterRobot.subscribe_fall_down_state(
    callback: Callable[[FallDownState], None],
    *,
    queue_size: int = 0,
    overflow: OverflowPolicy = "drop_oldest",
) -> SensorSubscription
```

**功能：**

订阅摔倒检测状态更新，数据到达时，回调函数会收到 `FallDownState` 对象。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`callback`|`Callable[[FallDownState], None]`|必填|摔倒状态回调函数。|
|`queue_size`|`int`|`0`|待处理回调队列大小；`0` 表示不限制。|
|`overflow`|`OverflowPolicy`|`"drop_oldest"`|队列满时处理策略，见「[4\.5 任务与订阅 / OverflowPolicy](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcn5TWDwEUsxTKd2fEUCG2Ftg)」。|

**返回：**

`SensorSubscription`，见「[4\.5 任务与订阅 / SensorSubscription](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnjWingulFMQcB49ED0xI1Qe)」

**异常：**

无。

**示例：**

```python
import time
from boosteros.robots.booster import BoosterRobot, FallDownState

robot = BoosterRobot()

def on_state(data: FallDownState) -> None:
    print(data.state, data.recoverable)
    # 输出示例：normal False

sub = robot.subscribe_fall_down_state(on_state)
try:
    time.sleep(2.0)
finally:
    sub.unsubscribe()
```

### 2\.5 运动控制接口

在真实机器人上调用运动控制、动作或轨迹回放前，请确认现场空间、机器人姿态、急停手段和安全策略均已就绪。

#### `set_mode`

**接口签名：**

```python
BoosterRobot.set_mode(mode_name: RobotModeName) -> None
```

**功能：**

切换机器人模式，并等待切换完成。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`mode_name`|`RobotModeName`|必填|目标模式名，见「[4\.4 机器人元信息 / RobotModeName](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnxSCcTMiQimzzjGUESEAQ6g)」。|

**返回：**

`None`

**异常：**

`ValueError`: `mode_name` 不受支持。

`RuntimeError`: 模式切换失败、超时，或切换到非预期模式。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
robot.set_mode("prepare")
robot.set_mode("walk")
mode = robot.get_mode()
print(mode)
# 输出示例：walk
```

#### `set_gait`

**接口签名：**

```python
BoosterRobot.set_gait(gait: RobotGaitName) -> None
```

**功能：**

设置 `"walk"` 模式使用的步态。若当前已在 `"walk"` 模式，立即同步切换；否则记录目标步态，下次调用 `set_mode("walk")` 时生效。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`gait`|`RobotGaitName`|必填|目标步态名，见「[4\.4 机器人元信息 / RobotGaitName](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnun6IuNDpi1lNIdoqbZ5UJb)」。|

**返回：**

`None`

**异常：**

`ValueError`: `gait` 不受支持。

`RuntimeError`: 步态切换失败或超时。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
robot.set_gait("soccer")
robot.set_mode("walk")
gait = robot.get_gait()
print(gait)
# 输出示例：soccer
```

#### `set_velocity`

**接口签名：**

```python
BoosterRobot.set_velocity(vx: float, vy: float, vyaw: float) -> None
```

**功能：**

在机器人坐标系下发送平面速度控制命令，调用前应先进入 `"walk"` 模式。初次调试建议从较小值开始，参见「[5\.1 常见问题](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcn2NvWJmvou9J2qSADKSIA4c)」。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`vx`|`float`|必填|前后线速度，单位 m/s，向前为正。初次调试建议不超过 `0.3 m/s`。|
|`vy`|`float`|必填|左右线速度，单位 m/s，向左为正。|
|`vyaw`|`float`|必填|绕 Z 轴偏航角速度，单位 rad/s，逆时针为正。|

**返回：**

`None`

**异常：**

无。

**示例：**

```python
import time
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
robot.set_mode("walk")
try:
    robot.set_velocity(0.2, 0.0, 0.0)
    time.sleep(1.0)
finally:
    robot.set_velocity(0.0, 0.0, 0.0)
```

#### `upper_body_control`

**接口签名：**

```python
BoosterRobot.upper_body_control(enable: bool) -> None
```

**功能：**

在 `"walk"` 模式下启用或关闭上身自定义控制。启用后，头部和双臂（前 10 个关节）的控制权交由调用方，需配合 `set_joints()` 发送上身关节命令，腿部继续由系统控制行走；关闭后上身控制权交还系统。与 `"custom"` 模式的区别在于：`"custom"` 模式下腿部也由调用方接管，而此接口仅接管上身。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`enable`|`bool`|必填|`True` 启用上身自定义控制；`False` 关闭。|

**返回：**

`None`

**异常：**

`RuntimeError`: 当前不在 `"walk"` 模式，或上身自定义控制命令发送失败。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot
import time

robot = BoosterRobot()
robot.set_mode("walk")
robot.upper_body_control(True)
time.sleep(5.0)
robot.upper_body_control(False)
```

#### `set_joints`

**接口签名：**

```python
BoosterRobot.set_joints(joint_commands: list[JointCommand]) -> None
```

**功能：**

批量下发关节控制命令。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`joint_commands`|`list[JointCommand]`|必填|关节控制命令列表；按 `name` 匹配关节。|

`JointCommand` 表示单个关节的控制目标和控制参数，至少包含关节名 `name` 和目标位置 `position`；位置控制建议显式设置 `kp` 和 `kd`。完整字段见「[4\.3 关节相关数据 / JointCommand](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnZnNT8K5vji8MXyDxh78Q7f)」。

**返回：**

`None`

**异常：**

`ValueError`: 关节名称集合不等于全部关节，也不等于前 10 个上身关节。

`RuntimeError`: 关节控制命令发送失败。

**示例：**

```python
import time
from boosteros.robots.booster import BoosterRobot
from boosteros.types import JointCommand

robot = BoosterRobot()

# K1 全身关节示例增益；T1 会在腰部位置插入一组 gain。
kp_values = [
    40.0, 40.0,                    # neck
    20.0, 30.0, 10.0, 10.0,        # left arm
    20.0, 30.0, 10.0, 10.0,        # right arm
    250.0, 250.0, 150.0, 250.0, 120.0, 120.0,  # left leg
    250.0, 250.0, 150.0, 250.0, 120.0, 120.0,  # right leg
]
kd_values = [
    2.0, 2.0,                      # neck
    1.5, 2.0, 0.5, 0.5,            # left arm
    1.5, 2.0, 0.5, 0.5,            # right arm
    15.0, 15.0, 8.0, 12.0, 8.0, 8.0,  # left leg
    15.0, 15.0, 8.0, 12.0, 8.0, 8.0,  # right leg
]

try:
    # 先进入 prepare 并等待姿态稳定，再用当前姿态接管 custom 控制。
    robot.set_mode("prepare")
    time.sleep(2.0)

    joint_names = [joint.name for joint in robot.list_joints()]
    if len(joint_names) == 23:  # T1 腰部关节
        kp_values.insert(10, 100.0)
        kd_values.insert(10, 8.0)
    if len(joint_names) != len(kp_values):
        raise RuntimeError(f"Unsupported joint count: {len(joint_names)}")

    states = robot.get_joint_states()
    state_map = {state.name: state.position for state in states.joints}

    commands = []
    for i, name in enumerate(joint_names):
        position = state_map.get(name)
        if position is None:
            raise RuntimeError(f"Joint state is not ready: {name}")
        commands.append(
            JointCommand(
                name=name,
                position=position,
                kp=kp_values[i],
                kd=kd_values[i],
            )
        )

    robot.set_mode("custom")
    robot.set_joints(commands)
    time.sleep(2.0)
finally:
    robot.set_mode("prepare")
```

#### `set_head_angle`

**接口签名：**

```python
BoosterRobot.set_head_angle(pitch: float, yaw: float) -> None
```

**功能：**

在 `"walk"` 模式下控制头部俯仰和偏航角度。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`pitch`|`float`|必填|俯仰角，单位 rad；正值向下，负值向上。关节限位因机型而异，见 [T1](https://booster.feishu.cn/wiki/H2Dowdnokij7p8ks9K3cZPuJnOg)/[K1](https://booster.feishu.cn/wiki/Ly55wYhRZivZdhkou09cs1oPn6e) 说明书；超出限位不报错，机器人停止响应。|
|`yaw`|`float`|必填|偏航角，单位 rad；正值向左，负值向右。关节限位因机型而异，见 [T1](https://booster.feishu.cn/wiki/H2Dowdnokij7p8ks9K3cZPuJnOg)/[K1](https://booster.feishu.cn/wiki/Ly55wYhRZivZdhkou09cs1oPn6e) 说明书；超出限位不报错，机器人停止响应。|

**返回：**

`None`

**异常：**

`RuntimeError`: 当前不在 `"walk"` 模式。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
robot.set_mode("walk")
robot.set_head_angle(pitch=2.0, yaw=2.0)
```

#### `reset_odom`

**接口签名：**

```python
BoosterRobot.reset_odom() -> None
```

**功能：**

将机器人里程计重置到零位。

**参数：**

无参数。

**返回：**

`None`

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
robot.reset_odom()
```

### 2\.6 高级任务接口

高级任务接口会立即返回 `TaskHandle`，可通过该句柄等待任务完成、请求取消或查询当前状态。

#### `get_active_tasks`

**接口签名：**

```python
BoosterRobot.get_active_tasks(
    filter: Callable[[TaskInfo], bool] | None = None,
) -> list[TaskHandle[None]]
```

**功能：**

获取当前未进入终态的 TaskHandle，可按 `TaskInfo` 过滤，完整字段见「[4\.5 任务与订阅 / TaskInfo](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnHGEa7HhYNap15GG5nyNGdh)」。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`filter`|`Callable[[TaskInfo], bool] | None`|`None`|可选过滤函数，入参为 `TaskInfo`，返回 `True` 表示保留该任务。|

**返回：**

`list[TaskHandle[None]]`，见「[4\.5 任务与订阅 / TaskHandle](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnkAEjbFOxXpRmjIMOEmrBCg)」

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
handle = robot.do_action("hand_wave")
active_motion_tasks = robot.get_active_tasks(lambda info: info.group == "motion")
print(active_motion_tasks)
# 输出示例：[<BoosterActionTaskHandle trace_id=b1019d44fcca4e0e90617a39cdc09d11 task_id='hand_wave' status=RUNNING>]
handle.cancel()
handle.wait()
```

#### `do_action`

**接口签名：**

```python
BoosterRobot.do_action(
    action_id: str,
    *,
    on_done: Callable[[TaskHandle[None]], None] | None = None,
    on_status_change: Callable[[TaskHandle[None]], None] | None = None,
) -> TaskHandle[None]
```

**功能：**

异步执行预定义动作，并返回 TaskHandle，调用前可用 `list_actions()` 获取支持的 `action_id`。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`action_id`|`str`|必填|预定义动作 ID。|
|`on_done`|`Callable[[TaskHandle[None]], None] | None`|`None`|任务进入终态时调用的回调。|
|`on_status_change`|`Callable[[TaskHandle[None]], None] | None`|`None`|任务状态变化时调用的回调。|

**返回：**

`TaskHandle[None]`，见「[4\.5 任务与订阅 / TaskHandle](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnkAEjbFOxXpRmjIMOEmrBCg)」

**异常：**

`ValueError`: `action_id` 不存在或当前版本不支持。

`RuntimeError`: 同组任务正在运行、机器人正在执行其他动作，或动作启动失败。

**示例：**

```python
import time
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
handle = robot.do_action("hand_wave")
time.sleep(10.0)
handle.cancel()
status = handle.wait(timeout=5.0)
print(status)
# 输出示例：'CANCELLED'
```

#### `get_up`

**接口签名：**

```python
BoosterRobot.get_up(
    *,
    on_done: Callable[[TaskHandle[None]], None] | None = None,
    on_status_change: Callable[[TaskHandle[None]], None] | None = None,
) -> TaskHandle[None]
```

**功能：**

异步触发机器人起身任务。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`on_done`|`Callable[[TaskHandle[None]], None] | None`|`None`|任务进入终态时调用的回调。|
|`on_status_change`|`Callable[[TaskHandle[None]], None] | None`|`None`|任务状态变化时调用的回调。|

**返回：**

`TaskHandle[None]`，见「[4\.5 任务与订阅 / TaskHandle](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnkAEjbFOxXpRmjIMOEmrBCg)」

该任务不可取消，`cancel()` 固定返回 `False`。

**异常：**

`RuntimeError`: 同组任务正在运行、机器人正在执行其他动作，或起身启动失败。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
handle = robot.get_up()
status = handle.wait(timeout=30.0)
print(status)
# 输出示例：'SUCCEEDED'
```

#### `execute_trajectory`

**接口签名：**

```python
BoosterRobot.execute_trajectory(
    trajectory: TrajectoryData,
    *,
    on_done: Callable[[TaskHandle[None]], None] | None = None,
    on_status_change: Callable[[TaskHandle[None]], None] | None = None,
) -> TaskHandle[None]
```

**功能：**

异步回放 `TrajectoryData` 示教轨迹。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`trajectory`|`TrajectoryData`|必填|要回放的轨迹对象。|
|`on_done`|`Callable[[TaskHandle[None]], None] | None`|`None`|任务进入终态时调用的回调。|
|`on_status_change`|`Callable[[TaskHandle[None]], None] | None`|`None`|任务状态变化时调用的回调。|

**返回：**

`TaskHandle[None]`，见「[4\.5 任务与订阅 / TaskHandle](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnkAEjbFOxXpRmjIMOEmrBCg)」

**异常：**

`TypeError`: `trajectory` 不是 `TrajectoryData` 对象。

`RuntimeError`: 当前未处于 `"walk"` 模式、示教模式正在启用、正在录制、已有回放任务，机器人正忙，或轨迹机型校验失败。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot
from boosteros.types import TrajectoryData
import time

robot = BoosterRobot()
robot.set_mode("walk")
# 替换为实际轨迹文件路径
trajectory = TrajectoryData.load("/data/demo.btraj")
handle = robot.execute_trajectory(trajectory)
time.sleep(10)
handle.cancel()
status = handle.wait(timeout=60.0)
print(status)
# 输出示例：'CANCELLED'
```

#### `play_sound`

**接口签名：**

```python
BoosterRobot.play_sound(
    audio: str | PathLike[str] | AudioData | Iterable[AudioData],
    *,
    volume: float | None = None,
    on_done: Callable[[TaskHandle[None]], None] | None = None,
    on_status_change: Callable[[TaskHandle[None]], None] | None = None,
) -> TaskHandle[None]
```

**功能：**

异步播放音频。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`audio`|`str | PathLike[str] | AudioData | Iterable[AudioData]`|必填|机器人运行环境可直接访问的本地音频文件路径（如 `/data/hello.wav`）、单个 `AudioData` 对象，或有限的 `AudioData` 序列；文件路径支持 `.wav`、`.mp3`、`.pcm`。|
|`volume`|`float | None`|`None`|播放音量，范围 `0.0` 到 `1.0`；`None` 使用默认音量。|
|`on_done`|`Callable[[TaskHandle[None]], None] | None`|`None`|任务进入终态时调用的回调。|
|`on_status_change`|`Callable[[TaskHandle[None]], None] | None`|`None`|任务状态变化时调用的回调。|

**返回：**

`TaskHandle[None]`，见「[4\.5 任务与订阅 / TaskHandle](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnkAEjbFOxXpRmjIMOEmrBCg)」

**异常：**

`TypeError`: `audio` 不是文件路径、`AudioData` 或有限 `AudioData` 序列。

`ValueError`: 文件路径为空字符串，或 `volume` 超出 `0.0` 到 `1.0`。

`RuntimeError`: 虚拟机器人没有音频管理器、同组音频任务正在运行，或播放启动失败。文件不存在、不是本地文件、后缀不支持时也会在启动阶段包装为 `RuntimeError` 抛出。内存音频格式校验和播放错误通常会写入 `handle.error`，并使任务进入 `FAILED`。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
handle = robot.play_sound("/data/hello.wav")
status = handle.wait(timeout=30.0)
print(status)
# 输出示例：'SUCCEEDED'

robot.audio_manager.start_recording()
input("Press Enter to stop recording")
audio = robot.audio_manager.stop_recording()
handle = robot.play_sound(audio, volume=0.3)
status = handle.wait(timeout=audio.duration.seconds + 10.0)
print(status)
# 输出示例：'SUCCEEDED'
```

### 2\.7 音频管理器

#### `BoosterRobot.audio_manager`

**属性访问：**

```python
robot.audio_manager
```

**属性类型：**

`BoosterAudioManager`

**功能：**

获取与当前 `BoosterRobot` 绑定的音频管理器实例，作为访问音频子系统接口的入口。

**异常：**

`RuntimeError`: 当前机器人不支持音频子系统，常见于虚拟机器人。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
audio_manager = robot.audio_manager
```

以下接口均为 `BoosterAudioManager` 的实例方法，需先通过 `robot.audio_manager` 获取管理器对象后调用。

#### `get_system_volume`

**接口签名：**

```python
BoosterAudioManager.get_system_volume() -> float
```

**功能：**

查询机器人系统输出音量。

**参数：**

无参数。

**返回：**

`float`

系统音量，范围通常为 `0.0` 到 `1.0`。`0.0` 表示静音。

**异常：**

`RuntimeError`: 音频子系统已关闭或系统音量查询失败。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
volume = robot.audio_manager.get_system_volume()
print(volume)
# 输出示例：0.5
```

#### `set_system_volume`

**接口签名：**

```python
BoosterAudioManager.set_system_volume(volume: float) -> None
```

**功能：**

设置机器人系统输出音量。该音量是系统级输出音量，不同于 `play(..., volume=...)` 的单次播放音量；设置为 `0.0` 表示静音。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`volume`|`float`|必填|系统音量，范围 `0.0` 到 `1.0`。|

**返回：**

`None`

**异常：**

`ValueError`: `volume` 不在 `0.0` 到 `1.0` 范围内。

`RuntimeError`: 音频子系统已关闭或系统音量设置失败。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
robot.audio_manager.set_system_volume(0.8)
```

#### `start_recording`

**接口签名：**

```python
BoosterAudioManager.start_recording(
    sample_rate: int = 16000,
    channels: int = 1,
    sample_format: PcmSampleFormat = "S16LE",
    *,
    use_naec: bool = False,
) -> None
```

**功能：**

开始一次内存录音会话，录音数据会在 `stop_recording()` 时合并为一个 `AudioData`。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`sample_rate`|`int`|`16000`|采样率，单位 Hz。|
|`channels`|`int`|`1`|声道数。|
|`sample_format`|`PcmSampleFormat`|`"S16LE"`|PCM 采样格式，见「[4\.2 感知与状态数据 / PcmSampleFormat](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnASPqlx1RZVwS33Zr5cTEb8)」。|
|`use_naec`|`bool`|`False`|是否优先使用 NAEC 音频，需要音频输入设备支持。|

**返回：**

`None`

**异常：**

`ValueError`: `sample_format` 不是 `"S16LE"`，或 `sample_rate`/`channels` 小于等于 0。

`RuntimeError`: 音频子系统已关闭、录音已在进行，或采集初始化失败。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot
import time

robot = BoosterRobot()
robot.audio_manager.start_recording()
time.sleep(5)
audio = robot.audio_manager.stop_recording()
print(audio.duration)
# 输出示例：<Duration 4.480000s>
```

#### `stop_recording`

**接口签名：**

```python
BoosterAudioManager.stop_recording() -> AudioData
```

**功能：**

停止当前录音会话，释放采集流，并返回合并后的音频数据。

**参数：**

无参数。

**返回：**

`AudioData`，见「[4\.2 感知与状态数据 / AudioData](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcn2tfwi1WtrU2Vu793DtcByh)」

**异常：**

`RuntimeError`: 当前没有进行中的录音，或录音期间没有采集到有效音频。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
robot.audio_manager.start_recording()
input("Recording... press Enter to stop")
audio = robot.audio_manager.stop_recording()
audio.save("recorded.wav")
```

#### `is_recording`

**接口签名：**

```python
BoosterAudioManager.is_recording() -> bool
```

**功能：**

查询当前是否存在进行中的音频录音会话。

**参数：**

无参数。

**返回：**

`bool`

`True` 表示正在录音。

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
is_recording = robot.audio_manager.is_recording()
print(is_recording)
# 输出示例：False
```

#### `get_recording_duration`

**接口签名：**

```python
BoosterAudioManager.get_recording_duration() -> float
```

**功能：**

获取当前已采集音频时长，未录音时返回 `0.0`。

**参数：**

无参数。

**返回：**

`float`

当前录音会话中已采集音频的秒数。

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot
import time

robot = BoosterRobot()
robot.audio_manager.start_recording()
time.sleep(5)
duration = robot.audio_manager.get_recording_duration()
print(f"{duration:.3f}s")
# 输出示例：4.480s
robot.audio_manager.stop_recording()
```

#### `record_stream`

**接口签名：**

```python
BoosterAudioManager.record_stream(
    sample_rate: int = 16000,
    channels: int = 1,
    sample_format: PcmSampleFormat = "S16LE",
    *,
    use_naec: bool = False,
    chunk_bytes: int | None = None,
    stop_event: threading.Event | None = None,
) -> Iterator[AudioData]
```

**功能：**

以生成器形式读取麦克风输入，持续产出 `AudioData` 音频块。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`sample_rate`|`int`|`16000`|采样率，单位 Hz。|
|`channels`|`int`|`1`|声道数。|
|`sample_format`|`PcmSampleFormat`|`"S16LE"`|PCM 采样格式，见「[4\.2 感知与状态数据 / PcmSampleFormat](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnASPqlx1RZVwS33Zr5cTEb8)」。|
|`use_naec`|`bool`|`False`|是否优先使用 NAEC 音频。|
|`chunk_bytes`|`int | None`|`None`|不为 `None` 时按指定字节数重新切分音频块。|
|`stop_event`|`threading.Event | None`|`None`|外部停止信号。|

**返回：**

`Iterator[AudioData]`，见「[4\.2 感知与状态数据 / AudioData](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcn2tfwi1WtrU2Vu793DtcByh)」

**异常：**

`ValueError`: 采样格式、采样率、声道数或 `chunk_bytes` 非法。

`RuntimeError`: 音频子系统已关闭，或采集过程发生错误。

**示例：**

```python
import threading
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
stop_event = threading.Event()
for index, chunk in enumerate(
    robot.audio_manager.record_stream(chunk_bytes=4096, stop_event=stop_event)
):
    print(chunk.duration.seconds)
    # 输出示例：0.128
    if index >= 2:
        stop_event.set()
```

#### `play_stream`

**接口签名：**

```python
BoosterAudioManager.play_stream(
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    sample_format: PcmSampleFormat = "S16LE",
    volume: float | None = None,
    queue_size: int = 64,
    overflow: OverflowPolicy = "block",
) -> AudioPlaybackStreamHandle
```

**功能：**

打开可写入的 PCM 播放流，适合 TTS、WebSocket 音频等动态数据源。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`sample_rate`|`int`|`16000`|采样率，单位 Hz。|
|`channels`|`int`|`1`|声道数。|
|`sample_format`|`PcmSampleFormat`|`"S16LE"`|PCM 采样格式，见「[4\.2 感知与状态数据 / PcmSampleFormat](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnASPqlx1RZVwS33Zr5cTEb8)」。|
|`volume`|`float | None`|`None`|播放音量，范围 `0.0` 到 `1.0`。|
|`queue_size`|`int`|`64`|写入队列大小，必须大于 0。|
|`overflow`|`OverflowPolicy`|`"block"`|队列满时处理策略，见「[4\.5 任务与订阅 / OverflowPolicy](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcn5TWDwEUsxTKd2fEUCG2Ftg)」。|

**返回：**

`AudioPlaybackStreamHandle`，见「[4\.5 任务与订阅 / AudioPlaybackStreamHandle](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnmqohLEuMFYplCE5VLDsPOe)」

**异常：**

`ValueError`: 采样格式、采样率、声道数、音量、队列大小或溢出策略非法。

`RuntimeError`: 同组音频任务正在运行。

**示例：**

```python
import time
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()

robot.audio_manager.start_recording()
time.sleep(5.0)
audio = robot.audio_manager.stop_recording()

stream = robot.audio_manager.play_stream(
    sample_rate=audio.sample_rate,
    channels=audio.channels,
    sample_format=audio.sample_format,
)
try:
    chunk_bytes = 4096
    for start in range(0, len(audio.data), chunk_bytes):
        stream.write(audio.with_data(audio.data[start : start + chunk_bytes]))
finally:
    stream.close()

status = stream.wait(timeout=audio.duration.seconds + 10.0)
print(status)
# 输出示例：'SUCCEEDED'
```

### 2\.8 示教管理器

示教管理器用于进入拖动示教模式并录制机器人轨迹。

#### `BoosterRobot.hand_guiding_manager`

**属性访问：**

```python
robot.hand_guiding_manager
```

**属性类型：**

`BoosterHandGuidingManager`

**功能：**

获取当前机器人的拖动示教管理器，也支持 `with robot.hand_guiding_manager as hand_guiding:` 上下文用法。

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
robot.set_mode("walk")
with robot.hand_guiding_manager as hand_guiding:
    print(hand_guiding.is_recording)
    # 输出示例：False
```

以下接口均为 `BoosterHandGuidingManager` 的实例方法，需先通过 `robot.hand_guiding_manager` 获取管理器对象后调用。

#### `start_recording`

**接口签名：**

```python
BoosterHandGuidingManager.start_recording() -> None
```

**功能：**

开始录制拖动示教轨迹，需在 `with robot.hand_guiding_manager as hand_guiding:` 上下文中调用。

**参数：**

无参数。

**返回：**

`None`

**异常：**

`RuntimeError`: 未进入拖动示教上下文，或已有录制正在进行。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
robot.set_mode("walk")
with robot.hand_guiding_manager as hand_guiding:
    hand_guiding.start_recording()
    input("Move the robot, then press Enter to stop")
    trajectory = hand_guiding.stop_recording()
    print(trajectory)
    # 输出示例：<TrajectoryData id='traj_20260610_200813_3b19' model='Booster K1' points=13 duration=0.024s>
```

#### `stop_recording`

**接口签名：**

```python
BoosterHandGuidingManager.stop_recording() -> TrajectoryData | None
```

**功能：**

停止由 `start_recording()` 开始的示教录制，并读取生成的轨迹数据。

**参数：**

无参数。

**返回：**

`TrajectoryData | None`

- `TrajectoryData`：成功读取到轨迹，见「[4\.6 轨迹数据 / TrajectoryData](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcngHpb9bYe61p0tfm0VMzBzc)」。

- `None`：录制停止后没有生成有效轨迹数据。

**异常：**

`RuntimeError`: 尚未调用 `start_recording()`，或当前没有进行中的录制；轨迹文件读取或解析失败。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
robot.set_mode("walk")
with robot.hand_guiding_manager as hand_guiding:
    input("Press Enter to start recording")
    hand_guiding.start_recording()
    input("Move the robot, then press Enter to stop")
    trajectory = hand_guiding.stop_recording()

if trajectory is not None:
    trajectory.save("/data/demo.btraj")
```

#### `is_recording`

**属性访问：**

```python
robot.hand_guiding_manager.is_recording
```

**属性类型：**

`bool`

**功能：**

查询当前是否正在录制示教轨迹；`True` 表示正在录制。

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
print(robot.hand_guiding_manager.is_recording)
# 输出示例：False
```

#### `get_recording_duration`

**接口签名：**

```python
BoosterHandGuidingManager.get_recording_duration() -> float
```

**功能：**

获取当前示教录制持续时间；未录制时返回 `0.0`。

**参数：**

无参数。

**返回：**

`float`

当前录制持续秒数。

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot
import time

robot = BoosterRobot()
robot.set_mode("walk")
with robot.hand_guiding_manager as hand_guiding:
    hand_guiding.start_recording()
    time.sleep(2)
    duration = robot.hand_guiding_manager.get_recording_duration()
    print(duration)
    # 输出示例：2.0
    hand_guiding.stop_recording()
```

### 2\.9 自动踢球管理器

`SoccerKickManager` 用于管理足球踢球流程：开启自动踢球控制，接收球位置、踢球方向和力度，并下发踢球指令。适合在识别到足球位置后，让机器人完成自动踢球动作。

典型调用顺序：

1. 创建 `BoosterRobot`。

2. 创建 `SoccerKickManager(robot)`。

3. 调用 `start()` 进入足球模式并开启视觉踢球控制。

4. 调用 `update_command(direction, power)` 设置踢球方向和力度。

5. 持续调用 `update_ball(x, y)` 更新当前球位置。

6. 调用 `stop()` 关闭视觉踢球控制。

#### `SoccerKickManager`

**接口签名：**

```python
SoccerKickManager(robot: BoosterRobot) -> SoccerKickManager
```

**功能：**

创建足球踢球管理器，并绑定到已初始化的机器人客户端。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`robot`|`BoosterRobot`|必填|已初始化的机器人客户端。|

**返回：**

`SoccerKickManager`

足球踢球管理器实例。

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot, SoccerKickManager

robot = BoosterRobot()
soccer_kick = SoccerKickManager(robot)
```

以下接口均为 `SoccerKickManager` 的实例方法。

#### `start`

**接口签名：**

```python
SoccerKickManager.start() -> None
```

**功能：**

开启自动踢球控制。

**参数：**

无参数。

**返回：**

`None`

**异常：**

`RuntimeError`: 切换到足球模式超时，或视觉踢球控制开启失败。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot, SoccerKickManager

robot = BoosterRobot()
soccer_kick = SoccerKickManager(robot)
soccer_kick.start()
```

#### `update_command`

**接口签名：**

```python
SoccerKickManager.update_command(direction: float, power: float) -> None
```

**功能：**

设置自动踢球的目标方向和踢球力度，用于控制机器人朝指定方向完成踢球动作。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`direction`|`float`|必填|踢球方向相对机器人当前坐标系的 yaw，单位 rad；`0.0` 表示正前方，正值表示左侧。|
|`power`|`float`|必填|踢球力度，范围为 `1.0` 到 `10.0`。|

**返回：**

`None`

**异常：**

`ValueError`: `direction` 不是有效数值，或 `power` 不在 `1.0` 到 `10.0` 范围内。

`RuntimeError`: 踢球指令下发失败。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot, SoccerKickManager

robot = BoosterRobot()
soccer_kick = SoccerKickManager(robot)
soccer_kick.start()
soccer_kick.update_command(direction=0.0, power=5.0)
```

#### `update_ball`

**接口签名：**

```python
SoccerKickManager.update_ball(x: float, y: float) -> None
```

**功能：**

更新足球在当前机器人坐标系下的位置，用于让自动踢球流程根据最新球位置完成踢球动作。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`x`|`float`|必填|足球在当前机器人坐标系下的 x 坐标，单位 m，向前为正。|
|`y`|`float`|必填|足球在当前机器人坐标系下的 y 坐标，单位 m，向左为正。|

**返回：**

`None`

**异常：**

`ValueError`: `x` 或 `y` 不是有效数值。

`RuntimeError`: 踢球指令下发失败。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot, SoccerKickManager

robot = BoosterRobot()
soccer_kick = SoccerKickManager(robot)
soccer_kick.start()
soccer_kick.update_command(direction=0.0, power=5.0)
soccer_kick.update_ball(x=0.6, y=0.0)
```

#### `stop`

**接口签名：**

```python
SoccerKickManager.stop() -> None
```

**功能：**

关闭自动踢球控制，并清空当前球位置和踢球指令。

**参数：**

无参数。

**返回：**

`None`

**异常：**

无。

**示例：**

```python
from boosteros.robots.booster import BoosterRobot, SoccerKickManager

robot = BoosterRobot()
soccer_kick = SoccerKickManager(robot)

soccer_kick.start()
try:
    soccer_kick.update_command(direction=0.0, power=5.0)
    soccer_kick.update_ball(x=0.6, y=0.0)
finally:
    soccer_kick.stop()
```

## 3\. 视觉与语音

本章介绍 `boosteros.brain` 下的高层能力，包括 `Speech` 和 `Detection`。

### 3\.1 `Speech`

`Speech` 提供麦克风流式语音识别和 AI 语音对话能力。

#### `Speech`

**接口签名：**

```python
Speech(robot: Robot, *, provider: str = "booster", **kwargs: Any) -> Speech
```

**功能：**

创建并初始化 Speech 客户端。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`robot`|`Robot`|必填|绑定的机器人实例。|
|`provider`|`str`|`"booster"`|语音服务提供商名称；当前仅支持 `"booster"`。|
|`**kwargs`|`Any`|\-|传递给所选 provider 的额外配置。|

**返回：**

`Speech`

初始化完成的语音能力实例。

**异常：**

`ValueError`: `provider` 不是已注册的语音服务提供商名称。

**示例：**

```python
from boosteros.brain import Speech
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
speech = Speech(robot)
```

#### `recognize_stream`

**接口签名：**

```python
Speech.recognize_stream(
    *,
    on_result: Callable[[str], None] | None = None,
    **kwargs: Any,
) -> TaskHandle[None]
```

**功能：**

使用机器人麦克风持续进行流式语音识别。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`on_result`|`Callable[[str], None] | None`|`None`|识别到非空文本时调用的回调函数。|
|`**kwargs`|`Any`|\-|可选扩展参数；当前 booster provider 暂无公开扩展参数。|

**返回：**

`TaskHandle[None]`，见「[4\.5 任务与订阅 / TaskHandle](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnkAEjbFOxXpRmjIMOEmrBCg)」

**异常：**

`RuntimeError`: 流式识别任务启动失败。

**示例：**

```python
from boosteros.brain import Speech
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
speech = Speech(robot)
handle = speech.recognize_stream(
    on_result=lambda text: print(f"识别结果: {text}", flush=True), # 输出示例：识别结果: 现在几点钟了？
)
try:
    print("流式语音识别已启动，按 Ctrl+C 退出")
    status = handle.wait()
except KeyboardInterrupt:
    print("\n正在停止流式语音识别...")
    handle.cancel()
    handle.wait(timeout=5.0)
```

#### `chat`

**接口签名：**

```python
Speech.chat(
    *,
    config: ChatConfig | None = None,
    **kwargs: Any,
) -> TaskHandle[None]
```

**功能：**

启动语音对话任务。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`config`|`ChatConfig | None`|`None`|语音对话配置；为 `None` 时使用默认配置。|
|`**kwargs`|`Any`|\-|可选配置参数。|

**ChatConfig 字段说明：**

|字段|类型|默认值|说明|
|---|---|---|---|
|`voice`|`str`|`"default"`|回复音色 ID，可通过 `list_voices()` 获取可用音色。|
|`system_prompt`|`str | None`|`None`|角色提示词；为 `None` 时使用默认提示词。|
|`welcome_msg`|`str | None`|`None`|对话开始时的欢迎语；为 `None` 时不设置欢迎语。|
|`volume`|`float | None`|`None`|对话播放音量，范围 `0.0` 到 `1.0`；为 `None` 时不修改系统音量。|

**可选配置参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`interrupt_speech_duration`|`int`|`800`|打断判定时长，单位 ms。|
|`interrupt_keywords`|`Sequence[str] | str`|`["停一下", "先别说", "stop"]`|触发打断的关键词。|
|`enable_face_tracking`|`bool`|`False`|是否启用对话过程中的人脸跟踪。|
|`enable_subtitle_log`|`bool`|`False`|是否记录对话字幕日志。|

**返回：**

`TaskHandle[None]`，见「[4\.5 任务与订阅 / TaskHandle](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnkAEjbFOxXpRmjIMOEmrBCg)」

**异常：**

`ValueError`: 配置参数非法。

`RuntimeError`: 语音对话任务启动失败。

**示例：**

```python
from boosteros.brain import Speech
from boosteros.brain.speech import ChatConfig
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
speech = Speech(robot)
handle = speech.chat(
    config=ChatConfig(
        system_prompt="你是一个天气预报助手，可以查询全国各地的天气预报，信息来源要靠谱。",
        welcome_msg="你好，我是天气预报助手，请问你想查询哪个城市的天气？",
        volume=0.1,
    ),
    enable_subtitle_log=True,
)
try:
    print("语音对话已启动，按 Ctrl+C 退出")
    status = handle.wait()
except KeyboardInterrupt:
    print("\n正在停止语音对话...")
    handle.cancel()
    handle.wait(timeout=5.0)
```

#### `list_voices`

**接口签名：**

```python
Speech.list_voices() -> list[dict[str, Any]]
```

**功能：**

获取可用音色列表。

**参数：**

无参数。

**返回：**

`list[dict[str, Any]]`

返回列表中每个音色项包含的字段：

|字段|类型|说明|
|---|---|---|
|`id`|`str`|音色 ID|
|`name`|`str`|音色名称|
|`gender`|`str`|音色性别标识|
|`capabilities`|`list[str]`|支持的语音能力列表，当前支持 `"chat"`|

**异常：**

无。

**示例：**

```python
from boosteros.brain import Speech
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
speech = Speech(robot)
for voice in speech.list_voices():
    print(voice["id"], voice["capabilities"])
    # 输出示例：warm_ahu ['chat']
```

### 3\.2 `Detection`

`Detection` 封装目标检测、模型切换和检测结果绘制能力。

#### `Detection`

**接口签名：**

```python
Detection(
    model: str = "default",
    backend: str = "local",
    **kwargs: Any,
) -> Detection
```

**功能：**

创建并初始化检测器。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`model`|`str`|`"default"`|模型 ID 或本地模型路径。|
|`backend`|`str`|`"local"`|推理后端，当前支持 `"local"`。|
|`device`|`str | None`|`None`|推理设备，例如 `"cpu"`、`"cuda:0"`。|
|`engine`|`str | None`|模型默认值|本地推理引擎，支持 `"yolo"`、`"onnx"`、`"tensorrt"`。|
|`providers`|`list[str] | None`|`None`|ONNX Runtime provider 列表。|

**返回：**

`Detection`

初始化完成的检测器实例。

**异常：**

`ValueError`: `backend`、`engine` 或模型文件不受支持。

`ImportError`: 所选推理引擎依赖缺失。

`RuntimeError`: 推理引擎模型加载失败。

**示例：**

```python
from boosteros.brain import Detection

detector = Detection(model="default", backend="local")
```

#### `list_models`

**接口签名：**

```python
Detection.list_models() -> list[dict[str, Any]]
```

**功能：**

获取可用检测模型列表。

**参数：**

无参数。

**返回：**

`list[dict[str, Any]]`

返回列表中每个模型项包含的字段：

|字段|类型|说明|
|---|---|---|
|`id`|`str`|模型 ID|
|`name`|`str`|模型名称或描述|
|`type`|`str`|模型类型|
|`backend`|`list[str]`|后端列表|

**异常：**

无。

**示例：**

```python
from boosteros.brain import Detection

for model in Detection.list_models():
    print(model["id"], model["name"])
    # 输出示例：default 通用物体检测 (快速)
```

#### `load_model`

**接口签名：**

```python
Detection.load_model(model: str) -> None
```

**功能：**

切换当前检测模型。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`model`|`str`|必填|模型 ID 或本地模型路径。|

**返回：**

`None`

**异常：**

`ValueError`: 模型或推理配置不受支持。

`ImportError`: 所选推理引擎依赖缺失。

`RuntimeError`: 推理引擎模型加载失败。

**示例：**

```python
from boosteros.brain import Detection

detector = Detection(model="default")
detector.load_model("person")
```

#### `detect`

**接口签名：**

```python
Detection.detect(
    image: PIL.Image.Image | str | numpy.ndarray,
    confidence: float = 0.5,
    iou_threshold: float = 0.45,
    **kwargs: Any,
) -> list[DetectionResult]
```

**功能：**

执行目标检测。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`image`|`PIL.Image.Image | str | numpy.ndarray`|必填|输入图像、图像文件路径或 NumPy 数组。|
|`confidence`|`float`|`0.5`|置信度阈值。|
|`iou_threshold`|`float`|`0.45`|NMS IoU 阈值。|
|`**kwargs`|`Any`|\-|传递给底层推理后端的额外参数。|

**返回：**

`list[DetectionResult]`，见「[4\.2 感知与状态数据 / DetectionResult](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnfiw1w014V3OcMwmbEULACb)」

**异常：**

`TypeError`: `image` 类型不受支持。

`ValueError`: 图像文件读取失败，或推理参数非法。

`RuntimeError`: 推理引擎内部执行失败。

**示例：**

```python
from boosteros.brain import Detection
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
detector = Detection(model="soccer")

image = robot.get_image(img_type="rgb")
results = detector.detect(image.to_numpy(), confidence=0.4)
print(results)
# 输出示例：[DetectionResult(class_name='Ball', class_id=0, confidence=0.794202983379364, ...), ...]
```

#### `plot`

**接口签名：**

```python
Detection.plot(
    image: PIL.Image.Image | str | numpy.ndarray,
    results: list[DetectionResult],
    as_image: bool = True,
) -> Image | numpy.ndarray
```

**功能：**

在图像上绘制检测框和类别标签。

**参数：**

|参数|类型|默认值|说明|
|---|---|---|---|
|`image`|`PIL.Image.Image | str | numpy.ndarray`|必填|原始输入图像、图像文件路径或 NumPy 数组。|
|`results`|`list[DetectionResult]`|必填|`detect()` 返回的检测结果。|
|`as_image`|`bool`|`True`|是否返回 `Image` 对象；为 `False` 时返回 NumPy 数组。|

**返回：**

`Image | numpy.ndarray`

- `Image`：`as_image=True` 时返回，见「[4\.2 感知与状态数据 / Image](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcn4Vn722JyNhutnL7kd5pIfc)」。

- `numpy.ndarray`：`as_image=False` 时返回。

**异常：**

`TypeError`: `image` 类型不受支持。

`ValueError`: 图像文件读取失败。

**示例：**

```python
from boosteros.brain import Detection
from boosteros.robots.booster import BoosterRobot

robot = BoosterRobot()
detector = Detection(model="soccer")

image = robot.get_image(img_type="rgb")
results = detector.detect(image.to_numpy(), confidence=0.4)
image_with_boxes = detector.plot(image.to_numpy(), results, as_image=True)
image_with_boxes.save("detections.png")
```

## 4\. 公共数据类型

本章列出的公共数据类型默认从 `boosteros.types` 导入，例外会在对应小节标注导入路径。

### 4\.1 基础时间与头信息

#### `Time`

表示一个高精度时间点，纳秒精度。

**属性：**

|属性|类型|说明|
|---|---|---|
|`nanoseconds`|`int`|纳秒时间戳|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`seconds`|`float`|秒级时间戳，由 `nanoseconds` 换算得到|
|`sec`|`int`|秒整数部分，由 `nanoseconds` 换算得到|
|`nanosec`|`int`|当前秒内的纳秒余数|

##### `Time`

**接口签名：**

```python
Time(nanoseconds: int | None = None)
```

**功能：**

构造一个 `Time` 实例。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`nanoseconds`|`int | None`|否|纳秒时间戳；不传或传 `None` 时使用当前系统时间|

**返回：**

`Time` 实例。

**异常：**

无。

**示例：**

```python
from boosteros.types import Time

t = Time()                      # 当前系统时间
t_explicit = Time(1_700_000_000_000_000_000)
```

##### `now`

**接口签名：**

```python
@classmethod
Time.now() -> Time
```

**功能：**

返回当前系统时间对应的 `Time` 实例。

**参数：**

无参数。

**返回：**

`Time`，取自 `time.time_ns()` 的当前系统时间。

**异常：**

无。

**示例：**

```python
from boosteros.types import Time

t = Time.now()
print(t.seconds)
# 输出示例：1781086228.3448012
```

##### `to_datetime`

**接口签名：**

```python
Time.to_datetime() -> datetime.datetime
```

**功能：**

转换为 Python `datetime.datetime` 对象，精度按 `datetime` 自身规则截断到微秒。

**参数：**

无参数。

**返回：**

`datetime.datetime`，本地时区表示的时间。

**异常：**

无。

**示例：**

```python
from boosteros.types import Time

t = Time.now()
dt = t.to_datetime()
print(dt)
# 输出示例：2026-06-12 10:00:00.344801
```

##### `from_datetime`

**接口签名：**

```python
@classmethod
Time.from_datetime(dt: datetime.datetime) -> Time
```

**功能：**

从 Python `datetime.datetime` 对象创建 `Time` 实例。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`dt`|`datetime.datetime`|是|Python 标准库 datetime 对象|

**返回：**

`Time`，对应 `dt` 的纳秒时间戳。

**异常：**

无。

**示例：**

```python
import datetime
from boosteros.types import Time

t = Time.from_datetime(datetime.datetime(2026, 6, 12, 10, 0))
```

#### `Duration`

表示一段时间长度，纳秒精度。

**属性：**

|属性|类型|说明|
|---|---|---|
|`nanoseconds`|`int`|纳秒时长|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`seconds`|`float`|秒级时长，由 `nanoseconds` 换算得到|

##### `Duration`

**接口签名：**

```python
Duration(nanoseconds: int)
```

**功能：**

构造一个 `Duration` 实例。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`nanoseconds`|`int`|是|纳秒时长；可正可负|

**返回：**

`Duration` 实例。

**异常：**

无。

**示例：**

```python
from boosteros.types import Time, Duration

d = Duration(500_000_000)               # 0.5 秒
diff = Time.now() - Time.now()          # 也是一个 Duration
```

#### `Header`

为数据对象提供时间戳和坐标系信息。

**属性：**

|属性|类型|说明|
|---|---|---|
|`stamp`|`Time`|纳秒精度时间戳对象|
|`frame_id`|`str`|坐标系 ID|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`timestamp`|`float`|秒级浮点时间戳，等价于 `stamp.seconds`|
|`sec`|`int`|`stamp.sec` 的快捷访问|
|`nanosec`|`int`|`stamp.nanosec` 的快捷访问|

##### `Header`

**接口签名：**

```python
Header(stamp: Time | None = None, frame_id: str = "")
```

**功能：**

构造一个 `Header` 实例。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`stamp`|`Time | None`|否|高精度时间戳；不传或传 `None` 时使用 `Time.now()`|
|`frame_id`|`str`|否|坐标系 ID，默认空字符串|

**返回：**

`Header` 实例。

**异常：**

无。

**示例：**

```python
from boosteros.types import Header, Time

h = Header()                                     # 当前时间，空 frame_id
h2 = Header(stamp=Time.now(), frame_id="base_link")
```

##### `from_sec`

**接口签名：**

```python
@classmethod
Header.from_sec(timestamp_sec: float, frame_id: str = "") -> Header
```

**功能：**

从秒级浮点时间戳创建 `Header`，常用于兼容外部以浮点秒为单位的时间源。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`timestamp_sec`|`float`|是|秒级时间戳；内部按 `1e9` 转为纳秒|
|`frame_id`|`str`|否|坐标系 ID，默认空字符串|

**返回：**

`Header` 实例。

**异常：**

无。

**示例：**

```python
from boosteros.types import Header

h = Header.from_sec(1_750_000_000.123, frame_id="base_link")
```

### 4\.2 感知与状态数据

#### `AnyImage`

`AnyImage = Image | CompressedImage`，是图像接口返回类型的类型别名。`Image` 表示原始像素图像（numpy 数组），`CompressedImage` 表示压缩编码图像（如 JPEG、PNG 字节流）。两者均继承自 `ImageBase`，提供相同的转换接口。

**共有属性：**

|属性|类型|说明|
|---|---|---|
|`header`|`Header`|图像元数据头|
|`width`|`int`|图像宽度|
|`height`|`int`|图像高度|

**`Image`**** 独有属性：**

|属性|类型|说明|
|---|---|---|
|`encoding`|`str`|像素格式，如 `"rgb8"`、`"bgr8"`、`"mono8"`、`"16uc1"`|

**`CompressedImage`**** 独有属性：**

|属性|类型|说明|
|---|---|---|
|`format`|`str`|编解码格式，如 `"jpeg"`、`"png"`|

##### `Image`

**接口签名：**

```python
Image(
    data: np.ndarray,
    header: Header,
    encoding: str = "unknown",
    width: int = 0,
    height: int = 0,
)
```

**功能：**

构造原始像素图像。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`data`|`np.ndarray`|是|HWC 布局的像素数组|
|`header`|`Header`|是|图像元数据头|
|`encoding`|`str`|否|像素格式，如 `"rgb8"`、`"bgr8"`|
|`width`|`int`|否|图像宽度；为 `0` 时由数据自动推断|
|`height`|`int`|否|图像高度；为 `0` 时由数据自动推断|

**返回：**

`Image` 实例。

##### `CompressedImage`

**接口签名：**

```python
CompressedImage(
    data: bytes,
    header: Header,
    format: str = "unknown",
    width: int = 0,
    height: int = 0,
)
```

**功能：**

构造压缩图像。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`data`|`bytes`|是|压缩图像字节流，如 JPEG payload|
|`header`|`Header`|是|图像元数据头|
|`format`|`str`|否|编解码格式，如 `"jpeg"`、`"png"`|
|`width`|`int`|否|图像宽度；为 `0` 时由字节流自动探测|
|`height`|`int`|否|图像高度；为 `0` 时由字节流自动探测|

**返回：**

`CompressedImage` 实例。

**异常：**

无。

##### `to_numpy`

**接口签名：**

```python
AnyImage.to_numpy() -> np.ndarray
```

**功能：**

返回 HWC 布局的 numpy 数组。

**参数：**

无参数。

**返回：**

`np.ndarray`，形状为 `(H, W, C)` 或 `(H, W)` 的图像数组。

**异常：**

`TypeError`: `CompressedImage` 的内部数据不是 `bytes` 类型。

**示例：**

```python
arr = image.to_numpy()
print(arr.shape)
# 输出示例：(480, 640, 3)
```

##### `to_pil`

**接口签名：**

```python
AnyImage.to_pil() -> PIL.Image.Image
```

**功能：**

转为 PIL 图像；非 8\-bit 数据（深度图等）会归一化到 0\-255。

**参数：**

无参数。

**返回：**

`PIL.Image.Image`。

**异常：**

`TypeError`: `CompressedImage` 的内部数据不是 `bytes` 类型。

##### `to_bytes`

**接口签名：**

```python
AnyImage.to_bytes() -> bytes
```

**功能：**

返回图像原始字节。压缩图像返回压缩 payload，原始图像返回 numpy `tobytes()` 结果。

**参数：**

无参数。

**返回：**

`bytes`。

**异常：**

`TypeError`: 内部数据类型不受支持。

##### `size`

**接口签名：**

```python
AnyImage.size() -> tuple[int, int]
```

**功能：**

返回 `(width, height)`；尺寸未知时会触发解码以获取元数据。

**参数：**

无参数。

**返回：**

`tuple[int, int]`，`(width, height)`。

**异常：**

无。

**示例：**

```python
size = image.size()
print(size)
# 输出示例：(640, 480)
```

##### `save`

**接口签名：**

```python
AnyImage.save(path: str, **kwargs: Any) -> None
```

**功能：**

保存图像到文件。压缩图像写入与文件后缀匹配时直接落盘原始 bytes，否则通过 PIL 转码保存。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`path`|`str`|是|输出文件路径，根据后缀自动选择格式|
|`**kwargs`|`Any`|否|透传给 `PIL.Image.save` 的额外参数，如 `quality`|

**返回：**

`None`。

**异常：**

`OSError`: 写入失败。

##### `resize`

**接口签名：**

```python
AnyImage.resize(width: int, height: int) -> Image
```

**功能：**

返回缩放后的新图像对象，原对象不变。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`width`|`int`|是|目标宽度|
|`height`|`int`|是|目标高度|

**返回：**

`Image`，缩放后的新图像对象（始终为原始像素图像）。

**异常：**

无。

##### `show`

**接口签名：**

```python
AnyImage.show() -> None
```

**功能：**

使用 PIL 弹窗显示图像，适合调试。

**参数：**

无参数。

**返回：**

`None`。

**异常：**

无。

#### `BoundingBox2D`

表示 2D 检测框，使用左上角坐标加宽高。

**属性：**

|属性|类型|说明|
|---|---|---|
|`x`|`int`|左上角 X 坐标|
|`y`|`int`|左上角 Y 坐标|
|`width`|`int`|检测框宽度|
|`height`|`int`|检测框高度|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`center_x`|`float`|中心点 X 坐标，由 `x` 和 `width` 计算得到|
|`center_y`|`float`|中心点 Y 坐标，由 `y` 和 `height` 计算得到|
|`area`|`int`|检测框面积，由 `width` 和 `height` 计算得到|

##### `BoundingBox2D`

**接口签名：**

```python
BoundingBox2D(x: int, y: int, width: int, height: int)
```

**功能：**

构造一个 `BoundingBox2D` 实例。该类型为不可变 dataclass。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`x`|`int`|是|左上角 X 坐标|
|`y`|`int`|是|左上角 Y 坐标|
|`width`|`int`|是|检测框宽度|
|`height`|`int`|是|检测框高度|

**返回：**

`BoundingBox2D` 实例。

**异常：**

无。

##### `to_dict`

**接口签名：**

```python
BoundingBox2D.to_dict() -> dict[str, int]
```

**功能：**

转为包含 `x` / `y` / `width` / `height` 的字典。

**参数：**

无参数。

**返回：**

`dict[str, int]`。

**异常：**

无。

#### `DetectionResult`

表示一条目标检测结果。

**属性：**

|属性|类型|说明|
|---|---|---|
|`class_name`|`str`|检测类别名称|
|`class_id`|`int`|检测类别 ID|
|`confidence`|`float`|置信度，范围通常为 `0.0 ~ 1.0`|
|`bbox`|`BoundingBox2D`|2D 检测框|
|`mask`|`object | None`|分割掩码等模型扩展输出|
|`keypoints`|`object | None`|关键点等模型扩展输出|
|`distance_m`|`float | None`|距离估计，单位 `m`|

##### `DetectionResult`

**接口签名：**

```python
DetectionResult(
    class_name: str,
    class_id: int,
    confidence: float,
    bbox: BoundingBox2D,
    mask: object | None = None,
    keypoints: object | None = None,
    distance_m: float | None = None,
)
```

**功能：**

构造一个 `DetectionResult` 实例。该类型为不可变 dataclass。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`class_name`|`str`|是|检测类别名称|
|`class_id`|`int`|是|检测类别 ID|
|`confidence`|`float`|是|置信度|
|`bbox`|`BoundingBox2D`|是|2D 检测框|
|`mask`|`object | None`|否|分割掩码等模型扩展输出|
|`keypoints`|`object | None`|否|关键点等模型扩展输出|
|`distance_m`|`float | None`|否|距离估计，单位 `m`|

**返回：**

`DetectionResult` 实例。

**异常：**

无。

##### `to_dict`

**接口签名：**

```python
DetectionResult.to_dict() -> dict[str, Any]
```

**功能：**

转为包含 `class_name` / `class_id` / `confidence` / `bbox` 的字典；扩展字段 `mask` / `keypoints` / `distance_m` 不包含在内。

**参数：**

无参数。

**返回：**

`dict[str, Any]`。

**异常：**

无。

#### `CameraInfo`

表示相机内参、畸变参数和投影矩阵等标定信息。

**属性：**

|属性|类型|说明|
|---|---|---|
|`header`|`Header`|相机信息元数据头|
|`width`|`int`|图像宽度|
|`height`|`int`|图像高度|
|`k`|`np.ndarray`|3x3 相机内参矩阵|
|`d`|`np.ndarray`|畸变参数|
|`r`|`np.ndarray`|3x3 修正旋转矩阵，默认单位阵|
|`p`|`np.ndarray`|3x4 投影矩阵，默认零阵|
|`distortion_model`|`str`|畸变模型，默认 `"plumb_bob"`|
|`binning_x`|`int`|水平像素合并参数|
|`binning_y`|`int`|垂直像素合并参数|
|`roi`|`RegionOfInterest`|感兴趣区域，见「[4\.2 感知与状态数据 / RegionOfInterest](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnV1ZHWMGRsNUmBjdeKJLM3u)」|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`timestamp`|`float`|秒级时间戳，等价于 `header.timestamp`|
|`frame_id`|`str`|坐标系 ID，等价于 `header.frame_id`|

##### `CameraInfo`

**接口签名：**

```python
CameraInfo(
    header: Header,
    width: int,
    height: int,
    k: np.ndarray,
    d: np.ndarray,
    r: np.ndarray = np.eye(3),
    p: np.ndarray = np.zeros((3, 4)),
    distortion_model: str = "plumb_bob",
    binning_x: int = 0,
    binning_y: int = 0,
    roi: RegionOfInterest = RegionOfInterest(),
)
```

**功能：**

构造一个 `CameraInfo` 实例。该类型为不可变 dataclass。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`header`|`Header`|是|相机信息元数据头|
|`width`|`int`|是|图像宽度|
|`height`|`int`|是|图像高度|
|`k`|`np.ndarray`|是|3x3 内参矩阵 `[fx 0 cx; 0 fy cy; 0 0 1]`|
|`d`|`np.ndarray`|是|畸变参数 `[k1, k2, t1, t2, k3, ...]`|
|`r`|`np.ndarray`|否|3x3 修正旋转矩阵，默认单位阵|
|`p`|`np.ndarray`|否|3x4 投影矩阵，默认零阵|
|`distortion_model`|`str`|否|畸变模型，默认 `"plumb_bob"`|
|`binning_x`|`int`|否|水平像素合并参数|
|`binning_y`|`int`|否|垂直像素合并参数|
|`roi`|`RegionOfInterest`|否|感兴趣区域|

**返回：**

`CameraInfo` 实例。

**异常：**

无。

##### `to_dict`

**接口签名：**

```python
CameraInfo.to_dict() -> dict[str, Any]
```

**功能：**

转为可 JSON/YAML 序列化的字典；矩阵字段 `k` / `d` / `r` / `p` 通过 `tolist()` 转为列表。

**参数：**

无参数。

**返回：**

`dict[str, Any]`。

**异常：**

无。

#### `RegionOfInterest`

表示图像中的感兴趣区域。

**属性：**

|属性|类型|说明|
|---|---|---|
|`x_offset`|`int`|ROI 左上角 X 偏移|
|`y_offset`|`int`|ROI 左上角 Y 偏移|
|`height`|`int`|ROI 高度；`0` 通常表示全图|
|`width`|`int`|ROI 宽度|
|`do_rectify`|`bool`|是否对 ROI 做矫正|

##### `RegionOfInterest`

**接口签名：**

```python
RegionOfInterest(
    x_offset: int = 0,
    y_offset: int = 0,
    height: int = 0,
    width: int = 0,
    do_rectify: bool = False,
)
```

**功能：**

构造一个 `RegionOfInterest` 实例。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`x_offset`|`int`|否|ROI 左上角 X 偏移|
|`y_offset`|`int`|否|ROI 左上角 Y 偏移|
|`height`|`int`|否|ROI 高度；`0` 通常表示全图|
|`width`|`int`|否|ROI 宽度|
|`do_rectify`|`bool`|否|是否对 ROI 做矫正|

**返回：**

`RegionOfInterest` 实例。

**异常：**

无。

##### `to_dict`

**接口签名：**

```python
RegionOfInterest.to_dict() -> dict[str, Any]
```

**功能：**

转为字典。

**参数：**

无参数。

**返回：**

`dict[str, Any]`。

**异常：**

无。

#### `IMUState`

表示 IMU 的线加速度、角速度和姿态数据。

**属性：**

|属性|类型|说明|
|---|---|---|
|`header`|`Header`|IMU 元数据头|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`linear_acceleration`|`np.ndarray`|线加速度 `[x, y, z]`，单位 `m/s^2`|
|`angular_velocity`|`np.ndarray`|角速度 `[x, y, z]`，单位 `rad/s`|
|`orientation`|`np.ndarray | None`|四元数 `[x, y, z, w]`|
|`timestamp`|`float`|秒级时间戳，等价于 `header.timestamp`|
|`frame_id`|`str`|坐标系 ID，等价于 `header.frame_id`|
|`rpy`|`np.ndarray | None`|欧拉角 `[roll, pitch, yaw]`，单位 `rad`；由 `orientation` 计算得到|

##### `IMUState`

**接口签名：**

```python
IMUState(
    linear_acceleration: np.ndarray,
    angular_velocity: np.ndarray,
    orientation: np.ndarray | None = None,
    header: Header | None = None,
)
```

**功能：**

构造一个 `IMUState` 实例。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`linear_acceleration`|`np.ndarray`|是|线加速度 `[x, y, z]`，单位 `m/s^2`|
|`angular_velocity`|`np.ndarray`|是|角速度 `[x, y, z]`，单位 `rad/s`|
|`orientation`|`np.ndarray | None`|否|四元数 `[x, y, z, w]`；为 `None` 时 `rpy` 也为 `None`|
|`header`|`Header | None`|否|IMU 元数据头；不传或传 `None` 时使用默认 `Header`|

**返回：**

`IMUState` 实例。

**异常：**

无。

**示例：**

```python
imu = robot.get_imu()
print(imu.timestamp, imu.frame_id)
# 输出示例：1781086228.3448012
print(imu.linear_acceleration)
# 输出示例：[-0.44691575  0.10313473  9.79927158]
print(imu.rpy)
# 输出示例：[ 0.01052435  0.04557293 -1.38284385]
```

##### `to_numpy`

**接口签名：**

```python
IMUState.to_numpy() -> dict[str, np.ndarray]
```

**功能：**

返回包含可用向量的字典。`linear_acceleration` 和 `angular_velocity` 始终存在；`orientation` 不为空时会带 `orientation` 和 `rpy`。

**参数：**

无参数。

**返回：**

`dict[str, np.ndarray]`。

**异常：**

无。

#### `OdomState`

表示机器人里程计位姿和速度。

**属性：**

|属性|类型|说明|
|---|---|---|
|`header`|`Header`|里程计元数据头|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`position`|`np.ndarray`|位置 `[x, y, z]`，单位 `m`|
|`orientation`|`np.ndarray`|四元数 `[x, y, z, w]`|
|`linear_velocity`|`np.ndarray | None`|线速度 `[x, y, z]`，单位 `m/s`|
|`angular_velocity`|`np.ndarray | None`|角速度 `[x, y, z]`，单位 `rad/s`|
|`timestamp`|`float`|秒级时间戳，等价于 `header.timestamp`|
|`frame_id`|`str`|参考坐标系，等价于 `header.frame_id`|
|`child_frame_id`|`str`|子坐标系|
|`rpy`|`np.ndarray`|欧拉角 `[roll, pitch, yaw]`，单位 `rad`；由 `orientation` 计算得到|
|`pose_2d`|`np.ndarray`|平面位姿 `[x, y, yaw]`|

##### `OdomState`

**接口签名：**

```python
OdomState(
    child_frame_id: str,
    position: np.ndarray,
    orientation: np.ndarray,
    linear_velocity: np.ndarray | None = None,
    angular_velocity: np.ndarray | None = None,
    pose_covariance: np.ndarray | None = None,
    twist_covariance: np.ndarray | None = None,
    header: Header | None = None,
)
```

**功能：**

构造一个 `OdomState` 实例。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`child_frame_id`|`str`|是|子坐标系|
|`position`|`np.ndarray`|是|位置 `[x, y, z]`，单位 `m`|
|`orientation`|`np.ndarray`|是|四元数 `[x, y, z, w]`|
|`linear_velocity`|`np.ndarray | None`|否|线速度 `[x, y, z]`，单位 `m/s`|
|`angular_velocity`|`np.ndarray | None`|否|角速度 `[x, y, z]`，单位 `rad/s`|
|`pose_covariance`|`np.ndarray | None`|否|位姿 6x6 协方差矩阵|
|`twist_covariance`|`np.ndarray | None`|否|速度 6x6 协方差矩阵|
|`header`|`Header | None`|否|里程计元数据头；不传或传 `None` 时使用默认 `Header`|

**返回：**

`OdomState` 实例。

**异常：**

无。

##### `to_numpy`

**接口签名：**

```python
OdomState.to_numpy() -> dict[str, np.ndarray]
```

**功能：**

返回所有可用向量和协方差矩阵的字典。包含 `position` / `orientation` / `rpy`；可选项 `linear_velocity` / `angular_velocity` / `pose_covariance` / `twist_covariance` 不为空时一并包含。

**参数：**

无参数。

**返回：**

`dict[str, np.ndarray]`。

**异常：**

无。

#### `Transform`

表示两个坐标系之间的三维位姿变换。

**属性：**

|属性|类型|说明|
|---|---|---|
|`header`|`Header`|变换元数据头|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`translation`|`np.ndarray`|平移 `[x, y, z]`，单位 `m`|
|`rotation`|`np.ndarray`|四元数 `[x, y, z, w]`|
|`timestamp`|`float`|秒级时间戳，等价于 `header.timestamp`|
|`source_frame`|`str`|源坐标系，等价于 `header.frame_id`|
|`target_frame`|`str`|目标坐标系，等价于构造参数 `child_frame_id`|
|`rpy`|`np.ndarray`|欧拉角 `[roll, pitch, yaw]`，单位 `rad`；由 `rotation` 计算得到|

##### `Transform`

**接口签名：**

```python
Transform(
    translation: np.ndarray,
    rotation: np.ndarray,
    child_frame_id: str,
    header: Header | None = None,
)
```

**功能：**

构造一个 `Transform` 实例。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`translation`|`np.ndarray`|是|平移 `[x, y, z]`，单位 `m`|
|`rotation`|`np.ndarray`|是|四元数 `[x, y, z, w]`|
|`child_frame_id`|`str`|是|目标坐标系|
|`header`|`Header | None`|否|变换元数据头；不传或传 `None` 时使用默认 `Header`|

**返回：**

`Transform` 实例。

**异常：**

无。

##### `inverse`

**接口签名：**

```python
Transform.inverse() -> Transform
```

**功能：**

返回逆变换；源坐标系和目标坐标系互换。

**参数：**

无参数。

**返回：**

`Transform`，新的逆变换对象。

**异常：**

无。

##### `to_matrix`

**接口签名：**

```python
Transform.to_matrix() -> np.ndarray
```

**功能：**

返回 4x4 齐次变换矩阵。

**参数：**

无参数。

**返回：**

`np.ndarray`，形状 `(4, 4)`。

**异常：**

无。

##### `to_numpy`

**接口签名：**

```python
Transform.to_numpy() -> dict[str, np.ndarray]
```

**功能：**

返回包含 `translation` / `rotation` / `rpy` / `matrix` 的字典。

**参数：**

无参数。

**返回：**

`dict[str, np.ndarray]`。

**异常：**

无。

#### `BatteryState`

表示电池电量、电压、电流和充电状态。

**属性：**

|属性|类型|说明|
|---|---|---|
|`header`|`Header`|电池元数据头|
|`temperature`|`float | None`|温度，单位摄氏度|
|`charge`|`float | None`|当前电量，单位 `Ah`|
|`capacity`|`float | None`|容量，单位 `Ah`|
|`serial_number`|`str`|电池序列号|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`percentage`|`float`|电量百分比，范围 `0.0 ~ 100.0`|
|`voltage`|`float | None`|电压，单位 `V`|
|`current`|`float | None`|电流，单位 `A`，负数通常表示放电|
|`status_code`|`int`|原始电源状态码|
|`power_supply_status`|`str`|可读状态，如 `"Charging"`、`"Discharging"`、`"Full"`、`"NotCharging"`、`"Unknown"`|
|`timestamp`|`float`|秒级时间戳，等价于 `header.timestamp`|
|`is_charging`|`bool`|是否正在充电|
|`is_low`|`bool`|电量是否低于 20%|
|`is_critical`|`bool`|电量是否低于 10%|

##### `BatteryState`

**接口签名：**

```python
BatteryState(
    percentage: float,
    voltage: float | None = None,
    current: float | None = None,
    status: int = 0,
    header: Header | None = None,
    temperature: float | None = None,
    charge: float | None = None,
    capacity: float | None = None,
    serial_number: str = "",
)
```

**功能：**

构造一个 `BatteryState` 实例。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`percentage`|`float`|是|电量百分比，范围通常为 `0.0 ~ 100.0`|
|`voltage`|`float | None`|否|电压，单位 `V`|
|`current`|`float | None`|否|电流，单位 `A`，负数通常表示放电|
|`status`|`int`|否|原始电源状态码：`0` 未知 / `1` 充电 / `2` 放电 / `3` 未充电 / `4` 已充满|
|`header`|`Header | None`|否|电池元数据头；不传或传 `None` 时使用默认 `Header`|
|`temperature`|`float | None`|否|温度，单位摄氏度|
|`charge`|`float | None`|否|当前电量，单位 `Ah`|
|`capacity`|`float | None`|否|容量，单位 `Ah`|
|`serial_number`|`str`|否|电池序列号|

**返回：**

`BatteryState` 实例。

**异常：**

无。

##### `to_numpy`

**接口签名：**

```python
BatteryState.to_numpy() -> dict[str, np.float32]
```

**功能：**

返回核心数值字段的字典。包含 `percentage`；可选项 `voltage` / `current` / `temperature` / `charge` / `capacity` 不为 `None` 时一并包含。

**参数：**

无参数。

**返回：**

`dict[str, np.float32]`。

**异常：**

无。

#### `FallDownState`

表示摔倒检测状态。

**导入路径：**`from boosteros.robots.booster import FallDownState`（该类型暂未从 `boosteros.types` 导出）

**属性：**

|属性|类型|说明|
|---|---|---|
|`header`|`Header`|摔倒状态元数据头|
|`state`|`str`|摔倒状态，取值为 `"normal"`、`"falling"`、`"fallen"`、`"getting_up"`、`"unknown"`|
|`recoverable`|`bool`|当前是否允许恢复动作|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`timestamp`|`float`|秒级时间戳，等价于 `header.timestamp`|
|`is_normal`|`bool`|是否为正常状态|
|`is_falling`|`bool`|是否正在摔倒|
|`has_fallen`|`bool`|是否已经倒地|
|`is_getting_up`|`bool`|是否正在起身|

##### `FallDownState`

**接口签名：**

```python
FallDownState(
    state: str,
    *,
    recoverable: bool = False,
    header: Header | None = None,
)
```

**功能：**

构造一个 `FallDownState` 实例。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`state`|`str`|是|摔倒状态，取值为 `"normal"`、`"falling"`、`"fallen"`、`"getting_up"`、`"unknown"`|
|`recoverable`|`bool`|否|当前是否允许恢复动作|
|`header`|`Header | None`|否|摔倒状态元数据头；不传或传 `None` 时使用默认 `Header`|

**返回：**

`FallDownState` 实例。

**异常：**

无。

#### `AudioData`

表示一段带格式信息的音频数据。

**属性：**

|属性|类型|说明|
|---|---|---|
|`header`|`Header`|音频元数据头|
|`channels`|`int`|声道数|
|`sample_rate`|`int`|采样率，单位 `Hz`|
|`coding_format`|`str`|编码或封装格式，如 `"pcm"`、`"mp3"`|
|`sample_format`|`PcmSampleFormat`|PCM 采样格式，见「[4\.2 感知与状态数据 / PcmSampleFormat](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnASPqlx1RZVwS33Zr5cTEb8)」|
|`bitrate`|`int`|比特率，单位 `bit/s`|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`data`|`bytes`|音频原始二进制数据|
|`format`|`str`|`coding_format` 的别名属性|
|`bit_depth`|`int`|由 `sample_format` 推导出的位深，如 `S16LE` → `16`|
|`timestamp`|`float`|秒级时间戳，等价于 `header.timestamp`|
|`duration`|`Duration`|音频时长；PCM 由字节长度精确计算，压缩格式按 `bitrate` 估算|

##### `AudioData`

**接口签名：**

```python
AudioData(
    data: bytes,
    header: Header,
    channels: int = 1,
    sample_rate: int = 16000,
    coding_format: str = "pcm",
    sample_format: PcmSampleFormat = "S16LE",
    bitrate: int = 0,
)
```

**功能：**

构造一个 `AudioData` 实例。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`data`|`bytes`|是|音频原始二进制数据|
|`header`|`Header`|是|音频元数据头|
|`channels`|`int`|否|声道数|
|`sample_rate`|`int`|否|采样率，单位 `Hz`|
|`coding_format`|`str`|否|编码或封装格式，如 `"pcm"`、`"mp3"`|
|`sample_format`|`PcmSampleFormat`|否|PCM 采样格式，见「[4\.2 感知与状态数据 / PcmSampleFormat](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnASPqlx1RZVwS33Zr5cTEb8)」|
|`bitrate`|`int`|否|比特率，单位 `bit/s`，压缩格式时用于估算时长|

**返回：**

`AudioData` 实例。

**异常：**

无。

##### `save`

**接口签名：**

```python
AudioData.save(path: str) -> None
```

**功能：**

保存音频到文件。文件后缀必须是 `.wav`、`.pcm` 或 `.mp3`，且数据必须是 PCM；保存为 `.mp3` 还需要系统已安装 `ffmpeg`。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`path`|`str`|是|输出文件路径，根据后缀自动选择保存格式|

**返回：**

`None`。

**异常：**

`ValueError`: 后缀不支持，或 `coding_format` 不是 `"pcm"`。

`RuntimeError`: 保存为 `.mp3` 时未找到 `ffmpeg` 或编码失败。

##### `with_data`

**接口签名：**

```python
AudioData.with_data(data: bytes) -> AudioData
```

**功能：**

复制当前对象并替换音频数据，其余格式字段沿用原值；返回新对象，原对象不变。常用于按 chunk 切分或流式传输。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`data`|`bytes`|是|新的音频原始二进制数据|

**返回：**

`AudioData`，新的音频对象。

**异常：**

无。

##### `concat`

**接口签名：**

```python
@classmethod
AudioData.concat(
    chunks: Iterable[AudioData],
    *,
    header: Header | None = None,
) -> AudioData
```

**功能：**

合并多个格式一致的音频块。所有 chunk 必须有相同的 `sample_rate` / `channels` / `sample_format` / `coding_format`。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`chunks`|`Iterable[AudioData]`|是|待合并的音频块序列，至少一项|
|`header`|`Header | None`|否|合并后音频的元数据头；不传时使用第一块的 `header`|

**返回：**

`AudioData`，合并后的新对象。

**异常：**

`ValueError`: `chunks` 为空，或 chunk 之间格式不一致。

##### `to_numpy`

**接口签名：**

```python
AudioData.to_numpy() -> np.ndarray
```

**功能：**

转为 `[-1.0, 1.0]` 范围的浮点数组。多声道时形状为 `(n_samples, channels)`，单声道时为 `(n_samples,)`。

**参数：**

无参数。

**返回：**

`np.ndarray`，`dtype=np.float32`。

**异常：**

`ValueError`: `coding_format` 不是 `"pcm"`。

#### `ImageType`

图像类型，`str` 字面量类型别名。

**取值：**

|取值|说明|
|---|---|
|`"rgb"`|RGB 彩色图像|
|`"depth"`|深度图像|

#### `PcmSampleFormat`

PCM 采样格式，`str` 字面量类型别名。

**取值：**

|取值|说明|
|---|---|
|`"S16LE"`|16 位有符号整数，小端序|

### 4\.3 关节相关数据

关节相关类型可以分为两类：

- **关节状态**：用于表示机器人反馈的关节状态；`JointStates` 表示一帧状态，内部包含多个 `JointState`。

- **关节指令**：用于表示关节控制输入；`JointCommand` 表示单个关节的控制目标和控制参数。

#### `JointState`

表示单个关节的状态信息。也用作录制和回放轨迹中的每帧关节值。

**属性：**

|属性|类型|说明|
|---|---|---|
|`name`|`str`|关节名称，必须与 `robot.list_joints()` 返回的名称一致|
|`position`|`float | None`|位置，单位 `rad`|
|`velocity`|`float | None`|速度，单位 `rad/s`|
|`effort`|`float | None`|力矩，单位 `Nm`|
|`extra`|`dict[str, Any] | None`|厂家特定的透传或额外扩展信息字典|

##### `JointState`

**接口签名：**

```python
JointState(
    *,
    name: str,
    position: float | None = None,
    velocity: float | None = None,
    effort: float | None = None,
    extra: dict[str, Any] | None = None,
)
```

**功能：**

构造一个 `JointState` 实例。所有参数仅支持关键字传入。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`name`|`str`|是|关节名称|
|`position`|`float | None`|否|位置|
|`velocity`|`float | None`|否|速度|
|`effort`|`float | None`|否|力矩|
|`extra`|`dict[str, Any] | None`|否|扩展信息字典|

**返回：**

`JointState` 实例。

**异常：**

无。

#### `JointStates`

由多个 `JointState` 组成的一帧关节状态快照，保留关节顺序，并提供按名称查找的能力。

**属性：**

|属性|类型|说明|
|---|---|---|
|`header`|`Header`|关节状态元数据头|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`joints`|`list[JointState]`|关节状态列表|
|`timestamp`|`float`|秒级时间戳，等价于 `header.timestamp`|
|`names`|`list[str]`|所有关节名称，由 `joints` 计算得到|

##### `JointStates`

**接口签名：**

```python
JointStates(
    joints: list[JointState] | Mapping[str, JointState],
    header: Header | None = None,
)
```

**功能：**

构造一个 `JointStates` 实例。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`joints`|`list[JointState] | Mapping[str, JointState]`|是|一帧关节状态；传入 `Mapping` 时按值列表化，键被忽略|
|`header`|`Header | None`|否|关节状态元数据头；不传或传 `None` 时使用默认 `Header`|

**返回：**

`JointStates` 实例。

**异常：**

无。

##### `get_joint`

**接口签名：**

```python
JointStates.get_joint(name: str) -> JointState | None
```

**功能：**

按名称线性查找一个关节状态；找不到返回 `None`。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`name`|`str`|是|关节名称|

**返回：**

`JointState` 或 `None`。

**异常：**

无。

**示例：**

```python
states = robot.get_joint_states()

joint_name = states.names[0]
joint = states.get_joint(joint_name)
same_joint = states[joint_name]

for joint in states:
    print(joint.name, joint.position)
    # 输出示例：AAHead_Yaw -2.3181339201983064e-05
```

##### `to_numpy`

**接口签名：**

```python
JointStates.to_numpy() -> dict[str, Any]
```

**功能：**

返回 `name` 列表和数值字段的 numpy 数组。`position` / `velocity` / `effort` / `acceleration` / `temperature` 中所有关节都有值的字段才会出现在结果里。

**参数：**

无参数。

**返回：**

`dict[str, Any]`

- `name`：`list[str]`，关节名称列表。

- `position` / `velocity` / `effort` / `acceleration` / `temperature`：`np.ndarray(dtype=np.float64)`，仅当所有关节该字段均有值时才出现。

**异常：**

无。

**示例：**

```python
arrays = states.to_numpy()
print(arrays["name"])
# 输出示例：['AAHead_Yaw', 'Head_Pitch', 'ALeft_Shoulder_Pitch', ...]
if "position" in arrays:
    print(arrays["position"])
    # 输出示例：[-2.31813392e-05  5.19215362e-03  3.48944142e-02 ...]
```

#### `JointCommand`

表示单个关节的控制命令，描述控制目标和控制参数。

调用 `set_joints()` 时需要传入完整命令列表，具体规则以第 2 章 `set_joints()` 接口说明为准。

对 Booster 位置控制，建议显式传入 `kp` 和 `kd`。未传入的 `velocity`、`effort`、`kp`、`kd`、`weight` 会按 `0.0` 发送给机器人控制侧；这不等价于“保持默认增益”。

**属性：**

|属性|类型|说明|
|---|---|---|
|`name`|`str`|关节名称，必须与 `robot.list_joints()` 返回的名称一致|
|`position`|`float`|目标位置，单位 `rad`|
|`velocity`|`float | None`|目标速度|
|`effort`|`float | None`|前馈力矩|
|`acceleration`|`float | None`|目标加速度|
|`kp`|`float | None`|比例增益（刚度），用于位置闭环控制|
|`kd`|`float | None`|微分增益（阻尼），用于速度闭环控制|
|`weight`|`float | None`|控制权重，常用范围 `0.0 ~ 1.0`|

##### `JointCommand`

**接口签名：**

```python
JointCommand(
    *,
    name: str,
    position: float,
    velocity: float | None = None,
    effort: float | None = None,
    acceleration: float | None = None,
    kp: float | None = None,
    kd: float | None = None,
    weight: float | None = None,
)
```

**功能：**

构造一个 `JointCommand` 实例。所有参数仅支持关键字传入。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`name`|`str`|是|关节名称|
|`position`|`float`|是|目标位置|
|`velocity`|`float | None`|否|目标速度|
|`effort`|`float | None`|否|前馈力矩|
|`acceleration`|`float | None`|否|目标加速度|
|`kp`|`float | None`|否|比例增益|
|`kd`|`float | None`|否|微分增益|
|`weight`|`float | None`|否|控制权重|

**返回：**

`JointCommand` 实例。

**异常：**

无。

**示例：**

```python
from boosteros.types import JointCommand

joint_infos = robot.list_joints()

cmd = JointCommand(
    name=joint_infos[0].name,
    position=0.2,
    velocity=0.0,
    effort=0.0,
    kp=40.0,
    kd=2.0,
    weight=1.0,
)
```

### 4\.4 机器人元信息

#### `RobotInfo`

表示机器人名称、型号、序列号等基础信息。

**属性：**

|属性|类型|说明|
|---|---|---|
|`manufacturer`|`str`|机器人厂家名称|
|`model`|`str`|机器人型号；不可用时为空字符串|
|`name`|`str`|机器人名称；不可用时为空字符串|
|`serial_number`|`str`|序列号；不可用时为空字符串|
|`firmware_version`|`str`|固件版本号；不可用时为空字符串|
|`extra`|`dict[str, object]`|扩展信息|

##### `RobotInfo`

**接口签名：**

```python
RobotInfo(
    manufacturer: str,
    model: str,
    name: str,
    serial_number: str,
    firmware_version: str,
    extra: dict[str, object] = {},
)
```

**功能：**

构造一个 `RobotInfo` 实例。该类型为不可变 dataclass。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`manufacturer`|`str`|是|机器人厂家名称|
|`model`|`str`|是|机器人型号|
|`name`|`str`|是|机器人名称|
|`serial_number`|`str`|是|序列号|
|`firmware_version`|`str`|是|固件版本号|
|`extra`|`dict[str, object]`|否|扩展信息，默认空字典|

**返回：**

`RobotInfo` 实例。

**异常：**

无。

#### `JointLimits`

表示关节物理限位。

**属性：**

|属性|类型|说明|
|---|---|---|
|`min`|`float`|最小位置，单位 `rad`|
|`max`|`float`|最大位置，单位 `rad`|

##### `JointLimits`

**接口签名：**

```python
JointLimits(min: float, max: float)
```

**功能：**

构造一个 `JointLimits` 实例。该类型为不可变 dataclass。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`min`|`float`|是|最小位置|
|`max`|`float`|是|最大位置|

**返回：**

`JointLimits` 实例。

**异常：**

无。

#### `JointInfo`

表示一个可控关节的静态元信息。

**属性：**

|属性|类型|说明|
|---|---|---|
|`name`|`str`|关节名称|
|`limits`|`JointLimits`|关节位置范围|
|`max_torque`|`float | None`|关节最大力矩|
|`max_velocity`|`float | None`|关节最大速度|
|`extra`|`dict[str, object]`|扩展信息|

##### `JointInfo`

**接口签名：**

```python
JointInfo(
    name: str,
    limits: JointLimits,
    max_torque: float | None,
    max_velocity: float | None,
    extra: dict[str, object] = {},
)
```

**功能：**

构造一个 `JointInfo` 实例。该类型为不可变 dataclass。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`name`|`str`|是|关节名称|
|`limits`|`JointLimits`|是|关节位置范围|
|`max_torque`|`float | None`|是|关节最大力矩|
|`max_velocity`|`float | None`|是|关节最大速度|
|`extra`|`dict[str, object]`|否|扩展信息，默认空字典|

**返回：**

`JointInfo` 实例。

**异常：**

无。

#### `ActionInfo`

表示一个预定义动作的 ID、类型和可中断属性。

**属性：**

|属性|类型|说明|
|---|---|---|
|`id`|`str`|动作 ID，可作为 `robot.do_action(action_id)` 的 `action_id` 参数|
|`type`|`str`|动作类型，如 `"upper_body"`、`"whole_body"`|
|`duration`|`float`|预估持续时间，`-1` 表示不确定|
|`interruptible`|`bool`|是否可以中断|

##### `ActionInfo`

**接口签名：**

```python
ActionInfo(id: str, type: str, duration: float, interruptible: bool)
```

**功能：**

构造一个 `ActionInfo` 实例。该类型为不可变 dataclass。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`id`|`str`|是|动作 ID|
|`type`|`str`|是|动作类型|
|`duration`|`float`|是|预估持续时间，`-1` 表示不确定|
|`interruptible`|`bool`|是|是否可以中断|

**返回：**

`ActionInfo` 实例。

**异常：**

无。

当前支持的 `ActionInfo.id` 包括：

|`ActionInfo.id`|说明|
|---|---|
|`"hand_shake"`|握手|
|`"hand_wave"`|挥手|
|`"dance_new_year"`|拜年舞蹈|
|`"dance_rock_rolling"`|摇滚舞蹈|
|`"dance_towards_future"`|走向未来舞蹈|
|`"gesture_dabing"`|嘻哈超人|
|`"gesture_ultraman"`|奥特曼|
|`"bow"`|作揖|
|`"cheer"`|双手欢呼|
|`"lucky_cat"`|招财猫|

#### `RobotModeName`

机器人模式名，`str` 字面量类型别名。

**取值：**

|取值|说明|
|---|---|
|`"damping"`|阻尼模式，关节有阻尼但不主动运动或保持位置，机器人需要有效支撑。|
|`"prepare"`|准备模式，机器人进入并保持站立姿态，可作为进入其他运动模式前的准备状态。|
|`"walk"`|行走模式，支持行走、转向、踏步、立定、转头等预制动作，抗扰能力高于准备模式。|
|`"custom"`|自定义关节控制模式，关节控制交由用户指令，适合自定义动作，需谨慎避免机器人失衡。|

#### `RobotGaitName`

机器人步态名，`str` 字面量类型别名。

**取值：**

|取值|说明|
|---|---|
|`"default"`|标准行走步态。|
|`"soccer"`|足球步态，用于足球相关动作与运动。|

### 4\.5 任务与订阅

#### `TaskHandle`

表示一个异步任务句柄，用于查询状态、等待任务进入终态或请求取消。`TaskHandle` 由 SDK 接口返回，应用代码不直接构造。

**属性：**

|属性|类型|说明|
|---|---|---|
|`trace_id`|`str`|任务实例追踪标识，由 `TaskHandle` 自动生成|
|`task_id`|`str`|同类任务的业务标识，如动作 ID、轨迹来源或音频来源|
|`type`|`str`|任务类型|
|`group`|`str`|任务分组，同组任务通常互斥|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`status`|`TaskStatus`|当前任务状态，见「[4\.5 任务与订阅 / TaskStatus](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnlsDVXN4gaewyg5eRZGi6ff)」|
|`error`|`Exception | None`|任务失败时的异常对象，否则为 `None`|

##### `wait`

**接口签名：**

```python
TaskHandle.wait(timeout: float | None = None) -> TaskStatus
```

**功能：**

阻塞等待任务进入终态；超时则返回当前状态。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`timeout`|`float | None`|否|等待超时秒数；不传或 `None` 表示无限等待|

**返回：**

`TaskStatus`，见「[4\.5 任务与订阅 / TaskStatus](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnlsDVXN4gaewyg5eRZGi6ff)」

**异常：**

无。

##### `cancel`

**接口签名：**

```python
TaskHandle.cancel() -> bool
```

**功能：**

请求取消任务；取消是否被接受由具体任务类型决定。

**参数：**

无参数。

**返回：**

`bool`，是否成功提交取消请求。

**异常：**

无。

##### `done`

**接口签名：**

```python
TaskHandle.done() -> bool
```

**功能：**

判断任务是否已进入终态（`SUCCEEDED` / `FAILED` / `CANCELLED`）。

**参数：**

无参数。

**返回：**

`bool`。

**异常：**

无。

##### `running`

**接口签名：**

```python
TaskHandle.running() -> bool
```

**功能：**

判断任务是否仍在执行（状态为 `RUNNING`）。

**参数：**

无参数。

**返回：**

`bool`。

**异常：**

无。

##### `task_info`

**接口签名：**

```python
TaskHandle.task_info() -> TaskInfo
```

**功能：**

返回任务身份与状态的不可变快照，常用于过滤函数。

**参数：**

无参数。

**返回：**

`TaskInfo`。

**异常：**

无。

##### `add_done_callback`

**接口签名：**

```python
TaskHandle.add_done_callback(fn: Callable[[TaskHandle[T]], None]) -> None
```

**功能：**

注册任务进入终态时的回调函数；如果任务已经处于终态，会立即调用一次。回调中抛出的异常会被记录但不会传播。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`fn`|`Callable[[TaskHandle[T]], None]`|是|终态回调，接收当前 `TaskHandle`|

**返回：**

`None`。

**异常：**

无。

##### `add_status_change_callback`

**接口签名：**

```python
TaskHandle.add_status_change_callback(fn: Callable[[TaskHandle[T]], None]) -> None
```

**功能：**

注册状态变化回调；注册时会立即用当前状态触发一次。回调中抛出的异常会被记录但不会传播。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`fn`|`Callable[[TaskHandle[T]], None]`|是|状态变化回调，接收当前 `TaskHandle`|

**返回：**

`None`。

**异常：**

无。

#### `TaskInfo`

表示任务身份与状态的不可变快照，常用于 `get_active_tasks(filter=...)` 的过滤函数。

**属性：**

|属性|类型|说明|
|---|---|---|
|`trace_id`|`str`|任务实例追踪标识|
|`task_id`|`str`|同类任务的业务标识|
|`type`|`str`|任务类型|
|`group`|`str`|任务分组|
|`status`|`TaskStatus`|任务状态，见「[4\.5 任务与订阅 / TaskStatus](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnlsDVXN4gaewyg5eRZGi6ff)」|

##### `TaskInfo`

**接口签名：**

```python
TaskInfo(
    trace_id: str,
    task_id: str,
    type: str,
    group: str,
    status: TaskStatus,
)
```

**功能：**

构造一个 `TaskInfo` 实例。该类型为不可变 dataclass，应用代码通常不直接构造。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`trace_id`|`str`|是|任务实例追踪标识|
|`task_id`|`str`|是|同类任务的业务标识|
|`type`|`str`|是|任务类型|
|`group`|`str`|是|任务分组|
|`status`|`TaskStatus`|是|任务状态，见「[4\.5 任务与订阅 / TaskStatus](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnlsDVXN4gaewyg5eRZGi6ff)」|

**返回：**

`TaskInfo` 实例。

**异常：**

无。

`group` 与 `type` 的取值如下：

|`group`|`type`|说明|
|---|---|---|
|`"motion"`|`"action"`|执行预设动作|
|`"motion"`|`"get_up"`|起身|
|`"motion"`|`"trajectory_replay"`|回放手把手轨迹|
|`"audio"`|`"play_audio_file"`|播放本地音频文件|
|`"audio"`|`"play_audio_buffer"`|播放内存中的有限 PCM 音频数据|
|`"audio"`|`"play_audio_stream"`|播放可持续写入的 PCM 音频流|
|`"audio"`|`"speech_stream_recognize"`|语音识别流|
|`"audio"`|`"speech_chat"`|语音对话|

#### `TaskStatus`

任务状态，`str` 字面量类型别名。

**取值：**

|取值|是否终态|说明|
|---|---|---|
|`"PENDING"`|否|任务已创建，尚未开始执行。|
|`"RUNNING"`|否|任务正在执行。|
|`"CANCELLING"`|否|任务正在取消。|
|`"SUCCEEDED"`|是|任务执行成功。|
|`"FAILED"`|是|任务执行失败。|
|`"CANCELLED"`|是|任务已取消。|

#### `OverflowPolicy`

订阅或流式写入队列满时的处理策略，`str` 字面量类型别名。

**取值：**

|取值|说明|
|---|---|
|`"block"`|等待队列可写入|
|`"drop_oldest"`|丢弃队列中最早的数据|
|`"drop_newest"`|丢弃新到达的数据|

#### `SensorSubscription`

表示传感器订阅句柄，由订阅接口返回。`SensorSubscription` 也实现了上下文管理协议，可用 `with` 语句自动取消订阅。

##### `unsubscribe`

**接口签名：**

```python
SensorSubscription.unsubscribe() -> None
```

**功能：**

取消订阅，后续不再触发回调。重复调用是幂等的。

**参数：**

无参数。

**返回：**

`None`。

**异常：**

无。

#### `AudioPlaybackStreamHandle`

表示可写入的 PCM 播放流句柄，由 `audio_manager.play_stream()` 返回。`AudioPlaybackStreamHandle` 不可直接构造。

##### `write`

**接口签名：**

```python
AudioPlaybackStreamHandle.write(audio: AudioData) -> None
```

**功能：**

追加格式一致的 `AudioData` chunk；格式（`sample_rate` / `channels` / `sample_format`）必须与打开流时一致。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`audio`|`AudioData`|是|待追加的音频数据，格式必须与流一致|

**返回：**

`None`。

**异常：**

`RuntimeError`: 流已关闭或写入失败。

##### `close`

**接口签名：**

```python
AudioPlaybackStreamHandle.close() -> None
```

**功能：**

结束写入；已写入的音频会继续播放直至播完。重复调用是幂等的。

**参数：**

无参数。

**返回：**

`None`。

**异常：**

无。

##### `is_playing`

**接口签名：**

```python
AudioPlaybackStreamHandle.is_playing() -> bool
```

**功能：**

查询当前是否正在播放音频。

**参数：**

无参数。

**返回：**

`bool`，`True` 表示正在播放。

**异常：**

无。

##### `has_pending_audio`

**接口签名：**

```python
AudioPlaybackStreamHandle.has_pending_audio() -> bool
```

**功能：**

查询队列中是否还有待播放的音频数据。

**参数：**

无参数。

**返回：**

`bool`，`True` 表示队列非空。

**异常：**

无。

##### `wait`

**接口签名：**

```python
AudioPlaybackStreamHandle.wait(timeout: float | None = None) -> TaskStatus
```

**功能：**

阻塞等待播放任务进入终态；超时则返回当前状态。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`timeout`|`float | None`|否|等待超时秒数；不传或 `None` 表示无限等待|

**返回：**

`TaskStatus`，见「[4\.5 任务与订阅 / TaskStatus](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnlsDVXN4gaewyg5eRZGi6ff)」

**异常：**

无。

##### `cancel`

**接口签名：**

```python
AudioPlaybackStreamHandle.cancel() -> bool
```

**功能：**

请求取消播放任务，立即停止播放并清空队列。

**参数：**

无参数。

**返回：**

`bool`，是否成功提交取消请求。

**异常：**

无。

### 4\.6 轨迹数据

#### `TrajectoryMeta`

表示通用轨迹的元数据。

**属性：**

|属性|类型|说明|
|---|---|---|
|`id`|`str`|轨迹 ID|
|`duration`|`float`|轨迹总时长，单位秒|
|`sample_interval`|`float`|采样间隔，单位秒|
|`manufacturer`|`str`|厂家|
|`model`|`str`|机型|
|`firmware_version`|`str`|固件版本|
|`boosteros_version`|`str`|BoosterOS 版本|

##### `TrajectoryMeta`

**接口签名：**

```python
TrajectoryMeta(
    *,
    id: str = "",
    duration: float = 0.0,
    sample_interval: float = 0.0,
    manufacturer: str = "",
    model: str = "",
    firmware_version: str = "",
    boosteros_version: str = "",
)
```

**功能：**

构造一个 `TrajectoryMeta` 实例。该类型为不可变 dataclass，所有参数仅支持关键字传入。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`id`|`str`|否|轨迹 ID|
|`duration`|`float`|否|轨迹总时长，单位秒|
|`sample_interval`|`float`|否|采样间隔，单位秒|
|`manufacturer`|`str`|否|厂家|
|`model`|`str`|否|机型|
|`firmware_version`|`str`|否|固件版本|
|`boosteros_version`|`str`|否|BoosterOS 版本|

**返回：**

`TrajectoryMeta` 实例。

**异常：**

无。

##### `to_dict`

**接口签名：**

```python
TrajectoryMeta.to_dict() -> dict[str, object]
```

**功能：**

转为可 JSON 序列化的字典。

**参数：**

无参数。

**返回：**

`dict[str, object]`。

**异常：**

无。

#### `JointTrajectoryPoint`

表示轨迹中的一帧关节数据。

**属性：**

|属性|类型|说明|
|---|---|---|
|`time_from_start`|`Duration`|从轨迹开始到当前帧的时间|
|`joints`|`list[JointState]`|当前帧的关节数值|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`joint_names`|`list[str]`|当前帧的关节名称，由 `joints` 计算得到|

##### `JointTrajectoryPoint`

**接口签名：**

```python
JointTrajectoryPoint(
    *,
    time_from_start: Duration,
    joints: list[JointState],
)
```

**功能：**

构造一个 `JointTrajectoryPoint` 实例。所有参数仅支持关键字传入。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`time_from_start`|`Duration`|是|从轨迹开始到当前帧的时间|
|`joints`|`list[JointState]`|是|当前帧的关节数值|

**返回：**

`JointTrajectoryPoint` 实例。

**异常：**

无。

##### `get_joint`

**接口签名：**

```python
JointTrajectoryPoint.get_joint(name: str) -> JointState | None
```

**功能：**

按名称查找当前帧的关节；找不到返回 `None`。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`name`|`str`|是|关节名称|

**返回：**

`JointState` 或 `None`。

**异常：**

无。

##### `to_dict`

**接口签名：**

```python
JointTrajectoryPoint.to_dict() -> dict[str, Any]
```

**功能：**

转为字典；`time_from_start` 序列化为纳秒整数。

**参数：**

无参数。

**返回：**

`dict[str, Any]`。

**异常：**

无。

#### `TrajectoryData`

表示一条通用轨迹，由元数据和多个 `JointTrajectoryPoint` 组成。

**属性：**

|属性|类型|说明|
|---|---|---|
|`meta`|`TrajectoryMeta`|轨迹元数据|
|`points`|`list[JointTrajectoryPoint]`|轨迹点列表|

**只读属性：**

|属性|类型|说明|
|---|---|---|
|`id`|`str`|轨迹 ID，等同于 `meta.id`|
|`joint_names`|`list[str]`|轨迹中的关节名称，取自第一个点|
|`duration`|`Duration`|轨迹总时长；可通过 `duration.seconds` 读取秒数|

##### `TrajectoryData`

**接口签名：**

```python
TrajectoryData(
    *,
    meta: TrajectoryMeta | None = None,
    points: list[JointTrajectoryPoint] | None = None,
)
```

**功能：**

构造一个 `TrajectoryData` 实例。所有参数仅支持关键字传入。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`meta`|`TrajectoryMeta | None`|否|轨迹元数据；未传时使用默认空元数据|
|`points`|`list[JointTrajectoryPoint] | None`|否|轨迹点列表；未传时使用空列表|

**返回：**

`TrajectoryData` 实例。

**异常：**

无。

##### `save`

**接口签名：**

```python
TrajectoryData.save(path: str | PathLike[str]) -> None
```

**功能：**

将轨迹保存为 `.btraj` 轨迹包；包内只包含 `meta.json` 和 `data.json`。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`path`|`str | PathLike[str]`|是|输出文件路径，必须以 `.btraj` 结尾|

**返回：**

`None`。

**异常：**

`ValueError`: 路径后缀不是 `.btraj`。

`RuntimeError`: 文件写入失败。

##### `load`

**接口签名：**

```python
@classmethod
TrajectoryData.load(path: str | PathLike[str]) -> TrajectoryData
```

**功能：**

从 `.btraj` 轨迹包加载并校验，返回新的 `TrajectoryData` 实例。

**参数：**

|参数名|类型|是否必填|说明|
|---|---|---|---|
|`path`|`str | PathLike[str]`|是|轨迹包路径，必须以 `.btraj` 结尾|

**返回：**

`TrajectoryData` 实例。

**异常：**

`ValueError`: 路径后缀不是 `.btraj`，或路径不是文件。

`FileNotFoundError`: 文件不存在。

`RuntimeError`: 包不是合法的 `.btraj`、JSON 解析失败、元数据缺字段或轨迹校验失败。

##### `to_numpy`

**接口签名：**

```python
TrajectoryData.to_numpy() -> dict[str, Any]
```

**功能：**

导出轨迹的 numpy 数组形式，便于分析或编辑。返回字典包含：

- `meta`：原始 `TrajectoryMeta`

- `joint_names`：`list[str]`

- `times`：`np.ndarray`，每帧 `time_from_start.seconds`

- `positions`：`np.ndarray`，形状 `(n_frames, n_joints)`，缺失值填 `np.nan`

- `velocities` / `accelerations` / `efforts`：可选，对应字段在任一帧存在时输出

**参数：**

无参数。

**返回：**

`dict[str, Any]`。

**异常：**

无。

## 5\. 常见问题与调试工具

### 5\.1 常见问题

|问题|常见原因|处理方式|
|---|---|---|
|`LocoClientInitError`|机器人尚未完全启动，或超时时间过短|等待机器人完全启动（通常开机后 30–60 秒）再连接；或增大 `timeout` 参数后重试，例如 `BoosterRobot(timeout=30)`|
|非电池类 `get_xxx()` 快照接口抛 `DataNotReadyError`|数据在 `timeout` 内未到达；虚拟机器人或直连供电场景部分数据通道不可用|先尝试增大 `timeout`（如 `BoosterRobot(timeout=10)`）后重试；若持续出现，该数据通道在当前机器人配置中不可用，可在代码中捕获 `DataNotReadyError` 并降级处理|
|`set_joints()` 抛 `ValueError`|传入的关节数量不对|`set_joints()` 只接受两种情况：<br>① 控制全部关节（各机型关节数见 [T1](https://booster.feishu.cn/wiki/H2Dowdnokij7p8ks9K3cZPuJnOg)/[K1](https://booster.feishu.cn/wiki/Ly55wYhRZivZdhkou09cs1oPn6e) 说明书）<br>② 只控制 10 个上身关节（头部 2 个 \+ 双臂 8 个）。用 `robot.list_joints()` 获取完整关节名和数量|
|`set_velocity()` 后机器人不动|速度值太小，或机器人处于非运动模式|检查速度值是否过小（建议从 `0.1` 开始逐步增大）|
|`set_head_angle()` 后头不动|角度值超出关节限位，或值太小|用 `robot.get_joint_states()` 查看头部关节当前角度；若未达限位，逐渐增大 `angle` 参数后重试|
|`Speech` 任务启动失败|语音能力不可用、音频服务异常或麦克风流不可用|① 确认使用的是真实机器人（虚拟机器人不支持语音）<br>② 检查机器人麦克风和扬声器是否正常工作<br>③ 硬件无异常则重启机器人后重试|
|`AudioData.save()` 转码失败|保存非 WAV 格式时没有安装 `ffmpeg`|安装 `ffmpeg` 后再试|
|`Detection` 无法导入|没装视觉可选依赖|运行 `python3 -m pip install "boosteros[brain]"`|
|`Detection` 初始化模型失败|模型路径不存在、可选依赖缺失、ONNX Runtime / CUDA / TensorRT 环境不可用|先使用 `Detection(model="default", device="cpu")` 验证基础链路，再切换自定义模型或 GPU 后端|
|示教录制/播放功能不正常|双板机型（如 T1）的示教功能只在运控板上可用|将示教相关代码部署到运控板上执行|

### 5\.2 提取示例代码

为了帮助你快速上手并验证硬件状态，`boosteros` 随安装包提供了一系列示例代码，并提供了一键提取工具。

安装 `boosteros` 后，你可以在终端直接运行以下命令，将内置示例拷贝到当前工作目录：

```bash
boosteros-examples
```

执行后，当前目录下会生成一个 `boosteros_examples` 文件夹，包含以下核心示例：

|示例文件|功能描述|适用模式|
|---|---|---|
|`booster/joint_control_example.py`|**交互式全量关节调试工具**。支持通过名称或索引微调所有关节，自动补全位姿，支持 K1/T1 机型。|`"custom"`|
|`booster/joint_upper_control_example.py`|**交互式上身调试工具**。在不影响下身行走的情况下，接管头部和双臂（前 10 关节）的控制权。|`"walk"`|
|`booster/action_control_example.py`|**交互式动作调试工具**。支持查看动作列表、启动动作、停止动作，并在结束时阻塞等待机器人动作真正结束。|高级任务接口|
|`booster/hand_guilding_example.py`|**拖动示教示例**。展示如何录制轨迹、可选保存为 `.btraj`、读取轨迹信息并回放。|`"walk"`|
|`booster/go_to_ball.py`|**视觉闭环控制示例**。展示如何结合 `boosteros.brain` 检测球体并控制机器人追踪。|`"walk"`|

### 5\.3 示例更新与备份机制

升级 `boosteros` 后，再次运行 `boosteros-examples` 会比对本地示例目录与当前 SDK 内置示例的版本：

- **版本一致**：提示已是最新版本并退出，不修改任何文件。

- **版本不一致**：进入交互菜单，提供 3 个选项：

    - `[b] Backup`（推荐）：将本地目录重命名为 `boosteros_examples_old_<时间戳>` 备份，再导出新版。

    - `[o] Overwrite`：直接用新版覆盖本地目录，**已有修改会丢失**。

    - `[q] Quit`：取消操作。

也可以通过命令行参数跳过交互，直接执行对应动作：

|参数|等价选项|行为|
|---|---|---|
|`boosteros-examples -b`|`[b] Backup`|强制备份旧版后导出新版|
|`boosteros-examples -f`|`[o] Overwrite`|强制覆盖已有目录|

### 5\.4 调试建议：增益优化

在调试关节控制时，可以先参考示例代码中的 KP/KD（刚度/阻尼）参数，再根据实机表现小幅调整：

|场景|KP 参考值|KD 参考值|
|---|---|---|
|上身 10 关节|`[40.0, 40.0, 20.0, 30.0, 10.0, 10.0, 20.0, 30.0, 10.0, 10.0]`|`[2.0, 2.0, 1.5, 2.0, 0.5, 0.5, 1.5, 2.0, 0.5, 0.5]`|
|腿部 6 关节（单侧）|`[250.0, 250.0, 150.0, 250.0, 120.0, 120.0]`|`[15.0, 15.0, 8.0, 12.0, 8.0, 8.0]`|
|T1 腰部|`100.0`|`8.0`|

如果动作过于生硬，可优先降低上身 KP；如果落脚冲击或震动明显，可优先增大腿部 KD。

## 6\. 通用 Robot 开发协议

`boosteros.base.Robot` 是机器人客户端的抽象基类，目的是提供统一的接口抽象，便于在编写跨机型通用代码时，将 `Robot` 用作函数或方法参数的类型注解。例如，通用函数可以声明接收 `Robot`，实际运行时传入 `BoosterRobot` 或其他继承 `Robot` 的具体实现。

`Robot` 是抽象类，不能直接实例化，也不负责连接任何具体机器人。下面的接口表用于快速了解公共方法的能力分类、返回类型和用途；具体调用签名、参数、异常和实现行为，请以对应机器人实现（如第 2 章 `BoosterRobot`）的接口说明为准。

通用接口速查表：

|能力分类|接口名|返回类型|功能说明|
|---|---|---|---|
|信息查询|`robot_info`|[`RobotInfo`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcngoJaAct5egCyRT63Lhn5Az)|获取机器人名称、型号、序列号、固件版本等基础元信息。|
|信息查询|`list_frames`|`list[str]`|获取当前可查询的坐标系名称。|
|信息查询|`list_joints`|`list[`[`JointInfo`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnODDIasVuVRZ0qpsNkAXJRg)`]`|获取当前机器人可用关节信息。|
|信息查询|`list_actions`|`list[`[`ActionInfo`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcndSrPhQjb6iWYix4wczYaRb)`]`|获取当前机器人支持的预定义动作列表。|
|感知数据|`get_image`|[`AnyImage`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcn9T5Bh7qH1pbr2CxIc4vI4c)|读取一帧相机图像快照。|
|感知数据|`get_camera_info`|[`CameraInfo`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnWZx2AAusBVKKSB15yPutfd)|读取相机内参与标定信息。|
|感知数据|`get_imu`|[`IMUState`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnDBFflMYQMF10BZ7fENTAVf)|读取一帧 IMU 数据快照。|
|感知数据|`get_odom`|[`OdomState`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcneioKwezCWSBSnI0hObTcRb)|读取一帧里程计数据快照。|
|感知数据|`get_joint_states`|[`JointStates`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnUcWuEanEB8xzh1zyJUCh7m)|读取当前所有关节状态。|
|感知数据|`get_battery`|[`BatteryState`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnTFtMCP6KRYq8ZIUqraGmGY)|读取当前电池状态。|
|感知数据订阅|`subscribe_image`|[`SensorSubscription`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnjWingulFMQcB49ED0xI1Qe)|订阅相机图像更新。|
|感知数据订阅|`subscribe_imu`|[`SensorSubscription`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnjWingulFMQcB49ED0xI1Qe)|订阅 IMU 数据更新。|
|感知数据订阅|`subscribe_odom`|[`SensorSubscription`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnjWingulFMQcB49ED0xI1Qe)|订阅里程计数据更新。|
|感知数据订阅|`subscribe_battery`|[`SensorSubscription`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnjWingulFMQcB49ED0xI1Qe)|订阅电池状态更新。|
|坐标变换|`get_transform`|[`Transform`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnzMwnDVKAdVdkl4REsZtOMf)|查询两个坐标系之间的最新变换。|
|基础控制|`set_velocity`|`None`|设置机器人在机器人坐标系下的平面运动速度。|
|基础控制|`set_joints`|`None`|批量下发关节控制命令。|
|基础控制|`reset_odom`|`None`|将机器人里程计重置到零位。|
|高级任务|`get_active_tasks`|`list[`[`TaskHandle`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnkAEjbFOxXpRmjIMOEmrBCg)`[None]]`|获取当前未结束的 TaskHandle，可按任务信息过滤。|
|高级任务|`do_action`|[`TaskHandle`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnkAEjbFOxXpRmjIMOEmrBCg)`[None]`|异步执行预定义动作并返回 TaskHandle。|
|高级任务|`execute_trajectory`|[`TaskHandle`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnkAEjbFOxXpRmjIMOEmrBCg)`[None]`|异步执行轨迹对象并返回 TaskHandle。|
|高级任务|`play_sound`|[`TaskHandle`](https://booster.feishu.cn/docx/QNrUdJBWroBsG8xf4kYcI2geniZ#doxcnkAEjbFOxXpRmjIMOEmrBCg)`[None]`|异步播放音频资源或文件并返回 TaskHandle。|

