from django.shortcuts import render
from django.conf import settings

# Create your views here.

# 主页面
def home(request):
    return render(request,'home.html', locals())

# 实时监测
def live_monitoring(request):
    ros_cfg = settings.ROS_RUNTIME_CONFIG
    context = {
        'ros_runtime': {
            'runtime_mode': ros_cfg.get('runtime_mode', 'windows_debug'),
            'debug_mode': bool(ros_cfg.get('debug_mode', False)),
            'active_profile': ros_cfg.get('active_profile', {}),
            'topics': ros_cfg.get('topics', {}),
            'action_detectors': ros_cfg.get('enabled_action_detectors', []),
            'exercise_dictionary': ros_cfg.get('exercise_dictionary', []),
        }
    }
    return render(request, 'live_monitoring.html', context)