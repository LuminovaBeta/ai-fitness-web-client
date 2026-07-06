部署：
```
git lfs install

git lfs pull

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

## RK3588 本地 NPU（OpenAI 兼容）对接说明

项目新增了 `web-ai-api/` 目录，用于将 `rkllm-ros2` 封装成 OpenAI 兼容接口：

- `GET /v1/models`
- `POST /v1/chat/completions`

后端 `services/llm_service.py` 已改为读取 `config/llm_rules.yaml` 中的配置进行调用，关键项：

- `api_client.base_url`（默认 `http://127.0.0.1:8081`）
- `api_client.timeout_sec`
- `models.default_local_model`

推荐在 RK3588 上按顺序启动：

1. `rkllm-ros2`（提供 ROS topic 推理能力）
2. `web-ai-api`（OpenAI 兼容桥接层）
3. Django 服务（业务后端）

## ✅ 当前后端实现进度（截至本次更新）

### 1) 数据模型新增与调整

- `UserProfile` 新增字段：
  - `phone`（手机号）
  - `avatar`（头像 URL）
- 新增 `TrainingSession` 模型（结构化训练会话持久化）：
  - `session_id`, `mode`, `exercises`, `sets`, `reps`, `rest_sec`, `intensity`
  - `status`, `phase`, `started_at`, `ended_at`, `final_reps`
  - `activity`（结束后关联 `Activity`）

> 迁移文件：`pages/migrations/0003_userprofile_phone_avatar_trainingsession.py`

### 2) 鉴权策略收敛（JWT）

除公开接口（注册/登录/人脸登录/TTS播报）外，主要业务接口已统一要求 JWT。

**公开接口：**
- `POST /api/auth/register/`
- `POST /api/auth/login/`
- `POST /api/auth/face-login/`
- `POST /api/train/tts-play/`

**需 JWT 的主要接口：**
- `POST /api/auth/face-register/`
- `POST /api/plan/init-generate/`
- `GET/POST /api/plan/current/`
- `GET /api/user/dashboard/`
- `GET /api/user/load/`
- `GET /api/user/activities/`
- `GET /api/user/activities/<pk>/`
- `GET /api/train/status/<pk>/`
- `POST /api/train/micro-coach/`
- `POST /api/train/finish/`
- `POST /api/chat/ask/`
- `GET /api/train/exercises/`

### 3) 新增结构化会话 API（已接入后端）

- `GET /api/user/profile/`（用户资料）
- `POST /api/train/session/start/`（开始会话）
- `GET /api/train/session/<session_id>/state/`（会话状态轮询）
- `POST /api/train/session/<session_id>/finish/`（结束会话并落盘 Activity）

### 4) 当前状态说明

- 结构化会话能力已从“进程内存”升级为“数据库持久化”，服务重启后会话可追溯。
- `A3` 会话状态接口目前为后端模拟状态（心率/血氧/进度）用于联调，后续可切换到 ROS 实时数据。
- API 详细入参与响应示例，请见：`API接口汇总与示例.md`。

---


1. 后端验证采用 Token 认证（如JWT）时，后端成功验证身份后会返回一段加密字符串。触屏前端或手机 App 只需要将其存入本地存储（LocalStorage 或 App 原生安全存储），并在后续每次向后端发送 HTTP 请求时，在请求头中附带 Authorization: Bearer <Token> 即可  
2. 我的业务逻辑目前规划是设计以下几个界面：
    - 全局顶部状态栏（类似安卓页面，左侧时间 右侧网络信号等状态）
    - 1.待机login登录界面，摄像头画面使用一个中央圆圈显示，下方保留传统的用户名密码登录
    - 2.登录后进入主界面，分为几个栏目：
    - -主界面（包括自主训练和开始训练（按计划）两个按钮以及一些主界面个人信息等dashboard）
    - -运动（训练计划以及历史记录，可以以列表或者日历两种方式查看，可以点进去查看详细记录，并可以在这里像ai提问总结）
    - -我（用户信息设置以及账号密码人脸录入等）
    - 3.点进自主训练后可以手动选择训练项目以及自定义个数及组数等信息
    - 4.开始训练 页面展示识别视频流以及各种提示框还有心率血氧等

