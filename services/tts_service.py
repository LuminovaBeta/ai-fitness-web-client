# services/tts_service.py
import edge_tts
import asyncio
import os
import platform
import tempfile
import subprocess

async def _generate_audio(text, voice, output_path):
    communicate = edge_tts.Communicate(text, voice, rate="+10%")
    await communicate.save(output_path)

def play_tts_sync(text, voice="zh-CN-YunyangNeural"):
    if not text:
        return 0 # 返回 0 秒
    
    temp_dir = tempfile.gettempdir()
    output_path = os.path.join(temp_dir, "coach.mp3")
    audio_duration = 0
        
    try:
        asyncio.run(_generate_audio(text, voice, output_path))
        
        if os.path.exists(output_path):
            # ====== 新增：解析 MP3 时长 ======
            try:
                from mutagen.mp3 import MP3
                audio = MP3(output_path)
                audio_duration = audio.info.length # 单位：秒 (float)
            except Exception as e:
                # 降级方案：如果没装 mutagen 或解析失败，按文字长度粗略估算 (约 4个字/秒)
                print(f"解析音频时长失败，使用估算: {e}")
                audio_duration = len(text) / 4.0 
            # ==================================
            
            if platform.system() == "Windows":
                print(f"[TTS Mock] 语音生成成功，内容：'{text}' (时长: {audio_duration:.2f}s)")
                os.system(f"start {output_path}") 
            else:
                subprocess.Popen(["mpg123", "-q", output_path])
                
    except Exception as e:
        print(f"TTS 播放失败: {e}")
        
    return audio_duration # 返回播放时长