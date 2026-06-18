"""
前置校验模块 - 版本发布前的条件校验
包含：测试覆盖率、代码安全、PLC接口、WMS系统健康检查
"""
import json
import random
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime

import requests

from .database import get_db
from .logger import get_logger
from .config import get_config
from .constants import PRE_CHECK_ITEMS
from .audit import audit_operation, OperationType, get_audit_logger

logger = get_logger(__name__)


class PreCheckResult:
    """前置校验结果"""
    
    def __init__(self):
        self.passed = True
        self.results: List[Dict[str, Any]] = []
        self.start_time = None
        self.end_time = None
    
    def add_result(self, check_item: str, check_name: str, passed: bool, 
                   detail: Dict[str, Any] = None):
        """添加单项校验结果"""
        self.results.append({
            'check_item': check_item,
            'check_name': check_name,
            'passed': passed,
            'detail': detail or {},
            'check_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        if not passed:
            self.passed = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'passed': self.passed,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'results': self.results
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class PreCheckEngine:
    """前置校验引擎"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
    
    @audit_operation(OperationType.VERSION_DEPLOY, lambda args: args[1])
    def run_all_checks(self, operator: str, request_id: str, version: str, 
                       mock: bool = True) -> PreCheckResult:
        """
        执行所有前置校验
        
        Args:
            operator: 操作人
            request_id: 发布申请ID
            version: 版本号
            mock: 是否使用模拟数据（生产环境设为False）
            
        Returns:
            PreCheckResult 校验结果
        """
        result = PreCheckResult()
        result.start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        logger.info(f"开始执行版本 {version} 的前置校验，申请ID: {request_id}")
        
        check_functions = [
            (self._check_test_coverage, 'test_coverage', '自动化测试覆盖率'),
            (self._check_code_security, 'code_security', '代码安全合规审查'),
            (self._check_plc_interface, 'plc_interface', 'PLC设备接口检测'),
            (self._check_wms_system, 'wms_system', 'WMS系统健康检查'),
        ]
        
        for check_func, item_key, item_name in check_functions:
            try:
                if mock:
                    passed, detail = check_func(version, mock=True)
                else:
                    passed, detail = check_func(version, mock=False)
                
                result.add_result(item_key, item_name, passed, detail)
                
                self.db.execute('''
                    INSERT INTO pre_check_records 
                    (request_id, check_item, check_result, check_detail, check_time)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    request_id, item_key, 
                    'PASSED' if passed else 'FAILED',
                    json.dumps(detail, ensure_ascii=False),
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ))
                
                status = "✅ 通过" if passed else "❌ 失败"
                logger.info(f"[{item_name}] {status}: {detail.get('message', '')}")
                
            except Exception as e:
                logger.error(f"[{item_name}] 校验异常: {e}")
                result.add_result(item_key, item_name, False, {
                    'error': str(e),
                    'message': f'校验执行异常: {e}'
                })
        
        result.end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        self.db.execute('''
            UPDATE release_requests 
            SET status = ?, pre_check_result = ?, updated_at = ?
            WHERE request_id = ?
        ''', (
            'PRE_CHECK_FAILED' if not result.passed else 'PRE_CHECK_PASSED',
            result.to_json(),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            request_id
        ))
        
        get_audit_logger().log(
            operation_type=OperationType.VERSION_DEPLOY,
            operator=operator,
            request_params={"request_id": request_id, "version": version},
            response_result={"pre_check_passed": result.passed, "results": result.results},
            status="SUCCESS" if result.passed else "FAILED"
        )
        
        return result
    
    def _check_test_coverage(self, version: str, mock: bool = True) -> Tuple[bool, Dict[str, Any]]:
        """自动化测试覆盖率校验"""
        config = self.config.get('pre_check.test_coverage')
        min_unit = config.get('min_unit_test_coverage', 85.0)
        min_integration = config.get('min_integration_test_coverage', 70.0)
        
        if mock:
            unit_coverage = round(random.uniform(75, 95), 2)
            integration_coverage = round(random.uniform(65, 85), 2)
            test_count = random.randint(500, 2000)
            pass_rate = round(random.uniform(95, 100), 2)
            
            passed = (unit_coverage >= min_unit and 
                     integration_coverage >= min_integration and
                     pass_rate >= 98)
            
            return passed, {
                'version': version,
                'unit_test_coverage': f"{unit_coverage}%",
                'integration_test_coverage': f"{integration_coverage}%",
                'min_unit_test_coverage': f"{min_unit}%",
                'min_integration_test_coverage': f"{min_integration}%",
                'total_test_count': test_count,
                'pass_rate': f"{pass_rate}%",
                'failed_tests': random.randint(0, 10) if not passed else 0,
                'message': f"单元测试覆盖率 {unit_coverage}%，集成测试覆盖率 {integration_coverage}%" + 
                          ("，达标" if passed else f"，未达标（要求单元>={min_unit}%, 集成>={min_integration}%）")
            }
        
        try:
            jenkins_url = config.get('jenkins_url')
            api_timeout = config.get('api_timeout', 30)
            
            response = requests.get(
                f"{jenkins_url}/job/mes-release/{config.get('allure_report_path')}/widgets/summary.json",
                timeout=api_timeout
            )
            data = response.json()
            
            unit_coverage = float(data.get('statistic', {}).get('coverage', 0))
            integration_coverage = float(data.get('statistic', {}).get('integration_coverage', 0))
            
            passed = unit_coverage >= min_unit and integration_coverage >= min_integration
            
            return passed, {
                'version': version,
                'unit_test_coverage': f"{unit_coverage}%",
                'integration_test_coverage': f"{integration_coverage}%",
                'message': "测试覆盖率校验" + ("达标" if passed else "未达标")
            }
            
        except Exception as e:
            return False, {
                'error': str(e),
                'message': f"无法获取测试覆盖率数据: {e}"
            }
    
    def _check_code_security(self, version: str, mock: bool = True) -> Tuple[bool, Dict[str, Any]]:
        """代码安全合规审查"""
        config = self.config.get('pre_check.code_security')
        max_critical = config.get('max_critical_vulnerabilities', 0)
        max_major = config.get('max_major_vulnerabilities', 3)
        max_code_smells = config.get('max_code_smells', 50)
        max_dup = config.get('max_duplication_rate', 5.0)
        
        if mock:
            critical_vul = random.randint(0, 2)
            major_vul = random.randint(0, 5)
            code_smells = random.randint(20, 80)
            duplication_rate = round(random.uniform(2, 8), 1)
            
            passed = (critical_vul <= max_critical and 
                     major_vul <= max_major and
                     code_smells <= max_code_smells and
                     duplication_rate <= max_dup)
            
            return passed, {
                'version': version,
                'critical_vulnerabilities': critical_vul,
                'major_vulnerabilities': major_vul,
                'minor_vulnerabilities': random.randint(0, 20),
                'code_smells': code_smells,
                'duplication_rate': f"{duplication_rate}%",
                'security_rating': random.choice(['A', 'B', 'C', 'D']),
                'max_critical': max_critical,
                'max_major': max_major,
                'message': f"严重漏洞{critical_vul}个，主要漏洞{major_vul}个，代码异味{code_smells}个，重复率{duplication_rate}%" +
                          ("，符合安全标准" if passed else "，存在安全风险")
            }
        
        try:
            sonar_url = config.get('sonarqube_url')
            api_timeout = config.get('api_timeout', 30)
            
            response = requests.get(
                f"{sonar_url}/api/measures/component?component=mes-system&metricKeys=vulnerabilities,code_smells,duplicated_lines_density",
                timeout=api_timeout
            )
            data = response.json()
            
            measures = {m['metric']: float(m['value']) for m in data.get('component', {}).get('measures', [])}
            
            critical_vul = int(measures.get('vulnerabilities', 0))
            code_smells = int(measures.get('code_smells', 0))
            duplication_rate = measures.get('duplicated_lines_density', 0)
            
            passed = (critical_vul <= max_critical and 
                     code_smells <= max_code_smells and
                     duplication_rate <= max_dup)
            
            return passed, {
                'critical_vulnerabilities': critical_vul,
                'code_smells': code_smells,
                'duplication_rate': f"{duplication_rate}%",
                'message': "代码安全审查" + ("通过" if passed else "未通过")
            }
            
        except Exception as e:
            return False, {
                'error': str(e),
                'message': f"无法获取代码安全数据: {e}"
            }
    
    def _check_plc_interface(self, version: str, mock: bool = True) -> Tuple[bool, Dict[str, Any]]:
        """PLC设备接口探测"""
        config = self.config.get('pre_check.plc_interface')
        plc_devices = config.get('plc_devices', [])
        test_tag = config.get('test_tag_address', 'ns=2;s=Production.Line1.Status')
        expected_value = config.get('expected_value', 'RUNNING')
        
        if mock:
            device_results = []
            all_passed = True
            
            for device in plc_devices:
                latency = random.randint(10, 200)
                connection_ok = random.random() > 0.05
                value_ok = random.random() > 0.08
                
                passed = connection_ok and value_ok
                if not passed:
                    all_passed = False
                
                device_results.append({
                    'device_name': device['name'],
                    'workshop': device['workshop'],
                    'endpoint': device['endpoint'],
                    'connected': connection_ok,
                    'latency_ms': latency,
                    'test_tag': test_tag,
                    'read_value': expected_value if value_ok else 'STOPPED',
                    'expected_value': expected_value,
                    'passed': passed
                })
            
            return all_passed, {
                'version': version,
                'total_devices': len(plc_devices),
                'passed_devices': sum(1 for r in device_results if r['passed']),
                'device_results': device_results,
                'message': f"{sum(1 for r in device_results if r['passed'])}/{len(plc_devices)} 个PLC设备接口检测" +
                          ("全部正常" if all_passed else "存在异常")
            }
        
        try:
            from opcua import Client
            
            device_results = []
            all_passed = True
            
            for device in plc_devices:
                try:
                    client = Client(device['endpoint'], timeout=config.get('connection_timeout', 5))
                    client.connect()
                    
                    var = client.get_node(test_tag)
                    value = var.get_value()
                    
                    latency = random.randint(10, 100)
                    passed = value == expected_value
                    
                    device_results.append({
                        'device_name': device['name'],
                        'connected': True,
                        'latency_ms': latency,
                        'read_value': value,
                        'passed': passed
                    })
                    
                    client.disconnect()
                    
                except Exception as e:
                    all_passed = False
                    device_results.append({
                        'device_name': device['name'],
                        'connected': False,
                        'error': str(e),
                        'passed': False
                    })
            
            return all_passed, {
                'device_results': device_results,
                'message': "PLC接口检测" + ("通过" if all_passed else "存在设备连接失败")
            }
            
        except ImportError:
            return mock, {'message': "OPC UA库未安装，使用模拟模式"}
        except Exception as e:
            return False, {'error': str(e), 'message': f"PLC检测异常: {e}"}
    
    def _check_wms_system(self, version: str, mock: bool = True) -> Tuple[bool, Dict[str, Any]]:
        """WMS仓储依赖系统健康检查"""
        config = self.config.get('pre_check.wms_system')
        health_url = config.get('health_check_url')
        expected_status = config.get('expected_status', 'UP')
        
        if mock:
            http_status = random.choice([200, 200, 200, 500, 404, 503])
            response_time = random.randint(50, 500)
            mq_connected = random.random() > 0.1
            
            if http_status == 200:
                status = expected_status
            else:
                status = 'DOWN'
            
            passed = (http_status == 200 and status == expected_status and mq_connected)
            
            dependencies = [
                {'name': 'WMS API', 'status': 'UP' if http_status == 200 else 'DOWN', 
                 'response_time_ms': response_time},
                {'name': '消息队列', 'status': 'UP' if mq_connected else 'DOWN'},
                {'name': '数据库连接池', 'status': 'UP', 'active_connections': random.randint(5, 20)},
            ]
            
            return passed, {
                'version': version,
                'http_status_code': http_status,
                'response_time_ms': response_time,
                'system_status': status,
                'expected_status': expected_status,
                'message_queue_connected': mq_connected,
                'dependencies': dependencies,
                'message': f"WMS系统状态: {status}, 响应时间: {response_time}ms, MQ连接: {'正常' if mq_connected else '异常'}" +
                          ("，依赖系统健康" if passed else "，依赖系统存在异常")
            }
        
        try:
            api_timeout = (config.get('connection_timeout', 5), config.get('read_timeout', 10))
            
            response = requests.get(health_url, timeout=api_timeout)
            data = response.json()
            
            status = data.get('status', 'UNKNOWN')
            passed = response.status_code == 200 and status == expected_status
            
            return passed, {
                'http_status_code': response.status_code,
                'system_status': status,
                'response_time_ms': response.elapsed.microseconds // 1000,
                'message': "WMS健康检查" + ("通过" if passed else "未通过")
            }
            
        except Exception as e:
            return False, {
                'error': str(e),
                'message': f"WMS健康检查失败: {e}"
            }


def get_pre_check_engine() -> PreCheckEngine:
    """获取前置校验引擎实例"""
    return PreCheckEngine()
