"""
测试审批拒绝流程
"""
import sys
import os
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mes_ops.approval import get_approval_manager, RiskLevel
from mes_ops.constants import DeploymentStatus


def test_approval_reject_normal():
    """测试常规版本审批拒绝"""
    print("=" * 60)
    print("测试1: 常规版本审批拒绝")
    print("=" * 60)
    
    approval_manager = get_approval_manager()
    
    # 创建一个常规版本发布申请
    request_id = approval_manager.create_release_request(
        version="MES_V2.6.0",
        risk_level=RiskLevel.L1_NORMAL,
        applicant="测试用户",
        department="研发部",
        description="测试审批拒绝流程",
        change_content="测试内容",
        target_production_lines=["冲压车间-01号线"]
    )
    print(f"创建发布申请: {request_id}")
    
    # 启动审批流程
    workflow = approval_manager.create_workflow(request_id)
    print(f"审批人: {[a['name'] for a in workflow.approvers]}")
    
    # 第一个审批人拒绝
    print("\n第一个审批人(生产经理)拒绝...")
    is_completed, status_msg = workflow.approve(
        approver_role='production_manager',
        approver_name='李四',
        approved=False,
        comment="功能不完善，需要重新评估"
    )
    
    # 查询申请详情
    request_detail = approval_manager.get_request_detail(request_id)
    
    print(f"\n审批完成: {is_completed}")
    print(f"状态信息: {status_msg}")
    print(f"申请状态: {request_detail.get('status')}")
    print(f"是否被拒绝: {workflow.is_rejected()}")
    print(f"是否已通过: {workflow.is_approved()}")
    
    # 打印审批记录
    print("\n审批记录:")
    for approval in request_detail.get('approvals', []):
        print(f"  - {approval['approver_name']} ({approval['approver_role']}): "
              f"{approval['approval_status']} - {approval.get('approval_comment', '')}")
    
    # 验证状态
    assert request_detail.get('status') == DeploymentStatus.APPROVAL_REJECTED.value, \
        f"状态应该是 APPROVAL_REJECTED，但实际是 {request_detail.get('status')}"
    assert workflow.is_rejected() == True, "应该被拒绝"
    assert workflow.is_approved() == False, "不应该被通过"
    
    print("\n✅ 常规版本审批拒绝测试通过！")
    return True


def test_approval_reject_urgent():
    """测试紧急版本审批拒绝"""
    print("\n" + "=" * 60)
    print("测试2: 紧急版本审批拒绝")
    print("=" * 60)
    
    approval_manager = get_approval_manager()
    
    # 创建一个紧急版本发布申请
    request_id = approval_manager.create_release_request(
        version="MES_V2.6.1-hotfix",
        risk_level=RiskLevel.L2_URGENT,
        applicant="测试用户",
        department="研发部",
        description="测试紧急版本审批拒绝",
        change_content="紧急修复内容",
        target_production_lines=["焊接车间-01号线"]
    )
    print(f"创建发布申请: {request_id}")
    
    # 启动审批流程
    workflow = approval_manager.create_workflow(request_id)
    print(f"审批人: {[a['name'] for a in workflow.approvers]}")
    
    # 第一个审批人通过
    print("\n第一个审批人(生产经理)通过...")
    is_completed, status_msg = workflow.approve(
        approver_role='production_manager',
        approver_name='李四',
        approved=True,
        comment="同意"
    )
    print(f"完成: {is_completed}, 状态: {status_msg}")
    
    # 第二个审批人拒绝
    print("\n第二个审批人(质量经理)拒绝...")
    is_completed, status_msg = workflow.approve(
        approver_role='quality_manager',
        approver_name='王五',
        approved=False,
        comment="存在质量风险，需要重新验证"
    )
    
    # 查询申请详情
    request_detail = approval_manager.get_request_detail(request_id)
    
    print(f"\n审批完成: {is_completed}")
    print(f"状态信息: {status_msg}")
    print(f"申请状态: {request_detail.get('status')}")
    print(f"是否被拒绝: {workflow.is_rejected()}")
    
    # 打印审批记录
    print("\n审批记录:")
    for approval in request_detail.get('approvals', []):
        print(f"  - {approval['approver_name']} ({approval['approver_role']}): "
              f"{approval['approval_status']} - {approval.get('approval_comment', '')}")
    
    # 验证状态
    assert request_detail.get('status') == DeploymentStatus.APPROVAL_REJECTED.value, \
        f"状态应该是 APPROVAL_REJECTED，但实际是 {request_detail.get('status')}"
    assert workflow.is_rejected() == True, "应该被拒绝"
    
    print("\n✅ 紧急版本审批拒绝测试通过！")
    return True


def test_reject_vs_deployment():
    """测试拒绝后是否不会进入灰度部署"""
    print("\n" + "=" * 60)
    print("测试3: 拒绝后不会进入灰度部署")
    print("=" * 60)
    
    approval_manager = get_approval_manager()
    
    # 创建发布申请
    request_id = approval_manager.create_release_request(
        version="MES_V2.6.2",
        risk_level=RiskLevel.L1_NORMAL,
        applicant="测试用户",
        department="研发部",
        description="测试拒绝后不进入部署",
        change_content="测试内容",
        target_production_lines=["涂装车间-01号线"]
    )
    
    # 启动审批流程
    workflow = approval_manager.create_workflow(request_id)
    
    # 第一个审批人拒绝
    is_completed, status_msg = workflow.approve(
        approver_role='production_manager',
        approver_name='李四',
        approved=False,
        comment="拒绝测试"
    )
    
    # 查询状态
    request_detail = approval_manager.get_request_detail(request_id)
    status = request_detail.get('status')
    
    print(f"申请状态: {status}")
    print(f"是否完成: {is_completed}")
    print(f"是否被拒绝: {workflow.is_rejected()}")
    
    # 验证状态不是 DEPLOYING 或 GRAY_OBSERVING
    assert status not in [DeploymentStatus.DEPLOYING.value, DeploymentStatus.GRAY_OBSERVING.value], \
        f"状态不应该是部署状态，但实际是 {status}"
    assert status == DeploymentStatus.APPROVAL_REJECTED.value, \
        f"状态应该是 APPROVAL_REJECTED，但实际是 {status}"
    
    print("\n✅ 拒绝后不会进入灰度部署测试通过！")
    return True


if __name__ == '__main__':
    try:
        test_approval_reject_normal()
        test_approval_reject_urgent()
        test_reject_vs_deployment()
        print("\n" + "=" * 60)
        print("🎉 所有审批拒绝测试通过！")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
