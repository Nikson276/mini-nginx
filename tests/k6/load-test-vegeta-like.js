// k6 эквивалент: vegeta attack -duration=30s -rate=500 | vegeta report
// 30 секунд, 500 запросов в секунду — постоянная нагрузка по RPS
import http from 'k6/http'
import { check } from 'k6'

const BASE_URL = __ENV.BASE_URL || 'http://proxy:8080'

export const options = {
  scenarios: {
    vegeta_like: {
      executor: 'constant-arrival-rate',
      rate: 500,
      timeUnit: '1s',
      duration: '30s',
      preAllocatedVUs: 100,
      maxVUs: 1000,
      gracefulStop: '5s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<5000', 'p(99)<10000'],
    http_reqs: ['rate>=450'],
  },
}

export default function () {
  const res = http.get(`${BASE_URL}/`)
  check(res, { 'status 200': r => r.status === 200 })
}
