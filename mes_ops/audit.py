"""
审计日志模块 - 全流程精细化审计追踪
"""
import json
import hashlib
import time
from typing import Dict, Any, Optional, List, Callable
from functools import wraps
from datetime import datetime

from .database import get_db
from .logger import get_logger, get_host_ip, generate_audit_id, set_audit_context
from .constants import OperationType
from .config import get_config

logger = get_logger(__name__)


class AuditLogger:
    """审计日志记录器 - 哈希链式存储确保不可篡改"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_chain()
        return cls._instance
    
    def _init_chain(self) -> None:
        """初始化哈希链，获取最后一条记录的哈希"""
        db = get_db()
        last_record = db.query_one('''
            SELECT current_hash FROM audit_logs 
            ORDER BY id DESC LIMIT 1
        ''')
        self._last_hash = last_record['current_hash'] if last_record else '0' * 64
    
    def _calculate_hash(self, audit_id: str, operation_type: str, operator: str, 
                       request_params: str, timestamp: str) -> str:
        """计算当前记录的哈希值，与前一记录哈希关联"""
        data = f"{self._last_hash}|{audit_id}|{operation_type}|{operator}|{request_params}|{timestamp}"
        return hashlib.sha256(data.encode('utf-8')).hexdigest()
    
    def log(self, operation_type: OperationType, operator: str, 
            request_params: Dict[str, Any] = None, response_result: Dict[str, Any] = None,
            status: str = "SUCCESS", duration_ms: int = 0, ip_address: str = None) -> str:
        """
        记录审计日志
        
        Args:
            operation_type: 操作类型
            operator: 操作人
            request_params: 请求参数
            response_result: 响应结果
            status: 操作状态 SUCCESS/FAILED
            duration_ms: 操作耗时(毫秒)
            ip_address: 操作IP
            
        Returns:
            审计ID
        """
        audit_id = generate_audit_id()
        ip_address = ip_address or get_host_ip()
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        params_json = json.dumps(request_params or {}, ensure_ascii=False)
        result_json = json.dumps(response_result or {}, ensure_ascii=False)
        
        current_hash = self._calculate_hash(
            audit_id, operation_type.value, operator, params_json, timestamp
        )
        
        db = get_db()
        db.execute('''
            INSERT INTO audit_logs 
            (audit_id, operation_type, operator, ip_address, request_params, 
             response_result, status, duration_ms, previous_hash, current_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            audit_id, operation_type.value, operator, ip_address, params_json,
            result_json, status, duration_ms, self._last_hash, current_hash, timestamp
        ))
        
        self._last_hash = current_hash
        
        logger.info(
            f"审计日志已记录: {audit_id} | {operation_type.value} | {operator} | {status} | {duration_ms}ms"
        )
        
        return audit_id
    
    def verify_chain(self) -> bool:
        """验证哈希链完整性，检测日志是否被篡改"""
        db = get_db()
        records = db.query('''
            SELECT audit_id, operation_type, operator, request_params, 
                   previous_hash, current_hash, created_at
            FROM audit_logs ORDER BY id ASC
        ''')
        
        expected_prev_hash = '0' * 64
        
        for record in records:
            timestamp = record['created_at']
            data = f"{expected_prev_hash}|{record['audit_id']}|{record['operation_type']}|{record['operator']}|{record['request_params']}|{timestamp}"
            calculated_hash = hashlib.sha256(data.encode('utf-8')).hexdigest()
            
            if calculated_hash != record['current_hash']:
                logger.error(f"哈希链验证失败，审计记录 {record['audit_id']} 可能被篡改")
                return False
            
            if record['previous_hash'] != expected_prev_hash:
                logger.error(f"哈希链断裂，审计记录 {record['audit_id']} 前序哈希不匹配")
                return False
            
            expected_prev_hash = record['current_hash']
        
        logger.info("审计日志哈希链完整性验证通过")
        return True
    
    def query_logs(self, operation_type: OperationType = None, operator: str = None,
                   start_time: str = None, end_time: str = None,
                   status: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """多条件查询审计日志"""
        db = get_db()
        sql = "SELECT * FROM audit_logs WHERE 1=1"
        params = []
        
        if operation_type:
            sql += " AND operation_type = ?"
            params.append(operation_type.value)
        if operator:
            sql += " AND operator = ?"
            params.append(operator)
        if start_time:
            sql += " AND created_at >= ?"
            params.append(start_time)
        if end_time:
            sql += " AND created_at <= ?"
            params.append(end_time)
        if status:
            sql += " AND status = ?"
            params.append(status)
        
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        
        return db.query(sql, tuple(params))


def audit_operation(operation_type: OperationType, operator_getter: Callable = None):
    """
    审计装饰器，自动记录函数调用的审计日志
    
    Usage:
        @audit_operation(OperationType.VERSION_DEPLOY, operator_getter=lambda args: args[0])
        def deploy_version(user, version):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            
            operator = operator_getter(*args, **kwargs) if operator_getter else "system"
            request_params = {"args": str(args), "kwargs": str(kwargs)}
            
            audit_id = generate_audit_id()
            set_audit_context(audit_id=audit_id, user=operator, operation=operation_type.value)
            
            try:
                result = func(*args, **kwargs)
                duration_ms = int((time.time() - start_time) * 1000)
                
                get_audit_logger().log(
                    operation_type=operation_type,
                    operator=operator,
                    request_params=request_params,
                    response_result={"result": str(result)},
                    status="SUCCESS",
                    duration_ms=duration_ms
                )
                
                return result
            except Exception as e:
                duration_ms = int((time.time() - start_time) * 1000)
                
                get_audit_logger().log(
                    operation_type=operation_type,
                    operator=operator,
                    request_params=request_params,
                    response_result={"error": str(e)},
                    status="FAILED",
                    duration_ms=duration_ms
                )
                raise
            finally:
                from .logger import clear_audit_context
                clear_audit_context()
        
        return wrapper
    return decorator


def get_audit_logger() -> AuditLogger:
    """获取审计日志实例"""
    return AuditLogger()
