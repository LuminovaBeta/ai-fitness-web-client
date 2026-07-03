部署：
```
#同步项目
uv sync

#执行数据库迁移
uv run manage.py makemigrations
uv run manage.py migrate

#安装 Node 依赖
pnpm install

#启动 Django 服务器
uv run manage.py runserver 0.0.0.0:8000
```


1. 后端验证采用 Token 认证（如JWT）时，后端成功验证身份后会返回一段加密字符串。触屏前端或手机 App 只需要将其存入本地存储（LocalStorage 或 App 原生安全存储），并在后续每次向后端发送 HTTP 请求时，在请求头中附带 Authorization: Bearer <Token> 即可  



# 智能健身体测一体机/App 全栈业务框架设计

本框架专为带有触摸屏、摄像头硬件设备及 App 套壳设计的“AI 智能健身终端”打造。彻底摒弃传统 Web 页面的“点击-跳转-刷新”模式，采用 **前端单页应用 (SPA) 状态机 + 边缘节点 (ROS) 高频推流 + 云端 (Django) 低频落盘与大模型调度** 的现代物联网架构。

## 壹、 数据库核心框架 (Django Models)

围绕业务闭环，数据库需要支持多用户档案、结构化训练计划、宏观训练记录与微观生理时序数据。

### 1. `UserProfile` (用户档案表)

存储用于大模型生成计划的基础生理数据和人脸特征指纹。

* `user`: OneToOneField(User)
* `gender`, `height`, `weight`, `hr_max` (最大心率)
* `face_feature_id`: CharField (绑定的人脸特征向量ID，用于快速识别人脸登录)

### 2. `TrainingPlan` (训练计划表)

存储大模型生成的、结构化的计划。

* `user`: ForeignKey(User)
* `is_active`: BooleanField (是否为当前执行计划)
* `plan_type`: CharField (如 'LLM_GENERATED', 'USER_CUSTOM')
* `plan_content`: JSONField
  * *数据结构要求*：`[{"day": 1, "exercises": [{"type": "squat", "sets": 3, "reps_per_set": 15, "rest_interval_sec": 60}]}]` (必须支持运动类型、组数、每组次数、间歇时间)

### 3. `Activity` (宏观训练记录表)

记录单次训练的整体表现（主表）。

* `user`: ForeignKey(User)
* `training_mode`: CharField (可选：'GUIDED' 引导计划模式, 'FREE' 自由训练模式)
* `activity_type`: CharField (自由训练时为单项，引导模式可标为 'mixed_plan')
* `duration`: IntegerField (总耗时)
* `total_reps`: IntegerField (总次数)
* `intensity`: CharField (前端基于静息期数据计算出的客观真实强度：LOW/MED/HIGH)
* `perceived_exertion`: IntegerField (用户主观感受/RPE，1-5级。1=很轻松，3=适中，5=精疲力尽)
* `quality_score`: IntegerField (大模型评分 1-10)

### 4. `ActivityTimeSeries` (高频微观时序表 - 静息高质数据)

存储运动间歇休息和结束时的高频微观生理数据（规避运动伪影）。

* `activity`: ForeignKey(Activity)
* `timestamp_offset`: IntegerField (相对开始时间的秒数偏移)
* `phase`: CharField (标记数据来源：'REST' 组间休息, 'END' 训练结束)
* `heart_rate`, `spo2`, `current_rep_count` (瞬时心率、血氧和计数)
* *写入机制*：仅在组间休息和训练结束时进行 1Hz 采样，训练彻底结束后由前端打包发给后端，通过 `bulk_create` 批量落盘。

### 5. `AIFeedback` (AI 评语与报告表)

* `activity`: OneToOneField(Activity)
* `feedback_text`: TextField (大模型的文字评语)
* `next_step_suggestion`: TextField (后续计划优化建议)

## 贰、 API 接口列表设计 (RESTful)

基于 Django REST Framework + Simple JWT 实现。

### 模块 1：设备驻留与认证 (Auth)

* **`POST /api/auth/register/`**：注册账号，顺带接收身高体重数据。成功后直接调用本地 LLM 生成初始 `TrainingPlan` 并返回 Token。
* **`POST /api/auth/login/`**：常规账密登录。
* **`POST /api/auth/face-login/`**：人脸快速识别登录。边缘端将抓拍的人脸或特征码发给后端，后端比对成功后直接发放该用户的 JWT Token。

### 模块 2：用户主页与数据池 (User & Dashboard)

* **`GET /api/user/dashboard/`**：**主页聚合接口**。一次性拉取用户的：基础档案、今日训练计划(plan_content)、最近7天负荷指数、累计训练天数。避免前端发过多请求。
* **`POST /api/user/profile/`**：修改用户信息（若修改了重大生理指标，可触发重新生成计划）。

### 模块 3：训练计划与控制 (Train)

* **`GET /api/plan/current/`**：获取当前激活的训练计划详情。前端依据此 JSON 渲染具体的“第 1 组，深蹲 15 次 -> 休息 60 秒 -> 第 2 组”交互流程。
* **`POST /api/train/finish/`**：**核心中枢接口**。
  * **Payload**: 包含 `action`, `training_mode`, 前端算好的 `intensity` (客观强度), 用户选择的 `perceived_exertion` (主观感受), 以及仅包含静息期的 `time_series` 时序数组。
  * **Backend Logic**:
    1. Django 落盘：保存主表 `Activity` 和微观子表 `ActivityTimeSeries`。
    2. Django 调度 LLM：构建 Prompt 调用本地大模型，将“客观传感器数据”与“主观感受打分”结合，综合评估本次训练，并生成评语。
    3. **LLM 直接修改计划数据库**：如果是引导模式 (GUIDED)，大模型根据表现直接生成新的 JSON 计划结构，后端自动将其覆写更新至数据库的 `TrainingPlan` 表中。
    4. 返回评分、评语和落盘状态给前端展示。

