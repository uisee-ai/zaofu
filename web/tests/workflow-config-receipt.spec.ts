import { expect, test } from '@playwright/test';

test('configuration intent receipt does not claim a candidate exists', async ({ page }) => {
  await page.goto('/?fixture=agent-session');
  const receipt = page.getByTestId('fx-workflow-request').locator('[data-proposal-action="workflow-config-propose"]');
  await expect(receipt).toContainText('Configuration request recorded · no YAML created');
  await expect(receipt).not.toContainText('Workflow config propose completed');
});
