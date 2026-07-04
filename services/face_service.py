# services/face_service.py

import cv2
import numpy as np
import base64
from django.contrib.auth.models import User
from pages.models import UserFaceEmbedding

# =====================================================================
# 边缘端模型加载桩（RK3588 环境下可替换为 rknn-toolkit2 或 onnxruntime 推理）
# =====================================================================
class InsightFaceEngine:
    """
    假定此处集成了 det_2.5g (RetinaFace) 和 w600k_r50 (ArcFace) 
    标准 InsightFace Python 库或板载 ONNXRuntime 的封装
    """
    def __init__(self):
        # 初始化模型配置，实际生产环境中在此处加载 model_file
        pass

    def detect_and_extract(self, img_np):
        """
        输入: NumPy 图像矩阵
        输出: 检出的人脸列表，每个脸包含:
              - 'bbox': [x1, y1, x2, y2]
              - 'kps': 5个关键点坐标 [[x,y], ...]
              - 'embedding': 512维向量 (np.array)
              - 'pose': [pitch, yaw, roll] 姿态角（部分引擎直接输出，若无可用关键点代算）
        """
        # 实际业务代码请替换为您的本地模型前向传播:
        # faces = self.app.get(img_np)
        # 此处写伪代码/桩代码以供逻辑跑通
        return [] 

# 初始化全局单例引擎
face_engine = InsightFaceEngine()

# =====================================================================
# 核心防误识别与距离/姿态过滤器
# =====================================================================
def process_face_pipeline(base64_image_str):
    """
    核心人脸处理管道
    返回值: (status_code, message, embedding_data)
    """
    # 1. 解码前端上传的 Base64 图片
    try:
        img_data = base64.b64decode(base64_image_str.split(',')[-1])
        nparr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return "IMG_ERROR", "图像解码失败，请检查摄像头输入", None
    except Exception:
        return "IMG_ERROR", "数据格式错误", None

    img_h, img_w, _ = img.shape

    # 2. 调用模型进行检测与特征提取 (det_2.5g + w600k_r50)
    # 生产环境中推荐直接读取实时视频帧，若是 Web API 则解析上传的图像
    faces = face_engine.detect_and_extract(img)
    
    if not faces or len(faces) == 0:
        return "NO_FACE", "未检测到人脸，请面向屏幕", None

    # 3. 多人识别机制：计算 BBox 面积，强行筛选出“最近/最大”的那张脸
    target_face = None
    max_area = 0
    
    for face in faces:
        x1, y1, x2, y2 = face['bbox']
        area = (x2 - x1) * (y2 - y1)
        if area > max_area:
            max_area = area
            target_face = face

    # 4. 防误判机制一：凑近校验（人脸宽度必须达到画面宽度的 28% 以上）
    tx1, ty1, tx2, ty2 = target_face['bbox']
    face_width = tx2 - tx1
    width_ratio = face_width / img_w
    
    if width_ratio < 0.28: # 工业级阈值，确保用户已经走到体测机前凑近
        return "TOO_FAR", "请靠近体测一体机摄像头", None

    # 5. 防误判机制二：正对校验（利用5点关键点计算面部偏角，防止侧脸或低头误扫）
    # 如果推理引擎直接输出了 pose 角 [pitch, yaw, roll]
    if 'pose' in target_face:
        pitch, yaw, _ = target_face['pose']
        if abs(pitch) > 15 or abs(yaw) > 15:
            return "NOT_FACING", "请抬起头，正对屏幕中央", None
    else:
        # 降级方案：利用左右眼与鼻尖的几何对称性粗略计算 Yaw 偏角
        kps = target_face['kps'] # [[x,y], ...] 依次为左眼、右眼、鼻尖
        left_eye_x = kps[0][0]
        right_eye_x = kps[1][0]
        nose_x = kps[2][0]
        
        left_dist = abs(nose_x - left_eye_x)
        right_dist = abs(right_eye_x - nose_x)
        total_eye_dist = right_eye_x - left_eye_x
        
        # 左右距离差值占总眼距比例过大，说明脸严重侧向一边
        if total_eye_dist > 0 and abs(left_dist - right_dist) / total_eye_dist > 0.35:
            return "NOT_FACING", "请正对摄像头，保持面部端正", None

    # 核心拦截器全部通过，返回成功状态及 512维特征向量 (List 格式)
    embedding_list = target_face['embedding'].tolist() if isinstance(target_face['embedding'], np.ndarray) else target_face['embedding']
    return "SUCCESS", "校验通过，正在识别身份", embedding_list


def verify_face_1_to_N(input_embedding, threshold=0.62):
    """
    高效率的 1:N 欧氏距离/余弦相似度比对算法
    """
    input_vec = np.array(input_embedding, dtype=np.float32)
    # L2 归一化，确保余弦相似度计算准确
    input_vec /= np.linalg.norm(input_vec)

    all_embeddings = UserFaceEmbedding.objects.select_related('user').all()
    
    best_match_user = None
    max_sim = -1.0

    for db_record in all_embeddings:
        db_vec = np.array(db_record.embedding, dtype=np.float32)
        db_vec /= np.linalg.norm(db_vec)
        
        # 计算余弦相似度
        similarity = np.dot(input_vec, db_vec)
        if similarity > max_sim:
            max_sim = similarity
            best_match_user = db_record.user

    # w600k_r50 配合 RetinaFace 推荐的安全阈值在 0.60 ~ 0.65 之间
    if max_sim >= threshold:
        return best_match_user, float(max_sim)
    
    return None, float(max_sim)