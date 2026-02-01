// k6 эквивалент: wrk -t4 -c128 -d30s http://127.0.0.1:8080/
// 4 потока, 128 соединений, 30 секунд — базовая производительность прокси (статичный контент)
import http from 'k6/http'
import { check } from 'k6'

const BASE_URL = __ENV.BASE_URL || 'http://proxy:8080'

export const options = {
  scenarios: {
    wrk_like: {
      executor: 'constant-vus',
      vus: 128,
      duration: '30s',
      startTime: '0s',
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
