import os

from flask import Flask, jsonify, request
from redis import Redis
from redis.exceptions import RedisError


app = Flask(__name__)
valkey_client = Redis(
    host=os.getenv("VALKEY_HOST", "my-valkey-db-svc"),
    port=int(os.getenv("VALKEY_PORT", "6379")),
    decode_responses=True,
)


@app.get("/")
def index():
    return jsonify(
        {
            "service": "valkey-demo-app",
            "valkey_host": os.getenv("VALKEY_HOST", "my-valkey-db-svc"),
            "usage": {
                "health": "/health",
                "set": "/set?key=demo&value=hola",
                "get": "/get?key=demo",
            },
        }
    )


@app.get("/health")
def health():
    try:
        pong = valkey_client.ping()
        return jsonify({"status": "ok", "valkey": pong})
    except RedisError as error:
        return jsonify({"status": "error", "message": str(error)}), 500


@app.get("/set")
def set_value():
    key = request.args.get("key")
    value = request.args.get("value")
    if not key or value is None:
        return jsonify({"error": "key and value are required"}), 400

    try:
        valkey_client.set(key, value)
        return jsonify({"stored": True, "key": key, "value": value})
    except RedisError as error:
        return jsonify({"stored": False, "message": str(error)}), 500


@app.get("/get")
def get_value():
    key = request.args.get("key")
    if not key:
        return jsonify({"error": "key is required"}), 400

    try:
        value = valkey_client.get(key)
        return jsonify({"key": key, "value": value})
    except RedisError as error:
        return jsonify({"message": str(error)}), 500
