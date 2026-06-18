"""
日志管理模块 - 支持审计日志上下文
"""
import os
import logging
import socket
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any
from contextvars import ContextVar
from datetime import datetime

from .config import get_config

audit_id_ctx: ContextVar[str] = ContextVar('audit_id', default='')
current_user_ctx: ContextVar[str] = ContextVar('current_user', default='system')
operation_ctx: ContextVar[str] = ContextVar('operation', default='')


class AuditContextFilter(logging.Filter):
    """审计上下文过滤器，注入审计ID、用户、操作类型"""
    
    def filter(self, record):
        record.audit_id = audit_id_ctx.get() or 'N/A'
        record.user = current_user_ctx.get() or 'system'
        record.operation = operation_ctx.get() or 'N/A'
        return True


class LoggerManager:
    """日志管理器"""
    
    _loggers: Dict[str, logging.Logger] = {}
    
    @classmethod
    def get_logger(cls, name: str = 'mes_ops') -> logging.Logger:
        """获取日志实例"""
        if name in cls._loggers:
            return cls._loggers[name]
        
        config = get_config()
        log_dir = config.get('logging.log_dir', 'logs')
        log_level = config.get('logging.level', 'INFO')
        max_bytes = config.get('logging.max_bytes', 10 * 1024 * 1024)
        backup_count = config.get('logging.backup_count', 20)
        log_format = config.get('logging.format', 
            '%(asctime)s - %(name)s - %(levelname)s - %(audit_id)s - %(user)s - %(operation)s - %(message)s')
        
        os.makedirs(log_dir, exist_ok=True)
        
        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, log_level.upper()))
        logger.propagate = False
        
        if not logger.handlers:
            file_handler = RotatingFileHandler(
                filename=os.path.join(log_dir, f'{name}.log'),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8'
            )
            
            console_handler = logging.StreamHandler()
            
            formatter = logging.Formatter(log_format)
            file_handler.setFormatter(formatter)
            console_handler.setFormatter(formatter)
            
            audit_filter = AuditContextFilter()
            file_handler.addFilter(audit_filter)
            console_handler.addFilter(audit_filter)
            
            logger.addHandler(file_handler)
            logger.addHandler(console_handler)
        
        cls._loggers[name] = logger
        return logger


def set_audit_context(audit_id: str = None, user: str = None, operation: str = None) -> None:
    """设置审计上下文"""
    if audit_id:
        audit_id_ctx.set(audit_id)
    if user:
        current_user_ctx.set(user)
    if operation:
        operation_ctx.set(operation)


def clear_audit_context() -> None:
    """清除审计上下文"""
    audit_id_ctx.set('')
    current_user_ctx.set('system')
    operation_ctx.set('')


def get_host_ip() -> str:
    """获取主机IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def generate_audit_id() -> str:
    """生成审计ID"""
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    return f'AUDIT-{timestamp}-{os.urandom(4).hex().upper()}'


def get_logger(name: str = 'mes_ops') -> logging.Logger:
    """便捷获取日志器"""
    return LoggerManager.get_logger(name)
