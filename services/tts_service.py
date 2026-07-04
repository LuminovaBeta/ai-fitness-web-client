# services/tts_service.py
import edge_tts
import asyncio
import subprocess
import os

async def _generate_audio(text, voice, output_path):
    # 增加 10% 语速，让教练听起来更干练
    communicate = edge_tts.Communicate(text, voice, rate="+10%")
    await communicate.save(output_path)

def play_tts_sync(text, voice="zh-CN-YunxiNeural", output_path="/tmp/coach.mp3"):
    """
    阻塞生成，异步播放。API 调用此函数后会立即返回，系统底层会自己播放声音。
    """
    if not text:
        return
        
    try:
        # 1. 下载生成语音文件
        asyncio.run(_generate_audio(text, voice, output_path))
        
        # 2. 调用底层 Linux 系统级播放器，非阻塞 (Popen)
        if os.path.exists(output_path):
            subprocess.Popen(["mpg123", "-q", output_path])
    except Exception as e:
        print(f"TTS 播放失败: {e}")