# 智能健身体测一体机/App 全栈业务框架设计

本框架专为带有触摸屏、摄像头硬件设备及 App 套壳设计的“AI 智能健身终端”打造。采用 **边缘计算 (RK3588) + 云端轻量协作** 架构：前端单页应用 (SPA) 状态机 + ROS 高频推流 + Django 低频落盘 + 全本地大语言模型 (LLM) 智能调度 + 零算力高质量 Edge-TTS 语音交互 + 高精度 NPU 人脸无感门禁。

---

## 壹、 数据库核心框架 (Django Models)

围绕业务闭环，数据库采用“宏观+微观”主从表分离，并将人脸高维特征独立成表，以保障 ORM 查询的极速响应。

### 1. UserProfile (用户身体档案表)

存储用于大模型生成计划的基础生理数据。

* `user`: `OneToOneField(User)`
* `gender`: `CharField` (性别选择：'M'男, 'F'女, 'O'其他)
* `phone`: `CharField` (手机号，可选)
* `avatar`: `URLField` (头像链接，可选)
* `height`, `weight`: `FloatField` (身高cm，体重kg)
* `hr_max`: `IntegerField` (最大心率，默认190)
* `face_feature_id`: `CharField` (绑定的特征向量ID，用于快速关联)
* `updated_at`: `DateTimeField` (更新时间)

### 2. UserFaceEmbedding (用户人脸特征向量表)

将高维度数组从主档案剥离，专为 RK3588 边缘端提取的人脸特征比对而设计。

* `user`: `OneToOneField(User)`
* `embedding`: `JSONField` (核心：存储 w600k_r50 模型生成的 512 维高精度人脸特征浮点数列表，如 `[0.023, -0.104, ...]`)
* `created_at`: `DateTimeField`

### 3. TrainingPlan (训练计划表)

存储大模型生成的、结构化的计划。

* `user`: `ForeignKey(User)`
* `is_active`: `BooleanField` (是否为当前执行计划)
* `plan_type`: `CharField` (如 'LLM_GENERATED', 'USER_CUSTOM')
* `plan_content`: `JSONField`
* *数据结构要求*：`[{"day": 1, "exercises": [{"type": "squat", "sets": 3, "reps_per_set": 15, "rest_interval_sec": 60}]}]`


* `created_at`: `DateTimeField`

### 4. Activity (宏观训练记录表)

记录单次训练的整体表现（主表）。

* `user`: `ForeignKey(User)`
* `training_mode`: `CharField` ('GUIDED' 引导计划模式, 'FREE' 自由训练模式)
* `activity_type`: `CharField` (运动类型，如 'squat', 'mixed_plan' 等)
* `start_time`: `DateTimeField` (开始时间)
* `duration`: `IntegerField` (总耗时，秒)
* `total_reps`: `IntegerField` (总次数)
* `intensity`: `CharField` (前端基于静息期数据计算出的客观真实强度：LOW/MED/HIGH)
* `perceived_exertion`: `IntegerField` (用户主观感受/RPE，1-5级。1=极度轻松，3=适中，5=精疲力尽)
* `quality_score`: `IntegerField` (大模型评分 1-10)
* `calories`: `IntegerField` (消耗热量，kcal，可选)

### 5. ActivityTimeSeries (高频微观时序表 - 静息高质数据)

存储运动间歇休息和结束时的高频微观生理数据（规避运动伪影）。

* `activity`: `ForeignKey(Activity)` <-- **核心：与主表强关联，配置联合索引提升查询速度**
* `timestamp_offset`: `IntegerField` (相对开始时间的偏移秒数)
* `phase`: `CharField` (标记数据来源：'REST' 组间休息, 'END' 训练结束)
* `heart_rate`, `spo2`, `current_rep_count` (瞬时心率、血氧和累计计数)
* **写入机制**：仅在组间休息和训练结束时进行 1Hz 采样，训练彻底结束后打包批量落盘 (`bulk_create`)。

