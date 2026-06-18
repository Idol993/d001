import os
import sys
import traceback
import time
from datetime import datetime, timedelta

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from main import MESReleaseSystem
from mes_ops.constants import RiskLevel, DeploymentStatus, DEFAULT_PRODUCTION_LINES

def test_demo_step_by_step():
    print("初始化系统...")
    try:
        system = MESReleaseSystem()
        system.start()
        print("✅ 系统初始化完成\n")
    except Exception as e:
        print(f"❌ 系统初始化失败: {e}")
        traceback.print_exc()
        return False
    
    # Step 1: 提交发布申请
    print("=" * 60)
    print("Step 1: 提交发布申请")
    print("=" * 60)
    try:
        request_id = system.submit_release_request(
            version="MES_V2.5.1",
            risk_level=RiskLevel.L1_NORMAL,
            applicant="张三",
            department="研发部",
            description="生产流程优化版本",
            change_content="优化工单调度算法",
            target_production_lines=DEFAULT_PRODUCTION_LINES[:6]
        )
        print(f"✅ 申请ID: {request_id}\n")
    except Exception as e:
        print(f"❌ 提交申请失败: {e}")
        traceback.print_exc()
        return False
    
    # Step 2: 前置校验
    print("=" * 60)
    print("Step 2: 执行前置校验")
    print("=" * 60)
    try:
        check_passed = system.run_pre_check(request_id)
        print(f"✅ 校验结果: {'通过' if check_passed else '失败'}\n")
        if not check_passed:
            return False
    except Exception as e:
        print(f"❌ 前置校验失败: {e}")
        traceback.print_exc()
        return False
    
    # Step 3: 审批
    print("=" * 60)
    print("Step 3: 分级审批流程")
    print("=" * 60)
    try:
        approvers = [
            ('production_manager', '李四'),
            ('quality_manager', '王五'),
            ('ops_manager', '赵六')
        ]
        for role, name in approvers:
            print(f"  {name} ({role}) 审批中...")
            is_completed = system.approve_release(
                request_id=request_id,
                approver_role=role,
                approver_name=name,
                approved=True,
                comment="同意发布"
            )
            print(f"  ✅ {name} 审批完成")
        print("✅ 所有审批通过\n")
    except Exception as e:
        print(f"❌ 审批流程失败: {e}")
        traceback.print_exc()
        return False
    
    # Step 4: 灰度部署
    print("=" * 60)
    print("Step 4: 灰度部署")
    print("=" * 60)
    try:
        for i in range(4):
            stage = system.deploy_to_next_stage(request_id)
            if stage:
                print(f"  ✅ 阶段 {stage.value}: {stage.name} 部署完成")
        print("✅ 全量部署完成\n")
    except Exception as e:
        print(f"❌ 灰度部署失败: {e}")
        traceback.print_exc()
        return False
    
    # Step 5: 监控
    print("=" * 60)
    print("Step 5: 启动实时监控")
    print("=" * 60)
    try:
        monitor_result = system.simulate_monitor_data(
            request_id=request_id,
            error_rate=0.01,
            latency=120,
            anomalies=1
        )
        print(f"✅ 收集到 {len(monitor_result)} 条监控数据\n")
    except Exception as e:
        print(f"❌ 监控失败: {e}")
        traceback.print_exc()
        return False
    
    # Step 6: 回滚
    print("=" * 60)
    print("Step 6: 触发自动回滚")
    print("=" * 60)
    try:
        print("   开始执行回滚...")
        rollback_result = system.trigger_rollback(
            request_id=request_id,
            reason="监控指标超过阈值，触发自动回滚"
        )
        print(f"✅ 回滚完成\n")
    except Exception as e:
        print(f"❌ 回滚失败: {e}")
        traceback.print_exc()
        return False
    
    # Step 7: 恢复
    print("=" * 60)
    print("Step 7: 故障修复与产线恢复")
    print("=" * 60)
    try:
        recovered = system.recover_production_line(
            production_line=DEFAULT_PRODUCTION_LINES[0],
            operator="运维工程师"
        )
        print(f"✅ 产线恢复: {'成功' if recovered else '失败'}\n")
    except Exception as e:
        print(f"❌ 产线恢复失败: {e}")
        traceback.print_exc()
        return False
    
    # Step 8: 数据兜底
    print("=" * 60)
    print("Step 8: 数据兜底功能测试")
    print("=" * 60)
    try:
        fallback_result = system.test_data_fallback()
        print(f"✅ 兜底测试完成\n")
    except Exception as e:
        print(f"❌ 兜底测试失败: {e}")
        traceback.print_exc()
        return False
    
    # Step 9: 应急演练
    print("=" * 60)
    print("Step 9: 应急演练")
    print("=" * 60)
    try:
        drill_result = system.start_emergency_drill(
            drill_type="data_collection_crash",
            operator="IT运维主管"
        )
        print(f"✅ 演练完成\n")
    except Exception as e:
        print(f"❌ 演练失败: {e}")
        traceback.print_exc()
        return False
    
    # Step 10: 周报表
    print("=" * 60)
    print("Step 10: 周度报表生成")
    print("=" * 60)
    try:
        report_result = system.run_weekly_report_task()
        print(f"✅ 报表ID: {report_result['report_id']}\n")
    except Exception as e:
        print(f"❌ 报表生成失败: {e}")
        traceback.print_exc()
        return False
    
    # Step 11: 查询导出
    print("=" * 60)
    print("Step 11: 查询与导出")
    print("=" * 60)
    try:
        records = system.query_release_records(
            start_date=datetime.now() - timedelta(days=7),
            status=DeploymentStatus.FULL_DEPLOYED.value
        )
        print(f"✅ 查询到 {len(records)} 条记录\n")
    except Exception as e:
        print(f"❌ 查询导出失败: {e}")
        traceback.print_exc()
        return False
    
    print("\n" + "=" * 60)
    print("🎉 所有步骤执行成功！")
    print("=" * 60)
    return True

if __name__ == '__main__':
    success = test_demo_step_by_step()
    sys.exit(0 if success else 1)
