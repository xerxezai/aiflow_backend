import requests
import json

# Test login endpoint
url = 'http://localhost:8000/api/v1/auth/login/'
headers = {'Content-Type': 'application/json'}
data = {
    'email': 'lira.viaga@rejlers.ae',
    'password': 'Password123'
}

print("Testing login endpoint...")
print(f"URL: {url}")
print(f"Credentials: {data['email']} / {data['password']}")
print("=" * 60)

try:
    response = requests.post(url, json=data, headers=headers, timeout=10)
    print(f"Status Code: {response.status_code}")
    print(f"Response Headers: {dict(response.headers)}")
    print(f"\nResponse Body:")
    try:
        print(json.dumps(response.json(), indent=2))
    except:
        print(response.text)
    
    if response.status_code == 200:
        print("\n✅ LOGIN SUCCESSFUL!")
        result = response.json()
        if 'access' in result:
            print(f"Access Token: {result['access'][:50]}...")
        if 'refresh' in result:
            print(f"Refresh Token: {result['refresh'][:50]}...")
    else:
        print(f"\n❌ LOGIN FAILED!")
        
except requests.exceptions.ConnectionError:
    print("❌ Cannot connect to backend. Is it running?")
except Exception as e:
    print(f"❌ Error: {e}")
