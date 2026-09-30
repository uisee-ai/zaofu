import { getSettingsPreferences } from './useSettingsPreferences.js';
import { interfaceTranslations } from './interfaceTranslations.js';

// Interface copy only. Issue bodies, evidence, identifiers and provider output
// remain verbatim and are never translated by a DOM text replacement pass.
export const chineseUi: Record<string, string> = {
  'Overview': '概览', 'Inbox': '收件箱', 'Tasks': '任务', 'Workflows': '工作流',
  'Agents': '智能体', 'Automations': '自动化', 'Delivery': '交付', 'Loop': '循环',
  'Traces': '执行追踪', 'Settings': '设置', 'Workspace': '工作区', 'Monitoring': '监控',
  'System': '系统', 'Project': '项目', 'Channels': '频道',
  'Refresh': '刷新', 'Start Triage': '开始 Triage', 'Manage Run': '管理运行',
  'Last synced': '上次同步', 'RUNTIME INTERVENTIONS': '运行干预',
  'Cancel queued': '取消排队', 'No local queued request': '没有可取消的排队请求',
  'New issue': '新建 Issue', 'View Repo': '查看仓库', 'Previous': '上一页', 'Next': '下一页',
  'Read-only in ZaoFu. Edit labels, state, assignees, or content on GitHub.': 'ZaoFu 内只读；请在 GitHub 修改标签、状态、负责人或内容。',
  'Not synchronized yet': '尚未同步', 'Loading Issue mirror…': '正在读取 Issue 镜像…',
  'Could not load Issues. Use Refresh to try again.': '无法读取 Issue，请刷新重试。',
  'No matching GitHub Issues.': '没有匹配的 GitHub Issue。',
  'States': '状态', 'Group': '分组', 'Workflow': '工作流', 'Task': '任务',
  'Execution project': '执行所属项目', 'ZaoFu source revision': 'ZaoFu 源码版本',
  'Reporter': '报告者', 'Assignees': '负责人', 'Milestone': '里程碑', 'Comments': '评论',
  'Labels': '标签', 'Author': '作者', 'Source': '来源', 'None': '无', 'Unbound': '未绑定',
  'Created at': '创建时间', 'Close': '关闭', 'Open/Edit on GitHub': '在 GitHub 查看/编辑',
  'Issue body': 'Issue 正文', 'GitHub Markdown preview': 'GitHub Markdown 预览',
  'No comments.': '暂无评论。', 'Earlier Tasks': '历史任务',
  'Date updated': '更新时间', 'Date created': '创建时间', 'Issue number': 'Issue 编号',
  'Issue title': 'Issue 标题', 'Sort by': '排序依据', 'Ascending': '升序', 'Descending': '降序',
  'Select all': '全选', 'All selected': '全部已选', 'All': '全部', 'No options': '没有选项',
  'Clear': '清空', 'Search Issues': '搜索 Issue', 'Repository': '仓库', 'Decision': '决策',
  'Complete': '完成', 'Stopped': '已停止', 'Open': '开放', 'Closed': '已关闭',
  'Mirrored': '已镜像', 'Triage queued': 'Triage 排队中', 'Triaging': '调查中',
  'Triage paused': 'Triage 已暂停', 'Triage recovering': 'Triage 恢复中',
  'Needs info': '待补充信息', 'Needs info exhausted': '信息补充次数已用尽',
  'Unverified candidate': '候选结果未验证', 'Awaiting Fix approval': '待批准修复',
  'Fix queued': '修复排队中', 'Fixing': '修复中', 'Fix paused': '修复已暂停',
  'Fix recovering': '修复恢复中', 'Verifying': '验证中', 'Verified candidate': '候选结果已验证',
  'Approved for PR': '已批准创建 PR', 'Owner changes requested': '负责人要求修改',
  'Owner rejected': '负责人已拒绝', 'Publication prepared': '已准备发布', 'PR open': 'PR 待处理',
  'PR changes requested': 'PR 要求修改', 'PR approved': 'PR 已批准',
  'PR closed without merge': 'PR 未合并关闭', 'Merged': '已合并',
  'Triage blocked': 'Triage 阻塞', 'Triage cancelled': 'Triage 已取消',
  'Fix blocked': '修复阻塞', 'Fix cancelled': '修复已取消', 'Blocked': '阻塞', 'Failed': '失败',
  'Cancel': '取消', 'Keep queued': '保持排队', 'Queue Triage': '加入 Triage 队列',
  'Queueing…': '正在排队…', 'Cancelling…': '正在取消…', 'Selected Issue': '选中的 Issue',
  'GitHub Issue URL or number': 'GitHub Issue 链接或编号',
  'Enter one Issue number or a full GitHub Issue URL.': '输入一个 Issue 编号或完整的 GitHub Issue 链接。',
  'Queue the selected GitHub Issue for read-only triage.': '将选中的 GitHub Issue 加入只读调查队列。',
  'Queue a GitHub Issue for read-only triage.': '将 GitHub Issue 加入只读调查队列。',
  'Stays until you dismiss it': '保留到手动关闭', 'Scroll to review': '滚动查看',
  'Automatic recovery': '自动恢复', 'ZaoFu is recovering the selected workflow.': 'ZaoFu 正在恢复选中的工作流。',
  'Cancel failed': '取消失败', 'Triage could not be started': '无法开始 Triage',
  'Filters cleared': '筛选已清空', 'Search and all selections were cleared.': '搜索与所有筛选已清空。',
};

