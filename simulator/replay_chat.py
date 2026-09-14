"""Replay text messages as a stand-in for ESP until hardware arrives."""
import argparse
import json
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8080/api/chat')
parser.add_argument('--user', default='sim-user')
parser.add_argument('text', nargs='?', default='今天有點焦慮，想找人聊聊')
args = parser.parse_args()
payload = json.dumps({'user_id': args.user, 'device_id': 'sim-device', 'text': args.text}).encode()
request = urllib.request.Request(args.url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(request, timeout=60) as response:
    print(response.read().decode())

