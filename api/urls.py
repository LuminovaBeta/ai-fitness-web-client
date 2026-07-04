from django.urls import path
from . import views

urlpatterns = [
    path('auth/register/', views.RegisterView.as_view(), name='api_register'),
    path('auth/login/', views.LoginView.as_view(), name='api_login'),
    path('auth/face-login/', views.FaceLoginView.as_view(), name='api_face_login'),
    path('user/activities/', views.ActivityListView.as_view(), name='api_activities'),
    path('user/plan/', views.TrainingPlanView.as_view(), name='api_plan'),
    path('user/load/', views.TrainingLoadView.as_view(), name='api_load'),
    path('user/dashboard/', views.DashboardView.as_view(), name='api_dashboard'),
    path('chat/ask/', views.ChatbotView.as_view(), name='api_chat_ask'),
    path('train/micro-coach/', views.MicroCoachView.as_view(), name='api_micro_coach'),
    path('train/finish/', views.TrainFinishView.as_view(), name='api_train_finish'),
]