"""
定时任务调度器
基于APScheduler实现定时任务管理，包括每周三凌晨的周度报表生成
"""
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, JobEvent
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    BackgroundScheduler = None
    CronTrigger = None
    EVENT_JOB_EXECUTED = None
    EVENT_JOB_ERROR = None

from .config import get_config
from .logger import get_logger, set_audit_context, generate_audit_id
from .database import get_db
from .audit import AuditLogger, OperationType
from .reporting import WeeklyReportGenerator, get_report_generator
from .notification import NotificationService, AlertLevel

logger = get_logger(__name__)


@dataclass
class ScheduledTask:
    """定时任务定义"""
    task_id: str
    name: str
    description: str
    cron_expression: str
    func: Callable
    enabled: bool = True
    last_run_time: Optional[datetime] = None
    last_run_status: str = "PENDING"
    last_run_error: Optional[str] = None
    run_count: int = 0


class TaskScheduler:
    """定时任务调度器"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self.audit_logger = AuditLogger()
        self.notification = NotificationService()
        
        self._scheduler: Optional[BackgroundScheduler] = None
        self._tasks: Dict[str, ScheduledTask] = {}
        self._running = False
        self._lock = threading.Lock()
        
        self._init_tasks()
    
    def _init_tasks(self):
        """初始化所有定时任务"""
        weekly_cron = self.config.get('scheduler.weekly_report_cron', '0 2 * * 3')
        
        self._tasks = {
            'weekly_report': ScheduledTask(
                task_id='weekly_report',
                name='周度运维报表生成',
                description='每周三凌晨2点自动生成MES系统运维分析报告',
                cron_expression=weekly_cron,
                func=self._execute_weekly_report_task,
                enabled=True
            ),
            'data_sync': ScheduledTask(
                task_id='data_sync',
                name='兜底数据同步',
                description='每小时同步一次兜底模式下的待同步数据',
                cron_expression='0 * * * *',
                func=self._execute_data_sync_task,
                enabled=True
            ),
            'system_health_check': ScheduledTask(
                task_id='system_health_check',
                name='系统健康检查',
                description='每6小时执行一次系统健康检查',
                cron_expression='0 */6 * * *',
                func=self._execute_health_check_task,
                enabled=True
            ),
            'audit_log_verify': ScheduledTask(
                task_id='audit_log_verify',
                name='审计日志完整性校验',
                description='每日凌晨1点校验审计日志哈希链完整性',
                cron_expression='0 1 * * *',
                func=self._execute_audit_verify_task,
                enabled=True
            ),
            'approval_timeout_check': ScheduledTask(
                task_id='approval_timeout_check',
                name='审批超时检查',
                description='每30分钟检查一次审批超时',
                cron_expression='*/30 * * * *',
                func=self._execute_approval_timeout_task,
                enabled=True
            )
        }
    
    def start(self):
        """启动调度器"""
        if self._running:
            return
        
        if not APSCHEDULER_AVAILABLE:
            logger.warning("APScheduler未安装，使用内置简单调度器")
            self._start_simple_scheduler()
            return
        
        try:
            self._scheduler = BackgroundScheduler(timezone='Asia/Shanghai')
            
            for task_id, task in self._tasks.items():
                if task.enabled:
                    self._add_cron_job(task)
            
            self._scheduler.add_listener(self._on_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
            self._scheduler.start()
            self._running = True
            
            logger.info("定时任务调度器已启动")
            for task_id, task in self._tasks.items():
                if task.enabled:
                    logger.info(f"  - [{task_id}] {task.name}: {task.cron_expression}")
            
        except Exception as e:
            logger.error(f"启动调度器失败: {e}")
            self._start_simple_scheduler()
    
    def _add_cron_job(self, task: ScheduledTask):
        """添加Cron任务"""
        parts = task.cron_expression.split()
        if len(parts) != 5:
            raise ValueError(f"无效的Cron表达式: {task.cron_expression}")
        
        minute, hour, day, month, day_of_week = parts
        
        trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone='Asia/Shanghai'
        )
        
        self._scheduler.add_job(
            func=self._run_task_wrapper,
            trigger=trigger,
            args=[task.task_id],
            id=task.task_id,
            name=task.name,
            replace_existing=True
        )
    
    def _start_simple_scheduler(self):
        """启动简单调度器（当APScheduler不可用时）"""
        self._running = True
        self._simple_scheduler_thread = threading.Thread(
            target=self._simple_scheduler_loop,
            daemon=True
        )
        self._simple_scheduler_thread.start()
        logger.info("内置简单调度器已启动")
    
    def _simple_scheduler_loop(self):
        """简单调度器主循环"""
        while self._running:
            try:
                now = datetime.now()
                
                for task_id, task in self._tasks.items():
                    if not task.enabled:
                        continue
                    
                    if self._should_run_task(task, now):
                        self._run_task_wrapper(task_id)
                
                time.sleep(60)
                
            except Exception as e:
                logger.error(f"简单调度器异常: {e}")
                time.sleep(60)
    
    def _should_run_task(self, task: ScheduledTask, now: datetime) -> bool:
        """判断任务是否应该运行（简单Cron解析）"""
        if task.last_run_time:
            elapsed = (now - task.last_run_time).total_seconds()
            if elapsed < 60:
                return False
        
        parts = task.cron_expression.split()
        if len(parts) != 5:
            return False
        
        minute, hour, day, month, day_of_week = parts
        
        def match(value: int, pattern: str) -> bool:
            if pattern == '*':
                return True
            if ',' in pattern:
                return any(match(value, p) for p in pattern.split(','))
            if '-' in pattern:
                start, end = map(int, pattern.split('-'))
                return start <= value <= end
            if '/' in pattern:
                _, step = pattern.split('/')
                return value % int(step) == 0
            return value == int(pattern)
        
        return (match(now.minute, minute) and
                match(now.hour, hour) and
                match(now.day, day) and
                match(now.month, month) and
                match(now.weekday(), day_of_week))
    
    def _on_job_event(self, event: 'JobEvent'):
        """任务事件处理"""
        try:
            job_id = event.job_id
            task = self._tasks.get(job_id)
            if not task:
                return
            
            if event.code == EVENT_JOB_EXECUTED:
                task.last_run_status = "SUCCESS"
                logger.info(f"任务执行成功: {task.name}")
            elif event.code == EVENT_JOB_ERROR:
                task.last_run_status = "FAILED"
                task.last_run_error = str(event.exception) if hasattr(event, 'exception') else "未知错误"
                logger.error(f"任务执行失败: {task.name}, 错误: {task.last_run_error}")
                
                self.notification.send_notification(
                    alert_level=AlertLevel.LEVEL2,
                    title=f"定时任务执行失败告警",
                    content=f"任务【{task.name}】执行失败\n"
                           f"任务ID: {job_id}\n"
                           f"错误信息: {task.last_run_error}\n"
                           f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    channels=['email', 'wechat']
                )
        
        except Exception as e:
            logger.error(f"处理任务事件异常: {e}")
    
    def _run_task_wrapper(self, task_id: str):
        """任务执行包装器"""
        audit_id = generate_audit_id()
        set_audit_context(audit_id=audit_id, user='system', operation=OperationType.SYSTEM_CONFIG.value)
        
        task = self._tasks.get(task_id)
        if not task:
            logger.error(f"未找到任务: {task_id}")
            return
        
        task.last_run_time = datetime.now()
        task.run_count += 1
        
        start_time = time.time()
        
        try:
            logger.info(f"开始执行任务: {task.name}")
            task.func()
            task.last_run_status = "SUCCESS"
            task.last_run_error = None
            
            duration = int((time.time() - start_time) * 1000)
            
            self.audit_logger.log(
                operation_type=OperationType.SYSTEM_CONFIG,
                operator='system',
                request_params={'task_id': task_id, 'task_name': task.name},
                response_result={'status': 'success', 'duration_ms': duration},
                status='SUCCESS',
                duration_ms=duration
            )
            
            logger.info(f"任务执行完成: {task.name}, 耗时: {duration}ms")
            
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            task.last_run_status = "FAILED"
            task.last_run_error = str(e)
            
            self.audit_logger.log(
                operation_type=OperationType.SYSTEM_CONFIG,
                operator='system',
                request_params={'task_id': task_id, 'task_name': task.name},
                response_result={'status': 'failed', 'error': str(e), 'duration_ms': duration},
                status='FAILED',
                duration_ms=duration
            )
            
            logger.error(f"任务执行异常: {task.name}, 错误: {e}")
            
            self.notification.send_notification(
                alert_level=AlertLevel.LEVEL2,
                title=f"定时任务执行失败告警",
                content=f"任务【{task.name}】执行失败\n"
                       f"错误信息: {str(e)}\n"
                       f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                channels=['email', 'wechat']
            )
    
    def _execute_weekly_report_task(self):
        """执行周度报表生成任务"""
        try:
            report_generator = get_report_generator()
            
            end_date = datetime.now()
            start_date = end_date - timedelta(days=7)
            
            logger.info(f"开始生成周度报表: {start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}")
            
            result = report_generator.generate_weekly_report(
                start_date=start_date,
                end_date=end_date
            )
            
            if not isinstance(result, tuple) or len(result) < 4:
                logger.error(f"周度报表生成失败：返回值格式异常，期望4个元素，实际得到 {type(result).__name__} (长度={len(result) if isinstance(result, (tuple, list)) else 'N/A'})")
                return
            
            report_id, pdf_path, excel_path, report = result
            
            logger.info(f"周度报表生成完成: {report_id}")
            logger.info(f"  报表ID: {report_id}")
            logger.info(f"  PDF报告: {pdf_path}")
            logger.info(f"  Excel报表: {excel_path}")
            
            try:
                self.notification.send_notification(
                    alert_level=AlertLevel.LEVEL1,
                    title=f"MES系统周度运维报表已生成",
                    content=f"报告周期: {start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}\n"
                           f"报告ID: {report_id}\n"
                           f"PDF路径: {pdf_path}\n"
                           f"Excel路径: {excel_path}\n"
                           f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    channels=['email', 'wechat']
                )
            except Exception as e:
                logger.warning(f"周度报表通知发送失败（不影响任务本身）: {e}")
                
        except Exception as e:
            logger.error(f"周度报表任务执行失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _execute_data_sync_task(self):
        """执行数据同步任务"""
        from .data_fallback import get_fallback_manager
        
        fallback_manager = get_fallback_manager()
        lines = self.db.query('SELECT line_name FROM production_line_status')
        
        total_synced = 0
        for line in lines:
            count = fallback_manager._sync_pending_data(line['line_name'])
            total_synced += count
        
        if total_synced > 0:
            logger.info(f"定时数据同步完成，共同步 {total_synced} 条记录")
    
    def _execute_health_check_task(self):
        """执行系统健康检查任务"""
        from .data_fallback import get_fallback_manager
        
        fallback_manager = get_fallback_manager()
        status = fallback_manager.get_system_status()
        
        lines_in_manual = status['mode_distribution'].get('MANUAL', 0)
        lines_in_local_db = status['mode_distribution'].get('LOCAL_DB', 0)
        
        if lines_in_manual > 0 or lines_in_local_db > 0:
            alert_level = AlertLevel.LEVEL2 if lines_in_manual > 0 else AlertLevel.LEVEL1
            self.notification.send_notification(
                alert_level=alert_level,
                title=f"系统健康检查告警",
                content=f"检测到产线处于异常兜底模式:\n"
                       f"  人工模式: {lines_in_manual} 条\n"
                       f"  本地DB模式: {lines_in_local_db} 条\n"
                       f"  边缘缓冲使用率: {status['edge_buffer']['usage_percent']:.1f}%\n"
                       f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                channels=['email', 'wechat']
            )
        
        logger.info(f"系统健康检查完成: {status}")
    
    def _execute_audit_verify_task(self):
        """执行审计日志完整性校验任务"""
        try:
            result = self.audit_logger.verify_chain()
            
            if not isinstance(result, tuple) or len(result) < 2:
                logger.error(f"审计日志校验失败：返回值格式异常，期望2个元素的元组，实际得到 {type(result).__name__}")
                return
            
            is_valid, error_msg = result
            
            if not is_valid:
                logger.error(f"审计日志校验失败: {error_msg if error_msg else '未提供详细错误信息'}")
                try:
                    self.notification.send_notification(
                        alert_level=AlertLevel.LEVEL3,
                        title=f"审计日志完整性校验失败【严重】",
                        content=f"审计日志哈希链校验失败，数据可能被篡改！\n"
                               f"错误信息: {error_msg if error_msg else '未提供详细错误信息'}\n"
                               f"校验时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        channels=['email', 'wechat', 'dingtalk']
                    )
                except Exception as e:
                    logger.warning(f"审计日志校验告警通知发送失败（不影响任务本身）: {e}")
            else:
                logger.info("审计日志完整性校验通过")
                
        except Exception as e:
            logger.error(f"审计日志完整性校验任务执行失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _execute_approval_timeout_task(self):
        """执行审批超时检查任务"""
        try:
            from .approval import get_approval_manager
            
            approval_manager = get_approval_manager()
            
            if not hasattr(approval_manager, 'check_all_timeout'):
                logger.warning("审批超时检查方法 check_all_timeout 不存在，跳过检查")
                return
            
            timeout_count = approval_manager.check_all_timeout()
            
            if timeout_count is None:
                timeout_count = 0
                
            if not isinstance(timeout_count, int):
                logger.warning(f"审批超时检查返回值格式异常，期望int，实际为 {type(timeout_count).__name__}，已按0处理")
                timeout_count = 0
            
            if timeout_count > 0:
                logger.info(f"审批超时检查完成，共标记 {timeout_count} 个超时审批")
            else:
                logger.info(f"审批超时检查完成，没有发现超时审批")
                
        except Exception as e:
            logger.error(f"审批超时检查任务执行失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def run_task_manually(self, task_id: str, operator: str = 'system') -> bool:
        """手动执行任务
        
        Args:
            task_id: 任务ID
            operator: 操作人
            
        Returns:
            是否执行成功
        """
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"未找到任务: {task_id}")
        
        audit_id = generate_audit_id()
        set_audit_context(audit_id=audit_id, user=operator, operation=OperationType.SYSTEM_CONFIG.value)
        
        logger.info(f"操作人 {operator} 手动触发任务: {task.name}")
        
        try:
            self._run_task_wrapper(task_id)
            return task.last_run_status == "SUCCESS"
        except Exception as e:
            logger.error(f"手动执行任务失败: {e}")
            return False
    
    def enable_task(self, task_id: str, enabled: bool, operator: str = 'system') -> bool:
        """启用/禁用任务
        
        Args:
            task_id: 任务ID
            enabled: 是否启用
            operator: 操作人
            
        Returns:
            是否操作成功
        """
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"未找到任务: {task_id}")
        
        audit_id = generate_audit_id()
        set_audit_context(audit_id=audit_id, user=operator, operation=OperationType.SYSTEM_CONFIG.value)
        
        task.enabled = enabled
        
        if self._scheduler and APSCHEDULER_AVAILABLE:
            if enabled:
                self._scheduler.resume_job(task_id)
            else:
                self._scheduler.pause_job(task_id)
        
        action = "启用" if enabled else "禁用"
        logger.info(f"操作人 {operator} {action}任务: {task.name}")
        
        self.audit_logger.log(
            operation_type=OperationType.SYSTEM_CONFIG,
            operator=operator,
            request_params={'task_id': task_id, 'task_name': task.name, 'enabled': enabled},
            response_result={'status': 'success'},
            status='SUCCESS',
            duration_ms=0
        )
        
        return True
    
    def get_task_status(self) -> List[Dict[str, Any]]:
        """获取所有任务状态"""
        status_list = []
        for task_id, task in self._tasks.items():
            status_list.append({
                'task_id': task.task_id,
                'name': task.name,
                'description': task.description,
                'cron_expression': task.cron_expression,
                'enabled': task.enabled,
                'last_run_time': task.last_run_time.isoformat() if task.last_run_time else None,
                'last_run_status': task.last_run_status,
                'last_run_error': task.last_run_error,
                'run_count': task.run_count
            })
        return status_list
    
    def stop(self):
        """停止调度器"""
        if not self._running:
            return
        
        self._running = False
        
        if self._scheduler and APSCHEDULER_AVAILABLE:
            self._scheduler.shutdown(wait=False)
        
        logger.info("定时任务调度器已停止")
    
    def get_next_run_time(self, task_id: str) -> Optional[datetime]:
        """获取任务下次运行时间"""
        if not self._scheduler or not APSCHEDULER_AVAILABLE:
            return None
        
        job = self._scheduler.get_job(task_id)
        if job:
            return job.next_run_time
        return None


_scheduler_instance: Optional[TaskScheduler] = None


def get_scheduler() -> TaskScheduler:
    """获取调度器单例"""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = TaskScheduler()
    return _scheduler_instance
