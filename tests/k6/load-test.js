// k6/load-test.js
import { sleep, check } from 'k6'
import http from 'k6/http'

export const options = {
  thresholds: {
    http_reqs: ['count>=500']
  },
  stages: [
    { target: 50, duration: '1m' },
    { target: 100, duration: '1m30s' },
    { target: 50, duration: '30s' },
    { target: 100, duration: '30s' },
  ],
}

export default function () {
  let response
  const event = {
    id: `event-${__VU}-${__ITER}`,
    user_id: `user-${__VU}`,
    track_id: "Test-case-1",
    ingest_time: new Date().toISOString(), // ← клиент устанавливает время
    // store_time НЕ отправляется!
  }; 

  // Post played track message
  response = http.post(
    'http://proxy:8080/events/',  // ← ВАЖНО: внутри Docker — не 0.0.0.0!
    JSON.stringify(event),
    { headers: { 'Content-Type': 'application/json' } }
  )
  check(response, { 'status equals 200': r => r.status === 200 })

}