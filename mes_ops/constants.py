"""
全局常量定义
"""
from enum import Enum, IntEnum


class RiskLevel(str, Enum):
    """版本发布风险等级"""
    L1_NORMAL = "L1_NORMAL"
    L2_URGENT = "L2_URGENT"


class ApprovalStatus(str, Enum):
    """审批状态"""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"


class DeploymentStatus(str, Enum):
    """部署状态"""
    PRE_CHECKING = "PRE_CHECKING"
    PRE_CHECK_FAILED = "PRE_CHECK_FAILED"
    APPROVING = "APPROVING"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    DEPLOYING = "DEPLOYING"
    GRAY_OBSERVING = "GRAY_OBSERVING"
    FULL_DEPLOYED = "FULL_DEPLOYED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DeploymentStage(IntEnum):
    """灰度部署阶段"""
    PILOT = 1
    EXTENDED = 2
    HALF = 3
    FULL = 4


class MonitorMetricType(str, Enum):
    """监控指标类型"""
    WORK_ORDER_ERROR_RATE = "work_order_error_rate"
    DATA_COLLECTION_LATENCY = "data_collection_latency"
    PROCESS_PARAM_ANOMALIES = "process_param_anomalies"


class AlertLevel(str, Enum):
    """告警等级"""
    LEVEL1 = "LEVEL1"
    LEVEL2 = "LEVEL2"
    LEVEL3 = "LEVEL3"


class OperationType(str, Enum):
    """操作类型（用于审计日志）"""
    VERSION_DEPLOY = "version_deploy"
    VERSION_ROLLBACK = "version_rollback"
    PERMISSION_CHANGE = "permission_change"
    MONITOR_ALERT = "monitor_alert"
    MANUAL_APPROVAL = "manual_approval"
    EMERGENCY_DRILL = "emergency_drill"
    SYSTEM_CONFIG = "system_config"


class DrillStatus(str, Enum):
    """应急演练状态"""
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FallbackMode(str, Enum):
    """兜底模式"""
    NORMAL = "NORMAL"
    EDGE_BUFFER = "EDGE_BUFFER"
    LOCAL_DB = "LOCAL_DB"
    MANUAL = "MANUAL"


SYSTEM_NAME = "汽车零部件工厂MES生产执行系统运维管理平台"

PRE_CHECK_ITEMS = [
    ("test_coverage", "自动化测试覆盖率"),
    ("code_security", "代码安全合规审查"),
    ("plc_interface", "PLC设备接口检测"),
    ("wms_system", "WMS系统健康检查")
]

APPROVER_ROLES = [
    "production_manager",
    "quality_manager", 
    "ops_manager",
    "factory_director"
]

DEFAULT_PRODUCTION_LINES = [
    "冲压车间-01号线", "冲压车间-02号线", "冲压车间-03号线",
    "焊接车间-01号线", "焊接车间-02号线",
    "涂装车间-01号线", "涂装车间-02号线",
    "总装车间-01号线", "总装车间-02号线", "总装车间-03号线", "总装车间-04号线"
]

WORKSHOP_CONFIG = {
    "冲压车间": {
        "lines": ["冲压车间-01号线", "冲压车间-02号线", "冲压车间-03号线"],
        "output_rate": 120,
        "defect_rate": 0.015
    },
    "焊接车间": {
        "lines": ["焊接车间-01号线", "焊接车间-02号线"],
        "output_rate": 100,
        "defect_rate": 0.02
    },
    "涂装车间": {
        "lines": ["涂装车间-01号线", "涂装车间-02号线"],
        "output_rate": 80,
        "defect_rate": 0.025
    },
    "总装车间": {
        "lines": ["总装车间-01号线", "总装车间-02号线", "总装车间-03号线", "总装车间-04号线"],
        "output_rate": 60,
        "defect_rate": 0.018
    }
}
