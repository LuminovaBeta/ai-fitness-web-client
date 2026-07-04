# services/tts_service.py
import edge_tts
import asyncio
import os
import platform
import tempfile

async def _generate_audio(text, voice, output_path):
    communicate = edge_tts.Communicate(text, voice, rate="+10%")
    await communicate.save(output_path)

def play_tts_sync(text, voice="zh-CN-YunyangNeural"):
    if not text:
        return
    
    # 动态获取系统临时目录，兼容 Windows 和 Linux
    temp_dir = tempfile.gettempdir()
    output_path = os.path.join(temp_dir, "coach.mp3")
        
    try:
        asyncio.run(_generate_audio(text, voice, output_path))
        
        if os.path.exists(output_path):
            if platform.system() == "Windows":
                # Windows 下我们在控制台打个日志即可，也可取消注释下方代码实际播放
                print(f"[TTS Mock] 语音生成成功，内容：'{text}' (保存在 {output_path})")
                os.system(f"start {output_path}") 
            else:
                import subprocess
                subprocess.Popen(["mpg123", "-q", output_path])
    except Exception as e:
        print(f"TTS 播放失败: {e}")