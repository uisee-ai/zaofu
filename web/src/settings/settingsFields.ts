import type { EditablePreferenceKey } from './preferences';
export type TextPair=readonly [string,string];
export interface FieldCopy {label:TextPair;description:TextPair;unit?:TextPair}
export const fieldCopy:Record<EditablePreferenceKey,FieldCopy>={
 language:{label:['Language','语言'],description:['Change the interface language.','切换界面语言。']},
 timeZone:{label:['Time zone','时区'],description:['Change ZaoFu time display.','更改 ZaoFu 的时区显示。']},
 autoRefresh:{label:['Auto-refresh Triage','自动刷新 Triage'],description:['Read local Issue data; do not start GitHub synchronization.','只读取本地数据，不触发 GitHub 同步。']},
 refreshSeconds:{label:['Page refresh interval','页面刷新间隔'],description:['Wait between local data checks.','本地数据查询间隔。'],unit:['s','秒']},
 refreshOnReturn:{label:['Refresh when returning','回到页面立即刷新'],description:['Read local progress when the tab becomes visible.','回到页面时读取最新本地进度。']},
};
export const optionNames:Record<string,TextPair>={en:['English','English'],zh:['中文','中文'],ko:['한국어','한국어'],ja:['日本語','日本語'],local:['Browser time zone','浏览器时区'],light:['Light','浅色'],dark:['Dark','深色'],system:['System','跟随系统'],updated:['Date updated','更新时间'],created:['Date created','创建时间'],number:['Issue number','Issue 编号'],name:['Issue title','Issue 标题'],asc:['Ascending','升序'],desc:['Descending','降序'],relative:['Opened … ago','创建至今'],soft:['Soft colors','柔和配色'],muted:['Muted colors','低饱和配色'],standard:['Standard','标准'],reduced:['Reduced','减少'],off:['Off','关闭']};
export type SettingsPageId='appearance'|'reporting'|'capture'|'source'|'feishu'|'refresh'|'queue'|'execution'|'boundary'|'access';
export const settingsPages:Record<SettingsPageId,{label:TextPair;description:TextPair;scope:'user'|'developer';group:TextPair;icon:'screen'|'info'|'camera'|'folder'|'link'|'refresh'|'queue'|'shield'|'key'}>={
 appearance:{label:['Appearance','外观'],description:['Language, theme and readable text.','语言、主题和舒适的文字。'],scope:'user',group:['Personal settings','个人设置'],icon:'screen'},
 reporting:{label:['Self-Issue reporting','Self-Issue 报告'],description:['Project policy for collecting problems reported by users and ZaoFu.','收集用户和 ZaoFu 自身问题的项目策略。'],scope:'user',group:['Issue submission','Issue 提交'],icon:'info'},
 capture:{label:['Browser capture','浏览器取证'],description:['Control evidence capture for submitted Issues.','控制提交 Issue 时的浏览器取证。'],scope:'user',group:['Issue submission','Issue 提交'],icon:'camera'},
 source:{label:['Investigation source','调查源码目录'],description:['Choose source code on the Agent machine, without changing publication targets.','选择 Agent 机器上的调查源码，不改变发布仓库。'],scope:'user',group:['Issue submission','Issue 提交'],icon:'folder'},
 feishu:{label:['Feishu Issue-merge','飞书 Issue-merge'],description:['Connect the /issue-merge flow to Feishu.','配置 /issue-merge 的飞书接入。'],scope:'user',group:['Connections','连接'],icon:'link'},
 refresh:{label:['Refresh & data','刷新与数据'],description:['Read local Triage progress without triggering GitHub intake.','查询本地 Triage 进度，不触发 GitHub 接入。'],scope:'developer',group:['Triage workspace','Triage 工作区'],icon:'refresh'},
 execution:{label:['Diagnosis & fix project','诊断与修复项目'],description:['Choose the project that owns the developer workflow.','指定开发侧 Triage、Fix、验证与交付的归属项目。'],scope:'developer',group:['Project settings','项目设置'],icon:'folder'},
 queue:{label:['Queue & sync','排队与同步'],description:['Controlled background GitHub policy.','受控的 GitHub 后台同步策略。'],scope:'developer',group:['Project settings','项目设置'],icon:'queue'},
 boundary:{label:['Runtime boundaries','运行边界'],description:['Read-only sources of truth and permissions.','只读的状态事实与权限边界。'],scope:'developer',group:['System','系统'],icon:'shield'},
 access:{label:['Developer access','开发者访问'],description:['Server-verified access on this browser.','由后端验证的当前浏览器开发者权限。'],scope:'developer',group:['System','系统'],icon:'key'},
};
