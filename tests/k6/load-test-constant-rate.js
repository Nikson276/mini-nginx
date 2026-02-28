// Тест по целевому RPS (constant-arrival-rate), а не по числу VUs.
// Задаёт именно "сколько запросов в секунду", k6 поднимает VUs по необходимости.
//
// Запуск:
//   k6 run tests/k6/load-test-constant-rate.js                    # 500 RPS по умолчанию
//   k6 run tests/k6/load-test-constant-rate.js -e RPS=1000       # 1000 RPS
//   k6 run tests/k6/load-test-constant-rate.js -e RPS=5000 -e DURATION=60s
//
// Сравнение с load-test.js (stages по VUs): там RPS = VUs / avg_iteration_duration.
// При латентности 5–7 с и 1500 VUs получается ~100 RPS. Здесь мы фиксируем RPS и смотрим,
// выдерживает ли система и сколько VUs понадобилось.
import http from 'k6/http'
import { check } from 'k6'

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8080'
const TARGET_RPS = parseInt(__ENV.RPS || '500', 10)
const DURATION = __ENV.DURATION || '60s'

export const options = {
  scenarios: {
    constant_rate: {
      executor: 'constant-arrival-rate',
      rate: TARGET_RPS,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: Math.min(500, TARGET_RPS * 2),
      maxVUs: Math.max(1000, TARGET_RPS * 4),
      gracefulStop: '10s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<15000'],
    http_reqs: ['rate>=' + Math.floor(TARGET_RPS * 0.9)],
  },
}

export default function () {
  const event = {
    id: `event-${__VU}-${__ITER}`,
    user_id: `user-${__VU}`,
    track_id: 'constant-rate',
  }
  const res = http.post(
    `${BASE_URL}/events/`,
    JSON.stringify(event),
    { headers: { 'Content-Type': 'application/json' } }
  )
  check(res, { 'status 200': r => r.status === 200 })
}
