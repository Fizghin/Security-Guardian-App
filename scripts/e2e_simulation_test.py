import requests, time, json
url='http://127.0.0.1:8000/api/simulate/person'
print('Trigger simulation')
resp=requests.post(url)
print('Response', resp.status_code, resp.text)
time.sleep(12)
stats=requests.get('http://127.0.0.1:8000/api/stats')
print('Stats', json.dumps(stats.json(), indent=2))
