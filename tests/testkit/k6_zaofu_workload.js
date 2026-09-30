import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const requests = new Counter('testkit_requests');
const latency = new Trend('testkit_latency');
const mode = __ENV.ZF_K6_WORKLOAD || 'pressure';
const soakSeconds = Number(__ENV.ZF_K6_SOAK_SECONDS || '30');

const profiles = {
  pressure: { vus: 2, iterations: 20 },
  spike: { stages: [{ duration: '1s', target: 10 }, { duration: '3s', target: 10 }, { duration: '1s', target: 0 }] },
  stress: { stages: [{ duration: '2s', target: 1 }, { duration: '3s', target: 4 }, { duration: '3s', target: 8 }, { duration: '2s', target: 0 }] },
  soak: { vus: 1, duration: `${soakSeconds}s` },
};

if (!Object.prototype.hasOwnProperty.call(profiles, mode)) {
  throw new Error(`unsupported ZF_K6_WORKLOAD: ${mode}`);
}

export const options = {
  ...profiles[mode],
  thresholds: {
    checks: ['rate>0.99'],
    http_req_failed: ['rate<0.05'],
    testkit_requests: ['count>0'],
    testkit_latency: ['p(95)<2000', 'p(99)<5000'],
  },
};

export default function () {
  const path = (__ITER % 2 === 0) ? '/health' : '/api/state';
  const response = http.get(`${__ENV.ZF_BASE_URL}${path}`);
  requests.add(1);
  latency.add(response.timings.duration);
  check(response, {
    [`${path} returns 200`]: (r) => r.status === 200,
    [`${path} has a body`]: (r) => r.body.length > 0,
  });
  sleep(0.01);
}

export function handleSummary(data) {
  return { '/out/summary.json': JSON.stringify(data) };
}
