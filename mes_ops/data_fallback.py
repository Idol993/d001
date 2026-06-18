"""
产线停机故障数据兜底方案
实现4级兜底模式切换逻辑、数据缓存、断点续传、数据一致性保证
"""
import json
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, asdict
from enum import Enum

from .constants import FallbackMode, WORKSHOP_CONFIG
from .config import get_config
from .logger import get_logger, set_audit_context, generate_audit_id
from .database import get_db
from .audit import AuditLogger, audit_operation, OperationType
from .notification import NotificationService, AlertLevel

logger = get_logger(__name__)


class DataSourceType(str, Enum):
    """数据来源类型"""
    PLC_DEVICE = "plc_device"
    WORK_ORDER = "work_order"
    PROCESS_PARAM = "process_param"
    QUALITY_INSPECTION = "quality_inspection"
    WMS_INTERFACE = "wms_interface"


@dataclass
class FallbackDataRecord:
    """兜底数据记录"""
    record_id: str
    source: DataSourceType
    production_line: str
    data_content: Dict[str, Any]
    timestamp: datetime
    fallback_mode: FallbackMode
    is_synced: bool = False
    synced_at: Optional[datetime] = None


@dataclass
class ProductionLineHeartbeat:
    """产线心跳数据"""
    line_name: str
    is_alive: bool
    current_mode: FallbackMode
    last_heartbeat: datetime
    cpu_usage: float
    memory_usage: float
    network_latency: float


class EdgeGatewayBuffer:
    """边缘网关数据缓冲区
    
    当MES主系统不可用时，边缘网关临时缓存数据
    """
    
    def __init__(self, max_size: int = 10000, flush_interval: int = 60):
        self.max_size = max_size
        self.flush_interval = flush_interval
        self._buffer: List[FallbackDataRecord] = []
        self._lock = threading.Lock()
        self._flush_thread: Optional[threading.Thread] = None
        self._running = False
        self.config = get_config()
        self.db = get_db()
        self.audit_logger = AuditLogger()
    
    def start(self):
        """启动缓冲区刷新线程"""
        if self._running:
            return
        self._running = True
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()
        logger.info("边缘网关缓冲区已启动")
    
    def stop(self):
        """停止缓冲区刷新线程"""
        self._running = False
        if self._flush_thread:
            self._flush_thread.join(timeout=5)
        logger.info("边缘网关缓冲区已停止")
    
    def write(self, record: FallbackDataRecord) -> bool:
        """写入数据到缓冲区"""
        with self._lock:
            if len(self._buffer) >= self.max_size:
                logger.warning(f"边缘网关缓冲区已满，当前大小: {len(self._buffer)}")
                return False
            self._buffer.append(record)
            self._persist_to_local_db(record)
            return True
    
    def _persist_to_local_db(self, record: FallbackDataRecord) -> None:
        """持久化到本地数据库"""
        try:
            self.db.execute('''
                INSERT INTO fallback_data_records 
                (record_id, source, production_line, data_content, 
                 fallback_mode, is_synced, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
            ''', (
                record.record_id,
                record.source.value,
                record.production_line,
                json.dumps(record.data_content, ensure_ascii=False),
                record.fallback_mode.value,
                record.timestamp
            ))
        except Exception as e:
            logger.error(f"持久化兜底数据失败: {e}")
    
    def _flush_loop(self):
        """定期刷新缓冲区数据到主系统"""
        while self._running:
            try:
                self._flush_buffer()
            except Exception as e:
                logger.error(f"刷新缓冲区异常: {e}")
            time.sleep(self.flush_interval)
    
    def _flush_buffer(self):
        """将缓冲数据同步到主系统"""
        with self._lock:
            if not self._buffer:
                return
            
            unsynced = [r for r in self._buffer if not r.is_synced]
            if not unsynced:
                return
            
            success_count = 0
            for record in unsynced:
                if self._sync_to_main_system(record):
                    record.is_synced = True
                    record.synced_at = datetime.now()
                    self._update_sync_status(record)
                    success_count += 1
            
            if success_count > 0:
                logger.info(f"成功同步 {success_count}/{len(unsynced)} 条缓冲数据到主系统")
            
            self._buffer = [r for r in self._buffer if not r.is_synced][-self.max_size:]
    
    def _sync_to_main_system(self, record: FallbackDataRecord) -> bool:
        """同步单条数据到主系统"""
        main_system_url = self.config.get('fallback.main_system_url', 'http://localhost:8080/api/data')
        
        if self.config.get('fallback.mock_mode', True):
            time.sleep(0.01)
            return True
        
        try:
            import requests
            response = requests.post(
                main_system_url,
                json=asdict(record),
                timeout=5
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"同步数据到主系统失败: {e}")
            return False
    
    def _update_sync_status(self, record: FallbackDataRecord):
        """更新数据库同步状态"""
        try:
            self.db.execute('''
                UPDATE fallback_data_records 
                SET is_synced = 1, synced_at = ?
                WHERE record_id = ?
            ''', (record.synced_at, record.record_id))
        except Exception as e:
            logger.error(f"更新同步状态失败: {e}")
    
    def get_buffer_stats(self) -> Dict[str, Any]:
        """获取缓冲区统计信息"""
        with self._lock:
            unsynced_count = len([r for r in self._buffer if not r.is_synced])
            return {
                'total_size': len(self._buffer),
                'unsynced_count': unsynced_count,
                'max_size': self.max_size,
                'usage_percent': (len(self._buffer) / self.max_size) * 100
            }


