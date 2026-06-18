"""
数据库模型与数据访问层
"""
import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from contextlib import contextmanager
import json

from .config import get_config
from .logger import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """数据库管理器"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_database()
        return cls._instance
    
    def _init_database(self) -> None:
        """初始化数据库连接和表结构"""
        config = get_config()
        db_name = config.get('database.name', 'mes_ops.db')
        db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            db_name
        )
        self.db_path = db_path
        self._create_tables()
        logger.info(f"数据库初始化完成: {db_path}")
    
    @contextmanager
    def _get_connection(self):
        """获取数据库连接上下文"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"数据库操作失败: {e}")
            raise
        finally:
            conn.close()
    
    def _create_tables(self) -> None:
        """创建所有数据表"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS release_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT UNIQUE NOT NULL,
                    version TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    applicant TEXT NOT NULL,
                    department TEXT,
                    description TEXT,
                    change_content TEXT,
                    status TEXT NOT NULL,
                    pre_check_result TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    target_production_lines TEXT
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pre_check_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    check_item TEXT NOT NULL,
                    check_result TEXT NOT NULL,
                    check_detail TEXT,
                    check_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (request_id) REFERENCES release_requests(request_id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS approval_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    approver_role TEXT NOT NULL,
                    approver_name TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    approval_comment TEXT,
                    approved_at DATETIME,
                    FOREIGN KEY (request_id) REFERENCES release_requests(request_id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS deployment_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    stage INTEGER NOT NULL,
                    stage_name TEXT NOT NULL,
                    production_lines TEXT,
                    status TEXT NOT NULL,
                    start_time DATETIME,
                    end_time DATETIME,
                    rollback_reason TEXT,
                    FOREIGN KEY (request_id) REFERENCES release_requests(request_id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS monitor_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    metric_type TEXT NOT NULL,
                    metric_value REAL NOT NULL,
                    threshold REAL NOT NULL,
                    is_alert BOOLEAN DEFAULT 0,
                    production_line TEXT,
                    collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (request_id) REFERENCES release_requests(request_id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rollback_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    rollback_reason TEXT NOT NULL,
                    from_version TEXT NOT NULL,
                    to_version TEXT NOT NULL,
                    affected_lines TEXT,
                    trigger_metrics TEXT,
                    rollback_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                    estimated_defect_count INTEGER,
                    root_cause TEXT,
                    FOREIGN KEY (request_id) REFERENCES release_requests(request_id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS permission_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    production_line TEXT NOT NULL,
                    permission_status TEXT NOT NULL,
                    reason TEXT,
                    operator TEXT NOT NULL,
                    changed_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    audit_id TEXT UNIQUE NOT NULL,
                    operation_type TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    ip_address TEXT,
                    request_params TEXT,
                    response_result TEXT,
                    status TEXT NOT NULL,
                    duration_ms INTEGER,
                    previous_hash TEXT,
                    current_hash TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS emergency_drills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    drill_id TEXT UNIQUE NOT NULL,
                    drill_name TEXT NOT NULL,
                    drill_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    trigger_scenario TEXT,
                    drill_result TEXT,
                    improvements TEXT,
                    started_at DATETIME,
                    completed_at DATETIME,
                    operator TEXT NOT NULL
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS version_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT UNIQUE NOT NULL,
                    package_path TEXT NOT NULL,
                    md5_checksum TEXT NOT NULL,
                    is_stable BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS production_line_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    line_name TEXT UNIQUE NOT NULL,
                    is_running BOOLEAN DEFAULT 1,
                    auto_production_enabled BOOLEAN DEFAULT 1,
                    current_version TEXT,
                    fallback_mode TEXT DEFAULT 'NORMAL',
                    last_heartbeat DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS fallback_data_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT UNIQUE NOT NULL,
                    source TEXT NOT NULL,
                    data_content TEXT NOT NULL,
                    is_synced BOOLEAN DEFAULT 0,
                    synced_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS weekly_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id TEXT UNIQUE NOT NULL,
                    report_period TEXT NOT NULL,
                    publish_success_rate REAL,
                    emergency_rollback_count INTEGER,
                    avg_approval_duration REAL,
                    pdf_path TEXT,
                    excel_path TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS notification_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_id TEXT UNIQUE NOT NULL,
                    alert_level TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT,
                    channels TEXT,
                    recipients TEXT,
                    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    status TEXT
                )
            ''')
            
            self._init_production_lines(cursor)
            conn.commit()
    
    def _init_production_lines(self, cursor) -> None:
        """初始化产线状态表"""
        from .constants import DEFAULT_PRODUCTION_LINES
        
        for line in DEFAULT_PRODUCTION_LINES:
            cursor.execute('''
                INSERT OR IGNORE INTO production_line_status 
                (line_name, is_running, auto_production_enabled, fallback_mode)
                VALUES (?, 1, 1, 'NORMAL')
            ''', (line,))
    
    def execute(self, sql: str, params: Tuple = None) -> int:
        """执行SQL并返回lastrowid"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params or ())
            return cursor.lastrowid
    
    def query(self, sql: str, params: Tuple = None) -> List[Dict[str, Any]]:
        """执行查询并返回结果列表"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params or ())
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def query_one(self, sql: str, params: Tuple = None) -> Optional[Dict[str, Any]]:
        """执行查询并返回单行结果"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params or ())
            row = cursor.fetchone()
            return dict(row) if row else None


def get_db() -> DatabaseManager:
    """获取数据库实例"""
    return DatabaseManager()