## 叁、 前端业务框架 (Vue 3 触屏硬件架构)

这是打造“产品级交互”的核心。不可用传统的 Vue 网页写法。

### 1. 核心技术栈选择

* **核心**: Vue 3 (Composition API) + Vite
* **状态管理**: Pinia (缓存 JWT Token、当前用户的计划、高频 ROS 数据)
* **路由**: Vue Router (配置流畅的 `slide-left/slide-right` 页面切换过渡动画)
* **UI 组件库**: Vant (专为移动和触屏设计的 UI 库) 或 Element Plus (修改主题参数适配大屏)。

### 2. 状态机与页面流转 (Kiosk Flow)

触屏一体机不存在“浏览器后退按钮”，页面流转必须是有限状态机。

* **「待机识别页」 (Standby / Auto-Logout State)**:
  * 屏幕保护状态，常驻摄像头画面和温馨提示“靠近开始识别”。
  * 后台运行人脸识别轮询。识别成功 -> 触发登录 -> 存入 Pinia -> 路由至「用户主页」。

* **「用户主页」 (Home Dashboard)**:
  * 显示运动负荷指数、当前训练计划卡片、自由训练入口大按钮。
  * **全局闲置监听器 (Idle Timer)**：监听 `touchstart`。如果 3 分钟无操作，自动清空 Token，路由推回「待机识别页」。

* **「训练执行页」 (Active Training)**:
  * **UI 特点**: 全屏沉浸式。深色背景，巨大的计数数字。
  * **逻辑控制 (分段状态机)**：根据 `TrainingPlan` 维护步骤机：
    * **状态A：运动中 (EXERCISING)**。监听 ROS `currentRep` 进行动作计数。心率和血氧仅作 UI 看板显示，不写入缓存。
    * **状态B：组间休息 (RESTING)**。达到目标次数后跳入此状态。UI 弹出醒目提示：`“请保持传感器静止，正在精准抓取心率与血氧...”`，启动 1Hz 采样倒计时，将数据 push 进缓存。时间到跳回状态A。
  * **结束感受收集 (Feedback State)**: 训练全部完成后，弹出一个巨大的 5 档心情/感受选择器（如 😌极度轻松、🙂略有余力、😐强度适中、😓非常吃力、🥵精疲力竭）。
  * 感受选择完毕后，将分数赋值给 `perceived_exertion`，随后弹出 Loading 遮罩调用 `/api/train/finish/`。

* **「训练报告页」 (Summary Report)**:
  * 提取 `timeSeriesBuffer` 画出 ECharts 心率/血氧恢复曲线（重点展示静息期数据）。
  * 展示后端大模型返回的 `quality_score` (1-10分) 以及 AI 评语。

### 3. 间歇采样与高低频数据隔离策略

* **ROS 通信服务 (WebSocket)**: 封装独立 Vue 组合式函数 `useRosData.js`，实时更新响应式 `ref`。
* **运动静默，间歇抓取**：在 Pinia store 中声明 `timeSeriesBuffer`。只有当状态机处于 `RESTING` 或 `END` 时，才开启定时器，每秒将当前的精准生理体征存入数组。运动时定时器关闭。
* **低频上传**: 前端收集完高质静息数据（计算出客观 `intensity`）并结合用户点击的感受（主观 `perceived_exertion`）后，大数组一次性发给 Django。

## 肆、 后端业务框架 (Django + 本地 LLM 协同)

### 1. App 模块划分

* `apps.users`: 处理 `UserProfile`、JWT Auth、人脸绑定。
* `apps.training`: 处理 `Activity`, `ActivityTimeSeries`, `TrainingPlan`。
* `apps.ai_coach`: 专门封装与本地大模型 (如 Ollama) 通信的 Service 层。

### 2. 大模型协同工作流 (LLM Service Layer)

建一个独立的 `services/llm_service.py`。

* **初始化计划 (Onboarding)**：
  用户注册后，传入档案，LLM 返回结构化 JSON。使用 Pydantic 进行格式校验和异常兜底。

* **复盘与计划底层自进化 (Post-Workout Auto-Update)**：
  结算时，传入“实际完成组数”、“间歇期平均恢复心率”、“最低血氧”、“报错次数”以及**“用户主观感受打分(RPE)”**。

  * **主客观交叉验证逻辑**：
    * **降级计划触发条件**：心率/血氧客观指标异常，**或者**客观指标正常但用户主观感受为“精疲力尽(5分)”。这能有效防止用户在生病/状态差时过度训练。
    * **升级计划触发条件**：客观体征恢复迅速且动作标准，**并且**用户主观感受为“极度轻松/略有余力(1-2分)”。
  * **数据覆写**：LLM 输出新的 JSON 结构后，后端代码直接实例化新的 `TrainingPlan` 对象并保存，**零人工干预完成计划自适应进化**。

### 3. Kiosk/App 接口规范

* 后端所有接口返回的内容必须高度结构化，避免前端去处理复杂的逻辑。
* 遇到错误时返回友好的中文 `message`，让前端直接弹出 Toast（轻提示）。