### 6. AIFeedback (AI 评语与报告表)

* `activity`: `OneToOneField(Activity)`
* `feedback_text`: `TextField` (大模型的文字评语)
* `next_step_suggestion`: `TextField` (后续计划优化建议)
* `created_at`: `DateTimeField`

### 7. TrainingSession (结构化训练会话持久化表)

用于前端 `src/api` 新增训练会话流（开始/轮询/结束）的数据库持久化。

* `user`: `ForeignKey(User)`
* `session_id`: `CharField(unique=True, index=True)`
* `mode`: `CharField` (`free` / `guided`)
* `exercises`: `JSONField` (动作编码数组)
* `sets`, `reps`, `rest_sec`: `IntegerField`
* `intensity`: `CharField` (`low` / `medium` / `high`)
* `status`: `CharField` (`RUNNING` / `FINISHED`)
* `phase`: `CharField` (`WORK` / `REST` / `END`)
* `started_at`, `ended_at`: `DateTimeField`
* `final_reps`: `IntegerField`
* `activity`: `ForeignKey(Activity, null=True)`（结束时关联训练记录）

---

## 贰、 核心 API 接口列表 (RESTful & JWT)

系统基于 Django REST Framework 构建，采用 Simple JWT 进行无状态鉴权。接口设计高度解耦，并针对边缘端一体机的交互场景（如底层硬件发声、异步 AI 分析）进行了深度定制。

### 模块 1：认证与人脸视觉 (Auth & Face CV)

* **`POST /api/auth/register/`：基础注册初始化**
* **功能**：无需鉴权。接收 `username`, `password` 以及可选的身体参数（`gender`, `height`, `weight`），自动创建 User 并初始化 `UserProfile` 身体档案表，直接下发 JWT 访问与刷新令牌。


* **`POST /api/auth/login/`：账号密码登录**
* **功能**：常规账密校验，成功后返回 JWT。


* **`POST /api/auth/face-register/`：人脸特征录入 (需 JWT)**
* **功能**：接收前端传来的摄像头 Base64 实时流。后端调用 `process_face_pipeline` 执行严格的距离与姿态防误判校验，通过后提取 512 维特征，落盘或覆写至 `UserFaceEmbedding` 库，并触发“人脸录入成功”系统语音。


* **`POST /api/auth/face-login/`：无感扫脸登录**
* **功能**：接收 Base64 数据流，经防误判管道后，调用 `verify_face_1_to_N` 进行全局特征库遍历（计算余弦相似度）。匹配成功直接发放 JWT，实现“走近即登录”的 Kiosk 体验。



### 模块 2：主页聚合与用户数据 (Dashboard & Data)

* **`GET /api/user/profile/`：用户资料查询（新增）**
* **功能**：返回当前登录用户基础资料，用于“我”页面展示（`username/phone/role/avatar/gender/height/weight`）。

* **`GET /api/user/dashboard/`：主页超级聚合面板**
* **功能**：一次性拉取一体机主页所需的所有核心数据。包括基础档案、近 7 日总训练耗时、**今日计划精准进度计算**（底层遍历 JSON 计划与今日真实运动记录比对），并动态触发系统级入场语音（如：“欢迎回来，今天还有进度未完成”）。


* **`GET /api/user/load/`：滚动训练负荷评估**
* **功能**：聚合查询近 7 天的所有 Activity，依据强度权重算法（高强度x3，中强度x2，低强度x1）和时长，推算出一个客观的滚动负荷值，并返回阶段性健康状态评估（如“高效训练量”或“高负荷运转”）。


* **`GET /api/user/activities/`：历史运动记录列表**
* **功能**：返回用户历史宏观运动表现的精简列表（耗时、总次数、AI 评分等）。


