from django.shortcuts import render
# Create your views here.
from django.db.models import Sum, Avg, Min
from django.db import connection, transaction
import logging
import re
import json
import base64
try:
    from json_repair import repair_json
except Exception:
    repair_json = None
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from django.conf import settings
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils import timezone
import threading
from datetime import timedelta
from uuid import uuid4
from statistics import median
from pages.models import Activity, ActivityTimeSeries, AIFeedback, TrainingPlan, UserProfile, UserFaceEmbedding, TrainingSession
from services.tts_service import play_tts_sync, stop_tts_playback
from services.llm_service import (
    generate_micro_coaching,
    generate_post_workout_feedback,
    generate_post_workout_eval,
    generate_post_workout_new_plan,
    load_yaml,
    call_local_llm,
    LocalLLMError,
)
from services.face_service import process_face_pipeline, verify_face_1_to_N
from .realtime_store import upsert_session_realtime, get_session_realtime, pop_session_realtime

logger = logging.getLogger(__name__)

PLAN_JSON_SYSTEM_PROMPT = (
    "你是严格的JSON生成器。"
    "你只能输出一个合法JSON数组，不允许输出任何解释、注释、Markdown代码块。"
    "数组长度必须为7。"
    "每个元素必须且只能包含 day 与 exercises 两个键。"
    "day 为1-7整数，exercises为数组。"
)


def _get_default_tts_voice_from_rules() -> str:
    """从 llm_rules.yaml 读取默认 TTS 音色。"""
    try:
        config = load_yaml() or {}
        voice = str((config.get('models', {}) or {}).get('tts_voice', '')).strip()
        if voice:
            return voice
    except Exception:
        pass
    return 'zh-CN-YunjianNeural'


class ROSRuntimeConfigView(APIView):
    """ROS 运行时配置查询（用于前端按模式读取连接参数）"""
    permission_classes = [AllowAny]

    def get(self, request):
        ros_cfg = settings.ROS_RUNTIME_CONFIG
        return Response({
            "runtime_mode": ros_cfg.get("runtime_mode", "windows_debug"),
            "debug_mode": bool(ros_cfg.get("debug_mode", False)),
            "active_profile": ros_cfg.get("active_profile", {}),
            "session_realtime": ros_cfg.get("session_realtime", {}),
            "topics": ros_cfg.get("topics", {}),
            "action_detectors": ros_cfg.get("enabled_action_detectors", []),
            "exercise_dictionary": ros_cfg.get("exercise_dictionary", []),
        }, status=status.HTTP_200_OK)

# 登录相关逻辑 ###########################################

def get_tokens_for_user(user):
    """手动为指定用户生成 JWT 访问令牌和刷新令牌"""
    refresh = RefreshToken.for_user(user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }


def _is_valid_plan_content(plan_content):
    """校验训练计划结构是否为 [{day, exercises:[{type,...}]}]。"""
    if not isinstance(plan_content, list):
        return False

    for day_item in plan_content:
        if not isinstance(day_item, dict):
            return False
        if 'day' not in day_item or 'exercises' not in day_item:
            return False
        if not isinstance(day_item.get('exercises'), list):
            return False

        for ex in day_item.get('exercises', []):
            if not isinstance(ex, dict):
                return False
            if not ex.get('type'):
                return False

    return True


