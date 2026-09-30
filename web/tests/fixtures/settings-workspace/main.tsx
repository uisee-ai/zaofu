// Isolated settings/real-shell fixture. Never imported by the production entry.
import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { SettingsPage } from '../../../src/settings/SettingsPage';
import { refreshDeveloperAccess, useDeveloperAccess } from '../../../src/settings/developerAccess';
import { WorkspaceRail } from '../../../src/components/workspace/WorkspaceRail';
import type { Snapshot } from '../../../src/api/types';
import type { ThemeMode } from '../../../src/app/sharedTypes';
import '@fontsource-variable/inter';
import '../../../src/styles.css';
import '../../../src/cockpit.css';
import './fixture.css';
const snapshot={project:{name:'Settings fixture',root:'/test-project',state_dir:'/test-state/settings'},tasks:[],archive_tasks:[],runtime:{actions:{mutation_enabled:true}}} as unknown as Snapshot;
function Fixture(){
 const [theme,setTheme]=useState<ThemeMode>('light');const access=useDeveloperAccess();
 useEffect(()=>{document.documentElement.dataset.theme=theme;},[theme]);
 useEffect(()=>{void refreshDeveloperAccess();},[]);
 return <div className="app-shell"><header className="topbar"><span>Settings fixture · isolated workspace shell</span></header><main className="workspace">
  <WorkspaceRail activePage="settings" activeProjectId="settings-fixture" actionResult={null}
   canViewIssueTriage={access.verified&&access.mode==='developer'} channels={[]} channelsLoaded
   liveState="live" onAddProject={()=>{}} onNewChannel={()=>{}} onOpenChannel={()=>{}} onOpenPage={()=>{}}
   onRemoveProject={()=>{}} onSelectProject={()=>{}} projects={[]} removeDisabled selectedChannelId="" snapshot={snapshot}/>
  <section className="board-panel" id="self-issue-workspace-host"><section className="projection-scroll settings-host">
   <SettingsPage projectId="settings-fixture" actionReady={true} snapshot={snapshot} themeMode={theme} onThemeModeChange={setTheme}/>
  </section></section>
 </main></div>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
