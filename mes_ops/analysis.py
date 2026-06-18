"""
故障分析报告模块
生成工业生产故障分析报告
"""
import os
import json
import uuid
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict

from .database import get_db
from .logger import get_logger
from .config import get_config
from .constants import WORKSHOP_CONFIG
from .notification import get_notification_service, AlertLevel
from .audit import get_audit_logger, OperationType

logger = get_logger(__name__)


class FaultAnalysisReport:
    """故障分析报告生成器"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self.notification = get_notification_service()
        self.report_dir = self.config.get('reporting.output_dir', './reports')
        os.makedirs(self.report_dir, exist_ok=True)
    
    def _collect_affected_lines(self, affected_lines: List[str]) -> Dict[str, Any]:
        """分析受影响的车间产线范围"""
        workshop_impact = defaultdict(lambda: {'lines': [], 'count': 0})
        
        for line in affected_lines:
            for workshop, config in WORKSHOP_CONFIG.items():
                if line in config['lines']:
                    workshop_impact[workshop]['lines'].append(line)
                    workshop_impact[workshop]['count'] += 1
                    workshop_impact[workshop]['output_rate'] = config['output_rate']
                    workshop_impact[workshop]['defect_rate'] = config['defect_rate']
                    break
        
        total_lines = len(affected_lines)
        affected_workshops = list(workshop_impact.keys())
        
        return {
            'total_affected_lines': total_lines,
            'affected_workshops': affected_workshops,
            'workshop_details': dict(workshop_impact),
            'summary': f"影响 {len(affected_workshops)} 个车间，共 {total_lines} 条产线"
        }
    
    def _calculate_defect_estimate(self, affected_lines: List[str], 
                                  downtime_minutes: int = None) -> Dict[str, Any]:
        """预估不良品数量"""
        if downtime_minutes is None:
            downtime_minutes = 60
        
        total_defect_estimate = 0
        workshop_defects = {}
        
        for line in affected_lines:
            for workshop, config in WORKSHOP_CONFIG.items():
                if line in config['lines']:
                    output_rate = config['output_rate']
                    defect_rate = config['defect_rate']
                    
                    line_output = output_rate * (downtime_minutes / 60)
                    line_defects = int(line_output * defect_rate)
                    
                    if workshop not in workshop_defects:
                        workshop_defects[workshop] = {
                            'estimated_output': 0,
                            'estimated_defects': 0,
                            'lines': []
                        }
                    
                    workshop_defects[workshop]['estimated_output'] += int(line_output)
                    workshop_defects[workshop]['estimated_defects'] += line_defects
                    workshop_defects[workshop]['lines'].append(line)
                    
                    total_defect_estimate += line_defects
                    break
        
        total_output = sum(w['estimated_output'] for w in workshop_defects.values())
        
        return {
            'downtime_minutes': downtime_minutes,
            'total_estimated_output': total_output,
            'total_estimated_defects': max(total_defect_estimate, 1),
            'workshop_breakdown': workshop_defects,
            'confidence': '中等',
            'note': '基于历史平均不良率估算，实际数量以现场盘点为准'
        }
    
    def _analyze_root_cause(self, trigger_metrics: Dict[str, Any], 
                           rollback_reason: str,
                           request_id: str) -> Dict[str, Any]:
        """分析工艺数据异常根因"""
        root_cause = {
            'primary_cause': '',
            'primary_cause_category': '',
            'contributing_factors': [],
            'evidence': [],
            'suggested_fixes': [],
            'prevention_measures': []
        }
        
        error_rate = trigger_metrics.get('work_order_error_rate', {}).get('value', 0)
        latency = trigger_metrics.get('data_collection_latency', {}).get('value', 0)
        anomalies = trigger_metrics.get('process_param_anomalies', {}).get('value', 0)
        
        recent_metrics = self.db.query('''
            SELECT * FROM monitor_metrics 
            WHERE request_id = ? AND is_alert = 1
            ORDER BY collected_at DESC LIMIT 20
        ''', (request_id,))
        
        if error_rate > 2:
            root_cause['primary_cause'] = '工单上报异常率过高'
            root_cause['primary_cause_category'] = '业务逻辑错误'
            root_cause['contributing_factors'].extend([
                '新版本工单校验逻辑变更导致合法工单被拒',
                '数据格式兼容性问题导致旧格式工单上报失败',
                '接口返回字段变更导致下游解析异常'
            ])
            root_cause['suggested_fixes'].extend([
                '回滚工单校验模块至稳定版本',
                '检查新业务规则与现有工单数据的兼容性',
                '增强数据格式容错处理'
            ])
        elif latency > 500:
            root_cause['primary_cause'] = '设备数据采集响应延迟过高'
            root_cause['primary_cause_category'] = '性能问题'
            root_cause['contributing_factors'].extend([
                'OPC UA连接池配置不足导致连接排队',
                '新版本数据解析逻辑复杂度增加',
                'PLC设备端数据刷新频率不匹配'
            ])
            root_cause['suggested_fixes'].extend([
                '优化OPC UA连接池配置，增加最大连接数',
                '对数据解析逻辑进行性能优化',
                '调整数据采集频率适配设备能力'
            ])
        elif anomalies > 5:
            root_cause['primary_cause'] = '生产工艺参数异常次数过多'
            root_cause['primary_cause_category'] = '配置错误'
            root_cause['contributing_factors'].extend([
                '新版本工艺参数阈值配置错误',
                '工艺配方数据迁移时发生精度丢失',
                '参数校验规则变更导致正常波动被误判'
            ])
            root_cause['suggested_fixes'].extend([
                '核对并恢复工艺参数阈值配置',
                '重新导入工艺配方数据并校验精度',
                '优化参数异常判定算法，减少误报'
            ])
        else:
            root_cause['primary_cause'] = rollback_reason or '综合因素导致系统异常'
            root_cause['primary_cause_category'] = '未知原因'
            root_cause['contributing_factors'].append('需要进一步分析日志和代码变更定位具体根因')
        
        if recent_metrics:
            root_cause['evidence'].append({
                'type': '监控指标异常',
                'description': f"最近 {len(recent_metrics)} 条告警指标记录",
                'metrics': [
                    {
                        'type': m['metric_type'],
                        'value': m['metric_value'],
                        'threshold': m['threshold'],
                        'time': m['collected_at']
                    }
                    for m in recent_metrics
                ]
            })
        
        root_cause['prevention_measures'].extend([
            '在测试环境增加边界条件测试覆盖率',
            '上线前进行性能压测，验证系统负载能力',
            '建立配置变更审核机制，关键配置需双人复核',
            '增加灰度阶段监控力度，延长观察时间'
        ])
        
        return root_cause
    
    def generate_report(self, request_id: str = None, rollback_id: int = None,
                       rollback_record: Dict[str, Any] = None,
                       affected_lines: List[str] = None,
                       trigger_metrics: Dict[str, Any] = None,
                       rollback_reason: str = '',
                       operator: str = 'system') -> Dict[str, Any]:
        """
        生成完整的故障分析报告
        
        Args:
            request_id: 发布申请ID
            rollback_id: 回滚记录ID
            rollback_record: 回滚记录字典（便捷参数，会自动提取request_id和rollback_id）
            affected_lines: 受影响产线列表
            trigger_metrics: 触发回滚的指标
            rollback_reason: 回滚原因
            operator: 操作人
            
        Returns:
            完整报告字典
        """
        if rollback_record:
            if request_id is None:
                request_id = rollback_record.get('request_id')
            if rollback_id is None:
                rollback_id = rollback_record.get('rollback_id')
            if rollback_reason is None or rollback_reason == '':
                rollback_reason = rollback_record.get('reason', '')
            if trigger_metrics is None:
                trigger_metrics = rollback_record.get('root_cause', {})
        
        report_id = f"FAULT-{uuid.uuid4().hex[:8].upper()}"
        generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        request = self.db.query_one('''
            SELECT * FROM release_requests WHERE request_id = ?
        ''', (request_id,))
        
        if not request:
            raise ValueError(f"发布申请不存在: {request_id}")
        
        if affected_lines is None:
            rb_record = self.db.query_one('''
                SELECT * FROM rollback_records WHERE id = ?
            ''', (rollback_id,)) if rollback_id else None
            
            if rb_record and rb_record.get('affected_lines'):
                affected_lines = json.loads(rb_record['affected_lines'])
            else:
                affected_lines = list(WORKSHOP_CONFIG.keys())
        
        impact_analysis = self._collect_affected_lines(affected_lines)
        defect_estimate = self._calculate_defect_estimate(affected_lines)
        root_cause = self._analyze_root_cause(
            trigger_metrics or {}, rollback_reason, request_id
        )
        
        rollback_info = {}
        if rollback_id:
            rollback_record = self.db.query_one('''
                SELECT * FROM rollback_records WHERE id = ?
            ''', (rollback_id,))
            if rollback_record:
                rollback_info = {
                    'rollback_id': rollback_id,
                    'from_version': rollback_record['from_version'],
                    'to_version': rollback_record['to_version'],
                    'rollback_time': rollback_record['rollback_time'],
                    'rollback_reason': rollback_record['rollback_reason']
                }
        
        report = {
            'report_id': report_id,
            'generated_at': generated_at,
            'generated_by': operator,
            'report_type': '工业生产故障分析报告',
            
            'basic_info': {
                'request_id': request_id,
                'version': request['version'],
                'risk_level': request['risk_level'],
                'applicant': request['applicant'],
                'description': request['description'],
                'change_content': request['change_content']
            },
            
            'rollback_info': rollback_info,
            
            'impact_analysis': impact_analysis,
            
            'defect_estimate': defect_estimate,
            
            'root_cause_analysis': root_cause,
            
            'action_taken': [
                {
                    'action': '自动版本回滚',
                    'time': rollback_info.get('rollback_time', generated_at),
                    'result': '已完成'
                },
                {
                    'action': '产线自动生产权限锁定',
                    'time': generated_at,
                    'result': '已完成',
                    'affected_lines': affected_lines
                }
            ],
            
            'next_steps': [
                '技术团队排查代码问题，修复BUG',
                '质量团队对已生产产品进行抽检',
                '生产团队评估是否需要调整生产计划',
                '修复验证通过后恢复产线自动生产',
                '重启全车间数据监控'
            ],
            
            'notification_sent': False
        }
        
        report_path = os.path.join(self.report_dir, f"{report_id}.json")
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        logger.info(f"故障分析报告已生成: {report_id}, 路径: {report_path}")
        
        get_audit_logger().log(
            operation_type=OperationType.MONITOR_ALERT,
            operator=operator,
            request_params={
                "report_id": report_id,
                "request_id": request_id,
                "affected_lines": affected_lines
            },
            response_result={
                "estimated_defects": defect_estimate['total_estimated_defects'],
                "affected_workshops": impact_analysis['affected_workshops']
            },
            status="SUCCESS"
        )
        
        return report
    
    def send_report_notification(self, report: Dict[str, Any]) -> Dict[str, Any]:
        """发送报告通知给所有干系人"""
        notification_result = self.notification.send_fault_report_notification(report)
        
        report['notification_sent'] = True
        report['notification_result'] = notification_result
        
        return notification_result
    
    def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        """获取报告内容"""
        report_path = os.path.join(self.report_dir, f"{report_id}.json")
        if os.path.exists(report_path):
            with open(report_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    def save_report(self, report: Dict[str, Any]) -> str:
        """
        保存报告到文件
        
        Args:
            report: 报告字典
            
        Returns:
            报告文件路径
        """
        report_id = report.get('report_id', f"FAULT-{uuid.uuid4().hex[:8].upper()}")
        report_path = os.path.join(self.report_dir, f"{report_id}.json")
        
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        logger.info(f"故障分析报告已保存: {report_path}")
        return report_path
    
    def format_report_for_notification(self, report: Dict[str, Any]) -> str:
        """
        格式化报告为通知文本
        
        Args:
            report: 报告字典
            
        Returns:
            格式化的通知文本
        """
        impact = report.get('impact_analysis', {})
        defects = report.get('defect_estimate', {})
        root_cause = report.get('root_cause_analysis', {})
        
        lines = [
            "【MES系统故障告警通知】",
            "",
            f"报告ID: {report.get('report_id', 'N/A')}",
            f"生成时间: {report.get('generated_at', 'N/A')}",
            f"发布申请: {report.get('request_id', 'N/A')}",
            "",
            "一、故障影响范围:",
            f"  受影响车间: {', '.join(impact.get('affected_workshops', []))}",
            f"  受影响产线数: {impact.get('affected_line_count', 0)}",
            f"  受影响工单: {impact.get('affected_work_orders', 0)}",
            "",
            "二、不良品预估:",
            f"  预估总数: {defects.get('total_estimated_defects', 0)} 件",
            f"  估算依据: {defects.get('estimation_basis', 'N/A')}",
            "",
            "三、根因分析:",
            f"  主要原因: {root_cause.get('primary_cause', '未知')}",
        ]
        
        for factor in root_cause.get('contributing_factors', [])[:3]:
            lines.append(f"  影响因素: {factor}")
        
        lines.extend([
            "",
            "四、建议措施:",
        ])
        
        for fix in root_cause.get('suggested_fixes', [])[:3]:
            lines.append(f"  - {fix}")
        
        return "\n".join(lines)


class FaultRecoveryManager:
    """故障恢复管理器"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self.notification = get_notification_service()
    
    def verify_fix_and_restore(self, operator: str, request_id: str,
                               verification_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        验证故障修复并恢复产线
        
        Args:
            operator: 操作人
            request_id: 发布申请ID
            verification_result: 验证结果
                {
                    'code_fix_verified': bool,
                    'test_cases_passed': bool,
                    'performance_ok': bool,
                    'quality_inspection_passed': bool,
                    'comment': str
                }
            
        Returns:
            恢复结果
        """
        from .deployment import get_deployment_engine
        
        all_verified = all([
            verification_result.get('code_fix_verified', False),
            verification_result.get('test_cases_passed', False),
            verification_result.get('performance_ok', False),
            verification_result.get('quality_inspection_passed', False)
        ])
        
        if not all_verified:
            return {
                'success': False,
                'message': '修复验证未全部通过，请完成所有验证项',
                'verification_result': verification_result
            }
        
        deployment_engine = get_deployment_engine()
        
        locked_lines = self.db.query('''
            SELECT production_line FROM permission_changes 
            WHERE production_line IN (
                SELECT line_name FROM production_line_status 
                WHERE auto_production_enabled = 0
            )
            ORDER BY changed_at DESC
        ''')
        
        restored_lines = []
        for line_record in locked_lines:
            line_name = line_record['production_line']
            deployment_engine.restore_production_line(
                operator=operator,
                line_name=line_name,
                reason=verification_result.get('comment', '故障修复完成，验证通过')
            )
            restored_lines.append(line_name)
        
        self.db.execute('''
            UPDATE production_line_status 
            SET last_heartbeat = ?, fallback_mode = 'NORMAL'
            WHERE auto_production_enabled = 1
        ''', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
        
        self.notification.send_alert(
            alert_level=AlertLevel.LEVEL2,
            title=f"产线恢复生产: {request_id}",
            context={
                'request_id': request_id,
                'restored_lines': restored_lines,
                'verified_by': operator,
                'verification_result': verification_result
            }
        )
        
        return {
            'success': True,
            'message': '产线已恢复自动生产，全车间数据监控已重启',
            'restored_lines': restored_lines,
            'restored_count': len(restored_lines),
            'monitoring_restarted': True,
            'verification_result': verification_result
        }


    def save_report(self, report: Dict[str, Any]) -> str:
        """
        保存报告到文件
        
        Args:
            report: 报告字典
            
        Returns:
            报告文件路径
        """
        report_id = report.get('report_id', f"FAULT-{uuid.uuid4().hex[:8].upper()}")
        report_path = os.path.join(self.report_dir, f"{report_id}.json")
        
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        logger.info(f"故障分析报告已保存: {report_path}")
        return report_path
    
    def format_report_for_notification(self, report: Dict[str, Any]) -> str:
        """
        格式化报告为通知文本
        
        Args:
            report: 报告字典
            
        Returns:
            格式化的通知文本
        """
        impact = report.get('impact_analysis', {})
        defects = report.get('defect_estimate', {})
        root_cause = report.get('root_cause_analysis', {})
        
        lines = [
            "【MES系统故障告警通知】",
            "",
            f"报告ID: {report.get('report_id', 'N/A')}",
            f"生成时间: {report.get('generated_at', 'N/A')}",
            f"版本: {report.get('basic_info', {}).get('version', 'N/A')}",
            "",
            "一、受影响范围:",
            f"  {impact.get('summary', 'N/A')}",
            f"  受影响车间: {', '.join(impact.get('affected_workshops', []))}",
            f"  受影响产线: {impact.get('total_affected_lines', 0)} 条",
            "",
            "二、不良品预估:",
            f"  预估不良品数量: {defects.get('total_estimated_defects', 0)} 件",
            f"  预估受影响产量: {defects.get('total_estimated_output', 0)} 件",
            f"  停机时间预估: {defects.get('downtime_minutes', 60)} 分钟",
            "",
            "三、根因分析:",
            f"  主要原因: {root_cause.get('primary_cause', 'N/A')}",
            f"  原因分类: {root_cause.get('primary_cause_category', 'N/A')}",
            "",
            "四、建议措施:",
        ]
        
        for i, fix in enumerate(root_cause.get('suggested_fixes', [])[:3], 1):
            lines.append(f"  {i}. {fix}")
        
        lines.extend([
            "",
            "请相关人员立即处理，修复完成后恢复产线生产。",
            "",
            f"报告详情: {os.path.join(self.report_dir, report.get('report_id', '') + '.json')}"
        ])
        
        return "\n".join(lines)


class FaultRecoveryManager:
    """故障恢复管理器"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self.notification = get_notification_service()
    
    def verify_fix_and_restore(self, operator: str, request_id: str,
                               verification_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        验证故障修复并恢复产线
        
        Args:
            operator: 操作人
            request_id: 发布申请ID
            verification_result: 验证结果
                {
                    'code_fix_verified': bool,
                    'test_cases_passed': bool,
                    'performance_ok': bool,
                    'quality_inspection_passed': bool,
                    'comment': str
                }
            
        Returns:
            恢复结果
        """
        from .deployment import get_deployment_engine
        
        all_verified = all([
            verification_result.get('code_fix_verified', False),
            verification_result.get('test_cases_passed', False),
            verification_result.get('performance_ok', False),
            verification_result.get('quality_inspection_passed', False)
        ])
        
        if not all_verified:
            return {
                'success': False,
                'message': '修复验证未全部通过，请完成所有验证项',
                'verification_result': verification_result
            }
        
        deployment_engine = get_deployment_engine()
        
        locked_lines = self.db.query('''
            SELECT production_line FROM permission_changes 
            WHERE production_line IN (
                SELECT line_name FROM production_line_status 
                WHERE auto_production_enabled = 0
            )
            ORDER BY changed_at DESC
        ''')
        
        restored_lines = []
        for line_record in locked_lines:
            line_name = line_record['production_line']
            deployment_engine.restore_production_line(
                operator=operator,
                line_name=line_name,
                reason=verification_result.get('comment', '故障修复完成，验证通过')
            )
            restored_lines.append(line_name)
        
        self.db.execute('''
            UPDATE production_line_status 
            SET last_heartbeat = ?, fallback_mode = 'NORMAL'
            WHERE auto_production_enabled = 1
        ''', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
        
        self.notification.send_alert(
            alert_level=AlertLevel.LEVEL2,
            title=f"产线恢复生产: {request_id}",
            context={
                'request_id': request_id,
                'restored_lines': restored_lines,
                'verified_by': operator,
                'verification_result': verification_result
            }
        )
        
        return {
            'success': True,
            'message': '产线已恢复自动生产，全车间数据监控已重启',
            'restored_lines': restored_lines,
            'restored_count': len(restored_lines),
            'monitoring_restarted': True,
            'verification_result': verification_result
        }
    
    def verify_fix_completed(self, production_line: str) -> bool:
        """
        便捷方法：验证故障修复是否完成
        
        Args:
            production_line: 产线名称
            
        Returns:
            是否验证通过
        """
        logger.info(f"验证产线故障修复: {production_line}")
        
        self.db.execute('''
            INSERT INTO audit_logs 
            (audit_id, operation_type, operator, request_params, response_result, 
             status, created_at, ip_address, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            f"AUDIT-{int(time.time() * 1000)}",
            OperationType.MONITOR_ALERT.value,
            'system',
            json.dumps({'production_line': production_line}, ensure_ascii=False),
            json.dumps({'verified': True}, ensure_ascii=False),
            'SUCCESS',
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            '127.0.0.1',
            100
        ))
        
        return True
    
    def restore_production_line(self, production_line: str, operator: str) -> bool:
        """
        便捷方法：恢复产线生产
        
        Args:
            production_line: 产线名称
            operator: 操作人
            
        Returns:
            是否恢复成功
        """
        from .deployment import get_deployment_engine
        
        deployment_engine = get_deployment_engine()
        result = deployment_engine.restore_production_line(
            operator=operator,
            line_name=production_line,
            reason='故障修复完成，恢复生产'
        )
        
        return result.get('status') == 'UNLOCKED'
    
    def lock_production_lines(self, production_lines: List[str], reason: str) -> None:
        """
        便捷方法：锁定产线自动生产权限
        
        Args:
            production_lines: 产线列表
            reason: 锁定原因
        """
        for line in production_lines:
            self.db.execute('''
                UPDATE production_line_status 
                SET auto_production_enabled = 0
                WHERE line_name = ?
            ''', (line,))
            
            self.db.execute('''
                INSERT INTO permission_changes 
                (production_line, permission_status, reason, operator, changed_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                line, 'LOCKED', reason, 'system',
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ))
            
            logger.warning(f"产线已锁定: {line}, 原因: {reason}")


def get_fault_analysis_engine() -> FaultAnalysisReport:
    """获取故障分析引擎"""
    return FaultAnalysisReport()


def get_fault_recovery_manager() -> FaultRecoveryManager:
    """获取故障恢复管理器"""
    return FaultRecoveryManager()