* **`GET /api/user/activities/<pk>/`：单次训练深度详情**
* **功能**：联表查询单次运动的宏观数据、AI 复盘报告详情（`ai_feedback`），以及按时间戳排序的 `sensor_data_series`（高频心率/血氧变化阵列），用于前端绘制专业级数据复盘曲线。


* **`GET /api/train/status/<pk>/`：训练分析状态轮询**
* **功能**：提供给前端在训练结束后轮询，检查对应的 Activity 是否已绑定 AI 生成的反馈报告，从而切换“分析中”和“分析完成”的 UI 状态。



### 模块 3：AI 教练与训练中枢 (Training & AI)

* **`POST /api/train/session/start/`：开始训练会话（新增）**
* **功能**：接收自由训练配置（动作/组数/次数/休息/强度），创建持久化会话 `session_id`，用于训练页状态机驱动。


* **`GET /api/train/session/<session_id>/state/`：训练会话状态轮询（新增）**
* **功能**：按会话返回当前状态（`status/phase/progress/heart_rate/spo2/current_rep` 等），用于前端实时看板刷新。


* **`POST /api/train/session/<session_id>/finish/`：结束训练会话（新增）**
* **功能**：结束指定会话并自动生成一条 `Activity` 记录，回传 `activity_id` 供后续详情/复盘调用。

* **`POST /api/plan/init-generate/`：AI 初始计划生成**
* **功能**：新用户注册后，前端携带目标（如“减脂塑形”）调用。后端结合用户档案身高体重，调度 LLM 生成结构化 JSON 训练计划落盘。自带容灾机制，若大模型拥堵会自适应下发基础兜底计划。


* **`GET / POST /api/plan/current/`：当前训练计划读写**
* **功能**：获取当前处在 `is_active=True` 状态的计划。POST 请求可用于用户或大模型直接覆盖生成全新的 JSON 计划。


* **`POST /api/train/micro-coach/`：组间话疗微指导**
* **功能**：训练间歇期调用。传入当前动作和错误类型，由 LLM 极速生成简短纠正话术，**并直接唤起后端系统级扬声器播报**，全程不阻塞前端页面流程。


* **`POST /api/train/finish/`：核心训练结算中枢 (异步高并发)**
* **核心逻辑**：
1. **极速落盘**：同步将主观感受、客观强度以及大批量 `time_series` (高质静息心率血氧数组) 落盘，立即返回 `HTTP 202 Accepted`。
2. **异步进化**：后端剥离出独立的 `threading.Thread` 进行 LLM 深度复盘任务。
3. **闭环覆写**：子线程中大模型分析表现后，自动更新运动质量分 `quality_score`、落盘 `AIFeedback` 评语，并**直接静默覆写下周的训练计划表**。最终在 `finally` 块中安全释放数据库连接。



* **`POST /api/chat/ask/`：本地 RAG 运动伴侣**
* **功能**：智能健身体测机的 AI 语音管家。后端动态计算用户近 7 日耗时、真实运动强度偏好以及上一次教练评语，**精准注入 Prompt (RAG 机制)** 喂给大模型。拒绝 AI 瞎编数据，随后驱动系统扬声器将文本转语音回答用户。


* **`GET /api/train/exercises/`：标准动作字典查询**
* **功能**：供前端“自由训练”模式调用的下拉选单，直接从 Django Models 动态抽取合法的业务动作池。



### 模块 4：系统底层交互 (System)

* **`POST /api/train/tts-play/`：哑终端全局系统发声**
* **功能**：跨越浏览器安全限制的神器。前端或局域网设备只需传入 `text` 和可选音色（如 `zh-CN-YunyangNeural`），后端直接驱动 RK3588 或开发机的物理声卡进行非阻塞音频播放。





---

## 叁、 边缘端视觉算法引擎 (RK3588 NPU)

设备的人脸识别与骨骼点捕捉不消耗云端算力，全部在 RK3588 板载 NPU 上利用 rknn-toolkit2 进行硬解码加速。

### 1. 人脸检测与特征模型选型

