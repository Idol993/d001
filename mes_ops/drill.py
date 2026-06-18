"""
应急演练模块
支持IT运维团队手动发起产线停机故障应急演练
"""
import os
import json
import time
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime

from .database import get_db
from .logger import get_logger
from .config import get_config
from .constants import DrillStatus, OperationType, FallbackMode, DEFAULT_PRODUCTION_LINES
from .audit import audit_operation, get_audit_logger
from .notification import get_notification_service

logger = get_logger(__name__)


class DrillScenario:
    """演练场景定义"""
    
    SCENARIOS = {
        'data_collection_crash': {
            'name': '数据采集崩溃演练',
            'description': '模拟PLC数据采集服务崩溃，验证自动回退和人工兜底流程',
            'duration_minutes': 30,
            'steps': [
                '模拟PLC数据采集服务中断',
                '监控系统检测到数据采集延迟异常',
                '触发自动版本回退流程',
                '验证边缘网关本地缓存功能',
                '启动人工线下生产兜底流程',
                '模拟故障修复，恢复系统服务',
                '验证数据断点续传功能',
                '恢复产线自动生产'
            ]
        },
        'mes_system_crash': {
            'name': 'MES系统崩溃演练',
            'description': '模拟MES系统完全崩溃，验证数据兜底和恢复流程',
            'duration_minutes': 45,
            'steps': [
                '模拟MES主服务进程崩溃',
                '验证本地SQLite应急数据库启用',
                '检查工单数据本地写入功能',
                '模拟MES服务重启',
                '验证本地数据自动同步功能',
                '检查数据一致性',
                '恢复正常生产'
            ]
        },
        'network_outage': {
            'name': '车间网络中断演练',
            'description': '模拟车间与总部网络中断，验证车间独立运行模式',
            'duration_minutes': 60,
            'steps': [
                '模拟车间网络断开',
                '验证车间级独立运行模式启用',
                '检查本地生产调度功能',
                '模拟网络恢复',
                '验证双向数据同步',
                '检查数据冲突处理',
                '恢复联网模式'
            ]
        },
        'version_rollback_failure': {
            'name': '版本回滚失败演练',
            'description': '模拟自动回滚失败，验证人工干预流程',
            'duration_minutes': 40,
            'steps': [
                '模拟新版本发布后指标异常',
                '触发自动回滚流程',
                '模拟回滚操作失败',
                '验证告警升级机制',
                '执行人工手动回滚',
                '验证产线锁定功能',
                '故障排查与修复验证',
                '恢复生产'
            ]
        }
    }
    
    @classmethod
    def get_scenario(cls, scenario_type: str) -> Optional[Dict[str, Any]]:
        """获取演练场景"""
        return cls.SCENARIOS.get(scenario_type)
    
    @classmethod
    def list_scenarios(cls) -> List[Dict[str, Any]]:
        """列出所有演练场景"""
        return [
            {'type': key, 'name': value['name'], 'description': value['description']}
            for key, value in cls.SCENARIOS.items()
        ]


