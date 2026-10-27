import { actionPresentation } from '../src/components/agent-session/actionPresentation.js';
import { actionImpactRows } from '../src/components/agent-session/previewRegistry.js';

const proposal = actionPresentation('workflow-config-propose');
if (proposal.completedLabel !== 'Configuration request recorded · no YAML created') {
  throw new Error('An intent-only receipt must not claim YAML was created');
}
const rows = actionImpactRows('workflow-config-propose', { objective: 'Prepare PRD v4' });
if (!rows.some(row => row.label === 'next' && row.value.includes('no YAML'))) {
  throw new Error('The confirmation must explain that no candidate or Preview is created');
}
if (actionPresentation('workflow-config-validate').completedLabel !== 'Validation requested · result pending') {
  throw new Error('A validation request is not a validation result');
}
console.log('workflowConfigPresentation.test.ts OK');