class LocalDatabaseFallback:
    """本地数据库兜底
    
    当边缘网关也不可用时，使用本地SQLite数据库作为二级缓存
    """
    
    def __init__(self):
        self.db = get_db()
        self.config = get_config()
        self.batch_size = self.config.get('fallback.local_db_batch_size', 100)
    
    def save_data(self, record: FallbackDataRecord) -> bool:
        """保存数据到本地数据库"""
        try:
            self.db.execute('''
                INSERT INTO fallback_data_records 
                (record_id, source, production_line, data_content, 
                 fallback_mode, is_synced, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
            ''', (
                record.record_id,
                record.source.value,
                record.production_line,
                json.dumps(record.data_content, ensure_ascii=False),
                record.fallback_mode.value,
                record.timestamp
            ))
            return True
        except Exception as e:
            logger.error(f"本地数据库保存失败: {e}")
            return False
    
    def get_unsynced_data(self, limit: int = None) -> List[Dict[str, Any]]:
        """获取未同步的数据"""
        limit_clause = f"LIMIT {limit}" if limit else ""
        records = self.db.query(f'''
            SELECT * FROM fallback_data_records 
            WHERE is_synced = 0 
            ORDER BY created_at ASC
            {limit_clause}
        ''')
        return records
    
    def sync_batch(self, sync_func: Callable) -> int:
        """批量同步数据
        
        Args:
            sync_func: 同步函数，接收数据记录返回是否成功
            
        Returns:
            成功同步的数量
        """
        records = self.get_unsynced_data(self.batch_size)
        success_count = 0
        
        for record in records:
            try:
                data = json.loads(record['data_content'])
                if sync_func(data):
                    self.db.execute('''
                        UPDATE fallback_data_records 
                        SET is_synced = 1, synced_at = ?
                        WHERE id = ?
                    ''', (datetime.now(), record['id']))
                    success_count += 1
            except Exception as e:
                logger.error(f"同步记录失败: {e}")
        
        return success_count
    
    def get_stats(self) -> Dict[str, Any]:
        """获取本地数据库统计"""
        total = self.db.query_one('SELECT COUNT(*) as cnt FROM fallback_data_records')
        unsynced = self.db.query_one('SELECT COUNT(*) as cnt FROM fallback_data_records WHERE is_synced = 0')
        return {
            'total_records': total['cnt'] if total else 0,
            'unsynced_records': unsynced['cnt'] if unsynced else 0
        }


