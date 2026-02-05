// k6/load-test.js
import { sleep, check } from 'k6'
import http from 'k6/http'

export const options = {
  thresholds: {
    http_reqs: ['count>=1500']
  },
  stages: [
    { target: 150, duration: '1m' },
    { target: 500, duration: '1m30s' },
    { target: 600, duration: '30s' },
    { target: 1000, duration: '30s' },
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