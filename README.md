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

本框架专为带有触摸屏、摄像头硬件设备及 App 套壳设计的“AI 智能健身终端”打造。采用 **纯本地离线计算 (RK3588 边缘端)** 架构：前端单页应用 (SPA) 状态机 + ROS 高频推流 + Django 低频落盘 + **全本地大语言模型 (LLM) 智能调度**。

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
  * *数据结构要求*：`[{"day": 1, "exercises": [{"type": "squat", "sets": 3, "reps_per_set": 15, "rest_interval_sec": 60}]}]`

### 3. `Activity` (宏观训练记录表)
记录单次训练的整体表现（主表）。
* `user`: ForeignKey(User)
* `training_mode`: CharField (可选：'GUIDED' 引导计划模式, 'FREE' 自由训练模式)
* `activity_type`: CharField (单项名称，或 'mixed_plan')
* `duration`: IntegerField (总耗时)
* `total_reps`: IntegerField (总次数)
* `intensity`: CharField (客观真实强度：LOW/MED/HIGH)
* `perceived_exertion`: IntegerField (用户主观感受/RPE，1-5级。1=很轻松，3=适中，5=精疲力尽)
* `quality_score`: IntegerField (大模型评分 1-10)

### 4. `ActivityTimeSeries` (高频微观时序表 - 静息高质数据)
存储运动间歇休息和结束时的高频微观生理数据（规避运动伪影）。
* `activity`: ForeignKey(Activity)  <-- 核心：与主表强关联
* `timestamp_offset`: IntegerField
* `phase`: CharField (标记数据来源：'REST' 组间休息, 'END' 训练结束)
* `heart_rate`, `spo2`, `current_rep_count` (瞬时心率、血氧和计数)
* *写入机制*：仅在组间休息和训练结束时进行 1Hz 采样，训练彻底结束后打包批量落盘。

### 5. `AIFeedback` (AI 评语与报告表)
* `activity`: OneToOneField(Activity)
* `feedback_text`: TextField (大模型的文字评语)
* `next_step_suggestion`: TextField (后续计划优化建议)

## 贰、 API 接口列表设计 (RESTful)

基于 Django REST Framework + Simple JWT 实现。

### 模块 1：设备驻留与认证 (Auth)
* **`POST /api/auth/register/`**：注册，直接调用本地 LLM 生成初始计划并返回 Token。
* **`POST /api/auth/login/`** / **`POST /api/auth/face-login/`**：账密/人脸登录，获取 JWT Token。

### 模块 2：用户主页与智能管家 (Dashboard & Chatbot)
* **`GET /api/user/dashboard/`**：主页聚合接口，拉取基础档案、今日计划、近7天负荷。
* **`POST /api/chat/ask/`**：**【新增】个人运动数据库伴侣接口**。接收用户聊天提问，后端将其与用户的历史数据打包，喂给本地大模型生成个性化回复。

### 模块 3：训练计划与控制 (Train)
* **`GET /api/plan/current/`**：获取当前激活的训练计划详情。
* **`POST /api/train/micro-coach/`**：**【新增】组间话疗接口**。接收上一组的动作错误码，调用本地小规模 LLM 快速生成一两句鼓励与纠正话语，返回给前端展示或语音播报。
* **`POST /api/train/finish/`**：**核心中枢结算接口（支持异步）**。
  * **Payload**: 包含客观体征、主观 RPE 及静息时序数组。
  * **Backend Logic**:
    1. Django 立即落盘 `Activity` 和 `ActivityTimeSeries`。
    2. **响应分离**：立即向前端返回 `HTTP 202 Accepted` (数据已保存，AI正在分析中) 以及一个 `activity_id`。
    3. 后端启动后台异步任务，由本地大模型慢速推理评估、生成报告并覆写更新后续计划。

## 叁、 前端业务框架 (Vue 3 触屏硬件架构)

### 1. 核心技术栈选择
* **核心**: Vue 3 (Composition API) + Vite + Pinia (状态管理)
* **UI 组件库**: Vant (移动触屏优先) 或 Element Plus (大屏修改主题)。

### 2. 状态机与页面流转 (Kiosk Flow)
触屏一体机不存在“浏览器后退按钮”，页面流转必须是有限状态机。

* **「待机识别页」 (Standby State)**: 屏幕保护状态，常驻人脸识别轮询。识别成功即登入。
* **「用户主页」 (Home Dashboard)**:
  * 显示负荷指数、今日计划卡片。
  * **【新增】个人运动伴侣入口**: 悬浮的“AI 私教”语音/文字聊天按钮。点击弹窗，用户可随时针对自己的训练数据向 AI 提问。
  * **全局闲置监听器**: 3 分钟无操作自动清空 Token 并退出到待机页。

