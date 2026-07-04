from django.shortcuts import render
# Create your views here.
from django.db.models import Sum 

import base64
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from pages.models import UserProfile # 复用模型
from django.utils import timezone
import threading
from datetime import timedelta
from pages.models import Activity, ActivityTimeSeries, AIFeedback, TrainingPlan
from services.tts_service import play_tts_sync
from services.llm_service import generate_micro_coaching, generate_post_workout_feedback, load_yaml, call_local_llm
from services.face_service import process_face_pipeline, verify_face_1_to_N

# 登录相关逻辑 ###########################################

def get_tokens_for_user(user):
    """手动为指定用户生成 JWT 访问令牌和刷新令牌"""
    refresh = RefreshToken.for_user(user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }

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
        
        UserProfile.objects.create(
            user=user,
            gender=gender,
            height=height,
            weight=weight
        )
        
        # 3. 签发 Token 并快速返回
        tokens = get_tokens_for_user(user) # 假设你 utils 里有这个函数
        return Response({
            "msg": "注册成功，账号档案已建立",
            "username": user.username,
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
            
            # 触发 Linux 硬件底层播报，消除网页感
            play_tts_sync(f"识别成功，欢迎回来，{user.username}")
            
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
    def post(self, request):
        # 权限控制：强制要求录入前用户必须处于登录状态 (通过 JWT 验证)
        user = request.user
        if not user.is_authenticated:
            return Response({"code": "UNAUTHORIZED", "msg": "请先登录账号再进行人脸绑定"}, status=status.HTTP_401_UNAUTHORIZED)
            
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

        play_tts_sync("人脸特征录入成功")
        
        return Response({
            "code": "ENROLL_SUCCESS",
            "msg": "人脸特征绑定成功，已与当前身体档案互联"
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
            profile = user.userprofile # 依赖 models.py 中 OneToOneField 的 related_name，默认是小写
            gender = profile.gender
            height = profile.height
            weight = profile.weight
        except Exception:
            gender, height, weight = 'O', 170, 65 # 异常兜底

        try:
            # 1. 调度本地 LLM 运算
            config = load_yaml()
            prompt_template = config['prompts']['onboarding']
            prompt = prompt_template.format(
                gender=gender, 
                height=height, 
                weight=weight, 
                user_goal_text=user_goal
            )
            llm_reply = call_local_llm(prompt, max_tokens=300, temperature=0.2)
            
            # 2. 清理 Markdown 并解析 JSON
            clean_text = llm_reply.replace("```json", "").replace("```", "").strip()
            plan_json = json.loads(clean_text)

            # 3. 计划落盘
            plan = TrainingPlan.objects.create(
                user=user,
                plan_content=plan_json,
                is_active=True,
                plan_type='LLM_GENERATED'
            )
            
            return Response({
                "msg": "AI 专属训练计划生成成功",
                "plan_id": plan.id,
                "plan_content": plan_json
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            # 容灾降级机制：如果 LLM 超时或解析失败，给一个默认计划，防止前端无数据可用
            default_plan = [{"day": 1, "exercises": [{"type": "squat", "sets": 3, "reps_per_set": 15, "rest_sec": 60}]}]
            plan = TrainingPlan.objects.create(
                user=user,
                plan_content=default_plan,
                is_active=True,
                plan_type='LLM_GENERATED'
            )
            return Response({
                "msg": "当前 AI 算力拥挤，已为您匹配基础兜底计划",
                "plan_id": plan.id,
                "plan_content": default_plan
            }, status=status.HTTP_201_CREATED)

class ActivityListView(APIView):
    """
    对应规划：查询训练记录 (GET)
    支持触屏端和App拉取历史记录列表
    """
    def get(self, request):
        # 联调阶段：如果用户未登录则默认查询所有记录；App端上线后可改为强制过滤当前用户
        user = request.user if request.user.is_authenticated else None
        
        if user:
            activities = Activity.objects.filter(user=user)
        else:
            activities = Activity.objects.all()
            
        # 构造精简的 JSON 列表结构返回给前端
        data = []
        for act in activities:
            data.append({
                "id": act.id,
                "activity_type": act.activity_type,
                "start_time": act.start_time.strftime('%Y-%m-%d %H:%M:%S'),
                "duration": act.duration,
                "total_reps": act.total_reps,
                "intensity": act.get_intensity_display(), # 返回中文“低/中/高强度”
                "quality_score": act.quality_score
            })
        return Response({"records": data}, status=status.HTTP_200_OK)


class TrainingPlanView(APIView):
    """
    对应规划：更新训练计划 (POST) / 获取当前计划 (GET)
    通过大模型或前端手动调整一周的计划指标
    """
    def get(self, request):
        """获取当前正在执行的激活计划"""
        user = request.user if request.user.is_authenticated else None
        plan = TrainingPlan.objects.filter(user=user, is_active=True).first()
        
        if not plan:
            return Response({"msg": "暂无活动中的训练计划，请先生成"}, status=status.HTTP_404_NOT_FOUND)
            
        return Response({
            "plan_id": plan.id,
            "created_at": plan.created_at.strftime('%Y-%m-%d'),
            "plan_content": plan.plan_content
        }, status=status.HTTP_200_OK)

    def post(self, request):
        """前端或大模型更新、覆盖训练计划"""
        user = request.user if request.user.is_authenticated else None
        new_plan_content = request.data.get('plan_content')
        
        if not new_plan_content:
            return Response({"error": "计划内容不能为空"}, status=status.HTTP_400_BAD_REQUEST)
            
        # 将该用户之前的所有计划设为失效
        TrainingPlan.objects.filter(user=user, is_active=True).update(is_active=False)
        
        # 创建新计划
        plan = TrainingPlan.objects.create(
            user=user,
            plan_content=new_plan_content,
            is_active=True
        )
        return Response({"msg": "训练计划更新成功", "plan_id": plan.id}, status=status.HTTP_201_CREATED)


class TrainingLoadView(APIView):
    """
    对应规划：查询训练负荷 (GET)
    【核心逻辑】：后端自动查询最近 7 天的滚动数据，计算出累计运动负荷值
    """
    def get(self, request):
        user = request.user if request.user.is_authenticated else None
        
        # 计算 7 天前的时间节点
        seven_days_ago = timezone.now() - timedelta(days=7)
        
        # 查询最近 7 天该用户的所有运动记录
        recent_activities = Activity.objects.filter(
            start_time__gte=seven_days_ago
        )
        if user:
            recent_activities = recent_activities.filter(user=user)
            
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
    """主页聚合接口 (GET) - 一次性拉取用户核心数据"""
    def get(self, request):
        user = request.user
        if not user.is_authenticated:
            return Response({"error": "未登录"}, status=status.HTTP_401_UNAUTHORIZED)
        
        # 1. 基础档案
        profile = user.profile
        
        # 2. 今日计划
        active_plan = TrainingPlan.objects.filter(user=user, is_active=True).first()
        
        # 3. 近期负荷 (简单示例)
        recent_acts = Activity.objects.filter(user=user, start_time__gte=timezone.now()-timedelta(days=7))
        weekly_duration = sum(act.duration for act in recent_acts)
        
        # 入场语音欢迎（硬件直接发声）
        play_tts_sync(f"欢迎回来 {user.username}，今天有新的训练计划等你完成。")

        return Response({
            "user_info": {
                "username": user.username,
                "gender": profile.get_gender_display(),
                "height": profile.height,
                "weight": profile.weight
            },
            "weekly_duration_mins": round(weekly_duration / 60, 1),
            "active_plan_id": active_plan.id if active_plan else None
        })

class MicroCoachView(APIView):
    """组间话疗微指导 (POST)"""
    def post(self, request):
        activity_type = request.data.get('activity_type', '运动')
        error_text = request.data.get('error_text', '姿势标准')
        
        # 1. 请求本地 LLM 生成短评
        coach_words = generate_micro_coaching(activity_type, error_text)
        
        # 2. 直接调用系统底层发声 (不经过前端音频标签)
        play_tts_sync(coach_words)
        
        return Response({"spoken_text": coach_words}, status=status.HTTP_200_OK)

class TrainFinishView(APIView):
    """训练核心中枢结算接口 (POST)"""
    def post(self, request):
        user = request.user
        data = request.data
        
        # 1. 立即落盘宏观主表 Activity (原有逻辑保持不变)
        activity = Activity.objects.create(
            user=user,
            training_mode=data.get('training_mode', 'FREE'),
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
            
            # 3.3 生成反馈与新计划
            result_json = generate_post_workout_feedback(llm_payload)
            if result_json:
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
                if new_plan:
                    TrainingPlan.objects.filter(user_id=user_id, is_active=True).update(is_active=False)
                    TrainingPlan.objects.create(
                        user_id=user_id,
                        plan_content=new_plan,
                        is_active=True,
                        plan_type='LLM_GENERATED'
                    )

        thread = threading.Thread(target=background_llm_task, args=(activity.id, user.id, data))
        thread.start()

        play_tts_sync("训练辛苦了！数据已经保存，AI正在为您生成分析报告，请稍候。")
        return Response({"msg": "数据已保存，AI后台分析中", "activity_id": activity.id}, status=status.HTTP_202_ACCEPTED)

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
        
        # 6. 一体机工业级发声体验：调用底层 Linux 物理声道进行同步非阻塞语音播报，拒绝网页感
        play_tts_sync(reply_text)
        
        # 7. 将文本结果同时返回给前端用于 Kiosk 屏幕上气泡的渲染
        return Response({
            "reply": reply_text,
            "rag_meta": {
                "injected_duration_mins": weekly_duration,
                "injected_intensity": weekly_intensity
            }
        }, status=status.HTTP_200_OK)