class ManualOperationRecorder:
    """人工操作记录器
    
    当系统完全不可用时，记录人工线下生产操作
    """
    
    def __init__(self):
        self.db = get_db()
        self.audit_logger = AuditLogger()
    
    @audit_operation(OperationType.SYSTEM_CONFIG, lambda *args, **kwargs: kwargs.get('operator', 'system'))
    def record_manual_operation(self, 
                                production_line: str,
                                operator: str,
                                operation_type: str,
                                work_order: str,
                                material_batch: str,
                                quantity: int,
                                quality_result: str,
                                remarks: str = None,
                                **kwargs) -> str:
        """记录人工生产操作
        
        Args:
            production_line: 产线名称
            operator: 操作人员
            operation_type: 操作类型（上料/加工/质检/下料）
            work_order: 工单号
            material_batch: 物料批次
            quantity: 生产数量
            quality_result: 质量结果（合格/不合格）
            remarks: 备注
            
        Returns:
            记录ID
        """
        record_id = f"MANUAL_{datetime.now().strftime('%Y%m%d%H%M%S')}_{int(time.time() * 1000)}"
        
        data_content = {
            'production_line': production_line,
            'operator': operator,
            'operation_type': operation_type,
            'work_order': work_order,
            'material_batch': material_batch,
            'quantity': quantity,
            'quality_result': quality_result,
            'remarks': remarks,
            'record_time': datetime.now().isoformat()
        }
        
        self.db.execute('''
            INSERT INTO fallback_data_records 
            (record_id, source, production_line, data_content, 
             fallback_mode, is_synced, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
        ''', (
            record_id,
            'manual_operation',
            production_line,
            json.dumps(data_content, ensure_ascii=False),
            FallbackMode.MANUAL.value,
            datetime.now()
        ))
        
        logger.info(f"已记录人工操作: {record_id}, 产线: {production_line}, 操作人: {operator}")
        return record_id
    
    def get_manual_records(self, start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        """获取指定时间段的人工操作记录"""
        records = self.db.query('''
            SELECT * FROM fallback_data_records 
            WHERE source = 'manual_operation' 
            AND created_at BETWEEN ? AND ?
            ORDER BY created_at DESC
        ''', (start_time, end_time))
        
        for r in records:
            if r.get('data_content'):
                r['data_content'] = json.loads(r['data_content'])
        
        return records


class FallbackManager:
    """兜底模式管理器
    
    负责产线兜底模式的自动检测、切换和恢复
    """
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self.logger = logger
        self.edge_buffer = EdgeGatewayBuffer(
            max_size=self.config.get('fallback.edge_buffer_max_size', 10000),
            flush_interval=self.config.get('fallback.flush_interval', 60)
        )
        self.local_db = LocalDatabaseFallback()
        self.manual_recorder = ManualOperationRecorder()
        self.notification = NotificationService()
        self.audit_logger = AuditLogger()
        
        self.heartbeat_timeout = self.config.get('fallback.heartbeat_timeout', 30)
        self.mode_switch_history: List[Dict[str, Any]] = []
        
        self._heartbeat_monitor: Optional[threading.Thread] = None
        self._monitoring = False
    
    def start(self):
        """启动兜底管理服务"""
        self.edge_buffer.start()
        self._monitoring = True
        self._heartbeat_monitor = threading.Thread(target=self._heartbeat_monitor_loop, daemon=True)
        self._heartbeat_monitor.start()
        logger.info("兜底模式管理器已启动")
    
    def stop(self):
        """停止兜底管理服务"""
        self._monitoring = False
        if self._heartbeat_monitor:
            self._heartbeat_monitor.join(timeout=5)
        self.edge_buffer.stop()
        logger.info("兜底模式管理器已停止")
    
    def _heartbeat_monitor_loop(self):
        """心跳监测循环"""
        while self._monitoring:
            try:
                self._check_all_production_lines()
            except Exception as e:
                logger.error(f"心跳监测异常: {e}")
            time.sleep(10)
    
    def _check_all_production_lines(self):
        """检查所有产线的心跳状态"""
        lines = self.db.query('SELECT * FROM production_line_status')
        
        for line in lines:
            try:
                self._check_single_line(line)
            except Exception as e:
                logger.error(f"检查产线 {line['line_name']} 异常: {e}")
    
    def _check_single_line(self, line_status: Dict[str, Any]):
        """检查单条产线状态并自动切换兜底模式"""
        line_name = line_status['line_name']
        last_heartbeat = datetime.fromisoformat(line_status['last_heartbeat']) if isinstance(line_status['last_heartbeat'], str) else line_status['last_heartbeat']
        current_mode = FallbackMode(line_status['fallback_mode'])
        
        heartbeat_age = (datetime.now() - last_heartbeat).total_seconds()
        
        new_mode = current_mode
        
        if heartbeat_age > self.heartbeat_timeout * 3:
            new_mode = FallbackMode.MANUAL
        elif heartbeat_age > self.heartbeat_timeout * 2:
            new_mode = FallbackMode.LOCAL_DB
        elif heartbeat_age > self.heartbeat_timeout:
            new_mode = FallbackMode.EDGE_BUFFER
        else:
            new_mode = FallbackMode.NORMAL
        
        if new_mode != current_mode:
            self._switch_fallback_mode(line_name, current_mode, new_mode)
    
    @audit_operation(OperationType.SYSTEM_CONFIG, lambda *args, **kwargs: 'system')
    def _switch_fallback_mode(self, 
                              line_name: str, 
                              from_mode: FallbackMode, 
                              to_mode: FallbackMode,
                              **kwargs) -> None:
        """切换产线兜底模式"""
        audit_id = generate_audit_id()
        set_audit_context(audit_id=audit_id, user='system', operation=OperationType.SYSTEM_CONFIG.value)
        
        try:
            self.db.execute('''
                UPDATE production_line_status 
                SET fallback_mode = ?, last_heartbeat = ?
                WHERE line_name = ?
            ''', (to_mode.value, datetime.now(), line_name))
            
            history_record = {
                'line_name': line_name,
                'from_mode': from_mode.value,
                'to_mode': to_mode.value,
                'switch_time': datetime.now().isoformat(),
                'reason': self._get_mode_switch_reason(to_mode)
            }
            self.mode_switch_history.append(history_record)
            
            if len(self.mode_switch_history) > 1000:
                self.mode_switch_history = self.mode_switch_history[-1000:]
            
            alert_level = self._get_alert_level_for_mode(to_mode)
            
            self.notification.send_notification(
                alert_level=alert_level,
                title=f"产线兜底模式切换告警",
                content=f"产线【{line_name}】兜底模式已从 {from_mode.value} 切换为 {to_mode.value}\n"
                       f"切换原因: {self._get_mode_switch_reason(to_mode)}\n"
                       f"切换时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                channels=['email', 'wechat', 'dingtalk']
            )
            
            logger.warning(f"产线 {line_name} 兜底模式切换: {from_mode.value} -> {to_mode.value}")
            
        except Exception as e:
            logger.error(f"切换兜底模式失败: {e}")
            raise
    
    def _get_mode_switch_reason(self, mode: FallbackMode) -> str:
        """获取模式切换原因描述"""
        reasons = {
            FallbackMode.NORMAL: "系统恢复正常",
            FallbackMode.EDGE_BUFFER: "主系统响应超时，启用边缘网关缓冲",
            FallbackMode.LOCAL_DB: "边缘网关不可用，切换至本地数据库兜底",
            FallbackMode.MANUAL: "系统完全不可用，切换至人工线下生产模式"
        }
        return reasons.get(mode, "未知原因")
    
    def _get_alert_level_for_mode(self, mode: FallbackMode) -> AlertLevel:
        """根据兜底模式获取告警等级"""
        levels = {
            FallbackMode.NORMAL: AlertLevel.LEVEL1,
            FallbackMode.EDGE_BUFFER: AlertLevel.LEVEL1,
            FallbackMode.LOCAL_DB: AlertLevel.LEVEL2,
            FallbackMode.MANUAL: AlertLevel.LEVEL3
        }
        return levels.get(mode, AlertLevel.LEVEL2)
    
    def write_data(self, 
                   source: DataSourceType,
                   production_line: str,
                   data: Dict[str, Any]) -> bool:
        """写入数据（根据当前兜底模式自动选择存储方式）"""
        line_status = self.db.query_one('''
            SELECT * FROM production_line_status WHERE line_name = ?
        ''', (production_line,))
        
        if not line_status:
            logger.warning(f"产线 {production_line} 不存在，使用默认正常模式")
            current_mode = FallbackMode.NORMAL
        else:
            current_mode = FallbackMode(line_status['fallback_mode'])
        
        record_id = f"DATA_{datetime.now().strftime('%Y%m%d%H%M%S')}_{int(time.time() * 1000)}"
        
        record = FallbackDataRecord(
            record_id=record_id,
            source=source,
            production_line=production_line,
            data_content=data,
            timestamp=datetime.now(),
            fallback_mode=current_mode
        )
        
        if current_mode == FallbackMode.NORMAL:
            return self._write_normal(record)
        elif current_mode == FallbackMode.EDGE_BUFFER:
            return self.edge_buffer.write(record)
        elif current_mode == FallbackMode.LOCAL_DB:
            return self.local_db.save_data(record)
        elif current_mode == FallbackMode.MANUAL:
            return self.local_db.save_data(record)
        
        return False
    
    def _write_normal(self, record: FallbackDataRecord) -> bool:
        """正常模式写入（尝试写入主系统，失败则降级）"""
        main_system_url = self.config.get('fallback.main_system_url', 'http://localhost:8080/api/data')
        
        if self.config.get('fallback.mock_mode', True):
            self._persist_fallback_record(record)
            return True
        
        try:
            import requests
            response = requests.post(
                main_system_url,
                json=asdict(record),
                timeout=3
            )
            if response.status_code == 200:
                record.is_synced = True
                record.synced_at = datetime.now()
                self._persist_fallback_record(record)
                return True
        except Exception as e:
            logger.debug(f"主系统写入失败，降级到边缘缓冲: {e}")
        
        return self.edge_buffer.write(record)
    
    def _persist_fallback_record(self, record: FallbackDataRecord):
        """持久化兜底记录到数据库"""
        try:
            self.db.execute('''
                INSERT INTO fallback_data_records 
                (record_id, source, production_line, data_content, 
                 fallback_mode, is_synced, created_at, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                record.record_id,
                record.source.value,
                record.production_line,
                json.dumps(record.data_content, ensure_ascii=False),
                record.fallback_mode.value,
                1 if record.is_synced else 0,
                record.timestamp,
                record.synced_at
            ))
        except Exception as e:
            logger.error(f"持久化兜底记录失败: {e}")
    
    def update_heartbeat(self, heartbeat: ProductionLineHeartbeat) -> None:
        """更新产线心跳"""
        try:
            self.db.execute('''
                UPDATE production_line_status 
                SET last_heartbeat = ?, fallback_mode = ?
                WHERE line_name = ?
            ''', (heartbeat.last_heartbeat, heartbeat.current_mode.value, heartbeat.line_name))
        except Exception as e:
            logger.error(f"更新心跳失败: {e}")
    
    def manual_mode_entry(self, production_line: str, operator: str) -> str:
        """手动进入人工模式
        
        Args:
            production_line: 产线名称
            operator: 操作人
            
        Returns:
            操作记录ID
        """
        audit_id = generate_audit_id()
        set_audit_context(audit_id=audit_id, user=operator, operation=OperationType.SYSTEM_CONFIG.value)
        
        line_status = self.db.query_one('''
            SELECT * FROM production_line_status WHERE line_name = ?
        ''', (production_line,))
        
        if not line_status:
            raise ValueError(f"产线 {production_line} 不存在")
        
        from_mode = FallbackMode(line_status['fallback_mode'])
        self._switch_fallback_mode(production_line, from_mode, FallbackMode.MANUAL)
        
        logger.info(f"产线 {production_line} 已由 {operator} 手动切换至人工模式")
        return audit_id
    
    def restore_normal_mode(self, production_line: str, operator: str) -> str:
        """恢复正常模式
        
        Args:
            production_line: 产线名称
            operator: 操作人
            
        Returns:
            操作记录ID
        """
        audit_id = generate_audit_id()
        set_audit_context(audit_id=audit_id, user=operator, operation=OperationType.SYSTEM_CONFIG.value)
        
        line_status = self.db.query_one('''
            SELECT * FROM production_line_status WHERE line_name = ?
        ''', (production_line,))
        
        if not line_status:
            raise ValueError(f"产线 {production_line} 不存在")
        
        from_mode = FallbackMode(line_status['fallback_mode'])
        
        sync_count = self._sync_pending_data(production_line)
        
        self._switch_fallback_mode(production_line, from_mode, FallbackMode.NORMAL)
        
        self.db.execute('''
            UPDATE production_line_status 
            SET auto_production_enabled = 1
            WHERE line_name = ?
        ''', (production_line,))
        
        logger.info(f"产线 {production_line} 已由 {operator} 恢复至正常模式，同步数据 {sync_count} 条")
        
        self.notification.send_notification(
            alert_level=AlertLevel.LEVEL1,
            title=f"产线恢复正常运行",
            content=f"产线【{production_line}】已恢复正常模式\n"
                   f"操作人: {operator}\n"
                   f"恢复时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                   f"同步补传数据: {sync_count} 条",
            channels=['email', 'wechat']
        )
        
        return audit_id
    
    def _sync_pending_data(self, production_line: str) -> int:
        """同步指定产线的待处理数据"""
        total_synced = 0
        
        def sync_func(data):
            main_system_url = self.config.get('fallback.main_system_url', 'http://localhost:8080/api/data')
            if self.config.get('fallback.mock_mode', True):
                return True
            try:
                import requests
                response = requests.post(main_system_url, json=data, timeout=5)
                return response.status_code == 200
            except Exception:
                return False
        
        total_synced += self.local_db.sync_batch(sync_func)
        return total_synced
    
    def get_system_status(self) -> Dict[str, Any]:
        """获取兜底系统整体状态"""
        lines = self.db.query('SELECT * FROM production_line_status')
        
        mode_counts = {mode.value: 0 for mode in FallbackMode}
        for line in lines:
            mode_counts[line['fallback_mode']] = mode_counts.get(line['fallback_mode'], 0) + 1
        
        return {
            'total_lines': len(lines),
            'mode_distribution': mode_counts,
            'edge_buffer': self.edge_buffer.get_buffer_stats(),
            'local_db': self.local_db.get_stats(),
            'recent_mode_switches': self.mode_switch_history[-20:],
            'timestamp': datetime.now().isoformat()
        }
    
    def record_manual_production(self,
                                 production_line: str,
                                 operator: str,
                                 operation_type: str,
                                 work_order: str,
                                 material_batch: str,
                                 quantity: int,
                                 quality_result: str,
                                 remarks: str = None) -> str:
        """记录人工生产数据（快捷方法）"""
        return self.manual_recorder.record_manual_operation(
            production_line=production_line,
            operator=operator,
            operation_type=operation_type,
            work_order=work_order,
            material_batch=material_batch,
            quantity=quantity,
            quality_result=quality_result,
            remarks=remarks
        )
    
    def generate_fallback_report(self, start_date: datetime = None, 
                                 end_date: datetime = None) -> Dict[str, Any]:
        """生成兜底数据统计报告"""
        if not start_date:
            start_date = datetime.now() - timedelta(days=7)
        if not end_date:
            end_date = datetime.now()
        
        records = self.db.query('''
            SELECT * FROM fallback_data_records 
            WHERE created_at BETWEEN ? AND ?
            ORDER BY created_at DESC
        ''', (start_date, end_date))
        
        mode_stats = {}
        source_stats = {}
        line_stats = {}
        
        for r in records:
            mode = r['fallback_mode']
            source = r['source']
            line = r['production_line']
            
            mode_stats[mode] = mode_stats.get(mode, 0) + 1
            source_stats[source] = source_stats.get(source, 0) + 1
            line_stats[line] = line_stats.get(line, 0) + 1
        
        unsynced_count = len([r for r in records if not r['is_synced']])
        
        return {
            'period': f"{start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}",
            'total_records': len(records),
            'unsynced_records': unsynced_count,
            'mode_distribution': mode_stats,
            'source_distribution': source_stats,
            'line_distribution': line_stats,
            'manual_operation_records': self.manual_recorder.get_manual_records(start_date, end_date),
            'generated_at': datetime.now().isoformat()
        }


_fallback_manager_instance: Optional[FallbackManager] = None


def get_fallback_manager() -> FallbackManager:
    """获取兜底管理器单例"""
    global _fallback_manager_instance
    if _fallback_manager_instance is None:
        _fallback_manager_instance = FallbackManager()
    return _fallback_manager_instance
