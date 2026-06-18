import sys
import traceback
from datetime import datetime
from mes_ops.database import DatabaseManager
from mes_ops.deployment import GrayDeploymentEngine

def test_rollback():
    db = DatabaseManager()
    
    deployment = GrayDeploymentEngine()
    
    # 先获取一个存在的 request_id
    request = db.query_one('SELECT request_id, version FROM release_requests ORDER BY created_at DESC LIMIT 1')
    if not request:
        print("没有找到发布申请，先创建一个测试申请")
        # 创建一个测试申请
        request_id = f"TEST_REQ_{int(datetime.now().timestamp())}"
        db.execute('''
            INSERT INTO release_requests (request_id, version, module, risk_level, status, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (request_id, 'MES_V2.5.1', '生产管理', 'L1', 'APPROVED', '张三', datetime.now()))
    else:
        request_id = request['request_id']
    
    print(f"测试 rollback 方法，request_id: {request_id}")
    
    try:
        print("\n1. 测试 start_deployment...")
        result = deployment.start_deployment(request_id=request_id, operator='system')
        print(f"start_deployment 结果: {result}")
        
        print("\n2. 测试便捷 rollback 方法...")
        rollback_result = deployment.rollback(
            operator='system',
            request_id=request_id,
            reason='监控指标超过阈值，触发自动回滚'
        )
        print(f"rollback 结果: {rollback_result}")
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        print(f"错误类型: {type(e).__name__}")
        print(f"错误堆栈:")
        traceback.print_exc()
        return False
    
    print("\n✅ 测试成功！")
    return True

if __name__ == '__main__':
    success = test_rollback()
    sys.exit(0 if success else 1)
