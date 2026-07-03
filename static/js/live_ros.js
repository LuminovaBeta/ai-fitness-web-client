/**
 * 实时监测大屏 - ROS 通信核心逻辑
 * 依赖: roslibjs, eventemitter2
 */

document.addEventListener("DOMContentLoaded", function() {
    console.log("初始化 ROS 通信模块...");

    // ==========================================
    // 1. 建立 ROS WebSocket 连接
    // ==========================================
    // 请确保这里的 IP 地址是运行 rosbridge_server 的 RK3588 的局域网 IP
    const ROS_BRIDGE_URL = 'ws://192.168.123.103:9090'; 
    const ros = new ROSLIB.Ros({ url : ROS_BRIDGE_URL });

    ros.on('connection', function() {
        console.log('✅ 成功连接到 ROS NPU 引擎！');
        // 可选：在这里可以把页面上的某个红点变成绿色，表示连接成功
        writeToTerminal("SUCCESS", "Websocket 已连接至 RK3588 边缘节点。");
    });

    ros.on('error', function(error) {
        console.error('❌ ROS 连接错误: ', error);
        writeToTerminal("ERROR", "连接异常，请检查 rosbridge_server 是否启动。");
    });

    ros.on('close', function() {
        console.warn('⚠ ROS 连接已断开，正在尝试重连...');
        writeToTerminal("WARN", "连接已断开。");
        // 简单的自动重连机制
        setTimeout(() => { ros.connect(ROS_BRIDGE_URL); }, 3000);
    });


    // ==========================================
    // 2. 订阅深蹲状态与动作检测 (FSM)
    // ==========================================

    // 订阅当前深蹲状态
    const squatStateTopic = new ROSLIB.Topic({
        ros : ros,
        name : '/squat/state',
        messageType : 'std_msgs/String'
    });

    squatStateTopic.subscribe(function(message) {
        try {
            const stateData = JSON.parse(message.data);
            // 假设页面中有 id="current-phase" 的元素
            const phaseEl = document.getElementById('current-phase');
            if(phaseEl) phaseEl.innerText = stateData.msg;
        } catch (e) {
            console.error("解析深蹲状态 JSON 失败", e);
        }
    });

    // 订阅动作错误警报
    const squatErrorTopic = new ROSLIB.Topic({
        ros : ros,
        name : '/squat/errors',
        messageType : 'std_msgs/String'
    });
    
    squatErrorTopic.subscribe(function(message) {
        try {
            const errorData = JSON.parse(message.data);
            const warningEl = document.querySelector('.warning-text');
            if(warningEl) {
                // 收到错误时显示，并利用 CSS 动画引起注意
                warningEl.innerHTML = `⚠ 警报：${errorData.msg}`;
                warningEl.style.display = 'inline-block';
                writeToTerminal("WARN", `动作违规: 代码 ${errorData.code} - ${errorData.msg}`);
            }
        } catch (e) {
            console.error("解析错误警报 JSON 失败", e);
        }
    });


    // ==========================================
    // 3. 订阅生理体征数据 (蓝牙传感器)
    // ==========================================
    
    // 订阅心率
    const hrTopic = new ROSLIB.Topic({
        ros : ros,
        name : '/heart_sensor_node/heart_rate',
        messageType : 'std_msgs/Float32'
    });
    hrTopic.subscribe(function(message) {
        document.getElementById('hr-value').innerText = Math.round(message.data);
    });

    // 订阅血氧
    const spo2Topic = new ROSLIB.Topic({
        ros : ros,
        name : '/heart_sensor_node/spo2',
        messageType : 'std_msgs/Float32'
    });
    spo2Topic.subscribe(function(message) {
        document.getElementById('spo2-value').innerText = message.data.toFixed(1);
    });

    // 订阅丢包率
    const packetLossTopic = new ROSLIB.Topic({
        ros : ros,
        name : '/heart_sensor_node/packet_loss',
        messageType : 'std_msgs/Float32'
    });
    packetLossTopic.subscribe(function(message) {
        document.getElementById('packet-loss').innerText = message.data.toFixed(1);
    });


    // ==========================================
    // 4. 订阅大模型回复 (LLM Coach)
    // ==========================================
    const llmReplyTopic = new ROSLIB.Topic({
        ros : ros,
        name : '/llm_coach_reply',
        messageType : 'std_msgs/String'
    });
    
    llmReplyTopic.subscribe(function(message) {
        const aiSuggestionBox = document.getElementById('ai-suggestion-box');
        if(aiSuggestionBox) {
            aiSuggestionBox.innerHTML = `<strong>教练反馈：</strong> ${message.data}`;
            writeToTerminal("INFO", "收到 LLM 模型推理结果。");
        }
    });

    // ==========================================
    // 辅助函数：向极客终端写入日志
    // ==========================================
    function writeToTerminal(level, text) {
        const terminal = document.querySelector('.log-terminal');
        if(!terminal) return;

        const timeString = new Date().toLocaleTimeString('en-GB'); 
        let colorClass = 'info';
        if(level === 'WARN') colorClass = 'warn';
        if(level === 'ERROR') colorClass = 'error';
        if(level === 'SUCCESS') colorClass = 'success';

        terminal.innerHTML += `<div class="log-line"><span class="time">[${timeString}]</span> <span class="${colorClass}">${text}</span></div>`;
        terminal.scrollTop = terminal.scrollHeight; // 自动滚动到底部
    }
});