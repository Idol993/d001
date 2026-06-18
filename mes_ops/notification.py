"""
消息通知模块
支持邮件、企业微信、钉钉多渠道通知
"""
import json
import smtplib
import uuid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from typing import Dict, Any, List, Optional
from datetime import datetime

import requests

from .database import get_db
from .logger import get_logger
from .config import get_config
from .constants import AlertLevel, OperationType
from .audit import get_audit_logger

logger = get_logger(__name__)


class NotificationService:
    """通知服务"""
    
    def __init__(self):
        self.config = get_config()
        self.db = get_db()
        self._load_config()
    
    def _load_config(self) -> None:
        """加载通知配置"""
        self.email_config = self.config.get('notification.email', {})
        self.webhook_config = self.config.get('notification.webhook', {})
        self.escalation_config = self.config.get('notification.escalation_levels', {})
        self.approvers_config = self.config.get('approval.approvers', {})
    
    def _resolve_recipients(self, recipient_roles: List[str]) -> Dict[str, Any]:
        """解析接收人信息"""
        recipients = {
            'emails': [],
            'phones': [],
            'names': []
        }
        
        for role in recipient_roles:
            if role in self.approvers_config:
                info = self.approvers_config[role]
                if info.get('email'):
                    recipients['emails'].append(info['email'])
                if info.get('phone'):
                    recipients['phones'].append(info['phone'])
                if info.get('name'):
                    recipients['names'].append(info['name'])
        
        return recipients
    
    def _get_escalation_config(self, alert_level: AlertLevel) -> Dict[str, Any]:
        """获取告警升级配置"""
        config = self.escalation_config.get(alert_level.value, {})
        if not config:
            config = self.escalation_config.get('LEVEL1', {})
        
        return {
            'name': config.get('name', '一般告警'),
            'channels': config.get('channels', ['wechat_work']),
            'recipient_roles': config.get('recipients', ['ops_manager'])
        }
    
    def send_email(self, to_emails: List[str], subject: str, 
                   content: str, is_html: bool = False) -> bool:
        """发送邮件通知"""
        if not self.email_config:
            logger.warning("邮件配置不存在，跳过邮件发送")
            return False
        
        try:
            smtp_server = self.email_config.get('smtp_server')
            smtp_port = self.email_config.get('smtp_port', 465)
            use_ssl = self.email_config.get('use_ssl', True)
            username = self.email_config.get('username')
            password = self.email_config.get('password')
            sender_name = self.email_config.get('sender_name', 'MES运维平台')
            
            msg = MIMEMultipart()
            msg['From'] = Header(f"{sender_name} <{username}>", 'utf-8')
            msg['To'] = Header(", ".join(to_emails), 'utf-8')
            msg['Subject'] = Header(subject, 'utf-8')
            
            msg.attach(MIMEText(content, 'html' if is_html else 'plain', 'utf-8'))
            
            if use_ssl:
                server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=10)
            else:
                server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
                server.starttls()
            
            server.login(username, password)
            server.sendmail(username, to_emails, msg.as_string())
            server.quit()
            
            logger.info(f"邮件已发送到: {to_emails}, 主题: {subject}")
            return True
            
        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return False
    
    def send_wechat_work(self, webhook_url: str, content: str, 
                         title: str = None, mentioned_mobiles: List[str] = None) -> bool:
        """发送企业微信通知"""
        if not webhook_url:
            logger.warning("企业微信Webhook未配置")
            return False
        
        try:
            message = {
                "msgtype": "markdown",
                "markdown": {
                    "content": content
                }
            }
            
            if mentioned_mobiles:
                message['markdown']['mentioned_mobile_list'] = mentioned_mobiles
            
            response = requests.post(
                webhook_url,
                data=json.dumps(message),
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            result = response.json()
            if result.get('errcode') == 0:
                logger.info("企业微信通知已发送")
                return True
            else:
                logger.error(f"企业微信通知失败: {result}")
                return False
                
        except Exception as e:
            logger.error(f"企业微信通知异常: {e}")
            return False
    
    def send_dingtalk(self, webhook_url: str, content: str,
                      title: str = None, at_mobiles: List[str] = None) -> bool:
        """发送钉钉通知"""
        if not webhook_url:
            logger.warning("钉钉Webhook未配置")
            return False
        
        try:
            message = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title or "MES运维通知",
                    "text": content
                }
            }
            
            if at_mobiles:
                message['at'] = {
                    "atMobiles": at_mobiles,
                    "isAtAll": False
                }
            
            response = requests.post(
                webhook_url,
                data=json.dumps(message),
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            result = response.json()
            if result.get('errcode') == 0:
                logger.info("钉钉通知已发送")
                return True
            else:
                logger.error(f"钉钉通知失败: {result}")
                return False
                
        except Exception as e:
            logger.error(f"钉钉通知异常: {e}")
            return False
    
    def _build_alert_content(self, alert_level: AlertLevel, title: str, 
                             context: Dict[str, Any]) -> Dict[str, Any]:
        """构建告警内容"""
        level_config = self._get_escalation_config(alert_level)
        level_name = level_config.get('name', '告警')
        
        emoji_map = {
            AlertLevel.LEVEL1: 'ℹ️',
            AlertLevel.LEVEL2: '⚠️',
            AlertLevel.LEVEL3: '🚨'
        }
        emoji = emoji_map.get(alert_level, '⚠️')
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        markdown_parts = [
            f"{emoji} **【{level_name}】{title}**",
            f"",
            f"> **告警时间**: {timestamp}",
            f"> **告警等级**: {level_name}"
        ]
        
        html_parts = [
            f"<h2>{emoji} 【{level_name}】{title}</h2>",
            f"<p><strong>告警时间:</strong> {timestamp}</p>",
            f"<p><strong>告警等级:</strong> {level_name}</p>"
        ]
        
        if 'request_id' in context:
            markdown_parts.append(f"> **发布申请**: {context['request_id']}")
            html_parts.append(f"<p><strong>发布申请:</strong> {context['request_id']}</p>")
        
        if 'version' in context:
            markdown_parts.append(f"> **影响版本**: {context['version']}")
            html_parts.append(f"<p><strong>影响版本:</strong> {context['version']}</p>")
        
        if 'affected_lines' in context:
            lines = context['affected_lines']
            if isinstance(lines, list):
                lines_str = ", ".join(lines)
            else:
                lines_str = str(lines)
            markdown_parts.append(f"> **影响产线**: {lines_str}")
            html_parts.append(f"<p><strong>影响产线:</strong> {lines_str}</p>")
        
        if 'rollback_reason' in context:
            markdown_parts.append(f"> **回滚原因**: {context['rollback_reason']}")
            html_parts.append(f"<p><strong>回滚原因:</strong> {context['rollback_reason']}</p>")
        
        if 'estimated_defect_count' in context:
            markdown_parts.append(f"> **预估不良品**: {context['estimated_defect_count']} 件")
            html_parts.append(f"<p><strong>预估不良品:</strong> {context['estimated_defect_count']} 件")
        
        if 'root_cause' in context and isinstance(context['root_cause'], dict):
            rc = context['root_cause']
            markdown_parts.extend([
                f"",
                f"### 根因分析",
                f"- **主要原因**: {rc.get('primary_cause', '未知')}"
            ])
            html_parts.extend([
                f"<h3>根因分析</h3>",
                f"<ul><li><strong>主要原因:</strong> {rc.get('primary_cause', '未知')}</li>"
            ])
            
            if rc.get('contributing_factors'):
                markdown_parts.append("- **影响因素**:")
                html_parts.append("<li><strong>影响因素:</strong><ul>")
                for factor in rc['contributing_factors']:
                    markdown_parts.append(f"  - {factor}")
                    html_parts.append(f"<li>{factor}</li>")
                html_parts.append("</ul></li>")
            
            if rc.get('suggested_fixes'):
                markdown_parts.append("- **修复建议**:")
                html_parts.append("<li><strong>修复建议:</strong><ul>")
                for fix in rc['suggested_fixes']:
                    markdown_parts.append(f"  - {fix}")
                    html_parts.append(f"<li>{fix}</li>")
                html_parts.append("</ul></li>")
            
            html_parts.append("</ul>")
        
        if 'trigger_metrics' in context and isinstance(context['trigger_metrics'], dict):
            markdown_parts.extend([
                f"",
                f"### 触发指标"
            ])
            html_parts.extend([
                f"<h3>触发指标</h3>",
                "<table border='1' cellpadding='5' cellspacing='0'>",
                "<tr><th>指标名称</th><th>当前值</th><th>阈值</th></tr>"
            ])
            
            for m_type, info in context['trigger_metrics'].items():
                markdown_parts.append(
                    f"- **{info.get('name', m_type)}**: {info.get('value', '-')}{info.get('unit', '')} "
                    f"(阈值: {info.get('threshold', '-')}{info.get('unit', '')})"
                )
                html_parts.append(
                    f"<tr><td>{info.get('name', m_type)}</td>"
                    f"<td style='color:red;'>{info.get('value', '-')}{info.get('unit', '')}</td>"
                    f"<td>{info.get('threshold', '-')}{info.get('unit', '')}</td></tr>"
                )
            
            html_parts.append("</table>")
        
        if 'stakeholders' in context:
            markdown_parts.extend([
                f"",
                f"### 通知干系人"
            ])
            for stakeholder in context['stakeholders']:
                markdown_parts.append(f"- <@{stakeholder}>")
        
        markdown_content = "\n".join(markdown_parts)
        html_content = "\n".join(html_parts)
        
        return {
            'markdown': markdown_content,
            'html': html_content,
            'plain': f"[{level_name}] {title} - {context.get('rollback_reason', '请查看详情')}"
        }
    
    def send_alert(self, alert_level: AlertLevel, title: str, 
                   context: Dict[str, Any], 
                   additional_channels: List[str] = None) -> Dict[str, Any]:
        """
        发送告警通知
        
        Args:
            alert_level: 告警等级
            title: 告警标题
            context: 告警上下文
            additional_channels: 额外的通知渠道
            
        Returns:
            发送结果
        """
        notification_id = f"NOTIFY-{uuid.uuid4().hex[:8].upper()}"
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        level_config = self._get_escalation_config(alert_level)
        channels = level_config.get('channels', ['wechat_work'])
        if additional_channels:
            channels = list(set(channels + additional_channels))
        
        recipient_roles = level_config.get('recipient_roles', [])
        recipients = self._resolve_recipients(recipient_roles)
        
        all_stakeholders = ['生产负责人', '质量经理', '运维', '仓储', '技术']
        context['stakeholders'] = all_stakeholders
        
        content = self._build_alert_content(alert_level, title, context)
        
        send_results = {}
        
        if 'wechat_work' in channels:
            wechat_config = self.webhook_config.get('wechat_work', {})
            if wechat_config.get('enabled', False):
                success = self.send_wechat_work(
                    webhook_url=wechat_config.get('url', ''),
                    content=content['markdown'],
                    title=title,
                    mentioned_mobiles=recipients['phones']
                )
                send_results['wechat_work'] = success
        
        if 'dingtalk' in channels:
            dingtalk_config = self.webhook_config.get('dingtalk', {})
            if dingtalk_config.get('enabled', False):
                success = self.send_dingtalk(
                    webhook_url=dingtalk_config.get('url', ''),
                    content=content['markdown'],
                    title=title,
                    at_mobiles=recipients['phones']
                )
                send_results['dingtalk'] = success
        
        if 'email' in channels and recipients['emails']:
            success = self.send_email(
                to_emails=recipients['emails'],
                subject=f"【MES运维告警】{title}",
                content=content['html'],
                is_html=True
            )
            send_results['email'] = success
        
        self.db.execute('''
            INSERT INTO notification_records 
            (notification_id, alert_level, title, content, 
             channels, recipients, sent_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            notification_id, alert_level.value, title,
            content['plain'],
            json.dumps(channels, ensure_ascii=False),
            json.dumps(recipients, ensure_ascii=False),
            timestamp,
            'SENT' if any(send_results.values()) else 'FAILED'
        ))
        
        get_audit_logger().log(
            operation_type=OperationType.MONITOR_ALERT,
            operator="system",
            request_params={
                "notification_id": notification_id,
                "alert_level": alert_level.value,
                "title": title,
                "channels": channels,
                "recipients": recipients['names']
            },
            response_result=send_results,
            status="SUCCESS"
        )
        
        return {
            'notification_id': notification_id,
            'alert_level': alert_level.value,
            'channels': channels,
            'recipients': recipients['names'],
            'results': send_results,
            'sent_at': timestamp
        }
    
    def send_notification(self, alert_level: AlertLevel, title: str, 
                          content: str, channels: List[str] = None) -> Dict[str, Any]:
        """
        发送通知（兼容方法）
        
        Args:
            alert_level: 告警等级
            title: 通知标题
            content: 通知内容
            channels: 通知渠道列表
            
        Returns:
            发送结果
        """
        context = {
            'content': content,
            'title': title
        }
        return self.send_alert(
            alert_level=alert_level,
            title=title,
            context=context,
            additional_channels=channels
        )
    
    def send_fault_report_notification(self, report: Dict[str, Any]) -> Dict[str, Any]:
        """发送故障分析报告通知"""
        return self.send_alert(
            alert_level=AlertLevel.LEVEL3,
            title=f"工业生产故障分析报告 - {report.get('report_id', '')}",
            context=report
        )
    
    def send_approval_notification(self, request_id: str, version: str,
                                   approver_name: str, 
                                   action: str = 'pending') -> Dict[str, Any]:
        """发送审批通知"""
        action_text = {
            'pending': '待审批',
            'approved': '已批准',
            'rejected': '已拒绝'
        }.get(action, '待处理')
        
        return self.send_alert(
            alert_level=AlertLevel.LEVEL1,
            title=f"版本发布审批{action_text}: {version}",
            context={
                'request_id': request_id,
                'version': version,
                'approver': approver_name,
                'action': action_text
            }
        )
    
    def send_drill_notification(self, drill_id: str, drill_name: str,
                                status: str) -> Dict[str, Any]:
        """发送应急演练通知"""
        return self.send_alert(
            alert_level=AlertLevel.LEVEL1,
            title=f"应急演练{status}: {drill_name}",
            context={
                'drill_id': drill_id,
                'drill_name': drill_name,
                'status': status
            },
            additional_channels=['email']
        )


def get_notification_service() -> NotificationService:
    """获取通知服务实例"""
    return NotificationService()
