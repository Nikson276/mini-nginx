// k6/load-test.js
import { sleep, check } from 'k6'
import http from 'k6/http'

export const options = {
  thresholds: {
    http_reqs: ['count>=10000']
  },
  stages: [
    { duration: '30s', target: 100 },  // Базовый уровень
    { duration: '1m', target: 300 },   // Нормальная нагрузка
    { duration: '1m', target: 500 },   // Пиковая нагрузка
    { duration: '30s', target: 100 },  // Восстановление
  ],
}

export default function () {
  let response
  const event = {
    id: `event-${__VU}-${__ITER}`,
    user_id: `user-${__VU}`,
    track_id: "Test-case-2",
  }; 

  // Post played track message
  response = http.post(
    'http://proxy:8080/events/',
    JSON.stringify(event),
    { headers: { 'Content-Type': 'application/json' } }
  )
  check(response, { 'status equals 200': r => r.status === 200 })

}