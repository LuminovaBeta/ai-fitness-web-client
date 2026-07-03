from django.shortcuts import render
# Create your views here.
import base64
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from pages.models import UserProfile # 复用模型
from django.utils import timezone
from pages.models import Activity, TrainingPlan, UserProfile

# 登录相关逻辑 ###########################################

def get_tokens_for_user(user):
    """手动为指定用户生成 JWT 访问令牌和刷新令牌"""
    refresh = RefreshToken.for_user(user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }

class RegisterView(APIView):
    """3. 注册接口 (POST)"""
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
        
        # 2. 初始化你队友设计的身体档案表 (UserProfile)
        UserProfile.objects.create(
            user=user,
            gender=data.get('gender', 'O'),
            height=data.get('height'),
            weight=data.get('weight')
        )
        
        # 3. 注册成功后直接签发 Token，免去用户二次登录
        tokens = get_tokens_for_user(user)
        return Response({
            "msg": "注册成功",
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
    """2. 人脸识别登录接口 (POST)"""
    def post(self, request):
        """
        嵌入式端数据流逻辑：
        触屏终端或手机App调用摄像头抓拍人脸，转换成 Base64 编码或人脸特征向量传给后端。
        """
        face_data = request.data.get('face_data') # 接收到的图像数据或向量
        
        if not face_data:
            return Response({"error": "未检测到人脸数据"}, status=status.HTTP_400_BAD_REQUEST)
            
        # ==========================================================
        # 核心逻辑占位：此处需要调用你板子上的视觉比对算法或本地特征库
        # 假设我们通过某种算法识别出该人脸对应的是用户 ID 为 1 的用户
        # ==========================================================
        recognized_user_id = 1 
        
        try:
            user = User.objects.get(id=recognized_user_id)
            
            # 同样使用相同的 Token 生成管道
            tokens = get_tokens_for_user(user)
            return Response({
                "msg": "人脸识别成功",
                "username": user.username,
                **tokens
            }, status=status.HTTP_200_OK)
            
        except User.DoesNotExist:
            return Response({"error": "未在系统中找到匹配的人脸档案，请先使用账号登录并绑定人脸"}, status=status.HTTP_404_NOT_FOUND)
        

# 登录相关逻辑end ###########################################

# 用户训练相关逻辑 #############################################
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