* **检测与对齐 (Detection & Alignment)**: 采用 `det_2.5g` 模型 (轻量级 RetinaFace 变体)，极速输出人脸边界框 (Bounding Box) 和五官关键点。
* **特征提取 (Recognition)**: 采用 `w600k_r50` 模型 (基于 WebFace600K 训练的 ResNet50 ArcFace 模型)，生成高精度 512 维特征向量，抗光照和姿态变化能力极强。

### 2. 防误识别与多目标优选机制 (Anti-False Trigger)

为避免路人经过导致设备频繁登录/误切账号，在 ROS/C++ 推理节点中加入严格的过滤逻辑：

* **防误识别 (距离与姿态锁)**:
* **距离锁**：`det_2.5g` 检出的人脸框面积必须大于屏幕设定的阈值，只有人凑近设备才被视为有效意图。
* **姿态锁**：根据五官关键点计算 Yaw(偏航角) 和 Pitch(俯仰角)，限制在 ±15度 以内。只有正对摄像头，才将人脸送入 `w600k_r50` 提特征。


* **多目标优选 (最近优先)**:
* 如果画面中同时检测到多张有效人脸，算法自动计算所有 Bounding Box 的面积。
* **只截取面积最大的一张脸（离屏幕最近的人）** 送入提取特征并发起登录请求，其他人脸直接丢弃。



---

## 肆、 前端业务框架与 Kiosk 沉浸式部署 (Vue 3)

### 1. 核心技术栈选择

* **核心**: Vue 3 (Composition API) + Vite + Pinia (状态管理)
* **UI 组件库**: Vant (移动触屏优先，大按钮设计) 或 Element Plus (大屏修改主题)。

### 2. 工业级硬件界面部署方案 (Kiosk Mode)

为了打造纯正的“智能硬件一体机”体验，绝对不能让用户看到浏览器的任何痕迹。在 RK3588 (Ubuntu/Debian) 上，使用 Chromium 的 Kiosk 模式进行开机自启全屏部署。

**部署启动命令**:

```bash
chromium-browser --no-sandbox \
  --kiosk \
  --disable-infobars \
  --incognito \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  http://127.0.0.1:8000

```

### 3. 状态机与页面流转 (Kiosk Flow)

触屏一体机页面流转必须是有限状态机，并在合适的节点触发后端 API 进行系统级语音播报。

* **「待机识别页」 (Standby State) - 实时人脸镜像**
* 屏幕常驻一个全屏或居中的大尺寸实时摄像头画面，让设备像一面“数字镜子”。
* **UI 引导提示层 (OSD)**: 前端接收边缘算法状态叠显提示（如：“请靠近屏幕”、“请抬头正视摄像头”、“识别中，请稍候...”）。


* **「注册与初始化过渡页」 (Onboarding Loading)**:
* **【新增交互】**：注册获取 Token 后，前端自动路由至此页面。播放炫酷的“AI教练正在生成计划”动画，同时静默调用 `/api/plan/init-generate/`，完成后自动切入主页。


* **「用户主页」 (Home Dashboard)**:
* `[适度语音 1]` 登入成功时播报：“欢迎回来，今天有一组深蹲计划等你完成。”
* 显示负荷指数、今日计划。提供“AI 私教伴侣”唤醒按钮。
* **全局闲置监听器**: 3 分钟无操作自动清空 Token 并退出到待机页。


* **「训练执行页」 (Active Training)**: 全屏沉浸式。
* **状态A：运动中 (EXERCISING)**。静音期，无语音干扰。心率血氧仅看板显示，不缓存数据。
* **状态B：组间休息 (RESTING)**。弹出大字提示“请保持传感器静止，正在精准采样”。
* `[适度语音 2 - 微指导]`：休息倒计时开始时，静默请求后端 `/micro-coach/`接口，拿到 AI 鼓励话语，由后端底层进程直接发声播报。


