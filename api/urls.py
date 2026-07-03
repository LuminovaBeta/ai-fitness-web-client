from django.urls import path
from . import views

urlpatterns = [
    path('auth/register/', views.RegisterView.as_view(), name='api_register'),
    path('auth/login/', views.LoginView.as_view(), name='api_login'),
    path('auth/face-login/', views.FaceLoginView.as_view(), name='api_face_login'),
    path('user/activities/', views.ActivityListView.as_view(), name='api_activities'),
    path('user/plan/', views.TrainingPlanView.as_view(), name='api_plan'),
    path('user/load/', views.TrainingLoadView.as_view(), name='api_load'),
    path('user/save/', views.TrainingLoadView.as_view(), name='api_load'),
]