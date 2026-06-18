"""
灰度部署与版本管理模块
支持按车间产线灰度策略分批部署
"""
import os
import json
import hashlib
import shutil
import random
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from pathlib import Path

from .database import get_db
from .logger import get_logger
from .config import get_config
from .constants import DeploymentStatus, DeploymentStage, RiskLevel, OperationType
from .audit import audit_operation, get_audit_logger

logger = get_logger(__name__)


class VersionManager:
    """版本管理器"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self.version_repo = Path(self.config.get('deployment.version_repository', './versions'))
        self.backup_dir = Path(self.config.get('deployment.backup_directory', './backups'))
        self.max_backups = self.config.get('deployment.max_backup_count', 10)
        
        self.version_repo.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def _calculate_md5(self, file_path: Path) -> str:
        """计算文件MD5"""
        md5_hash = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()
    
    def register_version(self, version: str, package_path: str, 
                         is_stable: bool = True, md5_checksum: str = None,
                         mock_mode: bool = None) -> Dict[str, Any]:
        """注册新版本到版本仓库"""
        if mock_mode is None:
            mock_mode = self.config.get('deployment.mock_mode', True)
        
        src_path = Path(package_path)
        if not mock_mode and not src_path.exists():
            raise FileNotFoundError(f"版本包不存在: {package_path}")
        
        version_dir = self.version_repo / version
        version_dir.mkdir(parents=True, exist_ok=True)
        
        dest_path = version_dir / src_path.name
        
        if not mock_mode:
            shutil.copy2(src_path, dest_path)
            md5 = self._calculate_md5(dest_path)
        else:
            md5 = md5_checksum or self._generate_mock_md5(version)
        
        self.db.execute('''
            INSERT OR REPLACE INTO version_snapshots 
            (version, package_path, md5_checksum, is_stable, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            version, str(dest_path), md5, is_stable,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))
        
        logger.info(f"版本已注册: {version}, MD5: {md5}, 路径: {dest_path}")
        
        return {
            'version': version,
            'package_path': str(dest_path),
            'md5_checksum': md5,
            'is_stable': is_stable
        }
    
    def _generate_mock_md5(self, version: str) -> str:
        """生成模拟的MD5校验和"""
        import hashlib
        data = f"{version}_{datetime.now().isoformat()}_mock"
        return hashlib.md5(data.encode('utf-8')).hexdigest()
    
    def get_stable_version(self) -> Optional[Dict[str, Any]]:
        """获取当前稳定版本"""
        return self.db.query_one('''
            SELECT * FROM version_snapshots 
            WHERE is_stable = 1 
            ORDER BY id DESC LIMIT 1
        ''')
    
    def get_version(self, version: str) -> Optional[Dict[str, Any]]:
        """获取指定版本信息"""
        return self.db.query_one('''
            SELECT * FROM version_snapshots WHERE version = ?
        ''', (version,))
    
    def verify_version(self, version: str) -> Tuple[bool, str]:
        """校验版本完整性"""
        version_info = self.get_version(version)
        if not version_info:
            return False, f"版本不存在: {version}"
        
        package_path = Path(version_info['package_path'])
        if not package_path.exists():
            return False, f"版本包已丢失: {package_path}"
        
        current_md5 = self._calculate_md5(package_path)
        if current_md5 != version_info['md5_checksum']:
            return False, f"版本MD5校验失败，文件可能已损坏"
        
        return True, "版本校验通过"
    
    def list_versions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """列出所有版本"""
        return self.db.query('''
            SELECT * FROM version_snapshots ORDER BY id DESC LIMIT ?
        ''', (limit,))


class GrayDeploymentEngine:
    """灰度部署引擎"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self.version_manager = VersionManager()
        self._load_gray_strategy()
    
    def _load_gray_strategy(self) -> None:
        """加载灰度策略配置"""
        self.gray_stages = self.config.get('deployment.gray_strategy.stages', [])
        self.workshops = self.config.get('deployment.workshops', [])
        
        self.line_to_workshop = {}
        for workshop in self.workshops:
            for line in workshop.get('lines', []):
                self.line_to_workshop[line] = workshop['name']
    
    def _get_stage_config(self, stage: DeploymentStage) -> Dict[str, Any]:
        """获取阶段配置"""
        for s in self.gray_stages:
            if s['stage'] == stage.value:
                return s
        raise ValueError(f"未知的部署阶段: {stage}")
    
    def _resolve_production_lines(self, stage_lines: List[str], 
                                  target_lines: List[str] = None) -> List[str]:
        """解析实际要部署的产线"""
        if 'all' in stage_lines:
            return target_lines or list(self.line_to_workshop.keys())
        
        if target_lines:
            return [line for line in stage_lines if line in target_lines]
        
        return stage_lines
    
    @audit_operation(OperationType.VERSION_DEPLOY, lambda args: args[2])
    def deploy_to_stage(self, request_id: str, version: str, 
                        stage: DeploymentStage, operator: str,
                        target_lines: List[str] = None,
                        mock: bool = True) -> Dict[str, Any]:
        """
        部署到指定灰度阶段
        
        Args:
            request_id: 发布申请ID
            version: 版本号
            stage: 部署阶段
            operator: 操作人
            target_lines: 目标产线（可选，覆盖默认配置）
            mock: 是否模拟部署
            
        Returns:
            部署结果
        """
        stage_config = self._get_stage_config(stage)
        production_lines = self._resolve_production_lines(
            stage_config.get('production_lines', []), target_lines
        )
        
        logger.info(f"开始部署版本 {version} 到 {stage_config['name']}，产线: {production_lines}")
        
        self.db.execute('''
            UPDATE release_requests 
            SET status = ?, updated_at = ?
            WHERE request_id = ?
        ''', (
            DeploymentStatus.DEPLOYING.value,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            request_id
        ))
        
        deploy_id = self.db.execute('''
            INSERT INTO deployment_records 
            (request_id, version, stage, stage_name, production_lines, 
             status, start_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            request_id, version, stage.value, stage_config['name'],
            json.dumps(production_lines, ensure_ascii=False),
            DeploymentStatus.DEPLOYING.value,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))
        
        deploy_results = []
        success_count = 0
        failed_count = 0
        
        for line in production_lines:
            try:
                if mock:
                    success = random.random() > 0.1
                    deploy_time = random.randint(30, 120)
                    
                    if success:
                        self.db.execute('''
                            UPDATE production_line_status 
                            SET current_version = ?, last_heartbeat = ?
                            WHERE line_name = ?
                        ''', (
                            version, 
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            line
                        ))
                else:
                    success = self._actual_deploy(version, line)
                    deploy_time = random.randint(30, 120)
                
                if success:
                    success_count += 1
                    deploy_results.append({
                        'line': line,
                        'workshop': self.line_to_workshop.get(line, '未知'),
                        'status': 'SUCCESS',
                        'deploy_time_seconds': deploy_time,
                        'message': f'产线 {line} 部署成功'
                    })
                else:
                    failed_count += 1
                    deploy_results.append({
                        'line': line,
                        'workshop': self.line_to_workshop.get(line, '未知'),
                        'status': 'FAILED',
                        'message': f'产线 {line} 部署失败'
                    })
            
            except Exception as e:
                failed_count += 1
                deploy_results.append({
                    'line': line,
                    'workshop': self.line_to_workshop.get(line, '未知'),
                    'status': 'FAILED',
                    'error': str(e),
                    'message': f'产线 {line} 部署异常: {e}'
                })
        
        overall_status = DeploymentStatus.GRAY_OBSERVING.value if success_count > 0 else DeploymentStatus.FAILED.value
        
        self.db.execute('''
            UPDATE deployment_records 
            SET status = ?, end_time = ?
            WHERE id = ?
        ''', (
            overall_status,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            deploy_id
        ))
        
        if overall_status == DeploymentStatus.GRAY_OBSERVING.value:
            self.db.execute('''
                UPDATE release_requests 
                SET status = ?, updated_at = ?
                WHERE request_id = ?
            ''', (
                DeploymentStatus.GRAY_OBSERVING.value,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                request_id
            ))
        
        result = {
            'deploy_id': deploy_id,
            'request_id': request_id,
            'version': version,
            'stage': stage.value,
            'stage_name': stage_config['name'],
            'traffic_ratio': stage_config.get('traffic_ratio', 0),
            'production_lines': production_lines,
            'success_count': success_count,
            'failed_count': failed_count,
            'total_count': len(production_lines),
            'results': deploy_results,
            'overall_status': overall_status,
            'observe_duration_minutes': stage_config.get('duration_minutes', 30)
        }
        
        get_audit_logger().log(
            operation_type=OperationType.VERSION_DEPLOY,
            operator=operator,
            request_params={
                "request_id": request_id,
                "version": version,
                "stage": stage.value,
                "production_lines": production_lines
            },
            response_result=result,
            status="SUCCESS" if success_count > 0 else "FAILED"
        )
        
        logger.info(f"部署完成: {success_count}/{len(production_lines)} 成功, 状态: {overall_status}")
        
        return result
    
    def _actual_deploy(self, version: str, production_line: str) -> bool:
        """实际执行部署（生产环境调用）"""
        version_info = self.version_manager.get_version(version)
        if not version_info:
            logger.error(f"版本不存在: {version}")
            return False
        
        valid, msg = self.version_manager.verify_version(version)
        if not valid:
            logger.error(f"版本校验失败: {msg}")
            return False
        
        # 实际部署逻辑：停止服务、替换文件、重启服务、健康检查
        # 这里为模拟实现
        logger.info(f"正在部署版本 {version} 到产线 {production_line}")
        return True
    
    @audit_operation(OperationType.VERSION_DEPLOY, lambda args: args[1])
    def confirm_full_deployment(self, operator: str, request_id: str, 
                                version: str) -> Dict[str, Any]:
        """确认全量部署完成"""
        all_lines = list(self.line_to_workshop.keys())
        
        self.db.execute('''
            UPDATE release_requests 
            SET status = ?, updated_at = ?
            WHERE request_id = ?
        ''', (
            DeploymentStatus.FULL_DEPLOYED.value,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            request_id
        ))
        
        self.db.execute('''
            INSERT INTO deployment_records 
            (request_id, version, stage, stage_name, production_lines, 
             status, start_time, end_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            request_id, version, 4, '全量阶段',
            json.dumps(all_lines, ensure_ascii=False),
            DeploymentStatus.FULL_DEPLOYED.value,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))
        
        for line in all_lines:
            self.db.execute('''
                UPDATE production_line_status 
                SET current_version = ?, last_heartbeat = ?
                WHERE line_name = ?
            ''', (
                version, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), line
            ))
        
        stable_version = self.version_manager.get_stable_version()
        if stable_version and stable_version['version'] != version:
            self.db.execute('''
                UPDATE version_snapshots SET is_stable = 0 WHERE version = ?
            ''', (stable_version['version'],))
        
        self.db.execute('''
            UPDATE version_snapshots SET is_stable = 1 WHERE version = ?
        ''', (version,))
        
        result = {
            'request_id': request_id,
            'version': version,
            'status': 'FULL_DEPLOYED',
            'production_lines': all_lines,
            'message': f'版本 {version} 已全量部署到所有产线，并标记为稳定版本'
        }
        
        logger.info(f"版本 {version} 全量部署完成，已标记为稳定版本")
        return result
    
    @audit_operation(OperationType.VERSION_ROLLBACK, lambda args: args[1])
    def rollback(self, operator: str, request_id: str, 
                 from_version: str, to_version: str,
                 reason: str, affected_lines: List[str] = None,
                 trigger_metrics: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        执行版本回滚
        
        Args:
            operator: 操作人
            request_id: 发布申请ID
            from_version: 回滚前版本
            to_version: 回滚目标版本（稳定版本）
            reason: 回滚原因
            affected_lines: 受影响产线
            trigger_metrics: 触发回滚的监控指标
            
        Returns:
            回滚结果
        """
        logger.warning(f"执行版本回滚: {from_version} -> {to_version}, 原因: {reason}")
        
        if affected_lines is None:
            affected_lines = list(self.line_to_workshop.keys())
        
        estimated_defect_count = self._estimate_defect_count(affected_lines)
        root_cause = self._analyze_root_cause(trigger_metrics, reason)
        
        self.db.execute('''
            UPDATE release_requests 
            SET status = ?, updated_at = ?
            WHERE request_id = ?
        ''', (
            DeploymentStatus.ROLLING_BACK.value,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            request_id
        ))
        
        rollback_results = []
        success_count = 0
        
        for line in affected_lines:
            try:
                success = random.random() > 0.05
                
                if success:
                    success_count += 1
                    self.db.execute('''
                        UPDATE production_line_status 
                        SET current_version = ?, auto_production_enabled = 0,
                            fallback_mode = 'LOCAL_DB', last_heartbeat = ?
                        WHERE line_name = ?
                    ''', (
                        to_version, 
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        line
                    ))
                    
                    self.db.execute('''
                        INSERT INTO permission_changes 
                        (production_line, permission_status, reason, operator, changed_at)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (
                        line, 'LOCKED', reason, operator,
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    ))
                
                rollback_results.append({
                    'line': line,
                    'workshop': self.line_to_workshop.get(line, '未知'),
                    'status': 'SUCCESS' if success else 'FAILED',
                    'permission_locked': success
                })
            
            except Exception as e:
                rollback_results.append({
                    'line': line,
                    'workshop': self.line_to_workshop.get(line, '未知'),
                    'status': 'FAILED',
                    'error': str(e)
                })
        
        overall_status = 'ROLLED_BACK' if success_count > 0 else 'ROLLBACK_FAILED'
        
        self.db.execute('''
            UPDATE release_requests 
            SET status = ?, updated_at = ?
            WHERE request_id = ?
        ''', (
            DeploymentStatus.ROLLED_BACK.value if success_count > 0 else DeploymentStatus.FAILED.value,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            request_id
        ))
        
        self.db.execute('''
            INSERT INTO deployment_records 
            (request_id, version, stage, stage_name, production_lines, 
             status, start_time, end_time, rollback_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            request_id, to_version, 0, '回滚',
            json.dumps(affected_lines, ensure_ascii=False),
            DeploymentStatus.ROLLED_BACK.value,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            reason
        ))
        
        rollback_id = self.db.execute('''
            INSERT INTO rollback_records 
            (request_id, rollback_reason, from_version, to_version, 
             affected_lines, trigger_metrics, rollback_time, 
             estimated_defect_count, root_cause)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            request_id, reason, from_version, to_version,
            json.dumps(affected_lines, ensure_ascii=False),
            json.dumps(trigger_metrics or {}, ensure_ascii=False),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            estimated_defect_count,
            json.dumps(root_cause, ensure_ascii=False)
        ))
        
        get_audit_logger().log(
            operation_type=OperationType.VERSION_ROLLBACK,
            operator=operator,
            request_params={
                "request_id": request_id,
                "from_version": from_version,
                "to_version": to_version,
                "reason": reason,
                "affected_lines": affected_lines
            },
            response_result={
                "rollback_id": rollback_id,
                "estimated_defect_count": estimated_defect_count,
                "root_cause": root_cause
            },
            status="SUCCESS"
        )
        
        return {
            'rollback_id': rollback_id,
            'request_id': request_id,
            'from_version': from_version,
            'to_version': to_version,
            'reason': reason,
            'affected_lines': affected_lines,
            'success_count': success_count,
            'total_count': len(affected_lines),
            'estimated_defect_count': estimated_defect_count,
            'root_cause': root_cause,
            'results': rollback_results,
            'overall_status': overall_status
        }
    
    def _estimate_defect_count(self, affected_lines: List[str]) -> int:
        """预估不良品数量"""
        total = 0
        downtime_minutes = random.randint(30, 120)
        
        for line in affected_lines:
            workshop = self.line_to_workshop.get(line)
            if workshop:
                for ws in self.workshops:
                    if ws['name'] == workshop:
                        output_rate = ws.get('output_rate_per_hour', 60)
                        defect_rate = ws.get('defect_rate', 0.02)
                        line_output = output_rate * (downtime_minutes / 60)
                        total += int(line_output * defect_rate)
                        break
        
        return max(total, 1)
    
    def _analyze_root_cause(self, trigger_metrics: Dict[str, Any], 
                            reason: str) -> Dict[str, Any]:
        """分析故障根因"""
        root_cause = {
            'primary_cause': '',
            'contributing_factors': [],
            'suggested_fixes': []
        }
        
        if trigger_metrics:
            error_rate = trigger_metrics.get('work_order_error_rate', 0)
            latency = trigger_metrics.get('data_collection_latency', 0)
            anomalies = trigger_metrics.get('process_param_anomalies', 0)
            
            if error_rate > 2:
                root_cause['primary_cause'] = '工单上报异常率过高'
                root_cause['contributing_factors'].append('数据校验逻辑变更导致工单写入失败')
                root_cause['suggested_fixes'].append('回滚工单校验模块，检查新业务规则')
            elif latency > 500:
                root_cause['primary_cause'] = '设备数据采集响应延迟过高'
                root_cause['contributing_factors'].append('PLC接口协议处理性能下降')
                root_cause['suggested_fixes'].append('检查OPC UA连接池配置，优化数据解析逻辑')
            elif anomalies > 5:
                root_cause['primary_cause'] = '生产工艺参数异常次数过多'
                root_cause['contributing_factors'].append('工艺参数阈值配置变更')
                root_cause['suggested_fixes'].append('检查参数阈值配置，验证工艺配方正确性')
        
        if not root_cause['primary_cause']:
            root_cause['primary_cause'] = reason or '综合因素导致系统异常'
            root_cause['contributing_factors'].append('需要进一步分析日志定位具体原因')
        
        return root_cause
    
    def get_deployment_status(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取部署状态"""
        request = self.db.query_one('''
            SELECT * FROM release_requests WHERE request_id = ?
        ''', (request_id,))
        
        if not request:
            return None
        
        deployments = self.db.query('''
            SELECT * FROM deployment_records WHERE request_id = ? ORDER BY id DESC
        ''', (request_id,))
        
        return {
            'request': request,
            'deployments': deployments
        }
    
    def get_production_line_status(self, line_name: str = None) -> List[Dict[str, Any]]:
        """获取产线状态"""
        if line_name:
            result = self.db.query_one('''
                SELECT * FROM production_line_status WHERE line_name = ?
            ''', (line_name,))
            return [result] if result else []
        
        return self.db.query('''
            SELECT * FROM production_line_status ORDER BY line_name
        ''')
    
    @audit_operation(OperationType.PERMISSION_CHANGE, lambda args: args[1])
    def restore_production_line(self, operator: str, line_name: str,
                                reason: str = "故障修复完成") -> Dict[str, Any]:
        """恢复产线自动生产权限"""
        logger.info(f"恢复产线自动生产权限: {line_name}, 原因: {reason}")
        
        self.db.execute('''
            UPDATE production_line_status 
            SET auto_production_enabled = 1, fallback_mode = 'NORMAL',
                last_heartbeat = ?
            WHERE line_name = ?
        ''', (
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            line_name
        ))
        
        self.db.execute('''
            INSERT INTO permission_changes 
            (production_line, permission_status, reason, operator, changed_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            line_name, 'UNLOCKED', reason, operator,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))
        
        get_audit_logger().log(
            operation_type=OperationType.PERMISSION_CHANGE,
            operator=operator,
            request_params={"line_name": line_name, "reason": reason},
            response_result={"status": "UNLOCKED"},
            status="SUCCESS"
        )
        
        return {
            'line_name': line_name,
            'status': 'UNLOCKED',
            'auto_production_enabled': True,
            'fallback_mode': 'NORMAL'
        }


def get_version_manager() -> VersionManager:
    """获取版本管理器实例"""
    return VersionManager()


def get_deployment_engine() -> GrayDeploymentEngine:
    """获取灰度部署引擎实例"""
    return GrayDeploymentEngine()
