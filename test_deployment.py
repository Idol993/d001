import traceback
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mes_ops.deployment import GrayDeploymentEngine
from mes_ops.database import get_db
from mes_ops.constants import DeploymentStatus

db = get_db()

# 先创建一个测试申请
request_id = "TEST_REQ_" + str(int(__import__('time').time()))
db.execute('''
    INSERT INTO release_requests 
    (request_id, version, risk_level, applicant, department, 
     description, change_content, status, target_production_lines)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
''', (
    request_id, 'MES_V2.5.1', 'L1_NORMAL', 'test_user', '研发部',
    '测试版本', '测试内容', DeploymentStatus.DEPLOYING.value,
    json.dumps(['Line1', 'Line2', 'Line3'], ensure_ascii=False)
))

engine = GrayDeploymentEngine()

try:
    print("测试 start_deployment...")
    result = engine.start_deployment(
        request_id=request_id,
        version='MES_V2.5.1',
        target_production_lines=['Line1', 'Line2', 'Line3'],
        operator='system'
    )
    print("start_deployment 结果:", result)
    
    print("\n测试 get_current_stage...")
    stage = engine.get_current_stage(request_id)
    print("当前阶段:", stage)
    
    print("\n测试 deploy_to_next_stage...")
    next_stage = engine.deploy_to_next_stage(
        request_id=request_id,
        operator='system',
        mock=True
    )
    print("下一阶段:", next_stage)
    
except Exception as e:
    print("Error:", e)
    traceback.print_exc()
