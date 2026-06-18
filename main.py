"""
汽车零部件工厂MES生产执行系统版本自动化发布、智能回滚运维管理系统
主入口程序 - 包含完整的端到端业务流程演示
"""
import os
import sys
import time
import json
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from mes_ops.config import get_config
from mes_ops.logger import get_logger, set_audit_context, generate_audit_id
from mes_ops.database import get_db
from mes_ops.audit import AuditLogger, OperationType, audit_operation
from mes_ops.constants import (
    RiskLevel, DeploymentStatus, DeploymentStage,
    MonitorMetricType, AlertLevel, FallbackMode,
    WORKSHOP_CONFIG, DEFAULT_PRODUCTION_LINES
)
from mes_ops.pre_check import PreCheckEngine
from mes_ops.approval import ApprovalManager, ApprovalWorkflow
from mes_ops.deployment import VersionManager, GrayDeploymentEngine
from mes_ops.monitor import AutoRollbackMonitor, MonitorMetricsCollector
from mes_ops.notification import NotificationService
from mes_ops.analysis import FaultAnalysisReport, FaultRecoveryManager
from mes_ops.drill import EmergencyDrillManager, DrillScenario
from mes_ops.reporting import WeeklyReportGenerator
from mes_ops.query_export import QueryExportManager
from mes_ops.data_fallback import (
    FallbackManager, DataSourceType, ProductionLineHeartbeat,
    get_fallback_manager
)
from mes_ops.scheduler import TaskScheduler, get_scheduler

logger = get_logger(__name__)


