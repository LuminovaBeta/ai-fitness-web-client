# AI Fitness Web Client 后端 API 接口汇总与请求示例

> 基础地址：`http://127.0.0.1:8000`
>
> 路由前缀：`/api/`

## 鉴权与人脸

### 1) 注册
- **URL**: `POST /api/auth/register/`
- **鉴权**: 否

**请求示例**
```json
{
  "username": "alice",
  "password": "123456",
  "gender": "F",
  "height": 165,
  "weight": 52
}
```

**响应示例**
```json
{
  "msg": "注册成功，账号档案已建立",
  "username": "alice",
  "refresh": "xxx",
  "access": "xxx"
}
```

---

### 2) 账号密码登录
- **URL**: `POST /api/auth/login/`
- **鉴权**: 否

**请求示例**
```json
{
  "username": "alice",
  "password": "123456"
}
```

**响应示例**
```json
{
  "msg": "登录成功",
  "username": "alice",
  "refresh": "xxx",
  "access": "xxx"
}
```

---

### 3) 人脸登录
- **URL**: `POST /api/auth/face-login/`
- **鉴权**: 否

**请求示例**
```json
{
  "face_data": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQ..."
}
```

**响应示例（成功）**
```json
{
  "code": "AUTH_SUCCESS",
  "msg": "登录成功",
  "username": "alice",
  "similarity": 0.92,
  "refresh": "xxx",
  "access": "xxx"
}
```

**业务状态码（`code`）说明**

| code | 含义 | 常见HTTP状态 |
|---|---|---|
| `AUTH_SUCCESS` | 人脸识别成功并完成登录 | `200` |
| `PARAMS_INVALID` | 未传有效 `face_data` | `400` |
| `USER_DISABLED` | 匹配到用户但账号被禁用 | `403` |
| `USER_NOT_FOUND` | 特征库未匹配到用户 | `404` |
| `IMG_ERROR` | 图像解码失败/数据格式错误 | `200`（业务错误） |
| `NO_FACE` | 未检测到人脸 | `200`（业务错误） |
| `TOO_FAR` | 人脸距离过远 | `200`（业务错误） |
| `NOT_FACING` | 人脸未正对摄像头 | `200`（业务错误） |

> 说明：当进入人脸防误判管道后，部分失败会以 `HTTP 200 + code!=SUCCESS` 返回，便于前端轮询统一处理。

---

### 4) 人脸录入
- **URL**: `POST /api/auth/face-register/`
- **鉴权**: 是（JWT）

**请求示例**
```json
{
  "face_data": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQ..."
}
```

**响应示例**
```json
{
  "code": "ENROLL_SUCCESS",
  "msg": "人脸特征绑定成功，已与当前身体档案互联"
}
```

**业务状态码（`code`）说明**

| code | 含义 | 常见HTTP状态 |
|---|---|---|
| `ENROLL_SUCCESS` | 人脸录入成功 | `201` |
| `PARAMS_INVALID` | `face_data` 为空 | `400` |
| `IMG_ERROR` / `NO_FACE` / `TOO_FAR` / `NOT_FACING` | 防误判管道未通过 | `200`（业务错误） |

---

## 训练计划与主页

### 5) 初始化训练计划生成
- **URL**: `POST /api/plan/init-generate/`
- **鉴权**: 是（JWT）

**请求示例**
```json
{
  "goal": "减脂塑形"
}
```

**响应示例**
```json
{
  "msg": "AI 专属训练计划生成成功",
  "plan_id": 12,
  "plan_content": [
    {
      "day": 1,
      "exercises": [
        {"type": "squat", "sets": 3, "reps_per_set": 15, "rest_sec": 60}
      ]
    }
  ]
}
```

---

### 6) 获取当前计划
- **URL**: `GET /api/plan/current/`
- **鉴权**: 是（JWT）

**响应示例**
```json
{
  "plan_id": 12,
  "created_at": "2026-07-05",
  "plan_content": []
}
```

---

### 7) 覆盖当前计划
- **URL**: `POST /api/plan/current/`
- **鉴权**: 是（JWT）

**请求示例**
```json
{
  "plan_content": [
    {
      "day": 1,
      "exercises": [
        {"type": "pushup", "sets": 4, "reps_per_set": 12, "rest_sec": 60}
      ]
    }
  ]
}
```

**响应示例**
```json
{
  "msg": "训练计划更新成功",
  "plan_id": 13
}
```

---

### 8) 主页聚合
- **URL**: `GET /api/user/dashboard/`
- **鉴权**: 是（JWT）

