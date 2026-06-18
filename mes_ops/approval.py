"""
分级审批流程模块
根据版本风险等级自动生成审批流程
"""
import json
import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta

from .database import get_db
from .logger import get_logger
from .config import get_config
from .constants import RiskLevel, ApprovalStatus, OperationType
from .audit import audit_operation, get_audit_logger

logger = get_logger(__name__)


class ApprovalWorkflow:
    """审批工作流"""
    
    def __init__(self, request_id: str, risk_level: RiskLevel = None):
        self.request_id = request_id
        self.config = get_config()
        self.db = get_db()
        
        if risk_level is None:
            req = self.db.query_one('SELECT risk_level FROM release_requests WHERE request_id = ?', (request_id,))
            if req:
                risk_level = RiskLevel(req['risk_level'])
            else:
                raise ValueError(f"未找到发布申请: {request_id}")
        
        self.risk_level = risk_level
        self._load_workflow_config()
    
    def _load_workflow_config(self) -> None:
        """加载审批流程配置"""
        risk_config = self.config.get(f'approval.risk_levels.{self.risk_level.value}')
        if not risk_config:
            raise ValueError(f"未知的风险等级: {self.risk_level}")
        
        self.required_approvers = risk_config.get('required_approvers', [])
        self.timeout_hours = risk_config.get('timeout_hours', 24)
        self.risk_name = risk_config.get('name', '')
        self.risk_description = risk_config.get('description', '')
        
        all_approvers = self.config.get('approval.approvers', {})
        self.approvers_info = {}
        for role in self.required_approvers:
            if role in all_approvers:
                self.approvers_info[role] = all_approvers[role]
    
    def create_approval_tasks(self) -> List[Dict[str, Any]]:
        """创建审批任务"""
        tasks = []
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        for role, info in self.approvers_info.items():
            self.db.execute('''
                INSERT INTO approval_records 
                (request_id, approver_role, approver_name, approval_status, approved_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                self.request_id, role, info['name'], 
                ApprovalStatus.PENDING.value, None
            ))
            
            tasks.append({
                'approver_role': role,
                'approver_name': info['name'],
                'approver_email': info['email'],
                'approver_phone': info['phone'],
                'status': ApprovalStatus.PENDING.value
            })
            
            logger.info(f"已创建审批任务: {self.request_id} -> {info['name']}({role})")
        
        self.db.execute('''
            UPDATE release_requests 
            SET status = ?, updated_at = ?
            WHERE request_id = ?
        ''', (
            'APPROVING',
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            self.request_id
        ))
        
        return tasks
    
    @audit_operation(OperationType.MANUAL_APPROVAL, lambda args: args[2])
    def approve(self, approver_role: str, operator: str, 
                comment: str = None) -> Tuple[bool, str]:
        """
        审批通过
        
        Args:
            approver_role: 审批人角色
            operator: 操作人
            comment: 审批意见
            
        Returns:
            (是否完成全部审批, 状态信息)
        """
        approval_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        self.db.execute('''
            UPDATE approval_records 
            SET approval_status = ?, approval_comment = ?, approved_at = ?
            WHERE request_id = ? AND approver_role = ?
        ''', (
            ApprovalStatus.APPROVED.value, comment, approval_time,
            self.request_id, approver_role
        ))
        
        approver_info = self.approvers_info.get(approver_role, {})
        logger.info(f"审批通过: {self.request_id} -> {approver_info.get('name', approver_role)}")
        
        get_audit_logger().log(
            operation_type=OperationType.MANUAL_APPROVAL,
            operator=operator,
            request_params={
                "request_id": self.request_id,
                "approver_role": approver_role,
                "comment": comment
            },
            response_result={"status": "APPROVED"},
            status="SUCCESS"
        )
        
        all_approved, pending_count, has_rejected = self._check_approval_status()
        
        if has_rejected:
            return True, f"审批已被拒绝，流程终止"
        
        if all_approved:
            self.db.execute('''
                UPDATE release_requests 
                SET status = ?, updated_at = ?
                WHERE request_id = ?
            ''', (
                'APPROVAL_PASSED', approval_time, self.request_id
            ))
            return True, f"所有审批已通过，可以开始部署"
        
        return False, f"审批通过，仍有 {pending_count} 人待审批"
    
    @audit_operation(OperationType.MANUAL_APPROVAL, lambda *args, **kwargs: kwargs.get('operator', args[2] if len(args) > 2 else 'system'))
    def reject(self, approver_role: str, operator: str, 
               comment: str) -> Tuple[bool, str]:
        """
        审批拒绝
        
        Args:
            approver_role: 审批人角色
            operator: 操作人
            comment: 拒绝原因
            
        Returns:
            (是否结束, 状态信息)
        """
        approval_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        self.db.execute('''
            UPDATE approval_records 
            SET approval_status = ?, approval_comment = ?, approved_at = ?
            WHERE request_id = ? AND approver_role = ?
        ''', (
            ApprovalStatus.REJECTED.value, comment, approval_time,
            self.request_id, approver_role
        ))
        
        self.db.execute('''
            UPDATE release_requests 
            SET status = ?, updated_at = ?
            WHERE request_id = ?
        ''', (
            'APPROVAL_REJECTED', approval_time, self.request_id
        ))
        
        approver_info = self.approvers_info.get(approver_role, {})
        logger.warning(f"审批拒绝: {self.request_id} -> {approver_info.get('name', approver_role)}, 原因: {comment}")
        
        get_audit_logger().log(
            operation_type=OperationType.MANUAL_APPROVAL,
            operator=operator,
            request_params={
                "request_id": self.request_id,
                "approver_role": approver_role,
                "comment": comment
            },
            response_result={"status": "REJECTED"},
            status="SUCCESS"
        )
        
        return True, f"审批已拒绝，流程终止"
    
    def _check_approval_status(self) -> Tuple[bool, int, bool]:
        """检查审批状态
        
        Returns:
            (是否全部通过, 待审批数量, 是否被拒绝)
        """
        records = self.db.query('''
            SELECT approver_role, approval_status 
            FROM approval_records 
            WHERE request_id = ?
        ''', (self.request_id,))
        
        approved_count = 0
        pending_count = 0
        has_rejected = False
        
        for record in records:
            if record['approval_status'] == ApprovalStatus.APPROVED.value:
                approved_count += 1
            elif record['approval_status'] == ApprovalStatus.PENDING.value:
                pending_count += 1
            elif record['approval_status'] == ApprovalStatus.REJECTED.value:
                has_rejected = True
        
        all_approved = (approved_count == len(self.required_approvers))
        return all_approved, pending_count, has_rejected
    
    def is_rejected(self) -> bool:
        """检查是否被拒绝"""
        _, _, has_rejected = self._check_approval_status()
        return has_rejected
    
    def check_timeout(self) -> bool:
        """检查审批是否超时"""
        request = self.db.query_one('''
            SELECT created_at FROM release_requests WHERE request_id = ?
        ''', (self.request_id,))
        
        if not request:
            return False
        
        created_at = datetime.strptime(request['created_at'], '%Y-%m-%d %H:%M:%S')
        deadline = created_at + timedelta(hours=self.timeout_hours)
        is_timeout = datetime.now() > deadline
        
        if is_timeout:
            self.db.execute('''
                UPDATE approval_records 
                SET approval_status = ?
                WHERE request_id = ? AND approval_status = ?
            ''', (
                ApprovalStatus.TIMEOUT.value, self.request_id, 
                ApprovalStatus.PENDING.value
            ))
            
            self.db.execute('''
                UPDATE release_requests 
                SET status = ?, updated_at = ?
                WHERE request_id = ?
            ''', (
                'APPROVAL_TIMEOUT', 
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                self.request_id
            ))
            
            logger.warning(f"审批超时: {self.request_id}, 超时时间: {self.timeout_hours}小时")
        
        return is_timeout
    
    def get_approval_progress(self) -> Dict[str, Any]:
        """获取审批进度"""
        records = self.db.query('''
            SELECT ar.*, a.role as approver_role_name
            FROM approval_records ar
            LEFT JOIN approval_config a ON ar.approver_role = a.role
            WHERE ar.request_id = ?
            ORDER BY ar.id
        ''', (self.request_id,))
        
        all_approved, pending_count, has_rejected = self._check_approval_status()
        
        return {
            'request_id': self.request_id,
            'risk_level': self.risk_level.value,
            'risk_name': self.risk_name,
            'total_approvers': len(self.required_approvers),
            'approved_count': sum(1 for r in records if r['approval_status'] == ApprovalStatus.APPROVED.value),
            'pending_count': pending_count,
            'has_rejected': has_rejected,
            'all_approved': all_approved,
            'timeout_hours': self.timeout_hours,
            'approval_details': records
        }
    
    @property
    def approvers(self) -> List[Dict[str, Any]]:
        """获取审批人列表（便捷属性）"""
        approver_list = []
        for role in self.required_approvers:
            if role in self.approvers_info:
                info = self.approvers_info[role]
                approver_list.append({
                    'role': role,
                    'name': info['name'],
                    'email': info.get('email', ''),
                    'phone': info.get('phone', '')
                })
        return approver_list
    
    def approve(self, approver_role: str, approver_name: str = None, 
                approved: bool = True, comment: str = None) -> Tuple[bool, str]:
        """
        兼容方法：审批操作（支持通过/拒绝）
        
        Args:
            approver_role: 审批人角色
            approver_name: 审批人姓名（可选，默认为角色配置的姓名）
            approved: 是否通过（True=通过，False=拒绝）
            comment: 审批意见
            
        Returns:
            (是否完成全部审批, 状态信息)
        """
        operator = approver_name or approver_role
        
        if not approved:
            return self.reject(approver_role, operator, comment or "审批拒绝")
        
        return super().approve(approver_role, operator, comment) if hasattr(super(), 'approve') \
            else self._approve(approver_role, operator, comment)
    
    def _approve(self, approver_role: str, operator: str, comment: str = None) -> Tuple[bool, str]:
        """内部审批通过方法"""
        approval_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        self.db.execute('''
            UPDATE approval_records 
            SET approval_status = ?, approval_comment = ?, approved_at = ?
            WHERE request_id = ? AND approver_role = ?
        ''', (
            ApprovalStatus.APPROVED.value, comment, approval_time,
            self.request_id, approver_role
        ))
        
        approver_info = self.approvers_info.get(approver_role, {})
        logger.info(f"审批通过: {self.request_id} -> {approver_info.get('name', approver_role)}")
        
        get_audit_logger().log(
            operation_type=OperationType.MANUAL_APPROVAL,
            operator=operator,
            request_params={
                "request_id": self.request_id,
                "approver_role": approver_role,
                "comment": comment
            },
            response_result={"status": "APPROVED"},
            status="SUCCESS"
        )
        
        all_approved, pending_count, has_rejected = self._check_approval_status()
        
        if has_rejected:
            return True, f"审批已被拒绝，流程终止"
        
        if all_approved:
            self.db.execute('''
                UPDATE release_requests 
                SET status = ?, updated_at = ?
                WHERE request_id = ?
            ''', (
                'APPROVAL_PASSED', approval_time, self.request_id
            ))
            return True, f"所有审批已通过，可以开始部署"
        
        return False, f"审批通过，仍有 {pending_count} 人待审批"
    
    def is_approved(self) -> bool:
        """检查是否所有审批都已通过"""
        all_approved, _, _ = self._check_approval_status()
        return all_approved


class ApprovalManager:
    """审批管理器"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
    
    def create_release_request(self, version: str, risk_level: RiskLevel,
                               applicant: str, department: str = None,
                               description: str = None, change_content: str = None,
                               target_production_lines: List[str] = None) -> str:
        """
        创建版本发布申请
        
        Returns:
            request_id 发布申请ID
        """
        request_id = f"REL-{datetime.now().strftime('%Y%m%d%H%M%S')}-{int(time.time() * 1000) % 10000:04d}"
        
        target_lines_json = json.dumps(target_production_lines or [], ensure_ascii=False)
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        self.db.execute('''
            INSERT INTO release_requests 
            (request_id, version, risk_level, applicant, department, 
             description, change_content, status, target_production_lines, 
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            request_id, version, risk_level.value, applicant, department,
            description, change_content, 'CREATED', target_lines_json,
            created_at, created_at
        ))
        
        logger.info(f"已创建版本发布申请: {request_id}, 版本: {version}, 风险等级: {risk_level.value}")
        
        get_audit_logger().log(
            operation_type=OperationType.VERSION_DEPLOY,
            operator=applicant,
            request_params={
                "request_id": request_id,
                "version": version,
                "risk_level": risk_level.value,
                "change_content": change_content
            },
            response_result={"status": "CREATED"},
            status="SUCCESS"
        )
        
        return request_id
    
    def start_approval_workflow(self, request_id: str) -> ApprovalWorkflow:
        """启动审批工作流"""
        request = self.db.query_one('''
            SELECT risk_level FROM release_requests WHERE request_id = ?
        ''', (request_id,))
        
        if not request:
            raise ValueError(f"发布申请不存在: {request_id}")
        
        risk_level = RiskLevel(request['risk_level'])
        workflow = ApprovalWorkflow(request_id, risk_level)
        workflow.create_approval_tasks()
        
        return workflow
    
    def get_workflow(self, request_id: str) -> Optional[ApprovalWorkflow]:
        """获取审批工作流"""
        request = self.db.query_one('''
            SELECT risk_level FROM release_requests WHERE request_id = ?
        ''', (request_id,))
        
        if not request:
            return None
        
        return ApprovalWorkflow(request_id, RiskLevel(request['risk_level']))
    
    def create_workflow(self, request_id: str, risk_level: RiskLevel = None) -> ApprovalWorkflow:
        """
        便捷方法：创建审批工作流
        
        Args:
            request_id: 发布申请ID
            risk_level: 风险等级（可选，若未提供则从数据库查询）
            
        Returns:
            ApprovalWorkflow 审批工作流实例
        """
        return self.start_approval_workflow(request_id)
    
    def query_release_requests(self, status: str = None, risk_level: RiskLevel = None,
                               applicant: str = None, start_time: str = None,
                               end_time: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """查询发布申请列表"""
        sql = "SELECT * FROM release_requests WHERE 1=1"
        params = []
        
        if status:
            sql += " AND status = ?"
            params.append(status)
        if risk_level:
            sql += " AND risk_level = ?"
            params.append(risk_level.value)
        if applicant:
            sql += " AND applicant = ?"
            params.append(applicant)
        if start_time:
            sql += " AND created_at >= ?"
            params.append(start_time)
        if end_time:
            sql += " AND created_at <= ?"
            params.append(end_time)
        
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        
        return self.db.query(sql, tuple(params))
    
    def get_request_detail(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取申请详情"""
        request = self.db.query_one('''
            SELECT * FROM release_requests WHERE request_id = ?
        ''', (request_id,))
        
        if not request:
            return None
        
        approvals = self.db.query('''
            SELECT * FROM approval_records WHERE request_id = ? ORDER BY id
        ''', (request_id,))
        
        pre_checks = self.db.query('''
            SELECT * FROM pre_check_records WHERE request_id = ? ORDER BY id
        ''', (request_id,))
        
        deployments = self.db.query('''
            SELECT * FROM deployment_records WHERE request_id = ? ORDER BY id
        ''', (request_id,))
        
        request['approvals'] = approvals
        request['pre_checks'] = pre_checks
        request['deployments'] = deployments
        
        return request


def get_approval_manager() -> ApprovalManager:
    """获取审批管理器实例"""
    return ApprovalManager()
