from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class UserProfile(models.Model):
    """ 用户身体档案表 """
    GENDER_CHOICES = (('M', '男'), ('F', '女'), ('O', '其他'))
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, default='O', verbose_name="性别")
    height = models.FloatField(null=True, blank=True, verbose_name="身高(cm)")
    weight = models.FloatField(null=True, blank=True, verbose_name="体重(kg)")
    hr_max = models.IntegerField(default=190, verbose_name="最大心率")
    face_feature_id = models.CharField(max_length=255, null=True, blank=True, verbose_name="绑定的特征向量ID(用于人脸登录)")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    def __str__(self):
        return f"{self.user.username} 的档案"


class TrainingPlan(models.Model):
    """ 训练计划表 (结构化JSON) """
    PLAN_TYPE_CHOICES = (
        ('LLM_GENERATED', '大模型生成'),
        ('USER_CUSTOM', '用户自定义'),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='plans', null=True)
    is_active = models.BooleanField(default=True, verbose_name="是否为当前执行计划")
    plan_type = models.CharField(max_length=50, choices=PLAN_TYPE_CHOICES, default='LLM_GENERATED', verbose_name="计划类型")
    # 数据结构要求：[{"day": 1, "exercises": [{"type": "squat", "sets": 3, "reps_per_set": 15, "rest_interval_sec": 60}]}]
    plan_content = models.JSONField(verbose_name="结构化计划详情JSON") 
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="生成时间")

    def __str__(self):
        return f"[{self.get_plan_type_display()}] 用户计划 ({self.created_at.strftime('%Y-%m-%d')})"


class Activity(models.Model):
    """ 宏观训练记录表 """
    TRAINING_MODE_CHOICES = (
        ('GUIDED', '引导计划模式'),
        ('FREE', '自由训练模式'),
    )
    ACTIVITY_TYPES = (
        ('squat', '深蹲 (Squat)'),
        ('lunge', '弓箭步 (Lunge)'), 
        ('push_up', '俯卧撑 (Push-up)'),
        ('plank', '平板支撑 (Plank)'),
        ('mixed_plan', '混合计划 (引导模式)'),
    )
    INTENSITY_CHOICES = (('LOW', '低强度'), ('MED', '中强度'), ('HIGH', '高强度'))
    
    # 1. 基础信息
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activities', null=True)
    training_mode = models.CharField(max_length=20, choices=TRAINING_MODE_CHOICES, default='FREE', verbose_name="训练模式")
    activity_type = models.CharField(max_length=50, choices=ACTIVITY_TYPES, verbose_name="运动类型")
    start_time = models.DateTimeField(default=timezone.now, verbose_name="开始时间")
    
    # 2. 运动表现
    duration = models.IntegerField(default=0, verbose_name="总耗时(秒)")
    total_reps = models.IntegerField(default=0, verbose_name="总次数")
    
    # 3. 强度与主客观评价
    intensity = models.CharField(max_length=10, choices=INTENSITY_CHOICES, default='MED', verbose_name="客观训练强度")
    perceived_exertion = models.IntegerField(default=3, verbose_name="主观感受RPE(1-5级)")
    quality_score = models.IntegerField(default=5, verbose_name="AI质量评分(1-10)")
    
    # (可选保留) 之前的热量及其他宏观字段，如不需要可注释掉
    calories = models.IntegerField(default=0, verbose_name="消耗热量(kcal)")

    class Meta:
        db_table = 'activity_records'
        ordering = ['-start_time']

    def __str__(self):
        mode = "自由" if self.training_mode == 'FREE' else "引导"
        return f"[{mode}-{self.get_activity_type_display()}] {self.total_reps}次 - {self.start_time.strftime('%Y-%m-%d')}"


class ActivityTimeSeries(models.Model):
    """
    高频微观时序表 (静息高质数据)
    规避运动伪影，仅在组间休息和结束时进行 1Hz 采样落盘。
    """
    PHASE_CHOICES = (
        ('REST', '组间休息'),
        ('END', '训练结束'),
    )
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='time_series')
    timestamp_offset = models.IntegerField(verbose_name="相对开始时间的偏移(秒)")
    phase = models.CharField(max_length=10, choices=PHASE_CHOICES, default='REST', verbose_name="数据来源相位")
    
    heart_rate = models.IntegerField(null=True, blank=True, verbose_name="瞬时心率(BPM)")
    spo2 = models.FloatField(null=True, blank=True, verbose_name="瞬时血氧(%)")
    current_rep_count = models.IntegerField(default=0, verbose_name="当前累计次数")

    class Meta:
        indexes = [models.Index(fields=['activity', 'timestamp_offset'])]
        ordering = ['timestamp_offset']


class AIFeedback(models.Model):
    """ AI 评语与报告表 """
    activity = models.OneToOneField(Activity, on_delete=models.CASCADE, related_name='ai_feedback')
    feedback_text = models.TextField(verbose_name="大模型文字评语")
    next_step_suggestion = models.TextField(verbose_name="后续计划优化建议")
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"反馈 - {self.activity.id}"