**响应示例**
```json
{
  "user_info": {
    "username": "alice",
    "gender": "女",
    "height": 165,
    "weight": 52
  },
  "weekly_duration_mins": 128.5,
  "plan_status": {
    "active_plan_id": 13,
    "is_completed": false,
    "progress_percent": 60,
    "today_exercises": [
      {"type": "squat", "target": 60, "actual": 36, "is_done": false}
    ]
  }
}
```

---

### 9) 训练负荷评估
- **URL**: `GET /api/user/load/`
- **鉴权**: 是（JWT）

**响应示例**
```json
{
  "rolling_7_days_load": 82.3,
  "workout_count": 6,
  "load_assessment": "高效训练量，心肺稳步提升"
}
```

---

## 训练记录

### 10) 历史训练列表
- **URL**: `GET /api/user/activities/`
- **鉴权**: 是（JWT）

**响应示例**
```json
{
  "records": [
    {
      "id": 101,
      "activity_type": "squat",
      "start_time": "2026-07-05 09:30:00",
      "duration": 900,
      "total_reps": 80,
      "intensity": "中强度",
      "quality_score": 8
    }
  ]
}
```

---

### 11) 单次训练详情
- **URL**: `GET /api/user/activities/<pk>/`
- **鉴权**: 是（JWT）

**响应示例**
```json
{
  "id": 101,
  "activity_type": "深蹲",
  "training_mode": "引导训练",
  "start_time": "2026-07-05 09:30:00",
  "duration_seconds": 900,
  "total_reps": 80,
  "intensity": "中强度",
  "perceived_exertion": 3,
  "quality_score": 8,
  "ai_report": {
    "feedback": "动作节奏稳定，后半程注意核心收紧。",
    "suggestion": "系统基于最新运动表现自动调优"
  },
  "sensor_data_series": [
    {"offset": 120, "phase": "组间休息", "heart_rate": 122, "spo2": 98, "current_rep": 30}
  ]
}
```

---

### 12) 训练分析状态轮询
- **URL**: `GET /api/train/status/<pk>/`
- **鉴权**: 是（JWT）

**响应示例（处理中）**
```json
{
  "status": "PROCESSING",
  "msg": "AI 后台正在疯狂运算中..."
}
```

**响应示例（已完成）**
```json
{
  "status": "COMPLETED",
  "msg": "AI 分析已完成",
  "quality_score": 9,
  "feedback_preview": "今天动作稳定，恢复..."
}
```

**业务状态字段（`status`）**

| status | 含义 |
|---|---|
| `PROCESSING` | 后台 AI 分析中 |
| `COMPLETED` | AI 分析已完成 |

---

## 训练中枢与 AI

### 13) 组间微指导
- **URL**: `POST /api/train/micro-coach/`
- **鉴权**: 是（JWT）

**请求示例**
```json
{
  "activity_type": "squat",
  "error_text": "膝盖内扣"
}
```

**响应示例**
```json
{
  "spoken_text": "做得不错，膝盖朝脚尖方向再稳一点。"
}
```

---

### 14) 训练结算
- **URL**: `POST /api/train/finish/`
- **鉴权**: 是（JWT）

> 定位：**兼容型结算接口（V1）**。当前实现依赖前端/设备上报 `duration`、`total_reps`、`time_series` 等训练结果，再由后端落库并触发 AI 分析。

**请求示例**
```json
{
  "training_mode": "FREE",
  "activity_type": "squat",
  "duration": 900,
  "total_reps": 80,
  "intensity": "MED",
  "perceived_exertion": 3,
  "target_reps": 90,
  "error_count": 2,
  "time_series": [
    {"offset": 120, "phase": "REST", "heart_rate": 122, "spo2": 98, "current_rep": 30},
    {"offset": 240, "phase": "REST", "heart_rate": 126, "spo2": 97, "current_rep": 60}
  ]
}
```

**响应示例**
```json
{
  "msg": "数据已保存，AI后台分析中",
  "activity_id": 101
}
```

**是否仍有用？（与会话化接口关系）**

- 若你的数据链路已改成“后端直接读设备/ROS”，则 `POST /api/train/session/<session_id>/finish/`（A4）更符合当前架构。
- `POST /api/train/finish/` 仍有价值：
  1. 兼容旧前端/旧设备（仍走前端上报）。
  2. 作为兜底导入接口（批量补写历史训练数据）。
  3. 在设备直连异常时，允许前端临时回传关键统计。
- 结论：**可以保留，但建议标记为“兼容接口（计划下线）”**，新功能统一走 `session/start -> session/state -> session/finish`。

---