class MESReleaseSystem:
    """MES版本发布运维管理系统主类"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self.audit_logger = AuditLogger()
        
        self.pre_check_engine = PreCheckEngine()
        self.approval_manager = ApprovalManager()
        self.version_manager = VersionManager()
        self.deployment_engine = GrayDeploymentEngine()
        self.monitor = AutoRollbackMonitor()
        self.notification = NotificationService()
        self.fault_analysis = FaultAnalysisReport()
        self.fault_recovery = FaultRecoveryManager()
        self.drill_manager = EmergencyDrillManager()
        self.report_generator = WeeklyReportGenerator()
        self.query_manager = QueryExportManager()
        self.fallback_manager = get_fallback_manager()
        self.scheduler = get_scheduler()
        
        self._running = False
        
        logger.info("=" * 80)
        logger.info("汽车零部件工厂MES生产执行系统运维管理平台")
        logger.info("=" * 80)
    
    def start(self):
        """启动系统服务"""
        logger.info("正在启动系统服务...")
        
        self.fallback_manager.start()
        self.scheduler.start()
        self.monitor.start()
        
        self._running = True
        logger.info("系统服务启动完成")
    
    def stop(self):
        """停止系统服务"""
        logger.info("正在停止系统服务...")
        
        self._running = False
        self.monitor.stop()
        self.scheduler.stop()
        self.fallback_manager.stop()
        
        logger.info("系统服务已停止")
    
    @audit_operation(OperationType.VERSION_DEPLOY, lambda *args, **kwargs: kwargs.get('applicant', 'system'))
    def submit_release_request(self,
                               version: str,
                               risk_level: RiskLevel,
                               applicant: str,
                               department: str,
                               description: str,
                               change_content: str,
                               target_production_lines: List[str] = None,
                               **kwargs) -> str:
        """
        提交版本发布申请
        
        Args:
            version: MES系统版本号
            risk_level: 风险等级 (L1_NORMAL / L2_URGENT)
            applicant: 申请人
            department: 申请部门
            description: 版本描述
            change_content: 变更内容
            target_production_lines: 目标产线列表（默认全部产线）
            
        Returns:
            发布申请ID
        """
        audit_id = generate_audit_id()
        set_audit_context(audit_id=audit_id, user=applicant, operation=OperationType.VERSION_DEPLOY.value)
        
        if target_production_lines is None:
            target_production_lines = DEFAULT_PRODUCTION_LINES
        
        request_id = f"REL_{datetime.now().strftime('%Y%m%d%H%M%S')}_{int(time.time() * 1000)}"
        
        self.db.execute('''
            INSERT INTO release_requests 
            (request_id, version, risk_level, applicant, department, 
             description, change_content, status, target_production_lines)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            request_id, version, risk_level.value, applicant, department,
            description, change_content, DeploymentStatus.PRE_CHECKING.value,
            json.dumps(target_production_lines, ensure_ascii=False)
        ))
        
        self.version_manager.register_version(
            version=version,
            package_path=f"/data/mes/packages/{version}.tar.gz",
            md5_checksum=self._generate_md5(version)
        )
        
        logger.info(f"版本发布申请已提交: {request_id}")
        logger.info(f"  版本号: {version}")
        logger.info(f"  风险等级: {risk_level.value}")
        logger.info(f"  申请人: {applicant}")
        logger.info(f"  目标产线: {len(target_production_lines)} 条")
        
        self.notification.send_notification(
            alert_level=AlertLevel.LEVEL1,
            title=f"新版本发布申请已提交",
            content=f"版本【{version}】发布申请已提交，等待前置校验\n"
                   f"申请人: {applicant}\n"
                   f"风险等级: {risk_level.value}\n"
                   f"申请时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            channels=['email', 'wechat']
        )
        
        return request_id
    
    def _generate_md5(self, version: str) -> str:
        """生成MD5校验和（模拟）"""
        import hashlib
        data = f"{version}_{datetime.now().isoformat()}"
        return hashlib.md5(data.encode('utf-8')).hexdigest()
    
    @audit_operation(OperationType.VERSION_DEPLOY, lambda *args, **kwargs: 'system')
    def run_pre_check(self, request_id: str, **kwargs) -> bool:
        """
        执行前置校验
        
        Args:
            request_id: 发布申请ID
            
        Returns:
            校验是否通过
        """
        request = self.db.query_one('''
            SELECT * FROM release_requests WHERE request_id = ?
        ''', (request_id,))
        
        if not request:
            raise ValueError(f"未找到发布申请: {request_id}")
        
        logger.info(f"开始执行前置校验: {request_id}")
        
        check_result = self.pre_check_engine.run_all_checks(request_id)
        all_passed = all(r['passed'] for r in check_result.values())
        
        if all_passed:
            self.db.execute('''
                UPDATE release_requests 
                SET status = ?, pre_check_result = ?
                WHERE request_id = ?
            ''', (
                DeploymentStatus.APPROVING.value,
                json.dumps(check_result, ensure_ascii=False),
                request_id
            ))
            
            self._create_approval_workflow(request_id, RiskLevel(request['risk_level']))
            
            logger.info(f"前置校验全部通过，已进入审批流程")
        else:
            failed_items = [k for k, v in check_result.items() if not v['passed']]
            self.db.execute('''
                UPDATE release_requests 
                SET status = ?, pre_check_result = ?
                WHERE request_id = ?
            ''', (
                DeploymentStatus.PRE_CHECK_FAILED.value,
                json.dumps(check_result, ensure_ascii=False),
                request_id
            ))
            
            logger.error(f"前置校验未通过，失败项: {failed_items}")
            
            self.notification.send_notification(
                alert_level=AlertLevel.LEVEL2,
                title=f"版本发布前置校验失败",
                content=f"版本【{request['version']}】前置校验未通过\n"
                       f"失败项: {', '.join(failed_items)}\n"
                       f"申请ID: {request_id}",
                channels=['email', 'wechat']
            )
        
        return all_passed
    
    def _create_approval_workflow(self, request_id: str, risk_level: RiskLevel):
        """创建审批流程"""
        workflow = self.approval_manager.create_workflow(
            request_id=request_id,
            risk_level=risk_level
        )
        
        logger.info(f"已创建审批流程，需要 {len(workflow.approvers)} 人审批")
        for approver in workflow.approvers:
            logger.info(f"  - {approver['role']}: {approver['name']}")
        
        self.notification.send_notification(
            alert_level=AlertLevel.LEVEL1,
            title=f"版本发布待审批",
            content=f"新版本发布申请待审批，请相关负责人及时处理\n"
                   f"申请ID: {request_id}\n"
                   f"审批人员: {', '.join([a['name'] for a in workflow.approvers])}",
            channels=['email', 'wechat']
        )
    
    @audit_operation(OperationType.MANUAL_APPROVAL, lambda *args, **kwargs: kwargs.get('approver_name', 'system'))
    def approve_release(self,
                        request_id: str,
                        approver_role: str,
                        approver_name: str,
                        approved: bool,
                        comment: str = "",
                        **kwargs) -> bool:
        """
        审批版本发布
        
        Args:
            request_id: 发布申请ID
            approver_role: 审批人角色
            approver_name: 审批人姓名
            approved: 是否通过
            comment: 审批意见
            
        Returns:
            审批是否完成（全部通过或被拒绝）
        """
        audit_id = generate_audit_id()
        set_audit_context(audit_id=audit_id, user=approver_name, operation=OperationType.MANUAL_APPROVAL.value)
        
        logger.info(f"审批操作: {approver_name} ({approver_role}) 对 {request_id} 审批 {'通过' if approved else '拒绝'}")
        
        workflow = self.approval_manager.get_workflow(request_id)
        if not workflow:
            raise ValueError(f"未找到审批流程: {request_id}")
        
        is_completed = workflow.approve(approver_role, approver_name, approved, comment)
        
        if is_completed:
            request = self.db.query_one('''
                SELECT * FROM release_requests WHERE request_id = ?
            ''', (request_id,))
            
            if workflow.is_approved():
                self.db.execute('''
                    UPDATE release_requests SET status = ? WHERE request_id = ?
                ''', (DeploymentStatus.DEPLOYING.value, request_id))
                
                logger.info(f"审批全部通过，开始灰度部署: {request_id}")
                
                self.notification.send_notification(
                    alert_level=AlertLevel.LEVEL1,
                    title=f"版本发布审批通过",
                    content=f"版本【{request['version']}】审批已全部通过，即将开始灰度部署\n"
                           f"申请ID: {request_id}",
                    channels=['email', 'wechat']
                )
                
                self._start_gray_deployment(request_id)
            else:
                self.db.execute('''
                    UPDATE release_requests SET status = ? WHERE request_id = ?
                ''', (DeploymentStatus.APPROVAL_REJECTED.value, request_id))
                
                logger.warning(f"审批被拒绝: {request_id}")
                
                self.notification.send_notification(
                    alert_level=AlertLevel.LEVEL2,
                    title=f"版本发布审批被拒绝",
                    content=f"版本【{request['version']}】审批被拒绝\n"
                           f"申请ID: {request_id}\n"
                           f"拒绝人: {approver_name}",
                    channels=['email', 'wechat']
                )
        
        return is_completed
    
    def _start_gray_deployment(self, request_id: str):
        """开始灰度部署"""
        request = self.db.query_one('''
            SELECT * FROM release_requests WHERE request_id = ?
        ''', (request_id,))
        
        if not request:
            raise ValueError(f"未找到发布申请: {request_id}")
        
        target_lines = json.loads(request['target_production_lines'])
        
        self.deployment_engine.start_deployment(
            request_id=request_id,
            version=request['version'],
            target_production_lines=target_lines
        )
        
        logger.info(f"灰度部署已启动，将分4阶段部署到 {len(target_lines)} 条产线")
    
    def deploy_to_next_stage(self, request_id: str) -> DeploymentStage:
        """
        部署到下一阶段
        
        Args:
            request_id: 发布申请ID
            
        Returns:
            当前部署阶段
        """
        request = self.db.query_one('''
            SELECT * FROM release_requests WHERE request_id = ?
        ''', (request_id,))
        
        if not request:
            raise ValueError(f"未找到发布申请: {request_id}")
        
        current_stage = self.deployment_engine.get_current_stage(request_id)
        logger.info(f"当前部署阶段: {current_stage.name if current_stage else '未开始'}")
        
        next_stage = self.deployment_engine.deploy_to_next_stage(request_id)
        
        if next_stage:
            logger.info(f"已推进到阶段: {next_stage.name}")
            
            if next_stage == DeploymentStage.FULL:
                self.db.execute('''
                    UPDATE release_requests SET status = ? WHERE request_id = ?
                ''', (DeploymentStatus.FULL_DEPLOYED.value, request_id))
                
                logger.info(f"全量部署完成，开始启动监控: {request_id}")
                
                self.monitor.monitor_request(request_id)
                
                self.notification.send_notification(
                    alert_level=AlertLevel.LEVEL1,
                    title=f"版本全量部署完成",
                    content=f"版本【{request['version']}】已完成全量部署\n"
                           f"申请ID: {request_id}\n"
                           f"实时监控已启动，每5分钟检查一次关键指标",
                    channels=['email', 'wechat']
                )
        
        return next_stage
    
    def simulate_monitor_data(self, request_id: str, 
                              error_rate: float = 0.01,
                              latency: float = 150,
                              anomalies: int = 1) -> Dict[str, Any]:
        """
        模拟监控数据（用于演示）
        
        Args:
            request_id: 发布申请ID
            error_rate: 工单报错率
            latency: 数据采集延迟(ms)
            anomalies: 工艺参数异常次数
            
        Returns:
            监控指标结果
        """
        collector = MonitorMetricsCollector()
        
        request = self.db.query_one('''
            SELECT * FROM release_requests WHERE request_id = ?
        ''', (request_id,))
        
        if not request:
            raise ValueError(f"未找到发布申请: {request_id}")
        
        target_lines = json.loads(request['target_production_lines'])
        
        results = {}
        for line in target_lines[:3]:
            metrics = collector.simulate_metrics(
                request_id=request_id,
                production_line=line,
                error_rate=error_rate,
                latency=latency,
                anomalies=anomalies
            )
            results[line] = metrics
        
        logger.info(f"已模拟监控数据: {len(results)} 条产线")
        return results
    
    def trigger_rollback(self, request_id: str, reason: str) -> Dict[str, Any]:
        """
        触发版本回滚
        
        Args:
            request_id: 发布申请ID
            reason: 回滚原因
            
        Returns:
            回滚结果
        """
        logger.warning(f"触发版本回滚: {request_id}, 原因: {reason}")
        
        request = self.db.query_one('''
            SELECT * FROM release_requests WHERE request_id = ?
        ''', (request_id,))
        
        if not request:
            raise ValueError(f"未找到发布申请: {request_id}")
        
        target_lines = json.loads(request['target_production_lines'])
        
        rollback_result = self.deployment_engine.rollback(
            request_id=request_id,
            reason=reason,
            affected_lines=target_lines
        )
        
        report = self.fault_analysis.generate_report(
            request_id=request_id,
            rollback_record=rollback_result
        )
        
        report_path = self.fault_analysis.save_report(report)
        
        logger.info(f"故障分析报告已生成: {report_path}")
        
        self.fault_recovery.lock_production_lines(target_lines, reason)
        
        report_content = self.fault_analysis.format_report_for_notification(report)
        self.notification.send_notification(
            alert_level=AlertLevel.LEVEL3,
            title=f"【紧急】MES系统版本自动回滚告警",
            content=report_content,
            channels=['email', 'wechat', 'dingtalk']
        )
        
        return {
            'rollback_result': rollback_result,
            'fault_report': report,
            'report_path': report_path
        }
    
    def recover_production_line(self, production_line: str, operator: str) -> bool:
        """
        恢复产线生产
        
        Args:
            production_line: 产线名称
            operator: 操作人
            
        Returns:
            是否恢复成功
        """
        audit_id = generate_audit_id()
        set_audit_context(audit_id=audit_id, user=operator, operation=OperationType.PERMISSION_CHANGE.value)
        
        logger.info(f"恢复产线生产: {production_line}, 操作人: {operator}")
        
        is_verified = self.fault_recovery.verify_fix_completed(production_line)
        
        if is_verified:
            self.fault_recovery.restore_production_line(production_line, operator)
            self.fallback_manager.restore_normal_mode(production_line, operator)
            logger.info(f"产线 {production_line} 已恢复正常生产")
            return True
        else:
            logger.warning(f"产线 {production_line} 故障修复校验未通过")
            return False
    
    def start_emergency_drill(self, drill_type: str, operator: str) -> Dict[str, Any]:
        """
        启动应急演练
        
        Args:
            drill_type: 演练类型
            operator: 操作人
            
        Returns:
            演练结果
        """
        audit_id = generate_audit_id()
        set_audit_context(audit_id=audit_id, user=operator, operation=OperationType.EMERGENCY_DRILL.value)
        
        logger.info(f"启动应急演练: {drill_type}, 操作人: {operator}")
        
        scenario = self.drill_manager.create_scenario(
            drill_type=drill_type,
            operator=operator,
            target_lines=DEFAULT_PRODUCTION_LINES[:3]
        )
        
        drill_result = self.drill_manager.execute_drill(scenario)
        
        logger.info(f"应急演练完成: {scenario.drill_id}")
        logger.info(f"  演练名称: {scenario.name}")
        logger.info(f"  演练状态: {drill_result['status']}")
        logger.info(f"  发现问题: {len(drill_result.get('issues', []))} 个")
        logger.info(f"  整改建议: {len(drill_result.get('improvements', []))} 条")
        
        return {
            'scenario': scenario,
            'result': drill_result
        }
    
    def run_weekly_report_task(self) -> Dict[str, Any]:
        """
        手动执行周度报表生成任务
        
        Returns:
            报表生成结果
        """
        logger.info("手动执行周度报表生成任务")
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        report_id, pdf_path, excel_path = self.report_generator.generate_weekly_report(
            start_date=start_date,
            end_date=end_date
        )
        
        result = {
            'report_id': report_id,
            'report_period': f"{start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}",
            'pdf_path': pdf_path,
            'excel_path': excel_path
        }
        
        logger.info(f"周度报表生成完成: {report_id}")
        logger.info(f"  PDF: {pdf_path}")
        logger.info(f"  Excel: {excel_path}")
        
        return result
    
    def query_release_records(self, **filters) -> List[Dict[str, Any]]:
        """
        查询发布记录
        
        Args:
            **filters: 查询条件
            
        Returns:
            发布记录列表
        """
        records = self.query_manager.query_release_records(**filters)
        logger.info(f"查询到 {len(records)} 条发布记录")
        return records
    
    def export_records(self, export_type: str, output_path: str,
                       start_date: datetime = None, end_date: datetime = None) -> str:
        """
        导出记录
        
        Args:
            export_type: 导出类型
            output_path: 输出路径
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            导出文件路径
        """
        if start_date is None:
            start_date = datetime.now() - timedelta(days=30)
        if end_date is None:
            end_date = datetime.now()
        
        file_path = self.query_manager.export_to_excel(
            export_type=export_type,
            output_path=output_path,
            start_date=start_date,
            end_date=end_date
        )
        
        logger.info(f"记录已导出: {file_path}")
        return file_path
    
    def test_data_fallback(self) -> Dict[str, Any]:
        """
        测试数据兜底功能
        
        Returns:
            测试结果
        """
        logger.info("开始测试数据兜底功能...")
        
        test_line = DEFAULT_PRODUCTION_LINES[0]
        
        self.fallback_manager.manual_mode_entry(test_line, "test_operator")
        
        for i in range(5):
            self.fallback_manager.write_data(
                source=DataSourceType.PLC_DEVICE,
                production_line=test_line,
                data={
                    'timestamp': datetime.now().isoformat(),
                    'machine_id': f'PLC_{i:03d}',
                    'temperature': 25.0 + i,
                    'pressure': 1.0 + i * 0.1,
                    'status': 'running'
                }
            )
        
        for i in range(3):
            self.fallback_manager.record_manual_production(
                production_line=test_line,
                operator="test_worker_001",
                operation_type="加工",
                work_order=f"WO{datetime.now().strftime('%Y%m%d')}{i:04d}",
                material_batch=f"MAT202401{i:02d}",
                quantity=50,
                quality_result="合格" if i % 2 == 0 else "不合格",
                remarks=f"人工生产测试 {i}"
            )
        
        status = self.fallback_manager.get_system_status()
        
        self.fallback_manager.restore_normal_mode(test_line, "test_operator")
        
        report = self.fallback_manager.generate_fallback_report(
            start_date=datetime.now() - timedelta(hours=1),
            end_date=datetime.now()
        )
        
        return {
            'system_status': status,
            'fallback_report': report
        }
    
    def demo_end_to_end_workflow(self):
        """
        演示完整的端到端业务流程
        """
        print("\n" + "=" * 80)
        print("【演示】MES系统版本自动化发布完整流程")
        print("=" * 80)
        
        print("\n1. 提交版本发布申请")
        print("-" * 40)
        request_id = self.submit_release_request(
            version="MES_V2.5.1",
            risk_level=RiskLevel.L1_NORMAL,
            applicant="张三",
            department="研发部",
            description="生产流程优化版本，提升OEE 5%",
            change_content="1. 优化工单调度算法\n2. 新增质量统计报表\n3. 修复PLC数据采集延迟问题",
            target_production_lines=DEFAULT_PRODUCTION_LINES[:6]
        )
        print(f"   申请ID: {request_id}")
        time.sleep(1)
        
        print("\n2. 执行前置校验")
        print("-" * 40)
        check_passed = self.run_pre_check(request_id)
        print(f"   校验结果: {'通过' if check_passed else '失败'}")
        time.sleep(1)
        
        if not check_passed:
            print("   前置校验失败，流程终止")
            return
        
        print("\n3. 分级审批流程")
        print("-" * 40)
        approvers = [
            ('production_manager', '李四'),
            ('quality_manager', '王五'),
            ('ops_manager', '赵六')
        ]
        
        for role, name in approvers:
            print(f"   {name} ({role}) 审批中...")
            is_completed = self.approve_release(
                request_id=request_id,
                approver_role=role,
                approver_name=name,
                approved=True,
                comment="同意发布"
            )
            time.sleep(0.5)
        
        print("   审批全部通过")
        time.sleep(1)
        
        print("\n4. 灰度部署（4阶段）")
        print("-" * 40)
        for i in range(4):
            stage = self.deploy_to_next_stage(request_id)
            if stage:
                print(f"   阶段 {stage.value}: {stage.name} 部署完成")
                if i < 3:
                    print(f"   观察期 {[30, 60, 120][i]} 分钟...")
                    time.sleep(0.5)
        time.sleep(1)
        
        print("\n5. 启动实时监控")
        print("-" * 40)
        monitor_result = self.simulate_monitor_data(
            request_id=request_id,
            error_rate=0.01,
            latency=120,
            anomalies=1
        )
        print(f"   已收集 {len(monitor_result)} 条产线监控数据")
        print("   指标状态: 正常 (报错率1.0%, 延迟120ms, 异常1次)")
        time.sleep(1)
        
        print("\n6. 模拟异常触发自动回滚")
        print("-" * 40)
        print("   模拟监控到异常指标 (报错率 5.2%, 延迟 850ms, 异常 8次)...")
        rollback_result = self.trigger_rollback(
            request_id=request_id,
            reason="监控指标超过阈值，触发自动回滚"
        )
        print(f"   回滚完成，不良品预估: {rollback_result['rollback_result'].get('estimated_defect_count', 0)} 件")
        print(f"   根因分析: {rollback_result['fault_report'].get('root_cause_analysis', {}).get('primary_cause', '未知')}")
        time.sleep(1)
        
        print("\n7. 故障修复与产线恢复")
        print("-" * 40)
        print("   技术团队修复故障中...")
        time.sleep(1)
        recovered = self.recover_production_line(
            production_line=DEFAULT_PRODUCTION_LINES[0],
            operator="运维工程师"
        )
        print(f"   产线恢复: {'成功' if recovered else '失败'}")
        time.sleep(1)
        
        print("\n8. 数据兜底功能测试")
        print("-" * 40)
        fallback_result = self.test_data_fallback()
        print(f"   测试完成，共记录 {fallback_result['fallback_report']['total_records']} 条兜底数据")
        time.sleep(1)
        
        print("\n9. 应急演练")
        print("-" * 40)
        drill_result = self.start_emergency_drill(
            drill_type="data_collection_crash",
            operator="IT运维主管"
        )
        print(f"   演练完成，发现 {len(drill_result['result'].get('issues', []))} 个问题")
        time.sleep(1)
        
        print("\n10. 周度报表生成")
        print("-" * 40)
        report_result = self.run_weekly_report_task()
        print(f"   报表ID: {report_result['report_id']}")
        print(f"   PDF路径: {report_result['pdf_path']}")
        print(f"   Excel路径: {report_result['excel_path']}")
        time.sleep(1)
        
        print("\n11. 查询与导出")
        print("-" * 40)
        records = self.query_release_records(
            start_date=datetime.now() - timedelta(days=7),
            status=DeploymentStatus.FULL_DEPLOYED.value
        )
        print(f"   查询到 {len(records)} 条历史记录")
        time.sleep(1)
        
        print("\n" + "=" * 80)
        print("【演示完成】MES系统版本发布全流程演示结束")
        print("=" * 80)
        
        print("\n系统状态:")
        status = self.fallback_manager.get_system_status()
        print(f"  总产线数: {status['total_lines']}")
        print(f"  模式分布: {status['mode_distribution']}")
        print(f"  边缘缓冲使用率: {status['edge_buffer']['usage_percent']:.1f}%")
        
        print("\n定时任务状态:")
        for task in self.scheduler.get_task_status():
            print(f"  - {task['name']}: {'启用' if task['enabled'] else '禁用'} "
                  f"[{task['last_run_status']}]")

    def get_system_status(self) -> Dict[str, Any]:
        """获取系统整体状态"""
        return {
            'fallback_manager': self.fallback_manager.get_system_status(),
            'scheduler_tasks': self.scheduler.get_task_status(),
            'monitor_running': self.monitor._running,
            'timestamp': datetime.now().isoformat()
        }


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="汽车零部件工厂MES生产执行系统运维管理平台"
    )
    
    parser.add_argument(
        'command',
        choices=['demo', 'service', 'status', 'report', 'drill', 'query', 'export'],
        help='执行命令'
    )
    
    parser.add_argument(
        '--drill-type',
        default='data_collection_crash',
        help='应急演练类型'
    )
    
    parser.add_argument(
        '--export-type',
        default='all',
        help='导出类型'
    )
    
    parser.add_argument(
        '--output',
        default='./output',
        help='输出目录'
    )
    
    args = parser.parse_args()
    
    system = MESReleaseSystem()
    
    try:
        if args.command == 'demo':
            system.start()
            system.demo_end_to_end_workflow()
        
        elif args.command == 'service':
            print("启动MES运维管理服务... (Ctrl+C 停止)")
            system.start()
            try:
                while True:
                    time.sleep(60)
            except KeyboardInterrupt:
                print("\n收到停止信号...")
        
        elif args.command == 'status':
            status = system.get_system_status()
            print(json.dumps(status, indent=2, ensure_ascii=False))
        
        elif args.command == 'report':
            system.start()
            result = system.run_weekly_report_task()
            print(json.dumps(result, indent=2, ensure_ascii=False))
        
        elif args.command == 'drill':
            system.start()
            result = system.start_emergency_drill(
                drill_type=args.drill_type,
                operator="cli_user"
            )
            print(json.dumps(result['result'], indent=2, ensure_ascii=False))
        
        elif args.command == 'query':
            records = system.query_release_records(
                start_date=datetime.now() - timedelta(days=30)
            )
            for r in records[:10]:
                print(f"{r['request_id']} | {r['version']} | {r['risk_level']} | {r['status']} | {r['created_at']}")
            print(f"... 共 {len(records)} 条记录")
        
        elif args.command == 'export':
            os.makedirs(args.output, exist_ok=True)
            output_path = os.path.join(args.output, f"export_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx")
            file_path = system.export_records(
                export_type=args.export_type,
                output_path=output_path
            )
            print(f"导出完成: {file_path}")
    
    finally:
        if args.command in ['demo', 'service', 'report', 'drill']:
            system.stop()


if __name__ == '__main__':
    main()
