// k6/load-test.js
import { sleep, check } from 'k6'
import http from 'k6/http'

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8080'

export const options = {
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<15000'],
    http_reqs: ['rate>=4000'],
  },
  stages: [
    { duration: '30s', target: 500 },
    { duration: '1m', target: 1000 },
    { duration: '30s', target: 2000 },
    { duration: '30s', target: 3000 },
    { duration: '30s', target: 4000 },
    { duration: '30s', target: 5000 },
    { duration: '1m', target: 500 },
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
    `${BASE_URL}/events/`,
    JSON.stringify(event),
    { headers: { 'Content-Type': 'application/json' } }
  )
  check(response, { 'status equals 200': r => r.status === 200 })

}