### 15) AI 问答（RAG）
- **URL**: `POST /api/chat/ask/`
- **鉴权**: 是（JWT）

**请求示例**
```json
{
  "message": "我这周训练强度怎么样？"
}
```

**响应示例**
```json
{
  "reply": "你这周训练时长不错，整体以中强度为主，建议保持并逐步提升稳定性。",
  "rag_meta": {
    "injected_duration_mins": 128.5,
    "injected_intensity": "中强度"
  }
}
```

---

### 16) 动作字典查询
- **URL**: `GET /api/train/exercises/`
- **鉴权**: 是（JWT）

**响应示例**
```json
{
  "total": 6,
  "exercises": [
    {"code": "squat", "name": "深蹲"},
    {"code": "pushup", "name": "俯卧撑"}
  ]
}
```

---

### 17) 系统 TTS 播报
- **URL**: `POST /api/train/tts-play/`
- **鉴权**: 否（当前代码未强制）

**请求示例**
```json
{
  "text": "欢迎回来，今天继续加油！",
  "voice": "zh-CN-YunyangNeural"
}
```

**响应示例**
```json
{
  "msg": "系统扬声器已触发播报",
  "played_text": "欢迎回来，今天继续加油！",
  "voice": "zh-CN-YunyangNeural",
  "duration": 2.34
}
```

---

