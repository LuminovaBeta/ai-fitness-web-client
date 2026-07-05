"""
ROS 假数据注入器（用于 Windows 调试联调）

用途：
- 按固定频率发布模拟训练状态，便于前端/后端联调。
- 与 `config/ros_runtime.yaml` 中 topic 对齐。

依赖：
- rclpy
- std_msgs

示例：
python tools/mock_ros_injector.py --hz 2 --mode normal
python tools/mock_ros_injector.py --hz 1 --mode strict
"""

from __future__ import annotations

import argparse
import json
import random
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32, Int32, String


class MockRosInjector(Node):
    def __init__(self, hz: float, mode: str):
        super().__init__('mock_ros_injector')

        self.hz = max(0.2, hz)
        self.mode = mode
        self.rep_count = 0
        self.phase_idx = 0
        self.phases = ['准备', '下蹲', '底部稳定', '起身']

        self.pub_state = self.create_publisher(String, '/squat/state', 10)
        self.pub_rep = self.create_publisher(Int32, '/squat/rep_completed', 10)
        self.pub_error = self.create_publisher(String, '/squat/errors', 10)

        self.pub_hr = self.create_publisher(Float32, '/heart_sensor_node/heart_rate', 10)
        self.pub_spo2 = self.create_publisher(Float32, '/heart_sensor_node/spo2', 10)
        self.pub_loss = self.create_publisher(Float32, '/heart_sensor_node/packet_loss', 10)

        self.pub_llm = self.create_publisher(String, '/rkllm/output_string', 10)

        interval = 1.0 / self.hz
        self.timer = self.create_timer(interval, self._tick)

        self.get_logger().info(f'Mock 注入器启动: hz={self.hz}, mode={self.mode}')

    def _tick(self):
        # 1) 相位
        phase = self.phases[self.phase_idx % len(self.phases)]
        self.phase_idx += 1
        state_msg = String()
        state_msg.data = json.dumps({'msg': phase, 'ts': int(time.time())}, ensure_ascii=False)
        self.pub_state.publish(state_msg)

        # 2) reps（每完整循环+1）
        if self.phase_idx % len(self.phases) == 0:
            self.rep_count += 1
            rep_msg = Int32()
            rep_msg.data = self.rep_count
            self.pub_rep.publish(rep_msg)

        # 3) 生理数据
        hr = 120.0 + random.uniform(-8.0, 10.0)
        spo2 = 98.0 + random.uniform(-0.8, 0.5)
        loss = random.uniform(0.0, 1.8)

        if self.mode == 'strict':
            hr += random.uniform(5.0, 12.0)
            spo2 -= random.uniform(0.2, 0.8)
            loss += random.uniform(0.3, 1.2)

        hr_msg = Float32()
        hr_msg.data = float(max(70.0, min(180.0, hr)))
        self.pub_hr.publish(hr_msg)

        spo2_msg = Float32()
        spo2_msg.data = float(max(92.0, min(100.0, spo2)))
        self.pub_spo2.publish(spo2_msg)

        loss_msg = Float32()
        loss_msg.data = float(max(0.0, min(15.0, loss)))
        self.pub_loss.publish(loss_msg)

        # 4) 错误动作（概率触发）
        err_probability = 0.12 if self.mode == 'normal' else 0.25
        if random.random() < err_probability:
            err_msg = String()
            err_msg.data = json.dumps(
                {
                    'code': 1001,
                    'msg': random.choice(['膝盖内扣', '背部前倾', '下蹲深度不足']),
                    'level': 'warn',
                },
                ensure_ascii=False,
            )
            self.pub_error.publish(err_msg)

        # 5) 大模型建议
        coach_msg = String()
        coach_msg.data = random.choice(
            [
                '保持核心收紧，注意呼吸节奏。',
                '动作不错，起身时重心保持稳定。',
                '如果膝盖压力大，请适当减小幅度。',
                '节奏偏快，建议稍微放慢。',
            ]
        )
        self.pub_llm.publish(coach_msg)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='ROS mock 数据注入脚本')
    parser.add_argument('--hz', type=float, default=2.0, help='发布频率(Hz)，默认2')
    parser.add_argument(
        '--mode',
        type=str,
        default='normal',
        choices=['normal', 'strict'],
        help='注入模式：normal(常规) / strict(更严格更容易报错)',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    rclpy.init()
    node = MockRosInjector(hz=args.hz, mode=args.mode)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