* **「结束与交互等待页」 (Feedback & Async Loading)**:
* `[适度语音 3]` 训练结束触发：“训练辛苦了！请在屏幕上选择一下您现在的疲劳感受。”
* **第一步：感受收集**：弹出巨大的 5 档心情/感受选择器收集 `perceived_exertion` (RPE，如 😌极度轻松、🥵精疲力竭)。
* **第二步：后台生成策略**：提供“等待详细报告 (播放加载动画)”或“太累了，让 AI 后台生成，先回主页”两个选项。


* **「训练报告页」 (Summary Report)**: 画出 ECharts 静息心率/血氧恢复曲线，展示 AI 评分与评语。

### 4. 高低频数据隔离策略

* **ROS 实时流 (WebSocket)**：封装 Vue `useRosData.js`。
* **运动静默，间歇抓取**：仅在 RESTING 和 END 状态将精准生理体征数据 push 到 `timeSeriesBuffer` 数组。
* **低频上传**：训练彻底结束后前端推导综合强度，连同主观感受一次性全量提交给 Django。

---

## 伍、 后端业务框架 (Django + 本地 LLM 协同)

### 1. 大模型配置解耦 (YAML 静态配置)

在 `config/llm_rules.yaml` 中维护动作库字典、判定阈值以及 Prompt 模板骨架。`services/llm_service.py` 运行时直接读取 YAML 进行动态变量注入，支持随时热调参。

### 2. 大模型协同工作流 (LLM Service Layer)

在 `services/llm_service.py` 中，分为 4 个主要调度场景：

* **场景一：初始化计划 (Onboarding)**
【独立解耦】接收注册后前端的主动请求，结合 YAML 里的动作库限制，生成基础 JSON 计划。
* **场景二：组间休息话疗指导 (Micro-Coaching)**
向本地 LLM 请求极短的 `max_tokens`，确保存几秒内返回文字供底层转换语音，不阻塞下一组运动。
* **场景三：复盘与计划底层自进化 (Post-Workout Auto-Update)**
* **主客观交叉验证逻辑**：结合“客观恢复心率/血氧”与“主观感受 (RPE)”。
* **降级计划触发条件**：心率/血氧客观指标异常，或者客观指标正常但用户主观感受为“精疲力尽(5分)”。
* **升级计划触发条件**：客观体征恢复迅速且动作标准，并且用户主观感受为“极度轻松/略有余力(1-2分)”。
* **后台覆盖机制**：由 Django 开启后台线程执行。完成后直接覆写 SQLite 里的 TrainingPlan 对象，零人工干预完成计划自适应进化。


* **场景四：个人运动数据库伴侣 (Local Data Chatbot)**
* **RAG (检索增强生成) 轻量实现**：检索用户最近 7 天的 Activity 宏观数据，拼接进 Prompt，赋予设备本地私有记忆。



---

## 陆、 大模型提示词推荐库 (Prompt Templates)

推荐将以下提示词结构写入 `llm_rules.yaml`。

### 1. 初始化计划生成 (Onboarding)

* **参数设定**: `temperature: 0.2`, `max_tokens: 300`
* **System Prompt**:
> 你是一个严谨的AI运动医学教练。请根据用户体征与目标，生成为期7天的训练计划。
> 限制规则：
> 每日最大组数<=5，单组最大次数<=20。休息日动作类型必须为"rest"。
> 绝对只输出合法的纯JSON数组，格式如下：`[{"day": 1, "exercises": [{"type": "squat", "sets": 3, "reps_per_set": 15, "rest_sec": 60}]}]`
> 不要包含任何Markdown标记或解释。


* **User Prompt**: `"用户档案: 性别 {gender}, 身高 {height}cm, 体重 {weight}kg。用户训练目标: {user_goal_text}"`

### 2. 组间微指导 (Micro-Coaching)

* **参数设定**: `temperature: 0.7`, `max_tokens: 50`
* **System Prompt**:
> 你是一个陪在用户身边的私人教练。用户刚完成一组运动并进入休息期。
> 限制规则：
> 根据视觉算法抓取的错误，给出一句口语化的鼓励和纠正提示。
> 句式为：“先肯定鼓励 + 指出调整方向”。绝对不能使用专业晦涩词汇。
> 必须非常简短，总字数绝对不能超过30个中文字符！不要有废话。


