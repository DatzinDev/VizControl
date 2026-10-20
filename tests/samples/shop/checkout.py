import sys

from app.api import post_order

tier = sys.argv[1] if len(sys.argv) > 1 else "basic"
payload = {"user_id": 71, "email": "ana@example.com", "tier": tier, "token": "s3cr3t",
           "items": [["apple", 2, "1.50"], ["pear", 1, "2.25"]]}
print(post_order(payload))
