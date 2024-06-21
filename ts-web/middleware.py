import time
from flask import jsonify
from flask_http_middleware import BaseHTTPMiddleware
import os

class AccessMiddleware(BaseHTTPMiddleware):
    def __init__(self):
        super().__init__()

    def dispatch(self, request, call_next):
        if request.headers.get("Authorization") == "Bearer " + os.environ.get('TS_WEB_TOKEN'):
            return call_next(request)
        else:
            raise Exception("Authentication Failed")

    def error_handler(self, error):
        return jsonify({"error": str(error)})


class MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self):
        super().__init__()

    def dispatch(self, request, call_next):
        t0 = time.time()
        response = call_next(request)
        response_time = time.time()-t0
        response.headers.add("response_time", response_time)
        return response


class SecureRoutersMiddleware(BaseHTTPMiddleware):
    def __init__(self, secured_routers = []):
        super().__init__()
        self.secured_routers = secured_routers
        self.token = os.environ.get('TS_WEB_TOKEN')

    def dispatch(self, request, call_next):
        if request.path in self.secured_routers:
            if request.headers.get("Authorization") == "Bearer " + os.environ.get('TS_WEB_TOKEN'):
                return call_next(request)
            else:
                return jsonify({"message":"invalid token"})
        else:
            return call_next(request)