def _parse_plan_json_from_llm_reply(llm_reply: str):
    """尽量从 LLM 返回中提取可用 JSON，兼容 [JSON] / ```json 包裹。"""
    text = (llm_reply or "").strip()
    text = text.replace("```json", "").replace("```", "").strip()
    text = re.sub(r"^\s*\[[A-Z_]+\]\s*", "", text, flags=re.IGNORECASE)

    # 优先尝试整段解析
    try:
        return json.loads(text)
    except Exception:
        pass

    # 回退：提取候选 JSON 片段并逐个尝试
    candidates = re.findall(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue

    # 使用 json-repair 做通用修复（支持缺逗号/缺引号/括号不匹配等）
    if repair_json:
        try:
            repaired = repair_json(text)
            if repaired:
                return json.loads(repaired)
        except Exception:
            pass

    if repair_json:
        for candidate in candidates:
            try:
                repaired = repair_json(candidate)
                if repaired:
                    return json.loads(repaired)
            except Exception:
                continue

    # 强化兼容：修复类似
    # ["day":[],"exercises":[]],"day2":{"exercises":["rest"]},...
    # 这类“接近 JSON 但缺少最外层对象/数组结构”的输出
    def _build_exercises(exercises_raw: str):
        tokens = re.findall(r'"([^"\\]+)"', exercises_raw or '')
        exercises_local = []
        for token in tokens:
            code = _normalize_activity_code(token)
            if code in {'type', 'exercises', 'day'}:
                continue
            if code == 'rest':
                return []
            if code:
                exercises_local.append({
                    'type': code,
                    'sets': 3,
                    'reps_per_set': 12,
                    'rest_sec': 60,
                })
        return exercises_local

    day_map = {}

    # 形态 A: ["day": "1", "exercises": ["squat"]]
    malformed_day_ex_matches = re.findall(
        r'\[\s*"day"\s*:\s*([^,\]]+)\s*,\s*"exercises"\s*:\s*\[([^\]]*)\]\s*\]',
        text,
        flags=re.IGNORECASE,
    )
    for idx, (day_raw, exercises_raw) in enumerate(malformed_day_ex_matches, start=1):
        day_num = _safe_int(day_raw.strip().strip('"\''), idx)
        day_map[day_num] = {
            'day': day_num,
            'exercises': _build_exercises(exercises_raw),
        }

    # 形态 B: "day2": {"exercises": ["rest"]}
    day_key_matches = re.findall(
        r'"day\s*(\d+)"\s*:\s*\{\s*"exercises"\s*:\s*\[([^\]]*)\]'
        r'(?:\s*,\s*"day"\s*:\s*"?(\d+)"?)?\s*\}',
        text,
        flags=re.IGNORECASE,
    )
    for day_key, exercises_raw, day_override in day_key_matches:
        day_num = _safe_int(day_override or day_key, _safe_int(day_key, len(day_map) + 1))
        day_map[day_num] = {
            'day': day_num,
            'exercises': _build_exercises(exercises_raw),
        }

    if day_map:
        return [day_map[k] for k in sorted(day_map.keys())]

    # 兼容逐行伪 JSON 格式：
    # ["day": "1", "exercises": ["squat"]]
    line_pattern = re.compile(
        r'\[\s*"day"\s*:\s*"?(\d+)"?\s*,\s*"exercises"\s*:\s*\[([^\]]*)\]\s*\]',
        flags=re.IGNORECASE,
    )
    line_matches = line_pattern.findall(text)
    if line_matches:
        parsed_days = []
        for day_raw, exercises_raw in line_matches:
            day_num = _safe_int(day_raw, len(parsed_days) + 1)
            items = re.findall(r'"([^"]+)"', exercises_raw)
            exercises = []
            for item in items:
                code = _normalize_activity_code(item)
                if code == 'rest':
                    exercises = []
                    break
                if code:
                    exercises.append({
                        'type': code,
                        'sets': 3,
                        'reps_per_set': 12,
                        'rest_sec': 60,
                    })
            parsed_days.append({
                'day': day_num,
                'exercises': exercises,
            })
        if parsed_days:
            return parsed_days

    # 最后兜底：从非标准/截断文本里提取 type 列表
    type_candidates = re.findall(r'"type"\s*:\s*"([^"]+)"', text)
    if type_candidates:
        actions = []
        for raw in type_candidates:
            code = _normalize_activity_code(raw)
            if code:
                actions.append(code)
        if actions:
            return actions

    raise ValueError("大模型返回的内容中未找到可解析的 JSON")


def _coerce_plan_content(plan_json):
    """兼容模型返回 ['push_up','rest',...] 这类简化计划，转换为标准结构。"""
    if isinstance(plan_json, list) and all(isinstance(x, str) for x in plan_json):
        coerced = []
        for idx, item in enumerate(plan_json[:7], start=1):
            action = (item or "").strip().lower()
            if action == "rest":
                exercises = []
            else:
                exercises = [{
                    "type": action,
                    "sets": 3,
                    "reps_per_set": 12,
                    "rest_sec": 60,
                }]
            coerced.append({"day": idx, "exercises": exercises})
        return coerced

    # 兼容模型返回 [{"day":1,"exercises":["squat"]}, ...] 的简化结构
    # 将 exercises 内的字符串动作转换为标准对象结构
    if isinstance(plan_json, list) and all(isinstance(x, dict) for x in plan_json):
        changed = False
        coerced_days = []
        for idx, day_item in enumerate(plan_json, start=1):
            day = _safe_int(day_item.get('day', idx), idx)
            exercises_raw = day_item.get('exercises', [])
            if isinstance(exercises_raw, str):
                exercises_raw = [exercises_raw]
                changed = True
            if not isinstance(exercises_raw, list):
                exercises_raw = []
                changed = True

            exercises = []
            for ex in exercises_raw:
                if isinstance(ex, dict):
                    exercises.append(ex)
                    continue

                if isinstance(ex, str):
                    code = _normalize_activity_code(ex)
                    changed = True
                    if code == 'rest':
                        exercises = []
                        break
                    if code:
                        exercises.append({
                            "type": code,
                            "sets": 3,
                            "reps_per_set": 12,
                            "rest_sec": 60,
                        })

            coerced_days.append({"day": day, "exercises": exercises})

        if changed:
            return coerced_days

    return plan_json


def _get_allowed_plan_exercise_codes() -> list[str]:
    """获取计划生成允许的动作代码（仅来源于 ros_runtime.yaml，且不包含 rest）。"""
    ros_cfg = getattr(settings, 'ROS_RUNTIME_CONFIG', {}) or {}
    if not isinstance(ros_cfg, dict):
        return []

    allowed: list[str] = []

    # 首选：exercise_dictionary（由 ros_runtime.yaml 的 action_detectors 派生）
    exercise_dict = ros_cfg.get('exercise_dictionary', [])
    if isinstance(exercise_dict, list):
        for item in exercise_dict:
            if not isinstance(item, dict):
                continue
            code = _normalize_activity_code(item.get('code', ''))
            if code and code not in ('mixed_plan', 'rest'):
                allowed.append(code)

    # 兜底：直接读取 enabled_action_detectors（仍然来自 ros_runtime.yaml）
    if not allowed:
        detectors = ros_cfg.get('enabled_action_detectors', [])
        if isinstance(detectors, list):
            for item in detectors:
                if not isinstance(item, dict):
                    continue
                code = _normalize_activity_code(item.get('code', ''))
                if code and code not in ('mixed_plan', 'rest'):
                    allowed.append(code)

    return list(dict.fromkeys(allowed))


def _sanitize_plan_content(plan_json, allowed_codes: list[str]):
    """对计划结果做白名单过滤与字段规范化，避免写入不存在动作。"""
    allowed_set = set(allowed_codes)
    if not isinstance(plan_json, list):
        return plan_json

    day_to_exercises = {}
    for idx, day_item in enumerate(plan_json, start=1):
        if not isinstance(day_item, dict):
            continue

        day = _safe_int(day_item.get('day', idx), idx)
        if day < 1 or day > 7:
            continue
        exercises_raw = day_item.get('exercises', [])
        if not isinstance(exercises_raw, list):
            exercises_raw = []

        exercises = []
        for ex in exercises_raw:
            if not isinstance(ex, dict):
                continue
            ex_type = _normalize_activity_code(ex.get('type', ''))
            if not ex_type or ex_type == 'rest':
                continue
            if ex_type not in allowed_set:
                continue

            sets = max(1, min(_safe_int(ex.get('sets', 3), 3), 5))
            reps = max(1, min(_safe_int(ex.get('reps_per_set', ex.get('reps', 12)), 12), 20))
            rest_sec = max(10, min(_safe_int(ex.get('rest_sec', 60), 60), 300))

            exercises.append({
                'type': ex_type,
                'sets': sets,
                'reps_per_set': reps,
                'rest_sec': rest_sec,
            })

        day_to_exercises[day] = exercises

    # 强制补齐为 7 天：缺失天数自动补为休息日（exercises=[]）
    sanitized = [
        {'day': day, 'exercises': day_to_exercises.get(day, [])}
        for day in range(1, 8)
    ]

    return sanitized


def _plan_has_effective_exercises(plan_content) -> bool:
    """判断计划中是否至少包含一个有效训练动作（非 rest）。"""
    if not isinstance(plan_content, list):
        return False

    for day_item in plan_content:
        if not isinstance(day_item, dict):
            continue
        exercises = day_item.get('exercises', [])
        if not isinstance(exercises, list):
            continue
        for ex in exercises:
            if not isinstance(ex, dict):
                continue
            ex_type = _normalize_activity_code(ex.get('type', ''))
            if ex_type and ex_type != 'rest':
                return True
    return False


def _build_fallback_week_plan(allowed_codes: list[str]) -> list[dict]:
    """生成稳定可用的 7 天兜底计划，避免全周休息。"""
    normalized_codes = [
        _normalize_activity_code(code)
        for code in (allowed_codes or [])
        if _normalize_activity_code(code) and _normalize_activity_code(code) != 'rest'
    ]
    normalized_codes = list(dict.fromkeys(normalized_codes))

    if not normalized_codes:
        normalized_codes = ['squat', 'jumping_jack', 'push_up']

    training_days = {1, 2, 4, 5, 6}  # 3、7 为恢复日
    plan = []
    cursor = 0
    for day in range(1, 8):
        if day not in training_days:
            plan.append({'day': day, 'exercises': []})
            continue

        ex_type = normalized_codes[cursor % len(normalized_codes)]
        cursor += 1
        plan.append({
            'day': day,
            'exercises': [{
                'type': ex_type,
                'sets': 3,
                'reps_per_set': 12,
                'rest_sec': 60,
            }],
        })

    return plan


def _sanitize_post_workout_result(result_json, allowed_codes: list[str]) -> dict:
    """规范化训练后分析结果，避免异常字段污染存储。"""
    if not isinstance(result_json, dict):
        return {}

    quality_score = max(1, min(_safe_int(result_json.get('quality_score', 5), 5), 10))
    feedback_text = str(result_json.get('feedback_text', '干得很棒！')).strip() or '干得很棒！'

    sanitized = {
        'quality_score': quality_score,
        'feedback_text': feedback_text,
    }

    new_plan = result_json.get('new_plan')
    if new_plan is not None:
        plan = _coerce_plan_content(new_plan)
        plan = _sanitize_plan_content(plan, allowed_codes)
        if _is_valid_plan_content(plan):
            sanitized['new_plan'] = plan

    return sanitized


def _replace_active_plan_for_user(*, user=None, user_id=None, plan_content=None, plan_type='LLM_GENERATED'):
    """原子化替换用户当前激活计划，避免“先失活后创建失败”导致无计划。"""
    if user is None and user_id is None:
        raise ValueError('user 或 user_id 至少提供一个')

    if user is not None:
        target_qs = TrainingPlan.objects.filter(user=user)
        create_kwargs = {'user': user}
    else:
        target_qs = TrainingPlan.objects.filter(user_id=user_id)
        create_kwargs = {'user_id': user_id}

    with transaction.atomic():
        target_qs.filter(is_active=True).update(is_active=False)
        return TrainingPlan.objects.create(
            **create_kwargs,
            plan_content=plan_content,
            is_active=True,
            plan_type=plan_type,
        )


def _get_or_recover_active_plan(user):
    """获取激活计划；若历史数据异常导致无激活计划，自动回补最近一条。"""
    active_plans = TrainingPlan.objects.filter(user=user, is_active=True).order_by('-created_at')
    active_plan = active_plans.first()

    # 自愈：若出现多个 active，保留最新一条
    if active_plan and active_plans.count() > 1:
        TrainingPlan.objects.filter(user=user, is_active=True).exclude(id=active_plan.id).update(is_active=False)
        return active_plan

    if active_plan:
        return active_plan

    latest_plan = TrainingPlan.objects.filter(user=user).order_by('-created_at').first()
    if latest_plan:
        latest_plan.is_active = True
        latest_plan.save(update_fields=['is_active'])
        logger.warning(
            '检测到用户无激活计划，已自动恢复最近计划: user_id=%s, plan_id=%s',
            user.id,
            latest_plan.id,
        )
        return latest_plan

    return None

class RegisterView(APIView):
    """1. 基础注册接口 (POST) - 仅负责账号与身体档案初始化"""
    permission_classes = [AllowAny] # 注册接口无需鉴权

    def post(self, request):
        data = request.data
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            return Response({"error": "用户名和密码不能为空"}, status=status.HTTP_400_BAD_REQUEST)
            
        if User.objects.filter(username=username).exists():
            return Response({"error": "用户名已存在"}, status=status.HTTP_400_BAD_REQUEST)
            
        # 1. 创建 Django 核心用户
        user = User.objects.create_user(username=username, password=password)
        
        # 2. 初始化身体档案表 (UserProfile)
        # 目标(goal)如果 UserProfile 表里有字段，也可以存在这里，方便以后复用
        gender = data.get('gender', 'O')
        height = data.get('height', 170)
        weight = data.get('weight', 65)
        birthdate_raw = data.get('birthdate')
        birthdate_val = None
        if birthdate_raw:
            try:
                # 支持 YYYY-MM-DD 格式
                from datetime import datetime
                birthdate_val = datetime.strptime(birthdate_raw, '%Y-%m-%d').date()
            except Exception:
                birthdate_val = None
        
        UserProfile.objects.create(
            user=user,
            gender=gender,
            height=height,
            weight=weight,
            birthdate=birthdate_val
        )
        
        # 3. 签发 Token 并快速返回
        tokens = get_tokens_for_user(user) # 假设你 utils 里有这个函数
        return Response({
            "msg": "注册成功，账号档案已建立",
            "username": user.username,
            "profile_initialized": True,
            **tokens
        }, status=status.HTTP_201_CREATED)
    


class LoginView(APIView):
    """1. 账号密码登录接口 (POST)"""
    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')
        
        user = authenticate(username=username, password=password)
        
        if user is not None:
            if user.is_active:
                tokens = get_tokens_for_user(user)
                return Response({
                    "msg": "登录成功",
                    "username": user.username,
                    **tokens
                }, status=status.HTTP_200_OK)
            return Response({"error": "该账号已被禁用"}, status=status.HTTP_403_FORBIDDEN)
        return Response({"error": "用户名或密码错误"}, status=status.HTTP_401_UNAUTHORIZED)


class FaceLoginView(APIView):
    """
    优化后的一体机人脸防误判登录接口 (POST)
    """
    def post(self, request):
        face_data_base64 = request.data.get('face_data')
        if not face_data_base64:
            return Response({
                "code": "PARAMS_INVALID",
                "msg": "未接收到有效的摄像头人脸流"
            }, status=status.HTTP_400_BAD_REQUEST)

        # 1. 进入过滤管道校验（防误判、多人筛选、距离、姿态）
        pipe_code, pipe_msg, face_embedding = process_face_pipeline(face_data_base64)
        
        # 若没有通过硬件前置校验，立即返回提示，让前端在大屏展示对应警告
        if pipe_code != "SUCCESS":
            return Response({
                "code": pipe_code, 
                "msg": pipe_msg
            }, status=status.HTTP_200_OK) # 使用 200 返回业务级错误状态，便于前端轮询捕获

        # 2. 核心 1:N 人脸特征检索比对
        user, similarity = verify_face_1_to_N(face_embedding)
        
        if user is not None:
            if not user.is_active:
                return Response({"code": "USER_DISABLED", "msg": "该账号已被禁用"}, status=status.HTTP_403_FORBIDDEN)
            
            # 识别成功，签发 JWT
            tokens = get_tokens_for_user(user)
            
            
            return Response({
                "code": "AUTH_SUCCESS",
                "msg": "登录成功",
                "username": user.username,
                "similarity": round(similarity, 2),
                **tokens
            }, status=status.HTTP_200_OK)
        
        return Response({
            "code": "USER_NOT_FOUND",
            "msg": "未在系统中找到匹配的人脸，请先使用账号登录并绑定人脸"
        }, status=status.HTTP_404_NOT_FOUND)
    
class FaceEnrollView(APIView):
    """
    一体机人脸特征采集/录入接口 (POST)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
            
        face_data_base64 = request.data.get('face_data')
        if not face_data_base64:
            return Response({"code": "PARAMS_INVALID", "msg": "人脸数据流不能为空"}, status=status.HTTP_400_BAD_REQUEST)

        # 1. 录入时同样执行极度严格的防误判管道校验，确保录入的基础底片绝对标准
        pipe_code, pipe_msg, face_embedding = process_face_pipeline(face_data_base64)
        if pipe_code != "SUCCESS":
            return Response({"code": pipe_code, "msg": f"录入失败: {pipe_msg}"}, status=status.HTTP_200_OK)

        # 2. 特征写入或覆写库
        face_record, created = UserFaceEmbedding.objects.get_or_create(user=user, defaults={"embedding": face_embedding})
        if not created:
            # 如果之前录入过，则进行更新覆写
            face_record.embedding = face_embedding
            face_record.save()

        return Response({
            "code": "ENROLL_SUCCESS",
            "msg": "人脸特征绑定成功，已与当前身体档案互联",
            "tts_text": "人脸特征录入成功"
        }, status=status.HTTP_201_CREATED)
        

# 登录相关逻辑end ###########################################

# 用户训练相关逻辑 #############################################

class GenerateInitialPlanView(APIView):
    """2. 初始化计划生成接口 (POST) - 供前端在注册后携带 Token 自动调用"""
    permission_classes = [IsAuthenticated] # 必须带上注册时下发的 Token 才能调用

    def post(self, request):
        user = request.user
        data = request.data
        # 前端将用户的目标诉求传过来
        user_goal = data.get('goal', '减脂塑形') 

        # 从刚刚建好的 UserProfile 中提取生理数据
        try:
            profile = user.profile # 依赖 models.py 中 OneToOneField 的 related_name，默认是小写
            gender = profile.gender
            height = profile.height
            weight = profile.weight
        except Exception:
            gender, height, weight = 'O', 170, 65 # 异常兜底

        try:
            # 1. 调度本地 LLM 运算
            config = load_yaml()
            prompt_template = config['prompts']['onboarding']
            allowed_codes = _get_allowed_plan_exercise_codes()
            allowed_text = ', '.join(allowed_codes + ['rest'])
            prompt = prompt_template.format(
                gender=gender, 
                height=height, 
                weight=weight, 
                user_goal_text=user_goal
            )
            prompt += (
                "\n\n仅可使用以下 type 作为动作："
                f"{allowed_text}。"
                "若为休息日，type 必须为 rest，且 exercises 置空数组。"
                "严禁输出任何不在上述列表中的动作。"
                "输出必须是7天数组，每天仅包含 day 和 exercises 两个键，禁止额外外层键，禁止重复键。"
            )
            plan_json = None
            last_parse_err = None
            for attempt in range(1, 4):
                try:
                    llm_reply = call_local_llm(
                        prompt,
                        max_tokens=900,
                        temperature=0.0,
                        system_prompt=PLAN_JSON_SYSTEM_PROMPT,
                        raise_on_error=True,
                    )
                except LocalLLMError as llm_err:
                    last_parse_err = f"LLM请求失败: {llm_err}"
                    logger.warning(
                        "初始化训练计划请求失败，准备重试: user_id=%s, attempt=%s/3, err=%s",
                        user.id,
                        attempt,
                        str(llm_err),
                    )
                    continue
                try:
                    parsed = _parse_plan_json_from_llm_reply(llm_reply)
                    parsed = _coerce_plan_content(parsed)
                    parsed = _sanitize_plan_content(parsed, allowed_codes)
                    if not _is_valid_plan_content(parsed):
                        raise ValueError("训练计划结构非法")
                    if not _plan_has_effective_exercises(parsed):
                        raise ValueError("训练计划未包含有效训练动作，疑似大模型输出异常")
                    plan_json = parsed
                    break
                except Exception as parse_err:
                    last_parse_err = parse_err
                    logger.warning(
                        "初始化训练计划解析失败，准备重试: user_id=%s, attempt=%s/3, err=%s",
                        user.id,
                        attempt,
                        str(parse_err),
                    )

            if plan_json is None:
                raise ValueError(f"训练计划解析连续3次失败: {last_parse_err}")

            # 3. 计划落盘 (保持不变)
            plan = _replace_active_plan_for_user(
                user=user,
                plan_content=plan_json,
                plan_type='LLM_GENERATED'
            )
            
            return Response({
                "msg": "AI 专属训练计划生成成功",
                "plan_id": plan.id,
                "plan_content": plan_json,
                "is_ready": True
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            # 容灾降级机制：如果 LLM 超时或解析失败，给一个默认计划，防止前端无数据可用
            logger.warning(f"初始化训练计划生成失败，触发兜底计划: user_id={user.id}, err={str(e)}")
            allowed_codes = _get_allowed_plan_exercise_codes()
            default_plan = _build_fallback_week_plan(allowed_codes)
            plan = _replace_active_plan_for_user(
                user=user,
                plan_content=default_plan,
                plan_type='LLM_GENERATED'
            )
            return Response({
                "msg": "当前 AI 算力拥挤，已为您匹配基础兜底计划",
                "plan_id": plan.id,
                "plan_content": default_plan,
                "is_ready": True
            }, status=status.HTTP_201_CREATED)

class ActivityListView(APIView):
    """
    对应规划：查询训练记录 (GET)
    支持触屏端和App拉取历史记录列表
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        activities = Activity.objects.filter(user=user)
            
        # 构造精简的 JSON 列表结构返回给前端
        data = []
        for act in activities:
            normalized_type = _normalize_activity_code(act.activity_type)
            data.append({
                "id": act.id,
                "activity_type": normalized_type,
                "activity_name": _activity_display_name_zh(normalized_type),
                "start_time": timezone.localtime(act.start_time).strftime('%Y-%m-%d %H:%M:%S'),
                "duration": act.duration,
                "total_reps": act.total_reps,
                "intensity": act.get_intensity_display(), # 返回中文“低/中/高强度”
                "quality_score": act.quality_score
            })
        return Response({"records": data}, status=status.HTTP_200_OK)
    
class ActivityDetailView(APIView):
    """
    查询单次训练的详细记录 (包含 AI 报告与高频心率血氧数据)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        user = request.user
        
        try:
            activity = Activity.objects.get(pk=pk, user=user)
        except Activity.DoesNotExist:
            return Response({"error": "未找到该条训练记录，或无权访问"}, status=status.HTTP_404_NOT_FOUND)
            
        # 1. 提取基础宏观数据
        normalized_type = _normalize_activity_code(activity.activity_type)
        data = {
            "id": activity.id,
            "activity_type": normalized_type,
            "activity_name": _activity_display_name_zh(normalized_type),
            "training_mode": activity.get_training_mode_display(),
            "start_time": timezone.localtime(activity.start_time).strftime('%Y-%m-%d %H:%M:%S'),
            "duration_seconds": activity.duration,
            "total_reps": activity.total_reps,
            "intensity": activity.get_intensity_display(),
            "perceived_exertion": activity.perceived_exertion,
            "quality_score": activity.quality_score,
        }
        
        # 2. 提取跨表的 AI 深度复盘报告 (如果有)
        if hasattr(activity, 'ai_feedback'):
            data['ai_report'] = {
                "feedback": activity.ai_feedback.feedback_text,
                "suggestion": activity.ai_feedback.next_step_suggestion
            }
            
        # 3. 核心补充：提取关联的所有心率血氧时序数据
        # 使用 related_name 'time_series' 进行反向查询，并按时间偏移量升序排列
        time_series_qs = activity.time_series.all().order_by('timestamp_offset')
        data['sensor_data_series'] = [
            {
                "offset": ts.timestamp_offset,
                "phase": ts.get_phase_display(), # 转为中文如“组间休息”
                "heart_rate": ts.heart_rate,
                "spo2": ts.spo2,
                "current_rep": ts.current_rep_count
            } for ts in time_series_qs
        ]
        
        return Response(data, status=status.HTTP_200_OK)
    


class ActivityStatusView(APIView):
    """
    查询指定 Activity 的后台 AI 分析是否完成 (GET)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            activity = Activity.objects.get(pk=pk, user=request.user)
        except Activity.DoesNotExist:
            return Response({"error": "未找到该训练记录"}, status=status.HTTP_404_NOT_FOUND)

        # 核心判断逻辑：检查当前 activity 是否已经绑定了 ai_feedback
        # (因为大模型跑完后，会执行 AIFeedback.objects.create(activity_id=act_id...))
        if hasattr(activity, 'ai_feedback'):
            return Response({
                "status": "COMPLETED",
                "msg": "AI 分析已完成",
                "quality_score": activity.quality_score,
                # 可以选择把简短的提示传给前端
                "feedback_preview": activity.ai_feedback.feedback_text[:20] + "..." 
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "status": "PROCESSING",
                "msg": "AI 后台正在疯狂运算中..."
            }, status=status.HTTP_200_OK)


class TrainingPlanView(APIView):
    """
    对应规划：更新训练计划 (POST) / 获取当前计划 (GET)
    通过大模型或前端手动调整一周的计划指标
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """获取当前正在执行的激活计划"""
        user = request.user
        plan = _get_or_recover_active_plan(user)
        
        if not plan:
            return Response({"msg": "暂无活动中的训练计划，请先生成"}, status=status.HTTP_404_NOT_FOUND)
            
        return Response({
            "plan_id": plan.id,
            "created_at": plan.created_at.strftime('%Y-%m-%d'),
            "plan_content": plan.plan_content
        }, status=status.HTTP_200_OK)

    def post(self, request):
        """前端或大模型更新、覆盖训练计划"""
        user = request.user
        new_plan_content = request.data.get('plan_content')
        
        if not new_plan_content:
            return Response({"error": "计划内容不能为空"}, status=status.HTTP_400_BAD_REQUEST)
        if not _is_valid_plan_content(new_plan_content):
            return Response({"error": "计划结构非法，请传入 [{day, exercises:[{type,...}]}]"}, status=status.HTTP_400_BAD_REQUEST)
            
        # 将该用户之前的所有计划设为失效
        # 创建新计划
        plan = _replace_active_plan_for_user(
            user=user,
            plan_content=new_plan_content,
            plan_type='LLM_GENERATED'
        )
        return Response({"msg": "训练计划更新成功", "plan_id": plan.id}, status=status.HTTP_201_CREATED)


class TrainingLoadView(APIView):
    """
    对应规划：查询训练负荷 (GET)
    【核心逻辑】：后端自动查询最近 7 天的滚动数据，计算出累计运动负荷值
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # 计算 7 天前的时间节点
        seven_days_ago = timezone.now() - timedelta(days=7)
        
        # 查询最近 7 天该用户的所有运动记录
        recent_activities = Activity.objects.filter(
            user=user,
            start_time__gte=seven_days_ago
        )
            
        # 负荷计算权重算法：高强度权重为3，中强度为2，低强度为1
        total_load = 0
        intensity_weights = {'HIGH': 3, 'MED': 2, 'LOW': 1}
        
        for act in recent_activities:
            weight = intensity_weights.get(act.intensity, 2)
            # 滚动负荷 = 运动时长(分钟) × 强度权重
            total_load += (act.duration / 60.0) * weight
            
        # 根据计算出的总负荷值，给出一个宏观的健康状态评估
        status_msg = "自适应恢复期"
        if total_load > 150:
            status_msg = "高负荷运转，注意防范运动损伤"
        elif total_load > 60:
            status_msg = "高效训练量，心肺稳步提升"
            
        return Response({
            "rolling_7_days_load": round(total_load, 1),
            "workout_count": recent_activities.count(),
            "load_assessment": status_msg
        }, status=status.HTTP_200_OK)
    
##########################################################################

class DashboardView(APIView):
    """主页聚合接口 (GET) - 一次性拉取用户核心数据及今日任务进度"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # 1. 基础档案
        profile = getattr(user, 'profile', None)
        if profile is None:
            profile = UserProfile.objects.create(user=user)
        
        # 2. 获取近期负荷
        recent_acts = Activity.objects.filter(user=user, start_time__gte=timezone.now()-timedelta(days=7))
        weekly_duration = sum(act.duration for act in recent_acts)
        
        # ==========================================
        # 3. [新增] 核心逻辑：今日计划完成度计算
        # ==========================================
        active_plan = _get_or_recover_active_plan(user)
        today_completed = False
        progress_percent = 0
        today_exercises = []

        if active_plan:
            # 3.1 计算今天是计划的第几天 (假设 7 天一个循环)
            now_date = timezone.now().date()
            plan_start_date = active_plan.created_at.date()
            days_diff = (now_date - plan_start_date).days
            current_day_num = (days_diff % 7) + 1 # 结果为 1-7
            
            # 3.2 从 JSON 中找出今天的计划
            plan_json = active_plan.plan_content
            if not isinstance(plan_json, list):
                logger.warning(f"用户激活计划结构异常(非list): user_id={user.id}, plan_id={active_plan.id}")
                plan_json = []

            today_plan = next(
                (
                    day for day in plan_json
                    if isinstance(day, dict) and _safe_int(day.get('day'), 0) == current_day_num
                ),
                None
            )
            
            if today_plan and today_plan.get('exercises'):
                target_exercises = today_plan['exercises']
                total_target_reps = 0
                total_actual_reps = 0
                is_all_done = True
                
                # 获取今天所有的真实运动记录
                today_activities = Activity.objects.filter(
                    user=user,
                    start_time__date=now_date
                )
                
                # 3.3 遍历今日目标，与数据库记录进行碰撞比对
                for ex in target_exercises:
                    if not isinstance(ex, dict):
                        continue
                    if ex.get('type') == 'rest':
                        today_completed = True
                        progress_percent = 100
                        continue
                        
                    # 计算目标次数: 组数 * 每组次数
                    ex_sets = ex.get('sets', 1)
                    ex_reps_per_set = ex.get('reps_per_set', ex.get('reps', 0))
                    ex_target = ex_sets * ex_reps_per_set
                    total_target_reps += ex_target
                    
                    # 聚合今天这个特定动作实际做了多少次
                    ex_type_raw = ex.get('type', '')
                    ex_type = _normalize_activity_code(ex_type_raw)
                    if not ex_type:
                        continue

                    ex_actual_agg = today_activities.filter(activity_type__in=_activity_code_aliases(ex_type)).aggregate(total=Sum('total_reps'))
                    ex_actual = ex_actual_agg['total'] or 0
                    
                    # 为了防止超额完成导致进度条爆表超过 100%，做个封顶限制
                    total_actual_reps += min(ex_actual, ex_target)
                    
                    if ex_actual < ex_target:
                        is_all_done = False
                        
                    # 组装给前端展示的详情列表
                    today_exercises.append({
                        "type": ex_type,
                        "name": _activity_display_name_zh(ex_type),
                        "target": ex_target,
                        "actual": ex_actual,
                        "is_done": ex_actual >= ex_target,
                        "sets": ex_sets,
                        "reps_per_set": ex_reps_per_set
                    })
                
                # 3.4 最终结算百分比
                today_completed = is_all_done
                if total_target_reps > 0:
                    progress_percent = round((total_actual_reps / total_target_reps) * 100)

        # 4. 入场语音欢迎（硬件直接发声）(改到前端调用)
        # if today_completed:
        #     play_tts_sync(f"欢迎回来 {user.username}，您今天已经完成了今天所有训练任务！")
        # else:
        #     play_tts_sync(f"欢迎回来 {user.username}，今天还有进度未完成，继续努力。")

        return Response({
            "user_info": {
                "username": user.username,
                "gender": profile.get_gender_display(),
                "height": profile.height,
                "weight": profile.weight
            },
            "weekly_duration_mins": round(weekly_duration / 60, 1),
            "plan_status": {
                "active_plan_id": active_plan.id if active_plan else None,
                "is_generating": active_plan is None,
                "is_completed": today_completed,
                "progress_percent": progress_percent,
                "today_exercises": today_exercises # 供前端渲染列表：深蹲 (45/60次) 
            }
        })
class MicroCoachView(APIView):
    """组间话疗微指导 (POST)"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        activity_type = request.data.get('activity_type', '运动')
        error_text = request.data.get('error_text', '姿势标准')
        
        # 1. 请求本地 LLM 生成短评
        coach_words = generate_micro_coaching(activity_type, error_text)
        
        return Response({"spoken_text": coach_words, "tts_text": coach_words}, status=status.HTTP_200_OK)

class TrainFinishView(APIView):
    """训练核心中枢结算接口 (POST)"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        data = request.data
        training_mode = _normalize_training_mode(data.get('training_mode', 'FREE'))
        
        # 1. 立即落盘宏观主表 Activity (原有逻辑保持不变)
        activity = Activity.objects.create(
            user=user,
            training_mode=training_mode,
            activity_type=data.get('activity_type', 'mixed_plan'),
            duration=data.get('duration', 0),
            total_reps=data.get('total_reps', 0),
            intensity=data.get('intensity', 'MED'),
            perceived_exertion=data.get('perceived_exertion', 3)
        )
        
        # 2. 批量落盘高频静息时序数据 (原有逻辑保持不变)
        time_series_data = data.get('time_series', [])
        ts_objects = [
            ActivityTimeSeries(
                activity=activity,
                timestamp_offset=ts.get('offset'),
                phase=ts.get('phase', 'REST'),
                heart_rate=ts.get('heart_rate'),
                spo2=ts.get('spo2'),
                current_rep_count=ts.get('current_rep')
            ) for ts in time_series_data
        ]
        if ts_objects:
            ActivityTimeSeries.objects.bulk_create(ts_objects)

        # 3. 核心补充：异步线程重构
        def background_llm_task(act_id, user_id, raw_data):
            try:
                # 3.1 动态从数据库计算真实的客观体征均值，替代 Hardcode
                ts_qs = ActivityTimeSeries.objects.filter(activity_id=act_id, phase='REST')
                agg_res = ts_qs.aggregate(
                    avg_hr=Avg('heart_rate'),
                    min_spo2=Min('spo2')
                )
                # 若缺失硬件流数据，则设置合理的降级默认值
                avg_rest_hr = round(agg_res['avg_hr'] or 90) 
                min_spo2 = round(agg_res['min_spo2'] or 98)

                # 3.2 聚合真实负载数据给大模型
                llm_payload = {
                    "target_reps": raw_data.get('target_reps', raw_data.get('total_reps', 0)), 
                    "actual_reps": raw_data.get('total_reps', 0),
                    "error_count": raw_data.get('error_count', 0), # 需前端提交动作变形次数
                    "avg_rest_hr": avg_rest_hr,
                    "min_spo2": min_spo2,     
                    "rpe_score": raw_data.get('perceived_exertion', 3)
                }
                allowed_codes = _get_allowed_plan_exercise_codes()
                llm_payload['allowed_exercise_codes'] = allowed_codes
                
                # 3.3 拆分请求：先评估，再请求新计划，降低一次性生成负担
                eval_json = generate_post_workout_eval(llm_payload) or {}
                plan_json = generate_post_workout_new_plan(llm_payload)
                merged_result = eval_json if isinstance(eval_json, dict) else {}
                if plan_json is not None:
                    merged_result = {**merged_result, 'new_plan': plan_json}

                if merged_result:
                    result_json = _sanitize_post_workout_result(merged_result, allowed_codes)
                    # 核心修复点：将大模型打出的评分更新回主表
                    new_score = result_json.get('quality_score', 5)
                    Activity.objects.filter(id=act_id).update(quality_score=new_score)
                    
                    # 落盘详细评语
                    AIFeedback.objects.create(
                        activity_id=act_id,
                        feedback_text=result_json.get('feedback_text', '干得很棒！'),
                        next_step_suggestion="系统基于最新运动表现自动调优",
                    )
                    
                    # 计划自动进化：覆写新的 JSON
                    new_plan = result_json.get('new_plan')
                    # 仅按计划训练（GUIDED）才自动更新训练计划；自由训练只做总结
                    if new_plan and training_mode == 'GUIDED' and _plan_has_effective_exercises(new_plan):
                        _replace_active_plan_for_user(
                            user_id=user_id,
                            plan_content=new_plan,
                            plan_type='LLM_GENERATED'
                        )

            except Exception as e:
                # 捕获异步线程中的异常，防止静默崩溃
                logger.error(f"后台大模型分析任务执行失败: {str(e)}")
                
            finally: # <--- [改进点] 添加 finally 块释放连接
                # 确保无论正常执行完毕还是中途报错，当前线程的数据库连接都会被强制关闭回收
                connection.close()

        thread = threading.Thread(target=background_llm_task, args=(activity.id, user.id, data))
        thread.start()

        return Response({
            "msg": "数据已保存，AI后台分析中",
            "activity_id": activity.id,
            "tts_text": "数据已经保存，AI正在为您生成分析报告，请稍候。"
        }, status=status.HTTP_202_ACCEPTED)

class ChatbotView(APIView):
    """
    个人运动伴侣问答接口 (POST)
    通过 RAG 机制自动聚合用户真实的近期运动时序数据与AI评语，拒绝大模型瞎编。
    """
    def post(self, request):
        # 1. 获取前端一体机触屏或App输入的聊天文本
        user_message = request.data.get('message', '')
        if not user_message:
            return Response({"error": "提问内容不能为空"}, status=status.HTTP_400_BAD_REQUEST)
            
        # 2. 鉴权校验：获取当前活跃登录的用户
        user = request.user
        if not user.is_authenticated:
            return Response({"error": "当前设备无活跃登录用户，请先扫脸或登录"}, status=status.HTTP_401_UNAUTHORIZED)

        # ==========================================
        # 核心 RAG 数据处理流水线（无省略）
        # ==========================================
        
        # 设定滚动查找的时间窗口：最近 7 天
        seven_days_ago = timezone.now() - timedelta(days=7)
        
        # 核心数据流提取A：过滤出该用户最近 7 天的所有真实运动历史
        recent_activities = Activity.objects.filter(
            user=user,
            start_time__gte=seven_days_ago
        )
        
        # 核心数据流提取B：计算 7 天内的总训练耗时（将数据库存储的“秒”转换为“分钟”）
        total_duration_seconds = recent_activities.aggregate(total=Sum('duration'))['total'] or 0
        weekly_duration = round(total_duration_seconds / 60, 1) # 保留一位小数
        
        # 核心数据流提取C：统计最近 7 天的综合训练强度分布，计算出占比最高的强度
        intensity_counts = {'HIGH': 0, 'MED': 0, 'LOW': 0}
        for act in recent_activities:
            if act.intensity in intensity_counts:
                intensity_counts[act.intensity] += 1
                
        # 找出频次最高的强度标签并转化为易读的中文
        if recent_activities.exists():
            dominant_intensity = max(intensity_counts, key=intensity_counts.get)
            intensity_map = {'HIGH': '高强度', 'MED': '中强度', 'LOW': '低强度'}
            weekly_intensity = intensity_map.get(dominant_intensity, '中强度')
        else:
            weekly_intensity = '暂无训练记录'

        # 核心数据流提取D：跨表检索该用户“上一次”训练结算时，大模型写入的真实专业评语
        last_feedback_record = AIFeedback.objects.filter(
            activity__user=user
        ).order_by('-activity__start_time').first()
        
        if last_feedback_record:
            last_feedback = last_feedback_record.feedback_text
        else:
            last_feedback = '该用户近期刚加入，暂无历史AI评估报告。'

        # ==========================================
        # Prompt 动态变量注入与大模型调度
        # ==========================================
        
        # 3. 读取本地静态 YAML 提示词骨架
        try:
            config = load_yaml()
            prompt_template = config['prompts']['chatbot']
        except Exception as e:
            return Response({"error": f"读取提示词配置文件失败: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        # 4. 将真实的本地私有数据，精准灌入 Prompt 模板中 (实现 RAG)
        final_prompt = prompt_template.format(
            weekly_duration=weekly_duration,
            weekly_intensity=weekly_intensity,
            last_feedback=last_feedback,
            user_chat_message=user_message
        )
        
        # 5. 调用本地轻量化大模型进行安全推理
        reply_text = call_local_llm(final_prompt, max_tokens=150, temperature=0.6)
        
        # 6. 返回文本结果，由前端统一调 TTS 接口进行播报，保证动画时长一致
        return Response({
            "reply": reply_text,
            "tts_text": reply_text,
            "rag_meta": {
                "injected_duration_mins": weekly_duration,
                "injected_intensity": weekly_intensity
            }
        }, status=status.HTTP_200_OK)
    
class ExerciseDictionaryView(APIView):
    """
    查询系统当前支持的所有具体运动动作库 (GET)
    专为前端“自由训练”模式提供动作选单
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 从 ROS 运行时配置读取动作检测包，后续新增动作仅需改配置文件
        ros_cfg = settings.ROS_RUNTIME_CONFIG
        exercise_dict = ros_cfg.get("exercise_dictionary", [])

        exercises = []
        for item in exercise_dict:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code", "")).strip()
            if not code:
                continue
            exercises.append({
                "code": code,
                "name": item.get("name") or code,
                "name_zh": item.get("name_zh") or item.get("name") or code,
                "name_en": item.get("name_en") or code,
                "ros_package": item.get("ros_package", "")
            })
                
        return Response({
            "total": len(exercises),
            "exercises": exercises
        }, status=status.HTTP_200_OK)
    
class TTSPlayView(APIView):
    """
    系统底层 TTS 语音播报接口 (POST)
    未来作为“哑终端”服务：前端/App发送任意文本，后端主板无脑发声
    """
    # 如果希望这个接口完全开放供局域网设备调用，可以设为 AllowAny
    # permission_classes = [AllowAny] 
    
    def post(self, request):
        text = request.data.get('text', '').strip()
        # 允许前端自定义音色，默认使用阳光男声
        voice = request.data.get('voice') or _get_default_tts_voice_from_rules()
        
        if not text:
            return Response({"error": "播放文本不能为空"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            duration = play_tts_sync(text, voice=voice)
            return Response({
                "msg": "系统扬声器已触发播报", 
                "played_text": text,
                "voice": voice,
                "duration": round(duration, 2) # 传给前端
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({"error": f"硬件扬声器调用失败: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TTSStopView(APIView):
    """
    停止当前系统 TTS 播放 (POST)
    用于前端在关闭对话框等场景中立即打断播报
    """

    def post(self, request):
        try:
            stopped = stop_tts_playback()
            return Response({
                "msg": "已请求停止播报",
                "stopped": bool(stopped),
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": f"停止播报失败: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ============================== 新增结构化 API ==============================

def _normalize_training_mode(mode_value: str) -> str:
    mode_normalized = str(mode_value or '').strip().lower()
    mode_map = {
        'free': 'FREE',
        'guided': 'GUIDED',
        'plan': 'GUIDED',
        'FREE': 'FREE',
        'GUIDED': 'GUIDED',
    }
    return mode_map.get(mode_normalized, 'FREE')


def _normalize_intensity(intensity_value: str) -> str:
    intensity_map = {
        'low': 'LOW',
        'medium': 'MED',
        'high': 'HIGH',
        'LOW': 'LOW',
        'MED': 'MED',
        'HIGH': 'HIGH',
    }
    return intensity_map.get(intensity_value, 'MED')


def _normalize_activity_code(activity_code: str) -> str:
    code = str(activity_code or '').strip().lower()
    alias_map = {
        'pushup': 'push_up',
        'pushups': 'push_up',
        'push_ups': 'push_up',
        'push-up': 'push_up',
        'push up': 'push_up',
        'jumpingjack': 'jumping_jack',
        'jumpingjacks': 'jumping_jack',
        'jumping-jack': 'jumping_jack',
        'lunges': 'lunge',
        'squats': 'squat',
        'planks': 'plank',
        'reverse nordic curl': 'reverse_nordic_curl',
        'reverse nordic curls': 'reverse_nordic_curl',
        'reverse-nordic-curl': 'reverse_nordic_curl',
        'reversenordiccurl': 'reverse_nordic_curl',
        'mix_plan': 'mixed_plan',
        'mixed': 'mixed_plan',
    }
    return alias_map.get(code, code)


def _activity_code_aliases(activity_code: str) -> list[str]:
    normalized = _normalize_activity_code(activity_code)
    alias_table = {
        'push_up': ['push_up', 'pushup'],
        'jumping_jack': ['jumping_jack', 'jumpingjack'],
        'mixed_plan': ['mixed_plan', 'mix_plan', 'mixed'],
    }
    aliases = alias_table.get(normalized, [normalized])
    # 去重并保持顺序
    return list(dict.fromkeys(aliases))


def _activity_display_name_zh(activity_code: str) -> str:
    code = _normalize_activity_code(activity_code)

    # 优先使用 ros_runtime.yaml 中的动作字典
    ros_cfg = getattr(settings, 'ROS_RUNTIME_CONFIG', {}) or {}
    exercise_dict = ros_cfg.get('exercise_dictionary', []) if isinstance(ros_cfg, dict) else []
    for item in exercise_dict:
        item_code = _normalize_activity_code(str(item.get('code', '')).strip())
        if item_code == code:
            return str(item.get('name_zh') or item.get('name') or code)

    # 最终兜底：使用内置中文文案，不依赖 Activity.ACTIVITY_TYPES
    fallback_name_map = {
        'squat': '深蹲',
        'jumping_jack': '开合跳',
        'lunge': '弓箭步',
        'push_up': '俯卧撑',
        'pushup': '俯卧撑',
        'reverse_nordic_curl': '反向北欧腿蹲',
        'plank': '平板支撑',
        'mixed_plan': '混合计划',
    }
    return fallback_name_map.get(code, code)


def _safe_int(value, default_value):
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default_value
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default_value


def _pick_first_non_empty(data: dict, keys: list[str], default_value=None):
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == '':
            continue
        return value
    return default_value


def _safe_float(value, default_value=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default_value


def _measurement_confidence(valid_sample_count, has_rest_data, has_end_data):
    if valid_sample_count >= 6 and has_rest_data and has_end_data:
        return "HIGH"
    if valid_sample_count >= 3 and (has_rest_data or has_end_data):
        return "MED"
    return "LOW"


class UserProfileView(APIView):
    """A1) 用户资料查询（新增）"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        profile = getattr(user, 'profile', None)

        gender_text = ''
        height = None
        weight = None
        if profile:
            # 如果用户未选择性别 (存储为 'O' 或其他占位值)，前端显示为空，避免页面显示奇怪的 O
            raw_gender = getattr(profile, 'gender', '')
            if str(raw_gender).upper() == 'O' or str(raw_gender).strip() == '':
                gender_text = ''
            else:
                gender_text = profile.get_gender_display()
            height = profile.height
            weight = profile.weight
        else:
            profile = UserProfile.objects.create(user=user)

        data = {
            "username": user.username,
            "phone": profile.phone or '',
            "role": "管理员" if user.is_staff else "普通用户",
            "avatar": profile.avatar or '',
            "gender": gender_text,
            "height": height,
            "weight": weight,
            "birthdate": profile.birthdate.isoformat() if getattr(profile, 'birthdate', None) else ''
        }
        return Response(data, status=status.HTTP_200_OK)

    def put(self, request):
        user = request.user
        profile = getattr(user, 'profile', None)
        if profile is None:
            profile = UserProfile.objects.create(user=user)

        payload = request.data or {}

        raw_phone = payload.get('phone', payload.get('mobile'))
        if raw_phone is not None:
            profile.phone = str(raw_phone).strip()

        raw_avatar = payload.get('avatar')
        if raw_avatar is not None:
            profile.avatar = str(raw_avatar).strip()

        raw_height = payload.get('height')
        if raw_height is not None:
            if str(raw_height).strip() == '':
                profile.height = None
            else:
                height_val = _safe_float(raw_height, None)
                if height_val is None or height_val < 80 or height_val > 260:
                    return Response({"error": "身高范围应在 80-260 cm"}, status=status.HTTP_400_BAD_REQUEST)
                profile.height = height_val

        raw_weight = payload.get('weight')
        if raw_weight is not None:
            if str(raw_weight).strip() == '':
                profile.weight = None
            else:
                weight_val = _safe_float(raw_weight, None)
                if weight_val is None or weight_val < 20 or weight_val > 300:
                    return Response({"error": "体重范围应在 20-300 kg"}, status=status.HTTP_400_BAD_REQUEST)
                profile.weight = weight_val

        raw_birthdate = payload.get('birthdate')
        if raw_birthdate is not None:
            birth_text = str(raw_birthdate).strip()
            if birth_text == '':
                profile.birthdate = None
            else:
                from datetime import datetime
                try:
                    parsed_date = datetime.strptime(birth_text, '%Y-%m-%d').date()
                    profile.birthdate = parsed_date
                except Exception:
                    return Response({"error": "出生日期格式应为 YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)

        raw_gender = payload.get('gender')
        if raw_gender is not None:
            normalized_gender = str(raw_gender).strip().upper()
            if normalized_gender in ['男', 'M']:
                profile.gender = 'M'
            elif normalized_gender in ['女', 'F']:
                profile.gender = 'F'
            elif normalized_gender in ['', 'O', '其他']:
                profile.gender = 'O'
            else:
                return Response({"error": "性别仅支持 男/女"}, status=status.HTTP_400_BAD_REQUEST)

        profile.save()

        if profile.gender == 'M':
            gender_text = '男'
        elif profile.gender == 'F':
            gender_text = '女'
        else:
            gender_text = ''

        data = {
            "username": user.username,
            "phone": profile.phone or '',
            "role": "管理员" if user.is_staff else "普通用户",
            "avatar": profile.avatar or '',
            "gender": gender_text,
            "height": profile.height,
            "weight": profile.weight,
            "birthdate": profile.birthdate.isoformat() if getattr(profile, 'birthdate', None) else ''
        }
        return Response({"msg": "资料更新成功", **data}, status=status.HTTP_200_OK)


class TrainingSessionStartView(APIView):
    """A2) 开始训练会话（新增）"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = request.data
        mode = str(data.get('mode', 'free')).lower()
        exercises_raw = data.get('exercises', [])
        exercises = [_normalize_activity_code(item) for item in exercises_raw if str(item or '').strip()]
        sets_raw = _pick_first_non_empty(data, ['sets', 'total_sets', 'set_count', 'setCount'], 1)
        reps_raw = _pick_first_non_empty(data, ['reps', 'reps_per_set', 'rep_count', 'repCount'], 1)
        rest_raw = _pick_first_non_empty(data, ['restSec', 'rest_sec', 'rest_seconds', 'restSeconds'], 45)

        sets = max(1, _safe_int(sets_raw, 1))
        reps = max(1, _safe_int(reps_raw, 1))
        rest_sec = max(1, _safe_int(rest_raw, 45))
        intensity = str(data.get('intensity', 'medium')).lower()

        if len(exercises) == 0:
            return Response({"error": "exercises 必须为非空数组"}, status=status.HTTP_400_BAD_REQUEST)

        session_id = f"sess_{timezone.now().strftime('%Y%m%d')}_{uuid4().hex[:8]}"

        session = TrainingSession.objects.create(
            user=request.user,
            session_id=session_id,
            mode=mode,
            exercises=exercises,
            sets=sets,
            reps=reps,
            rest_sec=rest_sec,
            intensity=intensity,
            status='RUNNING',
            phase='WORK'
        )

        logger.info(
            "[TrainingSessionStart] session=%s user=%s mode=%s sets=%s reps=%s rest_sec=%s",
            session_id,
            request.user.id,
            mode,
            session.sets,
            session.reps,
            session.rest_sec,
        )

        return Response({
            "session_id": session_id,
            "msg": "训练会话创建成功",
            "exercises": session.exercises,
            "sets": session.sets,
            "reps": session.reps,
            "rest_sec": session.rest_sec,
            "target_reps": session.sets * session.reps,
        }, status=status.HTTP_200_OK)


class TrainingSessionStateView(APIView):
    """A3) 训练会话状态轮询（新增）"""
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = TrainingSession.objects.filter(session_id=session_id, user=request.user).first()
        if not session:
            return Response({"error": "训练会话不存在"}, status=status.HTTP_404_NOT_FOUND)

        ros_cfg = settings.ROS_RUNTIME_CONFIG
        session_rt_cfg = ros_cfg.get('session_realtime', {}) if isinstance(ros_cfg, dict) else {}
        source_mode = str(session_rt_cfg.get('source_mode', 'simulated')).lower()
        relay_ttl_sec = max(1, _safe_int(session_rt_cfg.get('relay_ttl_sec', 20), 20))

        relay_state = get_session_realtime(session_id, ttl_sec=relay_ttl_sec)
        has_relay_state = bool(relay_state)
        # 仅当配置显式为 simulated 才走模拟；其余场景只要有实时回传就优先使用
        prefer_relay = source_mode != 'simulated'

        if session.status == 'FINISHED':
            final_hr = _safe_int(relay_state.get('heart_rate'), 90) if has_relay_state else 90
            final_spo2 = _safe_float(relay_state.get('spo2'), 99)
            final_spo2 = 99 if final_spo2 is None else final_spo2
            return Response({
                "session_id": session_id,
                "status": "FINISHED",
                "phase": "END",
                "exercises": session.exercises or [],
                "heart_rate": final_hr,
                "spo2": final_spo2,
                "current_rep": session.final_reps,
                "target_reps": session.sets * session.reps,
                "current_set": session.sets,
                "total_sets": session.sets,
                "progress": 100.0,
                "coach_message": "训练已结束，恢复中"
            }, status=status.HTTP_200_OK)

        created_at = session.started_at
        elapsed_sec = max(0, int((timezone.now() - created_at).total_seconds()))
        target_reps = session.sets * session.reps

        simulated_rep_speed = 3  # 平均每 3 秒完成 1 次
        current_rep = min(target_reps, elapsed_sec // simulated_rep_speed)

        cycle_len = (session.reps * simulated_rep_speed) + session.rest_sec
        cycle_pos = elapsed_sec % cycle_len if cycle_len > 0 else 0
        work_len = session.reps * simulated_rep_speed
        phase = 'WORK' if cycle_pos < work_len else 'REST'
        finished_cycles = (elapsed_sec // cycle_len) if cycle_len > 0 else 0
        current_set = min(session.sets, max(1, finished_cycles + 1))

        intensity_map = {'low': 108, 'medium': 125, 'high': 145}
        hr_base = intensity_map.get(session.intensity, 125)
        heart_rate = hr_base if phase == 'WORK' else max(95, hr_base - 18)
        spo2 = 98 if phase == 'WORK' else 99
        progress = round((current_rep / target_reps) * 100, 1) if target_reps > 0 else 0.0

        coach_message = "动作节奏稳定，保持呼吸" if phase == 'WORK' else "休息阶段，调整呼吸准备下一组"

        if has_relay_state and prefer_relay:
            relay_phase = str(relay_state.get('phase', phase)).upper()
            if relay_phase in {'WORK', 'REST', 'END'}:
                phase = relay_phase

            current_rep = max(0, min(target_reps, _safe_int(relay_state.get('current_rep'), current_rep)))
            current_set = max(1, min(session.sets, _safe_int(relay_state.get('current_set'), current_set)))
            heart_rate = max(0, _safe_int(relay_state.get('heart_rate'), heart_rate))
            relay_spo2 = _safe_float(relay_state.get('spo2'), spo2)
            spo2 = relay_spo2 if relay_spo2 is not None else spo2

            relay_progress = _safe_float(relay_state.get('progress'), None)
            if relay_progress is None:
                progress = round((current_rep / target_reps) * 100, 1) if target_reps > 0 else 0.0
            else:
                progress = round(max(0.0, min(100.0, relay_progress)), 1)

            coach_message = str(relay_state.get('coach_message') or coach_message)

        # 防止计数回退：会话存储一个单调不减的 final_reps 水位
        if current_rep < session.final_reps:
            current_rep = session.final_reps
        elif current_rep > session.final_reps:
            session.final_reps = current_rep
            session.save(update_fields=['final_reps'])

        if session.phase != phase:
            session.phase = phase
            session.save(update_fields=['phase'])

        return Response({
            "session_id": session_id,
            "status": "RUNNING",
            "phase": phase,
            "exercises": session.exercises or [],
            "heart_rate": heart_rate,
            "spo2": spo2,
            "current_rep": current_rep,
            "reps_per_set": session.reps,
            "target_reps": target_reps,
            "current_set": current_set,
            "total_sets": session.sets,
            "progress": progress,
            "coach_message": coach_message
        }, status=status.HTTP_200_OK)


class TrainingSessionFinishView(APIView):
    """A4) 结束训练会话（新增）"""
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        session = TrainingSession.objects.filter(session_id=session_id, user=request.user).first()
        if not session:
            return Response({"error": "训练会话不存在"}, status=status.HTTP_404_NOT_FOUND)

        if session.status == 'FINISHED':
            activity_id = session.activity_id
            return Response({
                "msg": "训练会话已结束",
                "activity_id": activity_id,
                "analysis_status": "PROCESSING" if activity_id and not AIFeedback.objects.filter(activity_id=activity_id).exists() else "COMPLETED"
            }, status=status.HTTP_200_OK)

        data = request.data
        ros_cfg = settings.ROS_RUNTIME_CONFIG
        session_rt_cfg = ros_cfg.get('session_realtime', {}) if isinstance(ros_cfg, dict) else {}
        relay_ttl_sec = max(1, _safe_int(session_rt_cfg.get('relay_ttl_sec', 20), 20))
        relay_state = get_session_realtime(session_id, ttl_sec=relay_ttl_sec)
        created_at = session.started_at
        duration_sec = max(1, int((timezone.now() - created_at).total_seconds()))
        target_reps = session.sets * session.reps
        default_rep = duration_sec // 3
        current_rep = _safe_int(data.get('final_reps', data.get('total_reps', default_rep)), default_rep)
        if relay_state:
            current_rep = _safe_int(relay_state.get('current_rep', current_rep), current_rep)
        current_rep = max(0, min(target_reps, current_rep))

        perceived_exertion = _safe_int(data.get('perceived_exertion', 3), 3)
        perceived_exertion = max(1, min(5, perceived_exertion))
        error_count = max(0, _safe_int(data.get('error_count', 0), 0))

        training_mode = _normalize_training_mode(session.mode)
        normalized_intensity = _normalize_intensity(session.intensity)
        exercises = [_normalize_activity_code(item) for item in (session.exercises or []) if str(item or '').strip()]
        if len(exercises) == 1:
            activity_type = exercises[0]
        elif len(exercises) > 1:
            activity_type = 'mixed_plan'
        else:
            activity_type = 'mixed_plan'

        valid_activity_codes = set(_get_allowed_plan_exercise_codes())
        valid_activity_codes.add('mixed_plan')
        if activity_type not in valid_activity_codes:
            activity_type = 'mixed_plan'

        activity = Activity.objects.create(
            user=request.user,
            training_mode=training_mode,
            activity_type=activity_type,
            duration=duration_sec,
            total_reps=current_rep,
            intensity=normalized_intensity,
            perceived_exertion=perceived_exertion,
        )

        raw_time_series = data.get('time_series', [])
        valid_phases = {'REST', 'END'}
        filtered_ts_objects = []
        valid_measurement_count = 0

        if isinstance(raw_time_series, list):
            for ts in raw_time_series:
                if not isinstance(ts, dict):
                    continue

                phase = str(ts.get('phase', 'REST')).upper()
                if phase not in valid_phases:
                    continue

                is_stable = bool(ts.get('is_stable', True))
                hold_secs = max(0, _safe_int(ts.get('hold_secs', 0), 0))
                if not is_stable:
                    continue
                if hold_secs and hold_secs < 3:
                    continue

                heart_rate = _safe_int(ts.get('heart_rate'), None)
                spo2 = _safe_float(ts.get('spo2'), None)
                if heart_rate is None and spo2 is None:
                    continue

                offset = _safe_int(ts.get('offset', 0), 0)
                offset = max(0, offset)
                current_rep_count = max(0, _safe_int(ts.get('current_rep', current_rep), current_rep))

                filtered_ts_objects.append(
                    ActivityTimeSeries(
                        activity=activity,
                        timestamp_offset=offset,
                        phase=phase,
                        heart_rate=heart_rate,
                        spo2=spo2,
                        current_rep_count=current_rep_count
                    )
                )
                valid_measurement_count += 1

        if filtered_ts_objects:
            ActivityTimeSeries.objects.bulk_create(filtered_ts_objects)

        pop_session_realtime(session_id)

        session.status = 'FINISHED'
        session.phase = 'END'
        session.activity = activity
        session.final_reps = current_rep
        session.ended_at = timezone.now()
        session.save(update_fields=['status', 'phase', 'activity', 'final_reps', 'ended_at'])

        def background_llm_task(act_id, user_id):
            try:
                ts_qs = ActivityTimeSeries.objects.filter(activity_id=act_id)
                rest_qs = ts_qs.filter(phase='REST')
                end_qs = ts_qs.filter(phase='END')

                rest_hr_values = [v for v in rest_qs.values_list('heart_rate', flat=True) if v is not None]
                rest_spo2_values = [v for v in rest_qs.values_list('spo2', flat=True) if v is not None]
                end_hr_values = [v for v in end_qs.values_list('heart_rate', flat=True) if v is not None]
                end_spo2_values = [v for v in end_qs.values_list('spo2', flat=True) if v is not None]

                rest_hr_median = round(median(rest_hr_values)) if rest_hr_values else 90
                rest_spo2_median = round(median(rest_spo2_values), 1) if rest_spo2_values else 98.0
                end_hr = round(median(end_hr_values)) if end_hr_values else rest_hr_median
                end_spo2 = round(median(end_spo2_values), 1) if end_spo2_values else rest_spo2_median

                valid_sample_count = ts_qs.filter(heart_rate__isnull=False).count() + ts_qs.filter(spo2__isnull=False).count()
                has_rest_data = bool(rest_hr_values or rest_spo2_values)
                has_end_data = bool(end_hr_values or end_spo2_values)
                confidence = _measurement_confidence(valid_sample_count, has_rest_data, has_end_data)

                llm_payload = {
                    "target_reps": target_reps,
                    "actual_reps": current_rep,
                    "error_count": error_count,
                    "rpe_score": perceived_exertion,
                    "rest_hr_median": rest_hr_median,
                    "rest_spo2_median": rest_spo2_median,
                    "end_hr": end_hr,
                    "end_spo2": end_spo2,
                    "valid_sample_count": valid_sample_count,
                    "measurement_confidence": confidence,
                    # 兼容旧提示词字段
                    "avg_rest_hr": rest_hr_median,
                    "min_spo2": rest_spo2_median,
                }
                allowed_codes = _get_allowed_plan_exercise_codes()
                llm_payload['allowed_exercise_codes'] = allowed_codes

                eval_json = generate_post_workout_eval(llm_payload) or {}
                plan_json = generate_post_workout_new_plan(llm_payload)
                merged_result = eval_json if isinstance(eval_json, dict) else {}
                if plan_json is not None:
                    merged_result = {**merged_result, 'new_plan': plan_json}

                if merged_result:
                    result_json = _sanitize_post_workout_result(merged_result, allowed_codes)
                    new_score = result_json.get('quality_score', 5)
                    Activity.objects.filter(id=act_id).update(quality_score=new_score)

                    AIFeedback.objects.update_or_create(
                        activity_id=act_id,
                        defaults={
                            'feedback_text': result_json.get('feedback_text', '干得很棒！'),
                            'next_step_suggestion': "系统基于最新运动表现自动调优",
                        }
                    )

                    new_plan = result_json.get('new_plan')
                    if new_plan and _plan_has_effective_exercises(new_plan):
                        _replace_active_plan_for_user(
                            user_id=user_id,
                            plan_content=new_plan,
                            plan_type='LLM_GENERATED'
                        )
            except Exception as e:
                logger.error(f"会话结算AI分析任务执行失败: {str(e)}")
            finally:
                connection.close()

        thread = threading.Thread(target=background_llm_task, args=(activity.id, request.user.id))
        thread.start()

        return Response({
            "msg": "训练会话已结束",
            "activity_id": activity.id,
            "saved_timeseries_count": len(filtered_ts_objects),
            "analysis_status": "PROCESSING"
        }, status=status.HTTP_202_ACCEPTED)


class TrainingSessionRealtimeIngestView(APIView):
    """A5) 训练会话实时数据回传（前端 ROS 订阅后统一上报后端）"""
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        session = TrainingSession.objects.filter(session_id=session_id, user=request.user).first()
        if not session:
            return Response({"error": "训练会话不存在"}, status=status.HTTP_404_NOT_FOUND)

        if session.status == 'FINISHED':
            return Response({"msg": "训练会话已结束，忽略实时上报"}, status=status.HTTP_200_OK)

        data = request.data if isinstance(request.data, dict) else {}

        payload = {
            "phase": str(data.get('phase', session.phase or 'WORK')).upper(),
            "heart_rate": max(0, _safe_int(data.get('heart_rate'), 0)),
            "spo2": _safe_float(data.get('spo2'), None),
            "current_rep": max(0, _safe_int(data.get('current_rep', 0), 0)),
            "current_set": max(1, _safe_int(data.get('current_set', 1), 1)),
            "progress": _safe_float(data.get('progress'), None),
            "coach_message": str(data.get('coach_message', '')).strip(),
        }

        if payload["phase"] not in {'WORK', 'REST', 'END'}:
            payload["phase"] = session.phase or 'WORK'

        upsert_session_realtime(session_id, payload)

        # 将实时上报的最新进度固化到会话，避免轮询端出现回跳
        if payload["current_rep"] > session.final_reps:
            session.final_reps = payload["current_rep"]
            session.save(update_fields=['final_reps'])

        return Response({"msg": "实时状态已接收"}, status=status.HTTP_200_OK)