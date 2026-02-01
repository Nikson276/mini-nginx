// k6 эквивалент: ab -n 5000 -c 200 http://127.0.0.1:8080/
// 5000 запросов, 200 одновременных соединений — устойчивость к высокому RPS
import http from 'k6/http'
import { check } from 'k6'

const BASE_URL = __ENV.BASE_URL || 'http://proxy:8080'

export const options = {
  scenarios: {
    ab_like: {
      executor: 'per-vu-iterations',
      vus: 200,
      iterations: 25, // 200 × 25 = 5000 запросов всего
      maxDuration: '5m',
      gracefulStop: '5s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<5000', 'p(99)<10000'],
  },
}

export default function () {
  const res = http.get(`${BASE_URL}/`)
  check(res, { 'status 200': r => r.status === 200 })
}
