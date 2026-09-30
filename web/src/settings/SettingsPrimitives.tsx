import { uiPair, uiText } from './uiText';
import type { ReactNode } from 'react';
import { X } from 'lucide-react';
import { useSettingsPreferences } from './useSettingsPreferences';
import { IssueNotificationStack } from '../pages/IssueTriageNotifications';
import { IssueDialog } from '../pages/IssueOverlay';
export type Notice={id:number;title:string;message?:string;error?:boolean};
export type Notify=(title:string,message?:string,error?:boolean)=>void;
export function SettingsSection({title,description,action,children}:{title:string;description?:string;action?:ReactNode;children:ReactNode}){
 return <section className="settings-section" aria-label={title}><header><div><h3>{title}</h3>{description&&<p>{description}</p>}</div>{action}</header><div className="settings-card">{children}</div></section>;
}
export function SettingsRow({label,description,children,nested=false}:{label:string;description?:string;children:ReactNode;nested?:boolean}){
 return <div className={`settings-row${nested?' nested':''}`}><div className="settings-copy"><div className="settings-label">{label}</div>{description&&<p>{description}</p>}</div><div className="settings-control">{children}</div></div>;
}
export function SettingsDialog({title,description,busy=false,onClose,children,footer}:{title:string;description?:string;busy?:boolean;onClose:()=>void;children:ReactNode;footer:ReactNode}){
 const prefs=useSettingsPreferences();
 return <IssueDialog open busy={busy} className="settings-root settings-dialog" labelledBy="settings-dialog-title" initialFocus="[data-autofocus]" onClose={onClose}><header><div><h3 id="settings-dialog-title">{title}</h3>{description&&<p>{description}</p>}</div><button type="button" className="settings-icon-button" disabled={busy} aria-label={uiText('Close')} onClick={onClose}><X size={17}/></button></header><div className="settings-dialog-body">{children}</div><footer>{footer}</footer></IssueDialog>;
}
export function SettingsNotices({items,dismiss}:{items:Notice[];dismiss:(id:number)=>void}){
 const prefs=useSettingsPreferences();
 return <IssueNotificationStack items={items.map(item=>({id:item.id,title:item.title,message:item.message||'',tone:item.error?'danger':'success',persistent:!!item.error,duration:prefs.successSeconds*1000}))} onDismiss={dismiss}/>;
}
