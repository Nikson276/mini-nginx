// k6/load-test.js
import { sleep, check } from 'k6'
import http from 'k6/http'

export const options = {
  thresholds: {
    http_reqs: ['count>=10000']
  },
  stages: [
    { duration: '1m', target: 300 },
    { duration: '1m', target: 1200 },  // Резкий пик
    { duration: '1m', target: 300 },
    { duration: '1m', target: 1500 },  // Еще выше
    { duration: '2m', target: 300 },
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
    'http://127.0.0.1:8080/events/',
    JSON.stringify(event),
    { headers: { 'Content-Type': 'application/json' } }
  )
  check(response, { 'status equals 200': r => r.status === 200 })

}
