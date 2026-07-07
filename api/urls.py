from django.urls import path
from . import views

urlpatterns = [
    path('runtime/ros-config/', views.ROSRuntimeConfigView.as_view(), name='api_runtime_ros_config'),

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
          #训练记录详情查询 
    path('user/activities/<int:pk>/', views.ActivityDetailView.as_view(), name='api_activity_detail'),
    path('train/status/<int:pk>/', views.ActivityStatusView.as_view(), name='api_train_status'),
    
    # 训练中枢与大模型交互
    path('chat/ask/', views.ChatbotView.as_view(), name='api_chat_ask'),
    path('train/micro-coach/', views.MicroCoachView.as_view(), name='api_micro_coach'),
    path('train/finish/', views.TrainFinishView.as_view(), name='api_train_finish'),
    # 训练动作字典查询
    path('train/exercises/', views.ExerciseDictionaryView.as_view(), name='api_train_exercises'),
    path('train/tts-play/', views.TTSPlayView.as_view(), name='api_train_tts_play'),
    path('train/tts-stop/', views.TTSStopView.as_view(), name='api_train_tts_stop'),

    # 新增结构化 API
    path('user/profile/', views.UserProfileView.as_view(), name='api_user_profile'),
    path('train/session/start/', views.TrainingSessionStartView.as_view(), name='api_train_session_start'),
    path('train/session/<str:session_id>/state/', views.TrainingSessionStateView.as_view(), name='api_train_session_state'),
    path('train/session/<str:session_id>/ingest/', views.TrainingSessionRealtimeIngestView.as_view(), name='api_train_session_ingest'),
    path('train/session/<str:session_id>/finish/', views.TrainingSessionFinishView.as_view(), name='api_train_session_finish'),
]