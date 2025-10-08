import os
import ipaddress
from functools import wraps
from flask import Flask, request, jsonify, Response
import requests

CF_API = "https://api.cloudflare.com/client/v4"

CF_TOKEN        = os.getenv("CF_TOKEN", "")
CF_ZONE_NAME    = os.getenv("CF_ZONE_NAME", "")      
CF_RECORD_NAME  = os.getenv("CF_RECORD_NAME", "")     
CF_PROXIED      = os.getenv("CF_PROXIED", "false").lower() == "true" 
CF_TTL          = int(os.getenv("CF_TTL", "120"))


app = Flask(__name__)


def cf_headers():
    return {
        "Authorization": f"Bearer {CF_TOKEN}",
        "Content-Type": "application/json",
    }

def get_zone_id(zone_name):
    r = requests.get(f"{CF_API}/zones", headers=cf_headers(), params={"name": zone_name, "status": "active"})
    r.raise_for_status()
    result = r.json()["result"]
    if not result:
        raise RuntimeError("Zone not found")
    return result[0]["id"]

def get_record(zone_id, record_name, rtype=None):
    params = {"name": record_name}
    if rtype:
        params["type"] = rtype
    r = requests.get(f"{CF_API}/zones/{zone_id}/dns_records", headers=cf_headers(), params=params)
    r.raise_for_status()
    res = r.json()["result"]
    return res[0] if res else None

def upsert_record(zone_id, record_name, ip, proxied, ttl):
    try:
        ip_obj = ipaddress.ip_address(ip)
        rtype = "AAAA" if ip_obj.version == 6 else "A"
    except ValueError:
        return False, "badip"

    existing = get_record(zone_id, record_name, rtype)
    payload = {
        "type": rtype,
        "name": record_name,
        "content": ip,
        "ttl": ttl,
        "proxied": proxied
    }

    if existing:
        rid = existing["id"]
        r = requests.put(f"{CF_API}/zones/{zone_id}/dns_records/{rid}", headers=cf_headers(), json=payload)
    else:
        r = requests.post(f"{CF_API}/zones/{zone_id}/dns_records", headers=cf_headers(), json=payload)

    if r.status_code in (200, 201):
        return True, "good"
    return False, f"error {r.status_code}: {r.text}"

def client_ip():
    hdrs = ["CF-Connecting-IP", "X-Forwarded-For"]
    for h in hdrs:
        if h in request.headers:
            return request.headers[h].split(",")[0].strip()
    return request.remote_addr

@app.route("/health")
def health():
    return jsonify(status="ok")

@app.route("/update", methods=["GET"])
def update():
    hostname = request.args.get("hostname") or request.args.get("host") or CF_RECORD_NAME
    myip     = request.args.get("myip") or request.args.get("ip") or client_ip()

    if not (CF_TOKEN and CF_ZONE_NAME and hostname):
        return "nochg missing-config", 500

    if CF_RECORD_NAME and hostname != CF_RECORD_NAME:
        return "badauth wrong-hostname", 400

    try:
        zone_id = get_zone_id(CF_ZONE_NAME)
    except Exception as e:
        return f"error zone: {e}", 500

    ok, msg = upsert_record(zone_id, hostname, myip, CF_PROXIED, CF_TTL)
    return msg if ok else msg, (200 if ok else 500)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)