export const koreanUi: Record<string, string> = {
  Overview: '개요', Inbox: '받은편지함', Tasks: '작업', Workflows: '워크플로', Agents: '에이전트',
  Automations: '자동화', Delivery: '배포', Loop: '루프', Traces: '추적', Settings: '설정', Workspace: '워크스페이스',
  Monitoring: '모니터링', Channels: '채널', Project: '프로젝트', System: '시스템', Refresh: '새로고침',
  'Start Triage': 'Triage 시작', 'Manage Run': '실행 관리', 'Triage queued': 'Triage 대기 중',
  'Triage cancelled': 'Triage 취소됨', Triaging: 'Triage 진행 중', 'Fix queued': '수정 대기 중',
  Cancel: '취소', Clear: '지우기', Search: '검색', Loading: '로드 중', 'No events.': '이벤트 없음',
};
export const japaneseUi: Record<string, string> = {
  Overview: '概要', Inbox: '受信トレイ', Tasks: 'タスク', Workflows: 'ワークフロー', Agents: 'エージェント',
  Automations: '自動化', Delivery: 'デリバリー', Loop: 'ループ', Traces: 'トレース', Settings: '設定', Workspace: 'ワークスペース',
  Monitoring: 'モニタリング', Channels: 'チャンネル', Project: 'プロジェクト', System: 'システム', Refresh: '更新',
  'Start Triage': 'Triage を開始', 'Manage Run': '実行を管理', 'Triage queued': 'Triage 待機中',
  'Triage cancelled': 'Triage キャンセル済み', Triaging: 'Triage 実行中', 'Fix queued': '修正待機中',
  Cancel: 'キャンセル', Clear: 'クリア', Search: '検索', Loading: '読み込み中', 'No events.': 'イベントなし',
};

for (const [en, zh, ja, ko] of interfaceTranslations) {
  chineseUi[en] ??= zh;
  japaneseUi[en] ??= ja;
  koreanUi[en] ??= ko;
}

export function uiText(text: string): string {
  const language = getSettingsPreferences().language;
  const dictionary: Record<string, string> = language === 'zh' ? chineseUi : language === 'ko' ? koreanUi : language === 'ja' ? japaneseUi : {};
  const translated = dictionary[text.trim()];
  return translated ? text.replace(text.trim(), translated) : text;
}

/** Existing English/Chinese pairs share the same catalogue for other locales. */
export function uiPair(en: string, zh: string): string {
  return getSettingsPreferences().language === 'zh' ? zh : uiText(en);
}
