from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class UserProfile(models.Model):
    """ 用户身体档案表 (保持你的优秀设计不变) """
    GENDER_CHOICES = (('M', '男'), ('F', '女'), ('O', '其他'))
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, default='O', verbose_name="性别")
    height = models.FloatField(null=True, blank=True, verbose_name="身高(cm)")
    weight = models.FloatField(null=True, blank=True, verbose_name="体重(kg)")
    hr_max = models.IntegerField(default=190, verbose_name="最大心率")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def __str__(self):
        return f"{self.user.username} 的档案"


class Activity(models.Model):
    """
    运动记录主表（宏观摘要）
    融合了之前的 TrainingRecord，包含全部宏观生理、表现及主观评价。
    """
    ACTIVITY_TYPES = (
        ('squat', '深蹲 (Squat)'),
        ('lunge', '弓箭步 (Lunge)'), 
        ('push_up', '俯卧撑 (Push-up)'),
        ('plank', '平板支撑 (Plank)'),
    )
    
    # 1. 用户身份与运动时间
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activities', null=True) # 开发初期可设为 null 方便免密联调
    activity_type = models.CharField(max_length=50, choices=ACTIVITY_TYPES, verbose_name="运动类型")
    start_time = models.DateTimeField(default=timezone.now, verbose_name="开始时间")
    duration = models.IntegerField(default=0, verbose_name="运动时长(秒)")
    
    # 2. 运动表现
    total_reps = models.IntegerField(default=0, verbose_name="总动作次数")
    avg_accuracy_score = models.FloatField(default=0.0, verbose_name="平均动作标准度")
    calories = models.IntegerField(default=0, verbose_name="消耗热量(kcal)")
    
    # 3. 宏观生理体征
    avg_heart_rate = models.FloatField(null=True, blank=True, verbose_name="平均心率(BPM)")
    avg_spo2 = models.FloatField(null=True, blank=True, verbose_name="平均血氧(%)")
    
    # 4. 主观记录
    training_note = models.TextField(blank=True, null=True, verbose_name="训练随笔")
    # 保存前端计算好的强度与大模型评估的质量
    INTENSITY_CHOICES = (('LOW', '低强度'), ('MED', '中强度'), ('HIGH', '高强度'))
    intensity = models.CharField(max_length=10, choices=INTENSITY_CHOICES, default='MED', verbose_name="训练强度")
    quality_score = models.IntegerField(default=5, verbose_name="训练效果质量 (1-10)")

    class Meta:
        db_table = 'activity_records'
        ordering = ['-start_time']

    def __str__(self):
        return f"[{self.get_activity_type_display()}] {self.total_reps}次 - {self.start_time.strftime('%Y-%m-%d')}"


class ActivityTimeSeries(models.Model):
    """
    运动指标时间序列表（微观高频数据）
    支持绘制 ECharts 曲线，包含了 AI 视觉和蓝牙心率双重数据链路。
    """
    
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='time_series')
    timestamp_offset = models.IntegerField(verbose_name="相对开始时间的偏移(秒)")
    
    # 视觉计算模块数据
    current_rep_count = models.IntegerField(default=0, verbose_name="当前累计次数")
    pose_score = models.FloatField(null=True, blank=True, verbose_name="当前秒动作得分")
    joints_data = models.JSONField(null=True, blank=True, verbose_name="关节坐标帧数据") 
    
    # 蓝牙传感器模块数据 (新增)
    heart_rate = models.IntegerField(null=True, blank=True, verbose_name="瞬时心率(BPM)")
    spo2 = models.FloatField(null=True, blank=True, verbose_name="瞬时血氧(%)")

    class Meta:
        indexes = [models.Index(fields=['activity', 'timestamp_offset'])]
        ordering = ['timestamp_offset']


class ActionErrorLog(models.Model):
    """
    具体的动作违规纠错记录表 (取代了原来的一坨 Text 字段)
    用于日后统计用户的核心弱点，比如“左膝发力失衡占比”。
    """
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='errors')
    timestamp_offset = models.IntegerField(verbose_name="相对开始时间的偏移(秒)")
    error_code = models.IntegerField(verbose_name="错误代码 (如101)")
    error_msg = models.CharField(max_length=255, verbose_name="错误描述")

    def __str__(self):
        return f"{self.activity.id} - [{self.timestamp_offset}s] {self.error_msg}"


class AIFeedback(models.Model):
    """ AI 智能健身建议表 (保持不变) """
    SCORE_LEVELS = (('EXCELLENT', '优秀'), ('GOOD', '良好'), ('FAIR', '及格'), ('POOR', '需改进'))
    activity = models.OneToOneField(Activity, on_delete=models.CASCADE, related_name='ai_feedback')
    score_level = models.CharField(max_length=20, choices=SCORE_LEVELS, verbose_name="综合评价等级")
    suggestion_text = models.TextField(verbose_name="AI改进建议")
    created_at = models.DateTimeField(auto_now_add=True)

class TrainingPlan(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='plans', null=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="生成时间")
    is_active = models.BooleanField(default=True, verbose_name="当前执行中的计划")
    
    # 使用 JSONField 存储结构化的一周计划
    # 格式示例: {"mon": {"type": "squat", "sets": 3, "reps_per_set": 15}, "tue": {"type": "rest"}}
    plan_content = models.JSONField(verbose_name="一周计划详情JSON") 

    def __str__(self):
        return f"用户计划 ({self.created_at.strftime('%Y-%m-%d')})"
