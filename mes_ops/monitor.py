"""
实时监控与智能回滚模块
每5分钟监控核心指标，超过阈值自动回滚
"""
import json
import random
import time
import threading
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime

import requests

from .database import get_db
from .logger import get_logger
from .config import get_config
from .constants import (
    MonitorMetricType, AlertLevel, OperationType,
    DeploymentStatus, DeploymentStage
)
from .audit import get_audit_logger, audit_operation
from .deployment import get_deployment_engine, get_version_manager

logger = get_logger(__name__)


class MonitorMetricsCollector:
    """监控指标采集器"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self._load_metric_config()
    
    def _load_metric_config(self) -> None:
        """加载监控指标配置"""
        self.metrics_config = self.config.get('monitor.metrics', {})
        self.data_sources = self.config.get('monitor.data_sources', {})
    
    def _collect_work_order_error_rate(self, mock: bool = True) -> Dict[str, Any]:
        """采集工单上报报错率"""
        metric_config = self.metrics_config.get('work_order_error_rate', {})
        threshold = metric_config.get('threshold', 2.0)
        
        if mock:
            total_orders = random.randint(100, 500)
            error_orders = random.randint(0, int(total_orders * 0.05))
            error_rate = round((error_orders / total_orders * 100), 2) if total_orders > 0 else 0
            
            return {
                'metric_type': MonitorMetricType.WORK_ORDER_ERROR_RATE.value,
                'metric_name': metric_config.get('name', '工单上报报错率'),
                'metric_value': error_rate,
                'threshold': threshold,
                'unit': metric_config.get('unit', '%'),
                'weight': metric_config.get('weight', 0.4),
                'is_alert': error_rate > threshold,
                'detail': {
                    'total_orders': total_orders,
                    'error_orders': error_orders,
                    'error_rate': f"{error_rate}%"
                }
            }
        
        try:
            api_url = self.data_sources.get('work_order_api')
            response = requests.get(api_url, timeout=10)
            data = response.json()
            
            error_rate = float(data.get('error_rate', 0))
            total_orders = int(data.get('total', 0))
            error_orders = int(data.get('errors', 0))
            
            return {
                'metric_type': MonitorMetricType.WORK_ORDER_ERROR_RATE.value,
                'metric_name': metric_config.get('name', '工单上报报错率'),
                'metric_value': error_rate,
                'threshold': threshold,
                'unit': metric_config.get('unit', '%'),
                'weight': metric_config.get('weight', 0.4),
                'is_alert': error_rate > threshold,
                'detail': {
                    'total_orders': total_orders,
                    'error_orders': error_orders,
                    'error_rate': f"{error_rate}%"
                }
            }
        except Exception as e:
            return {
                'metric_type': MonitorMetricType.WORK_ORDER_ERROR_RATE.value,
                'metric_name': metric_config.get('name', '工单上报报错率'),
                'metric_value': 100,
                'threshold': threshold,
                'unit': metric_config.get('unit', '%'),
                'weight': metric_config.get('weight', 0.4),
                'is_alert': True,
                'detail': {'error': f'采集失败: {e}'}
            }
    
    def _collect_data_collection_latency(self, production_line: str = None, 
                                         mock: bool = True) -> Dict[str, Any]:
        """采集设备数据采集响应延迟"""
        metric_config = self.metrics_config.get('data_collection_latency', {})
        threshold = metric_config.get('threshold', 500.0)
        
        if mock:
            latency = random.randint(50, 800)
            
            return {
                'metric_type': MonitorMetricType.DATA_COLLECTION_LATENCY.value,
                'metric_name': metric_config.get('name', '设备数据采集响应延迟'),
                'metric_value': latency,
                'threshold': threshold,
                'unit': metric_config.get('unit', 'ms'),
                'weight': metric_config.get('weight', 0.35),
                'is_alert': latency > threshold,
                'production_line': production_line,
                'detail': {
                    'latency_ms': latency,
                    'production_line': production_line
                }
            }
        
        try:
            api_url = self.data_sources.get('plc_metrics_api')
            params = {'line': production_line} if production_line else {}
            response = requests.get(api_url, params=params, timeout=10)
            data = response.json()
            
            latency = float(data.get('avg_latency', 0))
            
            return {
                'metric_type': MonitorMetricType.DATA_COLLECTION_LATENCY.value,
                'metric_name': metric_config.get('name', '设备数据采集响应延迟'),
                'metric_value': latency,
                'threshold': threshold,
                'unit': metric_config.get('unit', 'ms'),
                'weight': metric_config.get('weight', 0.35),
                'is_alert': latency > threshold,
                'production_line': production_line,
                'detail': data
            }
        except Exception as e:
            return {
                'metric_type': MonitorMetricType.DATA_COLLECTION_LATENCY.value,
                'metric_name': metric_config.get('name', '设备数据采集响应延迟'),
                'metric_value': 1000,
                'threshold': threshold,
                'unit': metric_config.get('unit', 'ms'),
                'weight': metric_config.get('weight', 0.35),
                'is_alert': True,
                'production_line': production_line,
                'detail': {'error': f'采集失败: {e}'}
            }
    
    def _collect_process_param_anomalies(self, production_line: str = None,
                                         mock: bool = True) -> Dict[str, Any]:
        """采集生产工艺参数异常次数"""
        metric_config = self.metrics_config.get('process_param_anomalies', {})
        threshold = metric_config.get('threshold', 5)
        
        if mock:
            anomaly_count = random.randint(0, 15)
            
            param_types = ['温度', '压力', '流量', '转速', '扭矩']
            anomalies = []
            for i in range(min(anomaly_count, 10)):
                anomalies.append({
                    'param_name': random.choice(param_types),
                    'deviation': f"+{random.uniform(5, 20):.1f}%",
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })
            
            return {
                'metric_type': MonitorMetricType.PROCESS_PARAM_ANOMALIES.value,
                'metric_name': metric_config.get('name', '生产工艺参数异常次数'),
                'metric_value': anomaly_count,
                'threshold': threshold,
                'unit': metric_config.get('unit', '次/5min'),
                'weight': metric_config.get('weight', 0.25),
                'is_alert': anomaly_count > threshold,
                'production_line': production_line,
                'detail': {
                    'anomaly_count': anomaly_count,
                    'anomalies': anomalies,
                    'production_line': production_line
                }
            }
        
        try:
            api_url = self.data_sources.get('process_api')
            params = {'line': production_line} if production_line else {}
            response = requests.get(api_url, params=params, timeout=10)
            data = response.json()
            
            anomaly_count = int(data.get('anomaly_count', 0))
            
            return {
                'metric_type': MonitorMetricType.PROCESS_PARAM_ANOMALIES.value,
                'metric_name': metric_config.get('name', '生产工艺参数异常次数'),
                'metric_value': anomaly_count,
                'threshold': threshold,
                'unit': metric_config.get('unit', '次/5min'),
                'weight': metric_config.get('weight', 0.25),
                'is_alert': anomaly_count > threshold,
                'production_line': production_line,
                'detail': data
            }
        except Exception as e:
            return {
                'metric_type': MonitorMetricType.PROCESS_PARAM_ANOMALIES.value,
                'metric_name': metric_config.get('name', '生产工艺参数异常次数'),
                'metric_value': 999,
                'threshold': threshold,
                'unit': metric_config.get('unit', '次/5min'),
                'weight': metric_config.get('weight', 0.25),
                'is_alert': True,
                'production_line': production_line,
                'detail': {'error': f'采集失败: {e}'}
            }
    
    def collect_all_metrics(self, request_id: str = None, 
                            production_lines: List[str] = None,
                            mock: bool = True) -> List[Dict[str, Any]]:
        """
        采集所有监控指标
        
        Args:
            request_id: 关联的发布申请ID
            production_lines: 要监控的产线列表
            mock: 是否使用模拟数据
            
        Returns:
            指标列表
        """
        metrics = []
        
        error_rate = self._collect_work_order_error_rate(mock=mock)
        error_rate['request_id'] = request_id
        metrics.append(error_rate)
        
        lines = production_lines or ['all']
        
        for line in lines:
            latency = self._collect_data_collection_latency(
                production_line=line if line != 'all' else None,
                mock=mock
            )
            latency['request_id'] = request_id
            metrics.append(latency)
            
            anomalies = self._collect_process_param_anomalies(
                production_line=line if line != 'all' else None,
                mock=mock
            )
            anomalies['request_id'] = request_id
            metrics.append(anomalies)
        
        self._save_metrics(metrics)
        
        return metrics
    
    def _save_metrics(self, metrics: List[Dict[str, Any]]) -> None:
        """保存指标到数据库"""
        for metric in metrics:
            self.db.execute('''
                INSERT INTO monitor_metrics 
                (request_id, metric_type, metric_value, threshold, 
                 is_alert, production_line, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                metric.get('request_id'),
                metric['metric_type'],
                metric['metric_value'],
                metric['threshold'],
                1 if metric['is_alert'] else 0,
                metric.get('production_line'),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ))
    
    def simulate_metrics(self, request_id: str, production_line: str,
                         error_rate: float = 0.01,
                         latency: float = 150,
                         anomalies: int = 1) -> Dict[str, Any]:
        """
        便捷方法：模拟监控指标（用于演示和测试）
        
        Args:
            request_id: 发布申请ID
            production_line: 产线名称
            error_rate: 工单报错率（0-1）
            latency: 数据采集延迟(ms)
            anomalies: 工艺参数异常次数
            
        Returns:
            模拟的监控指标
        """
        error_rate_percent = error_rate * 100
        error_threshold = self.metrics_config.get('work_order_error_rate', {}).get('threshold', 2.0)
        latency_threshold = self.metrics_config.get('data_collection_latency', {}).get('threshold', 500)
        anomalies_threshold = self.metrics_config.get('process_param_anomalies', {}).get('threshold', 5)
        
        metrics = {
            'request_id': request_id,
            'production_line': production_line,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'work_order_error_rate': {
                'value': error_rate_percent,
                'threshold': error_threshold,
                'unit': '%',
                'is_alert': error_rate_percent > error_threshold,
                'detail': {
                    'total_orders': random.randint(100, 500),
                    'error_orders': int(error_rate * random.randint(100, 500))
                }
            },
            'data_collection_latency': {
                'value': latency,
                'threshold': latency_threshold,
                'unit': 'ms',
                'is_alert': latency > latency_threshold
            },
            'process_param_anomalies': {
                'value': anomalies,
                'threshold': anomalies_threshold,
                'unit': '次/5min',
                'is_alert': anomalies > anomalies_threshold
            }
        }
        
        self._save_metrics([
            {
                'request_id': request_id,
                'metric_type': MonitorMetricType.WORK_ORDER_ERROR_RATE.value,
                'metric_value': error_rate_percent,
                'threshold': error_threshold,
                'is_alert': error_rate_percent > error_threshold,
                'production_line': production_line
            },
            {
                'request_id': request_id,
                'metric_type': MonitorMetricType.DATA_COLLECTION_LATENCY.value,
                'metric_value': latency,
                'threshold': latency_threshold,
                'is_alert': latency > latency_threshold,
                'production_line': production_line
            },
            {
                'request_id': request_id,
                'metric_type': MonitorMetricType.PROCESS_PARAM_ANOMALIES.value,
                'metric_value': anomalies,
                'threshold': anomalies_threshold,
                'is_alert': anomalies > anomalies_threshold,
                'production_line': production_line
            }
        ])
        
        return metrics


class RollbackDecisionEngine:
    """回滚决策引擎"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self.consecutive_failures = {}
        self._load_config()
    
    def _load_config(self) -> None:
        """加载配置"""
        auto_rollback = self.config.get('monitor.auto_rollback', {})
        self.enabled = auto_rollback.get('enabled', True)
        self.max_consecutive_failures = auto_rollback.get('consecutive_failures', 2)
        self.rollback_timeout = auto_rollback.get('rollback_timeout_seconds', 300)
    
    def evaluate_metrics(self, request_id: str, 
                         metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        评估指标，决定是否需要回滚
        
        Returns:
            {
                'need_rollback': bool,
                'alert_level': AlertLevel,
                'risk_score': float,
                'trigger_metrics': dict,
                'reason': str
            }
        """
        alert_metrics = [m for m in metrics if m['is_alert']]
        
        if request_id not in self.consecutive_failures:
            self.consecutive_failures[request_id] = 0
        
        if not alert_metrics:
            self.consecutive_failures[request_id] = 0
            return {
                'need_rollback': False,
                'alert_level': AlertLevel.LEVEL1,
                'risk_score': 0,
                'trigger_metrics': {},
                'reason': '所有指标正常'
            }
        
        risk_score = 0.0
        trigger_metrics = {}
        max_weight = 0
        
        for metric in alert_metrics:
            weight = metric.get('weight', 0.33)
            value = metric['metric_value']
            threshold = metric['threshold']
            
            deviation_ratio = (value - threshold) / threshold if threshold > 0 else 1
            weighted_score = weight * min(deviation_ratio, 2.0)
            
            risk_score += weighted_score
            max_weight = max(max_weight, weight)
            
            trigger_metrics[metric['metric_type']] = {
                'value': value,
                'threshold': threshold,
                'unit': metric.get('unit', ''),
                'name': metric.get('metric_name', '')
            }
        
        if risk_score >= 0.6:
            alert_level = AlertLevel.LEVEL3
        elif risk_score >= 0.3:
            alert_level = AlertLevel.LEVEL2
        else:
            alert_level = AlertLevel.LEVEL1
        
        if alert_level == AlertLevel.LEVEL3:
            self.consecutive_failures[request_id] += 1
        else:
            self.consecutive_failures[request_id] = max(0, self.consecutive_failures[request_id] - 1)
        
        need_rollback = (self.enabled and 
                        alert_level == AlertLevel.LEVEL3 and 
                        self.consecutive_failures[request_id] >= self.max_consecutive_failures)
        
        reason_parts = []
        for m_type, info in trigger_metrics.items():
            reason_parts.append(f"{info['name']}: {info['value']}{info['unit']}(阈值:{info['threshold']}{info['unit']})")
        
        reason = "; ".join(reason_parts)
        
        if need_rollback:
            reason += f"，连续{self.consecutive_failures[request_id]}次高危告警，触发自动回滚"
        
        return {
            'need_rollback': need_rollback,
            'alert_level': alert_level,
            'risk_score': round(risk_score, 2),
            'trigger_metrics': trigger_metrics,
            'consecutive_failures': self.consecutive_failures[request_id],
            'reason': reason
        }


class AutoRollbackMonitor:
    """自动回滚监控服务"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self.collector = MonitorMetricsCollector()
        self.decision_engine = RollbackDecisionEngine()
        self.deployment_engine = get_deployment_engine()
        self.version_manager = get_version_manager()
        
        self.check_interval = self.config.get('monitor.check_interval_seconds', 300)
        self.running = False
        self.monitor_thread = None
        self.monitored_requests = {}
        
        self._on_rollback_callback: Optional[Callable] = None
    
    def set_rollback_callback(self, callback: Callable) -> None:
        """设置回滚回调"""
        self._on_rollback_callback = callback
    
    def start_monitoring(self, request_id: str, version: str,
                        production_lines: List[str] = None,
                        mock: bool = True) -> None:
        """开始监控某个发布"""
        self.monitored_requests[request_id] = {
            'version': version,
            'production_lines': production_lines,
            'mock': mock,
            'start_time': datetime.now(),
            'active': True
        }
        logger.info(f"开始监控发布: {request_id}, 版本: {version}")
    
    def stop_monitoring(self, request_id: str) -> None:
        """停止监控某个发布"""
        if request_id in self.monitored_requests:
            self.monitored_requests[request_id]['active'] = False
            logger.info(f"停止监控发布: {request_id}")
    
    @audit_operation(OperationType.MONITOR_ALERT, lambda args: ("system",))
    def _check_and_rollback(self, request_id: str, version: str,
                           production_lines: List[str], mock: bool) -> Dict[str, Any]:
        """检查指标并在必要时执行回滚"""
        metrics = self.collector.collect_all_metrics(
            request_id=request_id,
            production_lines=production_lines,
            mock=mock
        )
        
        decision = self.decision_engine.evaluate_metrics(request_id, metrics)
        
        get_audit_logger().log(
            operation_type=OperationType.MONITOR_ALERT,
            operator="system",
            request_params={
                "request_id": request_id,
                "version": version,
                "metrics": [{'type': m['metric_type'], 'value': m['metric_value']} for m in metrics]
            },
            response_result=decision,
            status="SUCCESS"
        )
        
        if decision['need_rollback']:
            logger.warning(f"触发自动回滚: {request_id}, 原因: {decision['reason']}")
            
            stable_version = self.version_manager.get_stable_version()
            if not stable_version:
                logger.error("没有可用的稳定版本，无法回滚")
                decision['need_rollback'] = False
                return decision
            
            rollback_result = self.deployment_engine.rollback(
                operator="system",
                request_id=request_id,
                from_version=version,
                to_version=stable_version['version'],
                reason=decision['reason'],
                affected_lines=production_lines,
                trigger_metrics=decision['trigger_metrics']
            )
            
            decision['rollback_result'] = rollback_result
            
            if self._on_rollback_callback:
                try:
                    self._on_rollback_callback(request_id, rollback_result)
                except Exception as e:
                    logger.error(f"回滚回调执行失败: {e}")
            
            self.stop_monitoring(request_id)
        
        return decision
    
    def start(self, mock: bool = True) -> None:
        """启动监控线程"""
        if self.running:
            return
        
        self.running = True
        
        def monitor_loop():
            while self.running:
                try:
                    active_requests = [
                        (rid, info) for rid, info in self.monitored_requests.items()
                        if info['active']
                    ]
                    
                    for request_id, info in active_requests:
                        try:
                            self._check_and_rollback(
                                request_id=request_id,
                                version=info['version'],
                                production_lines=info['production_lines'],
                                mock=mock
                            )
                        except Exception as e:
                            logger.error(f"监控 {request_id} 异常: {e}")
                    
                    time.sleep(self.check_interval)
                    
                except Exception as e:
                    logger.error(f"监控循环异常: {e}")
                    time.sleep(self.check_interval)
        
        self.monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info(f"自动回滚监控服务已启动，检查间隔: {self.check_interval}秒")
    
    def stop(self) -> None:
        """停止监控"""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        logger.info("自动回滚监控服务已停止")
    
    def get_monitor_status(self) -> Dict[str, Any]:
        """获取监控状态"""
        active_count = sum(1 for info in self.monitored_requests.values() if info['active'])
        
        return {
            'running': self.running,
            'check_interval_seconds': self.check_interval,
            'total_monitored': len(self.monitored_requests),
            'active_monitored': active_count,
            'monitored_requests': {
                rid: {
                    'version': info['version'],
                    'active': info['active'],
                    'start_time': info['start_time'].strftime('%Y-%m-%d %H:%M:%S'),
                    'consecutive_failures': self.decision_engine.consecutive_failures.get(rid, 0)
                }
                for rid, info in self.monitored_requests.items()
            }
        }
    
    def monitor_request(self, request_id: str) -> Dict[str, Any]:
        """
        便捷方法：开始监控指定的发布申请
        
        Args:
            request_id: 发布申请ID
            
        Returns:
            监控初始化结果
        """
        request = self.db.query_one('''
            SELECT version, target_production_lines FROM release_requests WHERE request_id = ?
        ''', (request_id,))
        
        if not request:
            raise ValueError(f"未找到发布申请: {request_id}")
        
        production_lines = json.loads(request['target_production_lines']) if request['target_production_lines'] else None
        
        self.start_monitoring(
            request_id=request_id,
            version=request['version'],
            production_lines=production_lines,
            mock=True
        )
        
        return {
            'request_id': request_id,
            'version': request['version'],
            'production_lines': production_lines,
            'monitoring': True,
            'message': '监控已启动，每5分钟检查一次关键指标'
        }
    
    @property
    def _running(self) -> bool:
        """兼容属性：返回监控服务运行状态"""
        return self.running


def get_metrics_collector() -> MonitorMetricsCollector:
    """获取指标采集器"""
    return MonitorMetricsCollector()


def get_rollback_engine() -> RollbackDecisionEngine:
    """获取回滚决策引擎"""
    return RollbackDecisionEngine()


def get_auto_monitor() -> AutoRollbackMonitor:
    """获取自动监控服务"""
    return AutoRollbackMonitor()
