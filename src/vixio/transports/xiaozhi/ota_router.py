"""
OTA Router - Over-The-Air update endpoints for Xiaozhi devices

Provides device configuration endpoints:
- GET /xiaozhi/ota/ - Health check
- POST /xiaozhi/ota/ - Device registration and configuration
- OPTIONS /xiaozhi/ota/ - CORS preflight
"""

import json
import time
import base64
from typing import Optional, Dict, Set, Callable

from fastapi import APIRouter, Request, Header
from fastapi.responses import JSONResponse
from loguru import logger

from vixio.transports.xiaozhi.auth import XiaozhiAuth, generate_mqtt_password


def create_ota_router(
    config: Dict,
    auth: XiaozhiAuth,
    auth_enabled: bool = False,
    allowed_devices: Optional[Set[str]] = None,
    get_websocket_url: Optional[Callable[[], str]] = None,
) -> APIRouter:
    """
    Create OTA router with dependencies.
    
    Args:
        config: Server configuration dict
        auth: XiaozhiAuth instance for authentication
        auth_enabled: Whether authentication is enabled
        allowed_devices: Set of allowed device IDs (if auth enabled)
        get_websocket_url: Callback to get WebSocket URL
        
    Returns:
        Configured APIRouter instance
    """
    router = APIRouter(prefix="/xiaozhi/ota", tags=["ota"])
    log = logger.bind(component="OTARouter")
    
    @router.get("/")
    async def ota_get():
        """
        Handle OTA GET request - health check.
        
        Returns:
            JSON with WebSocket URL and status
        """
        try:
            websocket_url = get_websocket_url() if get_websocket_url else "ws://unknown"
            message = f"OTA interface is running, websocket URL sent to device: {websocket_url}"
            return JSONResponse({
                "message": message,
                "websocket_url": websocket_url,
                "status": "available",
            })
        except Exception as e:
            log.error(f"OTA GET request error: {e}")
            return JSONResponse({
                "message": "OTA interface error",
                "status": "error",
            })
    
    @router.post("/")
    async def ota_post(
        request: Request,
        device_id: Optional[str] = Header(None, alias="device-id"),
        client_id: Optional[str] = Header(None, alias="client-id"),
    ):
        """
        Handle OTA POST request - device registration and configuration.
        
        Args:
            request: FastAPI request
            device_id: Device ID from header
            client_id: Client ID from header
            
        Returns:
            JSON with server time, firmware info, and connection config (MQTT or WebSocket)
        """
        cors_headers = {
            "Access-Control-Allow-Headers": "client-id, content-type, device-id",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Origin": "*",
        }
        
        try:
            body = await request.body()
            body_text = body.decode("utf-8")
            
            log.debug(f"OTA request method: {request.method}")
            log.debug(f"OTA request headers: {request.headers}")
            log.debug(f"OTA request data: {body_text}")
            
            if not device_id:
                raise ValueError("OTA request device-id header is empty")
            
            log.info(f"OTA request device ID: {device_id}")
            
            if not client_id:
                raise ValueError("OTA request client-id header is empty")
            
            log.info(f"OTA request client ID: {client_id}")
            
            data_json = json.loads(body_text)
            
            server_config = config.get("server", {})
            
            return_json = {
                "server_time": {
                    "timestamp": int(round(time.time() * 1000)),
                    "timezone_offset": server_config.get("timezone_offset", 8) * 60,
                },
                "firmware": {
                    "version": data_json.get("application", {}).get("version", "1.0.0"),
                    "url": "",
                },
            }
            
            mqtt_gateway_endpoint = server_config.get("mqtt_gateway")
            
            if mqtt_gateway_endpoint:
                # MQTT gateway configuration
                device_model = "default"
                try:
                    if "device" in data_json and isinstance(data_json["device"], dict):
                        device_model = data_json["device"].get("model", "default")
                    elif "model" in data_json:
                        device_model = data_json["model"]
                    group_id = f"GID_{device_model}".replace(":", "_").replace(" ", "_")
                except Exception as e:
                    log.error(f"Failed to get device model: {e}")
                    group_id = "GID_default"
                
                mac_address_safe = device_id.replace(":", "_")
                mqtt_client_id = f"{group_id}@@@{mac_address_safe}@@@{mac_address_safe}"
                
                user_data = {"ip": "unknown"}
                try:
                    user_data_json = json.dumps(user_data)
                    username = base64.b64encode(user_data_json.encode("utf-8")).decode("utf-8")
                except Exception as e:
                    log.error(f"Failed to generate username: {e}")
                    username = ""
                
                password = ""
                signature_key = server_config.get("mqtt_signature_key", "")
                if signature_key:
                    password = generate_mqtt_password(
                        mqtt_client_id + "|" + username,
                        signature_key
                    )
                    if not password:
                        password = ""
                else:
                    log.warning("Missing MQTT signature key, password left empty")
                
                return_json["mqtt"] = {
                    "endpoint": mqtt_gateway_endpoint,
                    "client_id": mqtt_client_id,
                    "username": username,
                    "password": password,
                    "publish_topic": "device-server",
                    "subscribe_topic": f"devices/p2p/{mac_address_safe}",
                }
                log.info(f"Configured MQTT gateway for device {device_id}")
            
            else:
                # WebSocket configuration
                token = ""
                if auth_enabled:
                    if allowed_devices:
                        if device_id not in allowed_devices:
                            token = auth.generate_ota_token(client_id, device_id)
                    else:
                        token = auth.generate_ota_token(client_id, device_id)
                
                return_json["websocket"] = {
                    "url": get_websocket_url() if get_websocket_url else "ws://unknown",
                    "token": token,
                }
                log.info(f"No MQTT gateway configured, sent WebSocket config for device {device_id}")
            
            return JSONResponse(content=return_json, headers=cors_headers)
            
        except Exception as e:
            log.error(f"OTA POST request error: {e}", exc_info=True)
            return JSONResponse(
                content={"success": False, "message": "request error."},
                headers=cors_headers
            )
    
    @router.options("/")
    async def ota_options():
        """
        Handle OTA OPTIONS request - CORS preflight.
        
        Returns:
            Empty JSON with CORS headers
        """
        return JSONResponse(
            content={},
            headers={
                "Access-Control-Allow-Headers": "client-id, content-type, device-id",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            }
        )
    
    return router

