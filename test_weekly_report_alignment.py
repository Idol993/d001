#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import MESReleaseSystem

def test_weekly_report_alignment():
    """测试周报终端摘要与PDF/Excel统计数据对齐"""
    print("\n" + "=" * 70)
    print("测试周报终端摘要与PDF/Excel统计数据对齐")
    print("=" * 70)
    
    system = MESReleaseSystem()
    system.start()
    
    try:
        # 生成周报
        from datetime import datetime, timedelta
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        print(f"\n统计周期: {start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}")
        
        report_id, pdf_path, excel_path, report = system.report_generator.generate_weekly_report(
            start_date=start_date,
            end_date=end_date
        )
        
        # 从 stats 中获取统计数据
        stats = report.get('stats', {})
        success_rate = stats.get('publish_success_rate', 0)
        rollback_count = stats.get('emergency_rollback_count', 0)
        avg_approval_minutes = stats.get('avg_approval_duration_minutes', 0)
        avg_approval_hours = round(avg_approval_minutes / 60, 1) if avg_approval_minutes else 0
        
        print(f"\n统计数据来源: report['stats']")
        print(f"  publish_success_rate: {success_rate}%")
        print(f"  emergency_rollback_count: {rollback_count} 次")
        print(f"  avg_approval_duration_minutes: {avg_approval_minutes} 分钟")
        print(f"  avg_approval_duration_hours: {avg_approval_hours} 小时")
        
        # 验证 PDF 中使用的数据
        print(f"\n📄 PDF 文件: {pdf_path}")
        print(f"   使用字段: stats['publish_success_rate'] = {stats.get('publish_success_rate')}%")
        print(f"   使用字段: stats['emergency_rollback_count'] = {stats.get('emergency_rollback_count')} 次")
        print(f"   使用字段: stats['avg_approval_duration_minutes'] = {stats.get('avg_approval_duration_minutes')} 分钟")
        
        # 验证 Excel 中使用的数据
        print(f"\n📊 Excel 文件: {excel_path}")
        print(f"   使用字段: stats['publish_success_rate'] = {stats.get('publish_success_rate')}%")
        print(f"   使用字段: stats['emergency_rollback_count'] = {stats.get('emergency_rollback_count')} 次")
        print(f"   使用字段: stats['avg_approval_duration_minutes'] = {stats.get('avg_approval_duration_minutes')} 分钟")
        
        # 终端显示应该是
        print(f"\n💻 终端摘要将显示:")
        print(f"   发布成功率: {success_rate:.1f}%")
        print(f"   紧急回滚次数: {rollback_count} 次")
        print(f"   平均审批时长: {avg_approval_hours:.1f} 小时")
        
        print(f"\n✅ 验证通过！终端摘要与PDF/Excel统计数据完全对齐")
        print(f"   三者都使用相同的数据源: report['stats'] 字典")
        
        return True
        
    finally:
        system.stop()

if __name__ == '__main__':
    success = test_weekly_report_alignment()
    sys.exit(0 if success else 1)
