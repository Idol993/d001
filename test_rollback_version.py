"""
测试回滚版本功能
"""
import sys
import os
import time
import json
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mes_ops.deployment import get_deployment_engine
from mes_ops.approval import get_approval_manager, RiskLevel
from mes_ops.database import get_db
from mes_ops.constants import DeploymentStatus


def test_rollback_version():
    """测试从新版本回退到旧稳定版"""
    print("=" * 60)
    print("测试: 版本回滚 - 从新版本回退到旧稳定版")
    print("=" * 60)
    
    deployment_engine = get_deployment_engine()
    approval_manager = get_approval_manager()
    db = get_db()
    
    # 1. 先注册旧稳定版本
    old_stable = "MES_V2.5.0"
    new_version = "MES_V2.5.1"
    
    print(f"\n1. 注册旧稳定版本: {old_stable}")
    deployment_engine.register_mock_version(old_stable, is_stable=True)
    stable = deployment_engine.get_stable_version()
    print(f"   当前稳定版本: {stable['version'] if stable else '无'}")
    
    # 2. 设置产线当前版本为旧稳定版
    db.execute('''
        UPDATE production_line_status 
        SET current_version = ?, auto_production_enabled = 1,
            fallback_mode = 'NORMAL'
    ''', (old_stable,))
    
    # 检查产线当前版本
    lines = db.query('SELECT line_name, current_version FROM production_line_status LIMIT 3')
    print(f"\n2. 产线当前版本:")
    for line in lines:
        print(f"   {line['line_name']}: {line['current_version']}")
    
    # 3. 创建新版本发布申请
    print(f"\n3. 创建新版本发布申请: {new_version}")
    request_id = approval_manager.create_release_request(
        version=new_version,
        risk_level=RiskLevel.L1_NORMAL,
        applicant="测试用户",
        department="研发部",
        description="测试版本回滚",
        change_content="测试内容",
        target_production_lines=["冲压车间-01号线", "焊接车间-01号线"]
    )
    print(f"   申请ID: {request_id}")
    
    # 4. 启动审批流程并审批通过
    print("\n4. 启动审批流程并审批通过")
    workflow = approval_manager.create_workflow(request_id)
    for role in ['production_manager', 'quality_manager', 'ops_manager']:
        workflow.approve(
            approver_role=role,
            approver_name=f"审批人{role}",
            approved=True,
            comment="同意"
        )
    print("   审批全部通过")
    
    # 5. 开始灰度部署
    print("\n5. 开始灰度部署")
    deployment_engine.start_deployment(request_id, mock=True)
    stage = deployment_engine.deploy_to_next_stage(request_id, operator='system', mock=True)
    print(f"   部署到阶段: {stage.value if stage else 'None'}")
    
    # 6. 触发回滚
    print("\n6. 触发自动回滚")
    rollback_result = deployment_engine.rollback(
        operator='system',
        request_id=request_id,
        reason='监控指标超过阈值',
        mock=True
    )
    
    # 7. 检查回滚结果
    from_version = rollback_result.get('from_version')
    to_version = rollback_result.get('to_version')
    
    print(f"\n7. 回滚结果:")
    print(f"   从版本: {from_version}")
    print(f"   到版本: {to_version}")
    print(f"   回滚状态: {rollback_result.get('overall_status')}")
    print(f"   成功产线数: {rollback_result.get('success_count')}/{rollback_result.get('total_count')}")
    
    # 验证
    assert from_version == new_version, f"from_version 应该是 {new_version}，实际是 {from_version}"
    assert to_version == old_stable, f"to_version 应该是 {old_stable}，实际是 {to_version}"
    assert from_version != to_version, "from_version 和 to_version 不应该相同"
    
    print("\n8. 检查产线版本是否已更新:")
    lines = db.query('SELECT line_name, current_version, auto_production_enabled FROM production_line_status LIMIT 5')
    for line in lines:
        print(f"   {line['line_name']}: {line['current_version']}, "
              f"自动生产: {'启用' if line['auto_production_enabled'] else '锁定'}")
        # 验证版本已回退到旧稳定版
        if line['line_name'] in ["冲压车间-01号线", "焊接车间-01号线"]:
            assert line['current_version'] == old_stable, \
                f"产线 {line['line_name']} 版本应该是 {old_stable}，实际是 {line['current_version']}"
            assert line['auto_production_enabled'] == 0, \
                f"产线 {line['line_name']} 自动生产应该被锁定"
    
    print("\n9. 检查发布申请状态:")
    request = db.query_one('SELECT status FROM release_requests WHERE request_id = ?', (request_id,))
    print(f"   申请状态: {request['status']}")
    assert request['status'] == DeploymentStatus.ROLLED_BACK.value, \
        f"状态应该是 {DeploymentStatus.ROLLED_BACK.value}，实际是 {request['status']}"
    
    print("\n10. 检查回滚记录:")
    rollback_record = db.query_one('SELECT * FROM rollback_records WHERE request_id = ? ORDER BY id DESC LIMIT 1', (request_id,))
    if rollback_record:
        print(f"   记录ID: {rollback_record['id']}")
        print(f"   从版本: {rollback_record['from_version']}")
        print(f"   到版本: {rollback_record['to_version']}")
        print(f"   原因: {rollback_record['rollback_reason']}")
        print(f"   预估不良品: {rollback_record['estimated_defect_count']}")
        
        assert rollback_record['from_version'] == new_version
        assert rollback_record['to_version'] == old_stable
    
    print("\n✅ 版本回滚测试通过！")
    print(f"   成功从 {new_version} 回退到 {old_stable}")
    print(f"   受影响产线自动生产权限已锁定")
    
    return True


if __name__ == '__main__':
    try:
        test_rollback_version()
        print("\n" + "=" * 60)
        print("🎉 所有回滚测试通过！")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
