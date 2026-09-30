import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const iterations = new Counter('testkit_iterations');
const latency = new Trend('testkit_latency');

export const options = {
  vus: 1,
  iterations: 1,
  thresholds: {
    checks: ['rate>0.99'],
    testkit_iterations: ['count>0'],
    testkit_latency: ['p(95)<100'],
  },
};

export default function () {
  iterations.add(1);
  latency.add(1);
  const value = __ENV.TESTKIT_FORCE_FAILURE === '1' ? false : true;
  check(value, { 'testkit smoke assertion': (actual) => actual === true });
}

export function handleSummary(data) {
  return { '/out/summary.json': JSON.stringify(data) };
}
