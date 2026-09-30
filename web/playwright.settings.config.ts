import { defineConfig, devices } from '@playwright/test';
const port=Number(process.env.ZF_SETTINGS_E2E_PORT||4178);
export default defineConfig({
 testDir:'./tests',testMatch:/(?:settings-workspace|settings-behavior|issue-source-settings)\.spec\.ts/,
 outputDir:'./test-results/settings/artifacts',
 reporter:[['list'],['html',{outputFolder:'test-results/settings/report',open:'never'}]],
 timeout:30000,expect:{timeout:6000},workers:1,
 use:{baseURL:`http://127.0.0.1:${port}`,viewport:{width:1440,height:1000},trace:'retain-on-failure',screenshot:'only-on-failure'},
 projects:[{name:'chromium',use:{...devices['Desktop Chrome'],launchOptions:process.env.ZF_E2E_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.ZF_E2E_CHROMIUM_EXECUTABLE_PATH}:{}}}],
 webServer:{command:`npm run dev -- --host 0.0.0.0 --port ${port} --strictPort`,url:`http://127.0.0.1:${port}/tests/fixtures/settings-workspace/index.html`,reuseExistingServer:false,stdout:'pipe',stderr:'pipe'},
});