## 通用请求头（JWT）
```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

## 备注
- 文档基于当前项目代码中的 `config/urls.py`、`api/urls.py`、`api/views.py` 汇总。
- 已完成主要业务接口的 JWT 鉴权收敛（除注册/登录/人脸登录/TTS 播报等公开接口）。
- 训练会话已改为数据库持久化（`TrainingSession`），不再依赖进程内存。
- 用户资料新增 `phone`、`avatar` 字段（`UserProfile`）。

---

## 新增结构化 API（与以上既有接口分开）

### A1) 用户资料查询（新增）
- **URL**: `GET /api/user/profile/`
- **鉴权**: 是（JWT）

> 字段说明：`phone` 与 `avatar` 来自 `UserProfile`，无值时返回空字符串。

**响应示例**
```json
{
  "username": "alice",
  "phone": "13800000000",
  "role": "普通用户",
  "avatar": "https://example.com/avatar.jpg",
  "gender": "女",
  "height": 165,
  "weight": 52
}
```

---

### A2) 开始训练会话（新增）
- **URL**: `POST /api/train/session/start/`
- **鉴权**: 是（JWT）

**请求示例**
```json
{
  "mode": "free",
  "exercises": ["squat", "push_up"],
  "sets": 4,
  "reps": 12,
  "restSec": 45,
  "intensity": "medium"
}
```

**响应示例**
```json
{
  "session_id": "sess_20260705_ab12cd34",
  "msg": "训练会话创建成功"
}
```

---

### A3) 训练会话状态轮询（新增）
- **URL**: `GET /api/train/session/<session_id>/state/`
- **鉴权**: 是（JWT）

> 当前实现为后端模拟状态（心率/血氧/进度），用于联调；后续可替换为 ROS 实时数据。

**响应示例**
```json
{
  "session_id": "sess_20260705_ab12cd34",
  "status": "RUNNING",
  "phase": "WORK",
  "heart_rate": 128,
  "spo2": 98,
  "current_rep": 21,
  "target_reps": 48,
  "progress": 43.7,
  "coach_message": "动作节奏稳定，保持呼吸"
}
```

**业务状态字段说明**

- `status`
  - `RUNNING`：会话进行中
  - `FINISHED`：会话已结束
- `phase`
  - `WORK`：运动阶段
  - `REST`：组间休息
  - `END`：结束恢复

---

### A4) 结束训练会话（新增）
- **URL**: `POST /api/train/session/<session_id>/finish/`
- **鉴权**: 是（JWT）

> 现已实现“完整结算链路”：创建 `Activity`、过滤并保存高质量 `REST/END` 时序、异步触发 AI 分析并生成反馈/计划迭代。

**请求示例**
```json
{
  "reason": "user_finish",
  "final_reps": 76,
  "perceived_exertion": 3,
  "error_count": 2,
  "time_series": [
    {
      "offset": 520,
      "phase": "REST",
      "heart_rate": 116,
      "spo2": 98,
      "current_rep": 64,
      "is_stable": true,
      "hold_secs": 8
    },
    {
      "offset": 660,
      "phase": "END",
      "heart_rate": 108,
      "spo2": 99,
      "current_rep": 76,
      "is_stable": true,
      "hold_secs": 10
    }
  ]
}
```

**响应示例**
```json
{
  "msg": "训练会话已结束",
  "activity_id": 101,
  "saved_timeseries_count": 2,
  "analysis_status": "PROCESSING"
}
```

**字段与过滤规则（关键）**

- `time_series` 仅处理 `phase in ["REST", "END"]`。
- 仅保存稳定样本：`is_stable=true`，且 `hold_secs`（若传）需 `>=3` 秒。
- `WORK` 阶段样本会被忽略，不入库、不参与 AI 计算。
- 若接口被重复调用（会话已结束），不会重复创建 `Activity`，直接返回已有 `activity_id`（幂等）。

**AI 分析输入（已切换为高质量指标）**

- `rest_hr_median`
- `rest_spo2_median`
- `end_hr`
- `end_spo2`
- `valid_sample_count`
- `measurement_confidence`（`HIGH` / `MED` / `LOW`）
- 以及负荷字段：`target_reps`、`actual_reps`、`error_count`、`rpe_score`

> 推荐：在“后端读取训练数据”的新架构下，优先使用该接口作为唯一结算入口。

---

## 结算接口选型建议（新增）

| 场景 | 推荐接口 | 说明 |
|---|---|---|
| 新架构：后端直连设备/ROS | `POST /api/train/session/<session_id>/finish/` | 会话态一致，便于状态机管理 |
| 旧架构：前端上传完整训练数据 | `POST /api/train/finish/` | 兼容历史调用，快速复用 |
| 灰度迁移期 | 两者并行（新走 A4，旧走 14） | 逐步观测并下线旧接口 |

**下线建议（可选）**

1. 在文档中将 `POST /api/train/finish/` 标记为 `Deprecated`。  
2. 后端对该接口增加响应头：`X-API-Deprecated: true`。  
3. 统计 2~4 周调用量，降到可控后再移除路由。

---

## 数据迁移说明（新增）

本次后端补齐涉及新迁移文件：`pages/migrations/0003_userprofile_phone_avatar_trainingsession.py`

请执行：

```bash
uv run manage.py migrate
```

---

## 前端统一状态处理建议（新增）

> 目标：前端不要只看 HTTP 状态码，还要优先根据业务字段 `code` / `status` 分支处理。

### 1) 人脸登录 / 录入（`code`）

| code | 前端建议动作 | 是否自动重试 |
|---|---|---|
| `AUTH_SUCCESS` | 进入主页，保存 JWT，停止摄像头轮询 | 否 |
| `ENROLL_SUCCESS` | 提示“录入成功”，返回设置页 | 否 |
| `PARAMS_INVALID` | 提示参数错误，检查采集与上传逻辑 | 否 |
| `IMG_ERROR` | 提示“画面异常”，重启摄像头流 | 是（短延迟） |
| `NO_FACE` | 提示“请进入画面” | 是 |
| `TOO_FAR` | OSD 提示“请靠近摄像头” | 是 |
| `NOT_FACING` | OSD 提示“请正对屏幕” | 是 |
| `USER_NOT_FOUND` | 引导用户账号登录后先做人脸绑定 | 否 |
| `USER_DISABLED` | 弹窗提示账号禁用，终止流程 | 否 |

### 2) 训练分析轮询（`status`）

| status | 前端建议动作 |
|---|---|
| `PROCESSING` | 保持 loading，继续轮询（建议 2~3 秒/次） |
| `COMPLETED` | 停止轮询，拉取详情并展示报告卡片 |

### 3) 训练会话轮询（`status` + `phase`）

| 字段 | 值 | 前端建议动作 |
|---|---|---|
| `status` | `RUNNING` | 继续轮询并更新实时指标 |
| `status` | `FINISHED` | 停止轮询，跳转结算页 |
| `phase` | `WORK` | 显示动作计数与节奏提示 |
| `phase` | `REST` | 显示倒计时/恢复提示 |
| `phase` | `END` | 显示“训练结束，恢复中” |

### 4) 轮询与提示统一规范（建议）

- **轮询间隔**：人脸与会话状态建议 `500ms~1000ms`；AI 分析建议 `2s~3s`。
- **终止条件**：出现 `AUTH_SUCCESS` / `ENROLL_SUCCESS` / `COMPLETED` / `FINISHED` 时立即停止轮询。
- **提示分级**：
  - 轻提示（toast）：`NO_FACE`、`TOO_FAR`、`NOT_FACING`
  - 强提示（弹窗）：`USER_DISABLED`、`PARAMS_INVALID`
  - 导航引导：`USER_NOT_FOUND`（跳转账号登录/绑定）
