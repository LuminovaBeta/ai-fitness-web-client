from django.urls import path
from . import views

urlpatterns = [
    # 鉴权与视觉注册相关
    path('auth/register/', views.RegisterView.as_view(), name='api_register'),
    path('auth/login/', views.LoginView.as_view(), name='api_login'),
    path('auth/face-login/', views.FaceLoginView.as_view(), name='api_face_login'),
    path('auth/face-register/', views.FaceEnrollView.as_view(), name='api_face_register'),
    
    # 业务查询与展示
    path('plan/init-generate/', views.GenerateInitialPlanView.as_view(), name='api_plan_generate_initial'),
    path('user/activities/', views.ActivityListView.as_view(), name='api_activities'),
    path('plan/current/', views.TrainingPlanView.as_view(), name='api_plan_current'),
    path('user/load/', views.TrainingLoadView.as_view(), name='api_load'),
    path('user/dashboard/', views.DashboardView.as_view(), name='api_dashboard'),
    
    # 训练中枢与大模型交互
    path('chat/ask/', views.ChatbotView.as_view(), name='api_chat_ask'),
    path('train/micro-coach/', views.MicroCoachView.as_view(), name='api_micro_coach'),
    path('train/finish/', views.TrainFinishView.as_view(), name='api_train_finish'),
]