* **User Prompt**: `"刚刚的动作是 {activity_type}，视觉检测到的主要错误是：{error_text}。请直接输出你要对用户说的话："`

### 3. 复盘与计划自进化 (Post-Workout Auto-Update)

* **参数设定**: `temperature: 0.3`, `max_tokens: 500`
* **System Prompt**:
> 你是高级运动科学引擎。请根据用户刚完成的训练数据进行深度复盘。
> 交叉验证规则：
> 若 [恢复期平均心率] > {threshold_hr}，或 [最低血氧] < {threshold_spo2}%，或 [RPE疲劳度] 为 5，你必须在 new_plan 中降低训练负荷（减组数或次数）。
> 若体征平稳且 [RPE疲劳度] <= 2，你必须在 new_plan 中提升训练负荷。
> 必须且只能输出严格的JSON格式，包含字段："quality_score"(1-10的整数), "feedback_text"(100字内评语), "new_plan"(下周训练计划JSON数组)。


* **User Prompt**: `"目标: {target_reps}次, 实际完成: {actual_reps}次。动作错误: {error_count}次。静息期平均心率: {avg_rest_hr}BPM, 运动中最低血氧: {min_spo2}%。主观疲劳度RPE: {rpe_score}。请输出评估与计划 JSON："`

### 4. 个人运动伴侣问答 (Local Data Chatbot / RAG)

* **参数设定**: `temperature: 0.6`, `max_tokens: 150`
* **System Prompt**:
> 你是这台智能健身体测机的 AI 语音管家。你的职责是基于系统为你提供的【用户近期真实数据】，回答用户的健康提问。
> 限制规则：
> 你的回答必须以【用户近期真实数据】为唯一事实依据，绝不能瞎编数据。
> 如果提供的数据无法回答用户的问题，请礼貌地回答“从目前的记录中我暂时无法得出结论”。
> 语气要亲切、自然，就像朋友一样。


* **User Prompt**:
> 【用户近期真实数据】：
> 近 7 天总训练耗时：{weekly_duration} 分钟。近 7 天平均综合强度：{weekly_intensity}。上次教练评语：{last_feedback}。
> 【用户的提问】：“{user_chat_message}”
> 请回答：



---

## 柒、 商业级 Edge-TTS 后端直出发声方案

为了避免浏览器 Kiosk 模式下严苛的“禁止音频自动播放”安全策略，同时彻底摒弃 Linux 系统劣质的默认 TTS (espeak)，系统采用 **“Django 后端结合 Edge-TTS 系统级底层发声”** 的完美方案。

### 1. 架构优势

* **零算力损耗**：调用微软云端 Edge-TTS（神经网络发声），彻底解放 RK3588 本地 CPU/NPU，保障 ROS 视觉算法的流畅运行。
* **无视跨域与浏览器安全限制**：音频流根本不经过前端 Vue 页面，由后端系统底层进程直出。
* **商业真人音质**：提供播音级音色体验（如推荐教练音色：`zh-CN-YunxiNeural` 云希，阳光活力男声）。

### 2. 实施逻辑 (services/tts_service.py)

* **环境准备**: 在 RK3588 安装超轻量命令行播放器 `sudo apt-get install mpg123`，Python 虚拟环境安装 `edge-tts`。
* **异步生成**: `llm_service.py` 生成微指导或欢迎话术后，调用 `edge_tts.Communicate(text, "zh-CN-YunxiNeural", rate="+10%")` 将高质量语音毫秒级下载并保存为 `/tmp/coach.mp3`。
* **底层并发播放**: Django 业务逻辑中调用 `subprocess.Popen(["mpg123", "-q", "/tmp/coach.mp3"])` 唤起 Linux 系统底层的音频通道（3.5mm 或 HDMI 音响）进行非阻塞后台播放。API 响应即刻返回给前端，画面流畅流转，声音在后台主板上同步响起。