* **「训练执行页」 (Active Training)**: 全屏沉浸式。根据 `TrainingPlan` 维护分段状态机：
  * **状态A：运动中 (EXERCISING)**。监听 ROS 计数。心率血氧仅看板显示不缓存。
  * **状态B：组间休息 (RESTING)**。弹出提示保持静止采样。**【新增】触发微指导(Micro-Coaching)**：在这几十秒休息期内，前端静默请求后端 `/micro-coach/` 接口，拿到 AI 根据上一组错误生成的鼓励话语，在 UI 底部展示或通过 TTS(文字转语音) 播报。

* **「结束与交互等待页」 (Feedback & Async Loading)**:
  * **第一步：感受收集**：弹出 5 档心情选择器收集 `perceived_exertion` (RPE)。
  * **第二步：后台生成策略**：调用 `/api/train/finish/`。由于本地大模型生成深度总结需要时间，UI 提供两个选项：
    * 选项 1：**[等一下，我要看详细报告]** (UI 播放酷炫的 AI 分析加载动画，轮询等待结果展示)。
    * 选项 2：**[太累了，让 AI 在后台生成，先回主页休息]** (退出到主页，后台大模型会继续慢速生成报告并更新计划)。

* **「训练报告页」 (Summary Report)**:
  * 提取 `timeSeriesBuffer` 画出 ECharts 静息心率/血氧恢复曲线。
  * 展示 AI 生成的最终评分 (1-10分)、评语和下一阶段计划变动提示。

### 3. 高低频数据隔离策略
* **ROS 实时流 (WebSocket)**：封装 Vue `useRosData.js`。
* **运动静默，间歇抓取**：仅在 `RESTING` 状态将精准生理体征数据 push 到 `timeSeriesBuffer` 数组。
* **低频上传**：训练彻底结束后一次性全量提交给 Django。

## 肆、 后端业务框架 (Django + 本地 LLM 协同)

在此纯本地架构下，RK3588 的算力被极致压榨。大模型的调度策略至关重要。

### 1. App 模块划分
* `apps.users`: 处理 `UserProfile`、JWT Auth、人脸绑定。
* `apps.training`: 处理 `Activity`, `ActivityTimeSeries`, `TrainingPlan`。
* `apps.ai_coach`: 专门封装与本地大模型通信的 Service 层。

### 2. 大模型协同工作流 (LLM Service Layer)

在 `services/llm_service.py` 中，根据业务划分为 4 个主要调度场景：

* **场景一：初始化计划 (Onboarding - 低频同步)**
  用户注册后，传入档案，LLM 生成基础 JSON 计划。使用 Pydantic 进行校验兜底。

* **场景二：组间休息话疗指导 (Micro-Coaching - 穿插异步)**
  * **提示词策略**：“用户正在做深蹲组间休息。上一组错误记录：[膝盖内扣]。请用教练口吻输出一句话（不超过30字）进行鼓励和动作纠正。”
  * **硬件考量**：此任务要求快，应向本地 LLM 请求较短的 `max_tokens`，确保在用户休息期结束前播报完毕，不阻塞下一个运动组。

* **场景三：复盘与计划底层自进化 (Post-Workout Auto-Update - 低频后台异步)**
  * **主客观交叉验证**：结合“客观恢复心率/血氧”与“主观感受 (RPE)”。
    * *降级条件*：心率血氧恢复极差，或用户 RPE 为 5 (精疲力尽)。LLM 输出降低强度的计划。
    * *升级条件*：体征恢复迅速，且用户 RPE 为 1-2。LLM 输出增重/加次的进阶计划。
  * **后台覆盖机制**：针对用户可能选择不等待直接回主页的情况，此生成任务由 Django 开启后台线程执行。完成后直接覆写 SQLite 里的 `TrainingPlan` 对象，**零人工干预完成计划自适应进化**。

* **场景四：个人运动数据库伴侣 (Local Data Chatbot - 交互同步)**
  * **应用场景**：纯正的智能硬件增值服务。
  * **RAG (检索增强生成) 轻量实现**：当用户提问“我最近几天练得怎样？”，后端自动检索该用户最近 7 天的 `Activity` 宏观数据，将其拼接进 Prompt (System Prompt: "你是该用户的私人教练，以下是他最近的训练数据：...，请回答他的问题：...")。让设备具备真正的本地私有记忆。