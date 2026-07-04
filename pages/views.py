from django.shortcuts import render

# Create your views here.

# 主页面
def home(request):
    return render(request,'home.html', locals())

# 实时监测
def live_monitoring(request):
    return render(request,'live_monitoring.html', locals())