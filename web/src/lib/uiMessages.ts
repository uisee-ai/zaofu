// Backend bodies and protocol identifiers retain their source language.
export const CHINESE_MESSAGES: Record<string, string> = {
  "Projects": "项目", "Project views": "项目视图", "Search workspace…": "搜索工作区…", "Add task": "添加任务", "Accent": "强调色", "Density": "密度",
  "diagnostics": "项诊断", "deliveries": "项交付", "total": "项总计",
  "Channels": "频道", "Open Channels": "打开频道", "New Channel": "新建频道", "Add Project": "添加项目", "System settings": "系统设置",
  "Evidence": "证据", "Reject reason": "拒绝原因",
  "ready": "就绪", "in_progress": "进行中", "testing": "验证", "blocked": "阻塞", "done": "完成", "cancelled": "已取消", "superseded": "已替代", "archived": "已归档",
  "All types": "所有类型", "Plan Ready": "计划待审批", "Human Decision": "人工决策", "Channel Decision": "频道决策", "Kanban Question": "任务提问", "Runtime Attention": "运行提醒", "Approval": "审批", "Run Delivery": "运行交付", "Repair": "修复",
  "Overview": "概览", "Inbox": "收件箱", "Tasks": "任务", "Settings": "设置", "Workspace": "工作区",
  "Workflows": "工作流", "Agents": "智能体", "Automations": "自动化", "Delivery": "交付", "Loop": "循环", "Traces": "追踪",
  "Monitoring": "监控", "System": "跟随系统", "General": "通用", "Appearance": "外观", "Language": "语言", "Date & time": "日期与时间",
  "Personalize your workspace.": "个性化你的工作区。", "Workspace details": "工作区信息", "Project name": "项目名称", "Project root": "项目目录",
  "State directory": "状态目录", "Configuration": "配置来源", "Managed by the project configuration.": "由项目配置管理。", "Runtime boundary": "运行时边界",
  "Theme": "主题", "Choose how ZaoFu looks on this device.": "选择此设备上 ZaoFu 的显示模式。", "Light": "浅色", "Dark": "深色",
  "Accent color": "强调色", "Used for selections, links and focus.": "用于选中状态、链接和焦点。", "indigo": "靛蓝", "blue": "蓝色", "violet": "紫色", "teal": "青绿", "rose": "玫红", "graphite": "石墨",
  "Sidebar density": "导航密度", "Spacing between navigation items.": "调整导航项目间距。", "Task density": "任务密度", "Applies to Board, List and Table.": "同时应用到看板、列表和表格。",
  "Comfortable": "舒适", "Compact": "紧凑", "Motion": "动效", "Respect your system preference or reduce animation.": "遵循系统偏好或减少动画。", "Reduce motion": "减少动效",
  "Display language": "显示语言", "Choose the language for navigation and these workspace views.": "选择导航与当前工作区视图的显示语言。",
  "Navigation, Tasks, Inbox and General settings are translated. Runtime diagnostics and content from your project keep their original language.": "已覆盖导航、任务、收件箱和通用设置。运行诊断与项目正文保留原始语言。",
  "Time zone": "时区", "Changes display only; schedules keep their configured time zone.": "仅改变显示；调度保留配置的时区。", "Date format": "日期格式", "Time format": "时间格式", "Preview": "预览",
  "Medium": "标准", "Short": "简短", "24 hour": "24 小时制", "12 hour": "12 小时制", "Local preferences are saved on this device.": "偏好保存在此设备上。", "Preferences could not be saved. Changes apply to this tab only.": "无法保存偏好，改动仅在此标签页生效。",
  "Search tasks": "搜索任务", "Filter": "筛选", "Sort": "排序", "Group": "分组", "List": "列表", "Board": "看板", "Table": "表格", "New task": "新建任务", "visible tasks": "项可见任务",
  "All": "全部", "Status": "状态", "Owner": "负责人", "Priority": "优先级", "Skill": "技能", "Signal": "信号", "Focus": "关注", "Title": "标题", "Activity": "最近活动", "Phase": "阶段", "None": "无",
  "Todo": "待办", "In Progress": "进行中", "Verify": "验证", "Blocked": "阻塞", "Done": "完成", "Ready": "就绪", "All tasks": "全部任务", "Focused work": "关注任务", "Failed": "失败",
  "Clear filters": "清除筛选", "Columns": "显示列", "Skills": "技能", "Dependencies": "依赖", "Retries": "重试", "Cost": "成本", "Unassigned": "未分配", "No matching tasks": "没有匹配的任务", "Try changing your search or filters.": "试试调整搜索词或筛选条件。",
  "Ascending": "升序", "Descending": "降序", "Task details": "任务详情", "Close task details": "关闭任务详情", "Back to inbox": "返回收件箱",
  "Search inbox": "搜索收件箱", "Search": "搜索", "Decisions": "待决策", "Runtime": "运行时", "Automation": "自动化", "Resolved": "已处理", "Refresh": "刷新", "Refreshing": "刷新中",
  "Loading inbox...": "正在加载收件箱…", "No matching inbox items.": "没有匹配的消息。", "No inbox items in this view.": "此视图没有消息。", "Select an item to read its details.": "选择一条消息查看详情。",
  "Open source": "打开来源", "Approve": "批准", "Reject": "拒绝", "Ack": "确认", "Ack all": "全部确认", "pending decisions": "项待决策", "pending": "待处理", "resolved": "已处理", "clear": "已清空", "loading": "加载中",
};
export function translate(message: string, language: "en" | "zh-CN"): string {
  return language === "zh-CN" ? CHINESE_MESSAGES[message] ?? message : message;
}