class EmergencyDrillManager:
    """应急演练管理器"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self.notification = get_notification_service()
        self.active_drills: Dict[str, Dict[str, Any]] = {}
    
    def _generate_drill_id(self) -> str:
        """生成演练ID"""
        return f"DRILL-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
    
    @audit_operation(OperationType.EMERGENCY_DRILL, lambda args: args[1])
    def create_drill(self, operator: str, scenario_type: str,
                     drill_name: str = None,
                     target_lines: List[str] = None) -> Dict[str, Any]:
        """
        创建并启动应急演练
        
        Args:
            operator: 操作人
            scenario_type: 演练场景类型
            drill_name: 演练名称（可选）
            target_lines: 目标产线（可选，默认全部产线）
            
        Returns:
            演练信息
        """
        scenario = DrillScenario.get_scenario(scenario_type)
        if not scenario:
            raise ValueError(f"未知的演练场景: {scenario_type}")
        
        drill_id = self._generate_drill_id()
        drill_name = drill_name or scenario['name']
        
        if target_lines is None:
            target_lines = DEFAULT_PRODUCTION_LINES.copy()
        
        started_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        self.db.execute('''
            INSERT INTO emergency_drills 
            (drill_id, drill_name, drill_type, status, trigger_scenario, 
             started_at, operator)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            drill_id, drill_name, scenario_type, DrillStatus.IN_PROGRESS.value,
            json.dumps(scenario, ensure_ascii=False), started_at, operator
        ))
        
        drill_plan = self._generate_drill_plan(scenario, target_lines)
        
        self.active_drills[drill_id] = {
            'drill_id': drill_id,
            'drill_name': drill_name,
            'scenario_type': scenario_type,
            'scenario': scenario,
            'target_lines': target_lines,
            'operator': operator,
            'started_at': started_at,
            'status': DrillStatus.IN_PROGRESS.value,
            'current_step': 0,
            'step_results': [],
            'drill_plan': drill_plan
        }
        
        logger.info(f"应急演练已启动: {drill_id} - {drill_name}, 操作人: {operator}")
        
        self.notification.send_drill_notification(
            drill_id=drill_id,
            drill_name=drill_name,
            status='已启动'
        )
        
        get_audit_logger().log(
            operation_type=OperationType.EMERGENCY_DRILL,
            operator=operator,
            request_params={
                "drill_id": drill_id,
                "scenario_type": scenario_type,
                "target_lines": target_lines
            },
            response_result={"status": "STARTED"},
            status="SUCCESS"
        )
        
        return {
            'drill_id': drill_id,
            'drill_name': drill_name,
            'scenario_type': scenario_type,
            'scenario_name': scenario['name'],
            'description': scenario['description'],
            'target_lines': target_lines,
            'started_at': started_at,
            'status': DrillStatus.IN_PROGRESS.value,
            'drill_plan': drill_plan,
            'estimated_duration_minutes': scenario['duration_minutes']
        }
    
    def _generate_drill_plan(self, scenario: Dict[str, Any],
                             target_lines: List[str]) -> Dict[str, Any]:
        """生成演练方案"""
        steps = []
        for i, step_desc in enumerate(scenario['steps'], 1):
            steps.append({
                'step': i,
                'description': step_desc,
                'status': 'PENDING',
                'result': None,
                'start_time': None,
                'end_time': None,
                'duration_seconds': 0
            })
        
        sim_data = self._generate_simulation_data(target_lines)
        
        return {
            'objective': scenario['description'],
            'scope': {
                'affected_workshops': list(set(line.split('-')[0] for line in target_lines)),
                'affected_lines': target_lines,
                'estimated_impact': '演练环境，不影响实际生产'
            },
            'rollback_plan': {
                'target_version': 'v2.1.0-stable',
                'fallback_mode': FallbackMode.LOCAL_DB.value,
                'recovery_steps': [
                    '停止演练模拟',
                    '恢复产线状态',
                    '清除演练数据',
                    '重启监控服务'
                ]
            },
            'manual_procedure': {
                'materials': [
                    '应急纸质生产单据',
                    '本地Excel数据录入模板',
                    '车间生产调度看板'
                ],
                'roles': [
                    {'role': '生产主管', 'responsibility': '确认切换线下模式，协调生产'},
                    {'role': '质量检验', 'responsibility': '人工检验关键工序'},
                    {'role': '数据录入', 'responsibility': '记录生产数据，系统恢复后补录'},
                    {'role': 'IT运维', 'responsibility': '系统故障排查与恢复'}
                ]
            },
            'simulation_data': sim_data,
            'steps': steps
        }
    
    def _generate_simulation_data(self, target_lines: List[str]) -> Dict[str, Any]:
        """生成模拟数据采集崩溃数据"""
        import random
        
        simulation_data = {
            'plc_data_crash': {
                'start_time': None,
                'crash_duration_seconds': random.randint(120, 300),
                'affected_devices': [
                    f"PLC-{i:02d}" for i in range(1, 5)
                ],
                'error_type': random.choice([
                    'OPC UA连接超时',
                    '数据帧解析错误',
                    '设备响应异常',
                    '网络中断'
                ]),
                'error_rate': round(random.uniform(85, 100), 2)
            },
            'edge_buffer': {
                'buffer_size_mb': 1024,
                'estimated_records': random.randint(5000, 20000),
                'buffer_used_percent': round(random.uniform(5, 30), 2),
                'records_synced': 0
            },
            'manual_records': {
                'expected_count': random.randint(100, 500),
                'template_version': 'v1.0',
                'fields': [
                    '工单号', '产品型号', '生产数量', '不良品数',
                    '操作人员', '设备编号', '工艺参数', '检验结果'
                ]
            }
        }
        
        return simulation_data
    
    @audit_operation(OperationType.EMERGENCY_DRILL, lambda args: args[1])
    def execute_step(self, operator: str, drill_id: str,
                     step_index: int, manual_result: str = None) -> Dict[str, Any]:
        """
        执行演练步骤
        
        Args:
            operator: 操作人
            drill_id: 演练ID
            step_index: 步骤索引（从1开始）
            manual_result: 手动输入的执行结果
            
        Returns:
            步骤执行结果
        """
        if drill_id not in self.active_drills:
            raise ValueError(f"演练不存在或已结束: {drill_id}")
        
        drill = self.active_drills[drill_id]
        steps = drill['drill_plan']['steps']
        
        if step_index < 1 or step_index > len(steps):
            raise ValueError(f"步骤索引超出范围: {step_index}")
        
        step = steps[step_index - 1]
        step['status'] = 'IN_PROGRESS'
        step['start_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        logger.info(f"执行演练步骤: {drill_id} - 步骤{step_index}: {step['description']}")
        
        step_result = self._simulate_step_execution(drill, step_index, manual_result)
        
        step['status'] = 'COMPLETED' if step_result['success'] else 'FAILED'
        step['result'] = step_result
        step['end_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        step['duration_seconds'] = step_result.get('duration', 0)
        
        drill['current_step'] = step_index
        drill['step_results'].append(step_result)
        
        if all(s['status'] == 'COMPLETED' for s in steps):
            self._complete_drill(drill_id, operator)
        
        return {
            'drill_id': drill_id,
            'step': step_index,
            'description': step['description'],
            'status': step['status'],
            'result': step_result,
            'start_time': step['start_time'],
            'end_time': step['end_time'],
            'duration_seconds': step['duration_seconds']
        }
    
    def _simulate_step_execution(self, drill: Dict[str, Any], step_index: int,
                                 manual_result: str = None) -> Dict[str, Any]:
        """模拟演练步骤执行"""
        import random
        
        start_time = time.time()
        scenario_type = drill['scenario_type']
        
        step_actions = {
            1: lambda: {
                'action': '注入故障',
                'details': drill['drill_plan']['simulation_data']['plc_data_crash'],
                'logs_generated': random.randint(50, 200)
            },
            2: lambda: {
                'action': '监控检测',
                'metrics': {
                    'data_collection_latency': random.randint(800, 2000),
                    'error_rate': random.uniform(90, 100)
                },
                'alert_triggered': True,
                'alert_level': 'LEVEL3'
            },
            3: lambda: {
                'action': '自动回滚',
                'rollback_triggered': random.random() > 0.2,
                'rollback_duration': random.randint(30, 120),
                'target_version': drill['drill_plan']['rollback_plan']['target_version'],
                'lines_rolled_back': drill['target_lines'][:random.randint(3, len(drill['target_lines']))]
            },
            4: lambda: {
                'action': '边缘缓存验证',
                'buffer_status': drill['drill_plan']['simulation_data']['edge_buffer'],
                'data_integrity': random.random() > 0.1,
                'records_buffered': random.randint(1000, 5000)
            },
            5: lambda: {
                'action': '人工兜底演练',
                'procedure': drill['drill_plan']['manual_procedure'],
                'records_created': random.randint(50, 200),
                'quality_checks_completed': random.randint(30, 100)
            },
            6: lambda: {
                'action': '故障修复',
                'root_cause_identified': True,
                'fix_applied': True,
                'system_restarted': True,
                'recovery_duration': random.randint(60, 180)
            },
            7: lambda: {
                'action': '数据续传验证',
                'records_synced': random.randint(5000, 15000),
                'sync_conflicts': random.randint(0, 10),
                'conflicts_resolved': True,
                'data_integrity_verified': True
            },
            8: lambda: {
                'action': '恢复生产',
                'lines_restored': drill['target_lines'],
                'auto_production_enabled': True,
                'monitoring_restarted': True
            }
        }
        
        action_func = step_actions.get(step_index, lambda: {'action': '演练步骤'})
        details = action_func()
        
        success = random.random() > 0.15
        duration = int(time.time() - start_time) + random.randint(5, 30)
        
        result = {
            'success': success,
            'duration': duration,
            'details': details,
            'manual_note': manual_result or '自动模拟执行',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        if not success:
            result['failure_reason'] = random.choice([
                '模拟网络延迟过高',
                '模拟数据库连接超时',
                '模拟人工操作耗时超出预期',
                '模拟数据一致性校验失败'
            ])
        
        return result
    
    def _complete_drill(self, drill_id: str, operator: str) -> None:
        """完成演练"""
        drill = self.active_drills[drill_id]
        
        improvements = self._generate_improvements(drill)
        drill_result = {
            'steps_completed': len(drill['step_results']),
            'success_count': sum(1 for r in drill['step_results'] if r['success']),
            'total_duration_minutes': sum(s.get('duration', 0) for s in drill['step_results']) // 60,
            'issues_found': [r['failure_reason'] for r in drill['step_results'] if not r['success']],
            'improvements': improvements
        }
        
        completed_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        self.db.execute('''
            UPDATE emergency_drills 
            SET status = ?, drill_result = ?, improvements = ?, completed_at = ?
            WHERE drill_id = ?
        ''', (
            DrillStatus.COMPLETED.value,
            json.dumps(drill_result, ensure_ascii=False),
            json.dumps(improvements, ensure_ascii=False),
            completed_at, drill_id
        ))
        
        drill['status'] = DrillStatus.COMPLETED.value
        drill['completed_at'] = completed_at
        drill['drill_result'] = drill_result
        drill['improvements'] = improvements
        
        self.notification.send_drill_notification(
            drill_id=drill_id,
            drill_name=drill['drill_name'],
            status='已完成'
        )
        
        logger.info(f"应急演练已完成: {drill_id}, 成功率: {drill_result['success_count']}/{drill_result['steps_completed']}")
    
    def _generate_improvements(self, drill: Dict[str, Any]) -> List[Dict[str, Any]]:
        """生成整改建议"""
        improvements = []
        failures = [r for r in drill['step_results'] if not r['success']]
        
        if failures:
            for i, failure in enumerate(failures, 1):
                improvements.append({
                    'id': i,
                    'issue': failure.get('failure_reason', '演练中发现问题'),
                    'severity': random.choice(['高', '中', '低']),
                    'suggested_action': f"针对{failure.get('failure_reason', '问题')}优化流程和预案",
                    'responsible_role': random.choice(['运维', '开发', '测试', '生产']),
                    'deadline_days': random.randint(7, 30)
                })
        
        improvements.extend([
            {
                'id': len(improvements) + 1,
                'issue': '演练过程记录完整性待加强',
                'severity': '中',
                'suggested_action': '增加演练过程自动录像和数据采集功能',
                'responsible_role': '运维',
                'deadline_days': 14
            },
            {
                'id': len(improvements) + 2,
                'issue': '人工兜底操作熟练度需提升',
                'severity': '高',
                'suggested_action': '定期组织线下操作培训，每季度至少演练1次',
                'responsible_role': '生产',
                'deadline_days': 30
            }
        ])
        
        return improvements
    
    @audit_operation(OperationType.EMERGENCY_DRILL, lambda args: args[1])
    def cancel_drill(self, operator: str, drill_id: str, 
                     reason: str) -> Dict[str, Any]:
        """取消演练"""
        if drill_id not in self.active_drills:
            raise ValueError(f"演练不存在或已结束: {drill_id}")
        
        drill = self.active_drills[drill_id]
        
        self.db.execute('''
            UPDATE emergency_drills 
            SET status = ?, drill_result = ?
            WHERE drill_id = ?
        ''', (
            DrillStatus.CANCELLED.value,
            json.dumps({'cancel_reason': reason}, ensure_ascii=False),
            drill_id
        ))
        
        self._cleanup_drill(drill_id)
        
        logger.info(f"应急演练已取消: {drill_id}, 原因: {reason}")
        
        return {
            'drill_id': drill_id,
            'status': DrillStatus.CANCELLED.value,
            'cancel_reason': reason,
            'cancelled_by': operator
        }
    
    def _cleanup_drill(self, drill_id: str) -> None:
        """清理演练数据，恢复环境"""
        if drill_id in self.active_drills:
            drill = self.active_drills[drill_id]
            
            for line in drill.get('target_lines', []):
                self.db.execute('''
                    UPDATE production_line_status 
                    SET auto_production_enabled = 1, fallback_mode = 'NORMAL',
                        last_heartbeat = ?
                    WHERE line_name = ?
                ''', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), line))
            
            del self.active_drills[drill_id]
    
    def get_drill_status(self, drill_id: str) -> Optional[Dict[str, Any]]:
        """获取演练状态"""
        if drill_id in self.active_drills:
            drill = self.active_drills[drill_id]
            return {
                'drill_id': drill['drill_id'],
                'drill_name': drill['drill_name'],
                'scenario_type': drill['scenario_type'],
                'status': drill['status'],
                'current_step': drill['current_step'],
                'total_steps': len(drill['drill_plan']['steps']),
                'started_at': drill['started_at'],
                'target_lines': drill['target_lines'],
                'step_results': drill['step_results']
            }
        
        return self.db.query_one('''
            SELECT * FROM emergency_drills WHERE drill_id = ?
        ''', (drill_id,))
    
    def list_drills(self, status: DrillStatus = None, 
                    operator: str = None, limit: int = 50) -> List[Dict[str, Any]]:
        """列出演练记录"""
        sql = "SELECT * FROM emergency_drills WHERE 1=1"
        params = []
        
        if status:
            sql += " AND status = ?"
            params.append(status.value)
        if operator:
            sql += " AND operator = ?"
            params.append(operator)
        
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        
        return self.db.query(sql, tuple(params))


def get_drill_manager() -> EmergencyDrillManager:
    """获取应急演练管理器"""
    return EmergencyDrillManager()
